"""Foundation 05.3: privacy, independent revocations and backup recovery."""
import json
import http.cookiejar
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor,Request,build_opener

from xblago.core import privacy_schema
from xblago.core.identity_schema import migrate_identity_store
from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.memory import SemanticMemoryRecord
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import cleanup, restore
from xblago.core.security_journal import JournalUnavailable, SecurityJournal
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import ApiError, WebApp,BlagoWebHandler
from test_identity_runtime import TestAuthenticator


def journal_logout_worker(args):
    path,token=args
    app=WebApp(SQLiteBlagoStore(Path(path)))
    app.logout(token)
    return True


def crash_after_journal_worker(args):
    path,token,challenge=args
    service=PrivacyService(WebApp(SQLiteBlagoStore(Path(path))))
    original=PrivacyControl.flush
    def interrupt(control):
        original(control)
        os._exit(74)  # synthetic process crash, not a clean Python rollback
    PrivacyControl.flush=interrupt
    service.delete(token,challenge,'УДАЛИТЬ')


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'app.sqlite3'
        self.directory = self.root/'control-plane'
        self.store = SQLiteBlagoStore(self.path)
        self.app = WebApp(self.store)
        migrate_identity_store(self.path)
        migrate_identity_runtime(self.path)
        SecurityJournal.initialize(self.directory)
        privacy_schema.migrate_privacy(self.path,self.directory)
        env = patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(self.directory),
            'XBLAGO_WEB_INVITE_CODE':'synthetic-invite','XBLAGO_LLM_PROVIDER':'local',
            'XBLAGO_PUBLIC_ORIGIN':'https://blago.test','XBLAGO_WEBAUTHN_RP_ID':'blago.test','XBLAGO_COOKIE_SECURE':'false'})
        env.start()
        self.addCleanup(env.stop)
        self.token,_ = self.app.register('person','long-test-password','synthetic-invite')
        self.uid = self.app.session(self.token)[0]
        self.service = PrivacyService(self.app)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return tuple(db.iterdump())

    def backup(self):
        return self.store.backup_database(self.root/('backup-'+str(time.time_ns())))

    def reopened(self,path):
        return WebApp(SQLiteBlagoStore(path))

    def record(self,key='skill:bicycle',value='ремонт велосипеда'):
        return SemanticMemoryRecord('',key,value,'skill','fixture','confirmed',1.0,'','')

    def add_passkey(self):
        authenticator = TestAuthenticator()
        options = self.app.identity.passkey_options('synthetic-binding',self.token,register=True)
        self.token,_ = self.app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.registration(options)},'synthetic-binding',self.token,register=True)
        return authenticator

    def test_миграция_повтор_и_проверка_привязки(self):
        before = self.snapshot()
        self.assertFalse(privacy_schema.migrate_privacy(self.path,self.directory))
        self.assertEqual(before,self.snapshot())
        other = self.root/'other-control'
        SecurityJournal.initialize(other)
        with self.assertRaises(ValueError):
            privacy_schema.migrate_privacy(self.path,other)
        self.assertEqual(before,self.snapshot())

    def test_откат_ddl_без_потери_старых_данных(self):
        path = self.root/'migration.sqlite3'
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_REQUIRE_PRIVACY_JOURNAL':''}):
            store = SQLiteBlagoStore(path)
            WebApp(store)
        migrate_identity_store(path)
        migrate_identity_runtime(path)
        with sqlite3.connect(path) as db:
            before = tuple(db.iterdump())
        original = privacy_schema._check_database
        calls = 0
        def fail(db):
            nonlocal calls
            calls += 1
            original(db)
            if calls==2:
                raise RuntimeError('synthetic migration failure')
        with patch.object(privacy_schema,'_check_database',side_effect=fail),self.assertRaises(RuntimeError):
            privacy_schema.migrate_privacy(path,self.directory)
        with sqlite3.connect(path) as db:
            self.assertEqual(before,tuple(db.iterdump()))

    def test_удалённый_trigger_и_checksum_обнаруживаются(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER privacy_session_delete')
        with self.assertRaisesRegex(ValueError,'drift'):
            privacy_schema.migrate_privacy(self.path,self.directory)

    def test_отзыв_сессии_не_воскресает_из_backup(self):
        other,_ = self.app.login('person','long-test-password')
        backup = self.backup()
        public_id = next(r['public_id'] for r in self.app.identity.sessions(self.token) if not r['current'])
        self.app.identity.revoke(self.token,public_id)
        recovered = self.reopened(backup)
        with self.assertRaises(ApiError):
            recovered.session(other)
        self.assertEqual(recovered.session(self.token)[0],self.uid)

    def test_ротация_и_logout_фиксируются_в_журнале(self):
        backup = self.backup()
        replacement,_ = self.app.login('person','long-test-password',self.token)
        with self.assertRaises(ApiError):
            self.reopened(backup).session(self.token)
        backup = self.backup()
        self.app.logout(replacement)
        with self.assertRaises(ApiError):
            self.reopened(backup).session(replacement)

    def test_сменённый_пароль_не_возвращается_после_restore(self):
        backup = self.backup()
        self.app.identity.set_password(self.token,'person','new-strong-password')
        with self.assertRaises(ApiError):
            self.reopened(backup).login('person','long-test-password')

    def test_отозванный_passkey_не_принимается_после_restore(self):
        authenticator = self.add_passkey()
        backup = self.backup()
        from webauthn.helpers import bytes_to_base64url
        self.app.identity.remove_method(self.token,'passkey',bytes_to_base64url(authenticator.cid))
        recovered = self.reopened(backup)
        options = recovered.identity.passkey_options('synthetic-binding')
        with self.assertRaises(ApiError):
            recovered.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)},'synthetic-binding')

    def test_забытые_факты_и_версии_не_восстанавливаются(self):
        memory = self.store.memory_for_user(self.uid)
        memory.set('city','Казань')
        memory.save_semantic(self.record())
        backup = self.backup()
        self.app.delete_memory(self.uid,'city')
        self.app.delete_memory(self.uid,'skill:bicycle')
        restored = self.reopened(backup)
        self.assertEqual(restored.store.all_memory(self.uid),{})
        self.assertEqual(restored.store.all_semantic_memory(self.uid,include_inactive=True),())

    def test_удаление_истории_не_воскресает(self):
        self.store.append_history(self.uid,'user','Личная история')
        backup = self.backup()
        self.store.clear_history(self.uid)
        self.assertEqual(self.reopened(backup).store.recent_history(self.uid,10),())

    def test_экспорт_только_своих_данных_без_секретов(self):
        foreign,_ = self.app.register('foreign','other-long-password','synthetic-invite')
        foreign_uid = self.app.session(foreign)[0]
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.store.memory_for_user(foreign_uid).set('city','Город другого участника')
        self.store.append_history(self.uid,'user','Мой диалог')
        data = self.service.export(self.token)
        raw = json.dumps(data,ensure_ascii=False)
        self.assertIn('Казань',raw)
        self.assertIn('Мой диалог',raw)
        self.assertNotIn('Город другого участника',raw)
        self.assertNotIn(foreign_uid,raw)
        for field in ('password_hash','salt','csrf','session_hash','public_key_cose','binding_hash','challenge_hash','key.json'):
            self.assertNotIn('"'+field+'"',raw)
        self.assertNotIn(self.token,raw)
        self.assertEqual(data['tables']['web_credentials'][0]['username'],'person')

    def test_экспорт_скрывает_контрагентов_и_чужие_записи(self):
        other,_ = self.app.register('other','other-long-password','synthetic-invite')
        peer = self.app.session(other)[0]
        request = self.app.create_payment_request(self.uid,{'title':'Счёт','amount_minor':100,'currency':'RUB','payment_purpose':'Ремонт'})
        self.app.create_payment_request(peer,{'title':'Чужой личный счёт','amount_minor':200,'currency':'RUB','payment_purpose':'Личное'})
        with self.store._locked_transaction() as db:
            db.execute('UPDATE blago_payment_requests SET executor_user_id=? WHERE id=?',(peer,request['id']))
        raw = json.dumps(self.service.export(self.token),ensure_ascii=False)
        self.assertIn('other_participant',raw)
        self.assertNotIn(peer,raw)
        self.assertNotIn('Чужой личный счёт',raw)

    def test_экспорт_и_удаление_требуют_fresh_auth(self):
        with self.store._locked_transaction() as db:
            db.execute('UPDATE web_sessions SET authenticated_at=0')
        for action in (lambda:self.service.export(self.token),lambda:self.service.preview(self.token)):
            with self.assertRaises(ApiError) as error:
                action()
            self.assertEqual(error.exception.status,403)

    def test_подтверждение_удаления_привязано_к_сессии_и_участнику(self):
        preview = self.service.preview(self.token)
        second,_ = self.app.login('person','long-test-password')
        foreign,_ = self.app.register('foreign','other-long-password','synthetic-invite')
        for token,confirm in ((self.token,'да'),(second,'УДАЛИТЬ'),(foreign,'УДАЛИТЬ')):
            with self.assertRaises(ApiError):
                self.service.delete(token,preview['challenge'],confirm)
        self.assertEqual(self.app.session(self.token)[0],self.uid)

    def test_истёкшее_подтверждение_не_удаляет_данные(self):
        preview = self.service.preview(self.token)
        with self.store._locked_transaction() as db:
            db.execute('UPDATE privacy_delete_challenges SET expires_at=1')
        with self.assertRaises(ApiError):
            self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        self.assertTrue(self.app.session(self.token))

    def test_полное_контролируемое_удаление_и_restore(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.store.append_history(self.uid,'user','Личное сообщение')
        backup = self.backup()
        preview = self.service.preview(self.token)
        self.assertTrue(self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')['deleted'])
        with self.assertRaises(ApiError):
            self.app.session(self.token)
        recovered = self.reopened(backup)
        with recovered.store._connect() as db:
            self.assertEqual(db.execute('SELECT status FROM users WHERE blago_user_id=?',(self.uid,)).fetchone()[0],'deleted')
            for table in ('profiles','memory_facts','dialogue_turns','web_credentials','web_sessions'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE blago_user_id=?',(self.uid,)).fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(ApiError):
            recovered.login('person','long-test-password')

    def test_экономические_связи_блокируют_удаление_без_частичного_стирания(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.app.create_payment_request(self.uid,{'title':'Счёт','amount_minor':100,'currency':'RUB','payment_purpose':'Ремонт'})
        before = self.snapshot()
        preview = self.service.preview(self.token)
        self.assertFalse(preview['eligible'])
        self.assertNotIn('challenge',preview)
        self.assertEqual(before,self.snapshot())

    def test_новая_неизвестная_fk_блокирует_удаление(self):
        with self.store._locked_transaction() as db:
            db.execute('CREATE TABLE future_records (owner TEXT REFERENCES users(blago_user_id))')
            db.execute('INSERT INTO future_records VALUES (?)',(self.uid,))
        self.assertIn('unreviewed_ownership_table',self.service.preview(self.token)['blockers'])

    def test_сбой_журнала_откатывает_основную_базу(self):
        before = self.snapshot()
        with patch.object(SecurityJournal,'append',side_effect=JournalUnavailable('synthetic fsync error')),self.assertRaises(JournalUnavailable):
            self.app.logout(self.token)
        self.assertEqual(before,self.snapshot())
        self.assertTrue(self.app.session(self.token))

    def test_сбой_после_durable_intent_не_воскрешает_удаление(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        preview = self.service.preview(self.token)
        original = PrivacyControl.flush
        def fail_after_append(control):
            original(control)
            raise JournalUnavailable('synthetic crash before SQLite commit')
        with patch.object(PrivacyControl,'flush',fail_after_append),self.assertRaises(JournalUnavailable):
            self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        recovered = self.reopened(self.path)
        self.assertEqual(recovered.store.all_memory(self.uid),{})
        with self.assertRaises(ApiError):
            recovered.session(self.token)

    def test_журнал_не_содержит_тексты_идентификаторы_и_секреты(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.store.delete_memory(self.uid,'city')
        self.app.logout(self.token)
        rows,_ = SecurityJournal(self.directory).read()
        raw = json.dumps(rows,ensure_ascii=False)
        for forbidden in (self.uid,self.token,'Казань','long-test-password','person'):
            self.assertNotIn(forbidden,raw)

    def test_потеря_ключа_и_неверный_журнал_закрывают_доступ(self):
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(self.root/'missing')}),self.assertRaises(JournalUnavailable):
            self.app.session(self.token)
        self.assertFalse((self.root/'missing').exists())
        other = self.root/'other-journal'
        SecurityJournal.initialize(other)
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(other)}),self.assertRaises(JournalUnavailable):
            self.app.session(self.token)

    def test_подмена_цепочки_и_rollback_журнала_обнаруживаются(self):
        original = (self.directory/'journal.sqlite3').read_bytes()
        self.app.logout(self.token)
        (self.directory/'journal.sqlite3').write_bytes(original)
        with self.assertRaises(JournalUnavailable):
            SecurityJournal(self.directory).read()

    def test_ttl_очищает_только_просроченные_технические_данные(self):
        now = int(time.time())
        expired,_ = self.app.login('person','long-test-password')
        with self.store._locked_transaction() as db:
            from xblago.integrations.identity import digest
            db.execute('UPDATE web_sessions SET last_seen_at=? WHERE session_hash=?',(now-1801,digest(expired)))
            for number,(created,ends) in enumerate(((1,100),(now,now+100))):
                tag = str(number+1)*64
                db.execute("INSERT INTO auth_challenges VALUES (?,NULL,'authenticate',?,'blago.test','https://blago.test',?,?,NULL)",(tag,'b'*64,created,ends))
                db.execute('INSERT INTO webauthn_pending VALUES (?,?)',(tag,b'opaque-handle'))
                db.execute('INSERT INTO telegram_exchanges VALUES (?,?,?,?,NULL)',(tag,'b'*64,created,ends))
                db.execute('INSERT INTO telegram_proofs VALUES (?,?,?)',(tag,self.uid,ends))
                db.execute("INSERT INTO activity_events VALUES (?,?,'login','web',?,?)",(str(number),self.uid,created,ends))
                db.execute("INSERT INTO security_signals VALUES (?,?,'failed_auth','pending',?,?)",(str(number),self.uid,created,ends))
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.store.begin_update('telegram','dedupe',self.uid)
        self.store.mark_update_processed('telegram','dedupe')
        counts = cleanup(self.store)
        self.assertEqual(counts['web_sessions'],1)
        self.assertEqual(counts['auth_challenges'],1)
        self.assertEqual(counts['telegram_proofs'],1)
        self.assertEqual(self.store.all_memory(self.uid),{'city':'Казань'})
        self.assertFalse(self.store.begin_update('telegram','dedupe',self.uid))
        self.assertTrue(self.app.session(self.token))
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM webauthn_pending').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertTrue(all(v==0 for v in cleanup(self.store).values()))

    def test_очистка_из_будущего_запрещена(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            cleanup(self.store,now=int(time.time())+100)
        self.assertEqual(before,self.snapshot())

    def test_аналитика_крупных_когорт_без_fingerprinting(self):
        own = self.service.analytics(self.token)
        self.assertIsNone(own['community']['active_participants'])
        self.assertEqual(own['personal']['active_sessions'],1)
        for number in range(4):
            self.app.register('user_'+str(number),'synthetic-long-password','synthetic-invite')
        data = self.service.analytics(self.token)
        self.assertEqual(data['community']['active_participants'],5)
        self.assertEqual(data['community']['active_sessions'],5)
        self.assertEqual(data['community']['session_methods']['password']['participants'],5)
        self.assertIsNone(data['community']['session_methods']['passkey']['participants'])
        raw = json.dumps(data)
        for field in ('ip','user_agent','fingerprint','device_id','channel_user_id',self.uid):
            self.assertNotIn('"'+field+'"',raw)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM activity_events').fetchone()[0],0)

    def test_merge_экспорт_и_удаление_исторических_профилей(self):
        identity = TelegramIdentity(101,101,'private',first_name='Имя из Telegram')
        source = self.store.resolve_telegram_user(identity)
        self.store.memory_for_user(source).set('city','Казань')
        source_token=self.app.identity.new_session(source,'telegram')[0]
        self.service.preview(source_token)
        backup = self.backup()
        self.app.redeem_telegram_link(source,self.app.new_link_code(self.uid,self.token))
        token,_ = self.app.login('person','long-test-password')
        export = self.service.export(token)
        self.assertEqual(export['accounts_in_scope'],2)
        self.assertIn('Имя из Telegram',json.dumps(export,ensure_ascii=False))
        preview = self.service.preview(token)
        self.service.delete(token,preview['challenge'],'УДАЛИТЬ')
        recovered = self.reopened(backup)
        for uid in (source,self.uid):
            self.assertEqual(recovered.store.all_memory(uid),{})
        with self.assertRaises(JournalUnavailable):
            recovered.store.resolve_telegram_user(identity)
        new_uid = recovered.store.resolve_telegram_user(identity,allow_reenroll=True)
        self.assertNotIn(new_uid,(source,self.uid))
        self.assertEqual(recovered.store.all_memory(new_uid),{})

    def test_restore_cli_повторяет_намерения_и_сбрасывает_все_сессии(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO security_signals VALUES ('expired',?,'failed_auth','pending',1,100)",(self.uid,))
        backup = self.backup()
        self.store.delete_memory(self.uid,'city')
        result = restore(backup,self.directory)
        self.assertEqual(result.all_memory(self.uid),{})
        with self.assertRaises(ApiError):
            self.reopened(backup).session(self.token)
        with result._connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('PRAGMA integrity_check')],[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('SELECT count(*) FROM security_signals').fetchone()[0],0)

    def test_два_процесса_фиксируют_отзывы_без_потери_цепочки(self):
        second,_=self.app.login('person','long-test-password')
        backup=self.backup()
        with multiprocessing.get_context('spawn').Pool(2) as pool:
            self.assertEqual(pool.map(journal_logout_worker,[(str(self.path),self.token),(str(self.path),second)]),[True,True])
        rows,chain=SecurityJournal(self.directory).read()
        self.assertEqual(len(chain),len(rows)+1)
        recovered=self.reopened(backup)
        for token in (self.token,second):
            with self.assertRaises(ApiError):
                recovered.session(token)

    def test_конкурентное_удаление_одна_успешная_фиксация(self):
        second,_=self.app.login('person','long-test-password')
        previews=[self.service.preview(self.token),self.service.preview(second)]
        # Each session gets its own confirmation; the second preview replaces the first.
        # This is intentional: only the latest explicit deletion review is valid.
        def remove(args):
            token,preview=args
            try:
                service=PrivacyService(self.reopened(self.path))
                return service.delete(token,preview['challenge'],'УДАЛИТЬ')['deleted']
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(remove,[(self.token,previews[0]),(second,previews[1])]))
        self.assertEqual(results.count(True),1)
        self.assertEqual(len(SecurityJournal(self.directory).read()[0])>0,True)

    def test_изменённая_подписанная_запись_обнаруживается(self):
        self.app.logout(self.token)
        with sqlite3.connect(self.directory/'journal.sqlite3') as db:
            db.execute("UPDATE entries SET payload='{}' WHERE seq=1")
        with self.assertRaises(JournalUnavailable):
            self.app.session(self.token)

    def test_удалённый_trigger_закрывает_runtime(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER privacy_session_delete')
        with self.assertRaises(JournalUnavailable):
            self.app.logout(self.token)

    def test_export_лимит_не_выдаёт_неполный_экспорт(self):
        with patch('xblago.integrations.privacy.MAX_EXPORT_ROWS',0),self.assertRaises(ApiError) as error:
            self.service.export(self.token)
        self.assertEqual(error.exception.status,413)

    def test_старые_неопределённые_merge_не_объявляются_удалёнными(self):
        source=self.store.resolve_telegram_user(TelegramIdentity(777,777,'private'))
        with self.store._locked_transaction() as db:
            db.execute("UPDATE users SET status='merged' WHERE blago_user_id=?",(source,))
        self.assertIn('legacy_merge_ownership_review',self.service.preview(self.token)['blockers'])

    def test_пересоздание_telegram_не_импортирует_удалённый_legacy_json(self):
        identity=TelegramIdentity(101,101,'private')
        source=self.store.resolve_telegram_user(identity)
        token=self.app.identity.new_session(source,'telegram')[0]
        preview=self.service.preview(token)
        self.service.delete(token,preview['challenge'],'УДАЛИТЬ')
        legacy=self.root/'legacy'
        legacy.mkdir()
        (legacy/'telegram_101.json').write_text(json.dumps({'city':'Старый город'}))
        store=SQLiteBlagoStore(self.path,legacy_memory_dir=legacy)
        with self.assertRaises(JournalUnavailable):
            store.resolve_telegram_user(identity)
        new_uid=store.resolve_telegram_user(identity,allow_reenroll=True)
        self.assertNotEqual(new_uid,source)
        self.assertEqual(store.all_memory(new_uid),{})

    def test_чужой_supersedes_fk_не_приводит_к_чужому_удалению(self):
        self.store.memory_for_user(self.uid).save_semantic(self.record())
        other,_=self.app.register('foreign','other-long-password','synthetic-invite')
        peer=self.app.session(other)[0]
        self.store.memory_for_user(peer).save_semantic(self.record('skill:other','рисование'))
        with self.store._locked_transaction() as db:
            first=db.execute('SELECT id FROM semantic_memory WHERE blago_user_id=?',(self.uid,)).fetchone()[0]
            db.execute('UPDATE semantic_memory SET supersedes_id=? WHERE blago_user_id=?',(first,peer))
        preview=self.service.preview(self.token)
        with self.assertRaises(ApiError):
            self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.store.all_memory(peer),{'skill:other':'рисование'})
        self.assertTrue(self.app.session(self.token))

    def test_http_csrf_idor_экспорт_удаление_и_отказ_без_журнала(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
        server.blago_web_app=self.app
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        jar=http.cookiejar.CookieJar()
        opener=build_opener(HTTPCookieProcessor(jar))
        def request(path,method='GET',data=None,csrf=None,origin=None):
            headers={'Origin':origin or base}
            if csrf:
                headers['X-CSRF-Token']=csrf
            raw=None
            if data is not None:
                raw=json.dumps(data).encode()
                headers['Content-Type']='application/json'
            try:
                with opener.open(Request(base+'/api/v1/'+path,data=raw,headers=headers,method=method),timeout=10) as result:
                    return result.status,json.loads(result.read())
            except HTTPError as error:
                return error.code,json.loads(error.read())
        self.assertEqual(request('privacy/analytics')[0],401)
        status,login=request('auth/login','POST',{'username':'person','password':'long-test-password'})
        self.assertEqual(status,200)
        csrf=login['csrf']
        self.assertEqual(request('privacy/export','POST',{})[0],403)
        self.assertEqual(request('privacy/export','POST',{},csrf,origin='https://evil.test')[0],403)
        self.assertEqual(request('privacy/export','POST',{'user_id':'other'},csrf)[0],400)
        self.assertEqual(request('privacy/export','POST',{},csrf)[1]['blago_user_id'],self.uid)
        self.assertEqual(request('privacy/analytics')[0],200)
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(self.root/'missing-journal')}):
            self.assertEqual(request('me')[0],503)
        status,preview=request('privacy/delete-preview','POST',{},csrf)
        self.assertEqual(status,200)
        self.assertEqual(request('privacy/account','DELETE',{'challenge':preview['challenge'],'confirm':'да'},csrf)[0],400)
        self.assertEqual(request('privacy/account','DELETE',{'challenge':preview['challenge'],'confirm':'УДАЛИТЬ'},csrf)[0],200)
        self.assertEqual(request('me')[0],401)

    def test_реальный_обрыв_процесса_после_журнала_до_commit(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        preview=self.service.preview(self.token)
        process=multiprocessing.get_context('spawn').Process(target=crash_after_journal_worker,args=((str(self.path),self.token,preview['challenge']),))
        process.start()
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join()
            self.fail('Synthetic crash process timed out')
        self.assertEqual(process.exitcode,74)
        recovered=self.reopened(self.path)
        self.assertEqual(recovered.store.all_memory(self.uid),{})
        with self.assertRaises(ApiError):
            recovered.session(self.token)

    def test_restore_снимка_до_миграции_053(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        backup=self.backup()
        with sqlite3.connect(backup) as db:
            objects=db.execute("SELECT type,name FROM sqlite_master WHERE name LIKE 'privacy_%' AND sql IS NOT NULL").fetchall()
            for kind,name in sorted(objects,key=lambda row:row[0]!='trigger'):
                db.execute(f'DROP {kind} "{name}"')
        self.store.delete_memory(self.uid,'city')
        with self.assertRaises(JournalUnavailable):
            self.reopened(backup)
        recovered=restore(backup,self.directory)
        self.assertEqual(recovered.all_memory(self.uid),{})

    def test_запоздавшая_запись_после_удаления_не_возвращает_данные(self):
        preview=self.service.preview(self.token)
        self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        for operation in (lambda:self.store.memory_for_user(self.uid).set('city','Казань'),
                          lambda:self.store.append_history(self.uid,'assistant','Поздний ответ'),
                          lambda:self.app.create_payment_request(self.uid,{'title':'Счёт','amount_minor':100,'currency':'RUB','payment_purpose':'Ремонт'})):
            with self.assertRaises(sqlite3.IntegrityError):
                operation()
        for sql in ("UPDATE users SET status='active' WHERE blago_user_id=?",'DELETE FROM users WHERE blago_user_id=?'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store._locked_transaction() as db:
                    db.execute(sql,(self.uid,))
        self.assertEqual(self.store.all_memory(self.uid),{})

    def test_отозванное_согласие_не_становится_granted_после_restore(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO consent_records VALUES ('consent',?,'optional_activity','1','granted',?)",(self.uid,int(time.time())))
        backup=self.backup()
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO consent_records VALUES ('withdrawal',?,'optional_activity','1','withdrawn',?)",(self.uid,int(time.time())))
        recovered=self.reopened(backup)
        with recovered.store._connect() as db:
            latest=db.execute('SELECT decision FROM consent_records WHERE blago_user_id=? ORDER BY recorded_at DESC LIMIT 1',(self.uid,)).fetchone()
            self.assertEqual(latest[0],'withdrawn')
            self.assertEqual(db.execute('SELECT count(*) FROM activity_events').fetchone()[0],0)
