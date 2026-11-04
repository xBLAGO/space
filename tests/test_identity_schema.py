"""Foundation 05: совместимость миграции с общей базой всех каналов."""
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from xblago.core import identity_schema as schema
from xblago.core.memory import SemanticMemoryRecord
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.web import WebApp


class IdentitySchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.store = SQLiteBlagoStore(self.path)
        env = patch.dict(os.environ, {'XBLAGO_LLM_PROVIDER': 'local'})
        env.start()
        self.addCleanup(env.stop)

    def user(self, number=101):
        return self.store.resolve_telegram_user(TelegramIdentity(number, number, 'private'))

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return tuple(db.iterdump())

    def test_пустая_инициализированная_база_и_повторный_запуск(self):
        self.assertTrue(schema.migrate_identity_store(self.path))
        before = self.snapshot()
        self.assertFalse(schema.migrate_identity_store(self.path))
        self.assertEqual(before, self.snapshot())
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_неинициализированная_и_несуществующая_базы_не_меняются(self):
        empty = self.path.parent / 'empty.sqlite3'
        sqlite3.connect(empty).close()
        with self.assertRaisesRegex(ValueError, 'Initialize Foundation'):
            schema.migrate_identity_store(empty)
        with sqlite3.connect(empty) as db:
            self.assertEqual(db.execute('SELECT * FROM sqlite_master').fetchall(), [])
        missing = self.path.parent / 'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            schema.migrate_identity_store(missing)
        self.assertFalse(missing.exists())

    def test_все_старые_данные_и_схема_сохраняются(self):
        WebApp(self.store)
        uid = self.user()
        memory = self.store.memory_for_user(uid)
        memory.set('город', 'Казань')
        memory.save_semantic(SemanticMemoryRecord('', 'preference:drink', 'кофе', 'preference', 'fixture', 'confirmed', 1.0, '', ''))
        self.store.append_history(uid, 'user', 'Я предпочитаю кофе', channel='web')
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO blago_needs VALUES ('need', ?, 'Велосипед', 'Ремонт', 'private', 'open', 'now')", (uid,))
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {t: db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
            old_schema = db.execute('SELECT name, sql FROM sqlite_master').fetchall()
        schema.migrate_identity_store(self.path)
        with self.store._connect() as db:
            self.assertEqual(before, {t: db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables})
            after_schema = db.execute('SELECT name, sql FROM sqlite_master').fetchall()
            self.assertTrue(all(row in after_schema for row in old_schema))
        self.assertEqual(self.store.all_memory(uid), {'город': 'Казань', 'preference:drink': 'кофе'})
        self.assertEqual(self.store.recent_history(uid, 10)[0].text, 'Я предпочитаю кофе')

    def test_повтор_telegram_update_после_миграции(self):
        schema.migrate_identity_store(self.path)
        bot = TelegramBot(None, self.path.parent, store=self.store)
        update = {'update_id': 1, 'message': {'from': {'id': 101}, 'chat': {'id': 101, 'type': 'private'}, 'text': 'Запомни мой город Казань'}}
        first = bot.replies_for_update(update)
        uid = self.user()
        before = (self.store.all_memory(uid), self.store.recent_history(uid, 100))
        self.assertEqual(first, bot.replies_for_update(update))
        self.assertEqual(before, (self.store.all_memory(uid), self.store.recent_history(uid, 100)))
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_конкурентные_telegram_привязки_и_миграции(self):
        def resolve(_):
            return SQLiteBlagoStore(self.path).resolve_telegram_user(TelegramIdentity(101, 101, 'private'))
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(len(set(pool.map(resolve, range(16)))), 1)
            results = list(pool.map(lambda _: schema.migrate_identity_store(self.path), range(8)))
        self.assertEqual(results.count(True), 1)

    def test_rollback_при_ошибке_после_создания_таблиц(self):
        self.user()
        before = self.snapshot()
        original = schema._check_database
        calls = 0
        def fail_after_ddl(db):
            nonlocal calls
            calls += 1
            original(db)
            if calls == 2:
                raise RuntimeError('Искусственный сбой перед commit')
        with patch.object(schema, '_check_database', side_effect=fail_after_ddl):
            with self.assertRaises(RuntimeError):
                schema.migrate_identity_store(self.path)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(schema.migrate_identity_store(self.path))

    def test_коллизия_схемы_и_повреждение_журнала_отклоняются(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE user_devices (incompatible TEXT)')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'collision'):
            schema.migrate_identity_store(self.path)
        self.assertEqual(before, self.snapshot())

    def test_подмена_checksum_или_индекса_отклоняется(self):
        schema.migrate_identity_store(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE identity_schema_migrations SET checksum='wrong'")
        with self.assertRaisesRegex(ValueError, 'ledger'):
            schema.migrate_identity_store(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE identity_schema_migrations SET checksum=?', (schema.CHECKSUM,))
            db.execute('DROP INDEX idx_passkey_user')
        with self.assertRaisesRegex(ValueError, 'drift'):
            schema.migrate_identity_store(self.path)

    def test_чужое_устройство_и_неизвестный_участник_запрещены(self):
        first, second = self.user(), self.user(202)
        schema.migrate_identity_store(self.path)
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO user_devices VALUES ('device', ?, NULL, 1, NULL)", (first,))
        for owner in (second, 'missing'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store._locked_transaction() as db:
                    db.execute('INSERT INTO passkey_credentials VALUES (?, ?, ?, ?, ?, 0, 0, 0, 1, NULL)',
                               (b'credential', owner, 'device', b'public-key', b'opaque-handle'))
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT device_id FROM user_devices WHERE blago_user_id=?', (second,)).fetchall(), [])
            self.assertEqual(db.execute('SELECT * FROM passkey_credentials').fetchall(), [])

    def test_конкурентная_привязка_одного_credential_не_меняет_владельца(self):
        users = (self.user(), self.user(202))
        schema.migrate_identity_store(self.path)
        def insert(owner):
            try:
                with SQLiteBlagoStore(self.path)._locked_transaction() as db:
                    db.execute('INSERT INTO passkey_credentials VALUES (?, ?, NULL, ?, ?, 0, 0, 0, 1, NULL)', (b'one', owner, b'key', b'handle'))
                return owner
            except sqlite3.IntegrityError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(insert, users))
        with self.store._connect() as db:
            owner = db.execute('SELECT blago_user_id FROM passkey_credentials').fetchone()[0]
        self.assertEqual([r for r in results if r], [owner])

    def test_недопустимые_challenge_и_события_отклоняются(self):
        uid = self.user()
        schema.migrate_identity_store(self.path)
        statements = [
            ("INSERT INTO auth_challenges VALUES (?, NULL, 'register_passkey', ?, 'example.test', 'https://example.test', 1, 10, NULL)", ('a'*64, 'b'*64)),
            ("INSERT INTO activity_events VALUES ('event', ?, 'raw_chat_text', 'web', 1, 2)", (uid,)),
            ("INSERT INTO security_signals VALUES ('signal', ?, 'fingerprint', 'pending', 1, 2)", (uid,)),
        ]
        for sql, params in statements:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with self.store._locked_transaction() as db:
                    db.execute(sql, params)

    def test_резервная_копия_до_и_после_миграции_восстанавливается(self):
        uid = self.user()
        self.store.memory_for_user(uid).set('city', 'Казань')
        before = self.store.backup_database(self.path.parent / 'before')
        schema.migrate_identity_store(self.path)
        after = self.store.backup_database(self.path.parent / 'after')
        self.assertTrue(schema.migrate_identity_store(before))
        self.assertFalse(schema.migrate_identity_store(after))
        for restored in (before, after):
            self.assertEqual(SQLiteBlagoStore(restored).all_memory(uid), {'city': 'Казань'})

    def test_запуск_приложения_не_применяет_новую_миграцию(self):
        WebApp(self.store)
        self.user()
        with self.store._connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='user_devices'").fetchone())

    def test_существующее_нарушение_fk_блокирует_миграцию_без_записи(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO profiles VALUES ('missing', NULL, NULL, NULL, 'now')")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'foreign_key_check'):
            schema.migrate_identity_store(self.path)
        self.assertEqual(before, self.snapshot())

    def test_web_telegram_mini_app_один_id_после_миграции_и_перезапуска(self):
        app = WebApp(self.store)
        with patch.dict(os.environ, {'XBLAGO_WEB_INVITE_CODE': 'synthetic-invite', 'TELEGRAM_BOT_TOKEN': '123:test-token'}):
            token, _ = app.register('test_user', 'synthetic-long-password', 'synthetic-invite')
            web_uid = app.session(token)[0]
            tg_uid = self.user()
            self.store.memory_for_user(tg_uid).set('city', 'Казань')
            self.store.append_history(tg_uid, 'user', 'Привет из Telegram')
            schema.migrate_identity_store(self.path)
            self.assertEqual(app.redeem_telegram_link(tg_uid, app.new_link_code(web_uid)), web_uid)
            params = {'user': json.dumps({'id': 101}), 'auth_date': str(int(time.time()))}
            check = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
            secret = hmac.new(b'WebAppData', b'123:test-token', hashlib.sha256).digest()
            params['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            mini_token, _ = app.telegram_login(urlencode(params))
            self.assertEqual(app.session(mini_token)[0], web_uid)
            restarted = WebApp(SQLiteBlagoStore(self.path))
            self.assertEqual(restarted.session(token)[0], web_uid)
            self.assertEqual(restarted.session(mini_token)[0], web_uid)
            self.assertEqual(restarted.store.all_memory(web_uid), {'city': 'Казань'})
            self.assertEqual(restarted.store.recent_history(web_uid, 10)[0].text, 'Привет из Telegram')
            with restarted.store._connect() as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
