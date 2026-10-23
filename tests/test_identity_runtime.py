"""Негативные и интеграционные проверки Foundation 05.2, без внешней LLM."""
import copy
import hashlib
import hmac
import http.cookiejar
import json
import multiprocessing
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url as b64

from xblago.core import identity_runtime_schema as schema
from xblago.core.identity_schema import migrate_identity_store
from xblago.core.memory import SemanticMemoryRecord
from xblago.core.storage import SQLiteBlagoStore, TelegramIdentity
from xblago.integrations.identity import IDLE_SECONDS, ABSOLUTE_SECONDS
from xblago.integrations.web import ApiError, WebApp, BlagoWebHandler
from xblago.integrations.telegram import TelegramBot


def signed_telegram(number=101, stamp=None):
    fields = {'user': json.dumps({'id': number, 'first_name': 'Тест'}), 'auth_date': str(stamp or int(time.time()))}
    check = '\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', b'123:test-only', hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def exchange_worker(args):
    path, raw, challenge, binding = args
    try:
        app = WebApp(SQLiteBlagoStore(Path(path)))
        return app.telegram_login(raw, challenge, binding)[0]
    except ApiError as exc:
        return exc.status


class TestAuthenticator:
    """Synthetic P-256 authenticator; production verification is entirely py_webauthn."""
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.cid = secrets.token_bytes(32)
        pub = self.private.public_key().public_numbers()
        self.cose = cbor2.dumps({1:2, 3:-7, -1:1, -2:pub.x.to_bytes(32,'big'), -3:pub.y.to_bytes(32,'big')})
        self.handle = None

    def registration(self, options, origin='https://blago.test', rp='blago.test', flags=69):
        self.handle = options['user']['id']
        client = json.dumps({'type':'webauthn.create','challenge':options['challenge'],'origin':origin,'crossOrigin':False}).encode()
        auth = hashlib.sha256(rp.encode()).digest()+bytes([flags])+bytes(4)+bytes(16)+len(self.cid).to_bytes(2,'big')+self.cid+self.cose
        return {'id':b64(self.cid),'rawId':b64(self.cid),'type':'public-key', 'response':{
            'clientDataJSON':b64(client),'attestationObject':b64(cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth}))}}

    def assertion(self, options, origin='https://blago.test', rp='blago.test', counter=1, flags=5):
        client = json.dumps({'type':'webauthn.get','challenge':options['challenge'],'origin':origin,'crossOrigin':False}).encode()
        auth = hashlib.sha256(rp.encode()).digest()+bytes([flags])+counter.to_bytes(4,'big')
        signature = self.private.sign(auth+hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'id':b64(self.cid),'rawId':b64(self.cid),'type':'public-key', 'response':{
            'clientDataJSON':b64(client),'authenticatorData':b64(auth),'signature':b64(signature),'userHandle':self.handle}}


class IdentityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'test.sqlite3'
        self.store = SQLiteBlagoStore(self.path)
        self.app = WebApp(self.store)
        env = patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:test-only', 'XBLAGO_WEB_INVITE_CODE':'test-invite',
            'XBLAGO_PUBLIC_ORIGIN':'https://blago.test', 'XBLAGO_WEBAUTHN_RP_ID':'blago.test',
            'XBLAGO_LLM_PROVIDER':'local', 'XBLAGO_COOKIE_SECURE':'false'})
        env.start()
        self.addCleanup(env.stop)
        migrate_identity_store(self.path)
        schema.migrate_identity_runtime(self.path)
        self.token, self.csrf = self.app.register('person', 'test-long-password', 'test-invite')
        self.uid = self.app.session(self.token)[0]
        self.runtime = self.app.identity
        self.binding = 'synthetic-browser-binding-32-characters'

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return tuple(db.iterdump())

    def register_passkey(self, token=None):
        token = token or self.token
        authenticator = TestAuthenticator()
        options = self.runtime.passkey_options(self.binding, token, register=True)
        replacement, csrf = self.runtime.passkey_verify({'challenge':options['challenge'], 'credential':authenticator.registration(options)}, self.binding, token, register=True)
        return authenticator, replacement, csrf

    def test_миграция_повтор_и_rollback(self):
        self.assertFalse(schema.migrate_identity_runtime(self.path))
        p = self.path.parent/'other.sqlite3'
        store = SQLiteBlagoStore(p)
        WebApp(store)
        migrate_identity_store(p)
        with sqlite3.connect(p) as db:
            before = tuple(db.iterdump())
        original = schema._check_database
        calls = 0
        def fail(db):
            nonlocal calls
            calls += 1
            original(db)
            if calls == 2:
                raise RuntimeError('Искусственный сбой')
        with patch.object(schema, '_check_database', side_effect=fail), self.assertRaises(RuntimeError):
            schema.migrate_identity_runtime(p)
        with sqlite3.connect(p) as db:
            self.assertEqual(before, tuple(db.iterdump()))
        self.assertTrue(schema.migrate_identity_runtime(p))

    def test_старый_аккаунт_и_сессия_не_получают_fresh_auth(self):
        p = self.path.parent/'legacy.sqlite3'
        store = SQLiteBlagoStore(p)
        app = WebApp(store)
        token, _ = app.register('legacy', 'test-long-password', 'test-invite')
        uid = app.session(token)[0]
        migrate_identity_store(p)
        schema.migrate_identity_runtime(p)
        self.assertEqual(app.session(token)[0], uid)
        with self.assertRaises(ApiError):
            app.new_link_code(uid, token)
        replacement, _ = app.login('legacy','test-long-password',token)
        with self.assertRaises(ApiError):
            app.session(token)
        self.assertEqual(app.session(replacement)[0], uid)

    def test_idle_и_absolute_expiration(self):
        now = int(time.time())
        with patch('xblago.integrations.identity.time.time', return_value=now+IDLE_SECONDS+1), self.assertRaises(ApiError):
            self.app.session(self.token)
        with self.store._locked_transaction() as db:
            db.execute('UPDATE web_sessions SET last_seen_at=?', (now+ABSOLUTE_SECONDS,))
        with patch('xblago.integrations.identity.time.time', return_value=now+ABSOLUTE_SECONDS+1), self.assertRaises(ApiError):
            self.app.session(self.token)

    def test_отзыв_одной_и_всех_других_сессий_и_idor(self):
        second, _ = self.app.login('person','test-long-password')
        foreign, _ = self.app.register('foreign', 'test-long-password', 'test-invite')
        rows = self.runtime.sessions(self.token)
        self.assertEqual(len(rows), 2)
        foreign_id = self.runtime.sessions(foreign)[0]['public_id']
        with self.assertRaises(ApiError) as error:
            self.runtime.revoke(self.token, foreign_id)
        self.assertEqual(error.exception.status, 404)
        other_id = next(r['public_id'] for r in rows if not r['current'])
        self.runtime.revoke(self.token, other_id)
        with self.assertRaises(ApiError):
            self.app.session(second)
        third, _ = self.app.login('person', 'test-long-password')
        self.runtime.revoke(self.token, others=True)
        with self.assertRaises(ApiError):
            self.app.session(third)
        self.assertEqual(self.app.session(self.token)[0], self.uid)
        self.assertNotEqual(self.app.session(foreign)[0], self.uid)

    def test_статус_merged_проверяется_при_каждом_запросе(self):
        with self.store._locked_transaction() as db:
            db.execute("UPDATE users SET status='merged' WHERE blago_user_id=?", (self.uid,))
        for call in (lambda:self.app.session(self.token), lambda:self.app.login('person','test-long-password')):
            with self.assertRaises(ApiError):
                call()

    def test_telegram_replay_и_подмена_binding_без_частичных_записей(self):
        raw = signed_telegram()
        challenge = self.runtime.telegram_challenge(self.binding)
        before = self.snapshot()
        with self.assertRaises(ApiError):
            self.app.telegram_login(raw, challenge, 'attacker-binding')
        self.assertEqual(before, self.snapshot())
        token, _ = self.app.telegram_login(raw, challenge, self.binding)
        self.assertTrue(self.app.session(token)[0])
        with self.assertRaises(ApiError):
            self.app.telegram_login(raw, challenge, self.binding)
        second = self.runtime.telegram_challenge(self.binding)
        with self.assertRaises(ApiError):
            self.app.telegram_login(raw, second, self.binding)
        reordered = '&'.join(reversed(raw.split('&')))
        with self.assertRaises(ApiError):
            self.app.telegram_login(reordered, second, self.binding)
        restarted = WebApp(SQLiteBlagoStore(self.path))
        self.assertEqual(restarted.session(token)[0], self.app.session(token)[0])
        # Fresh signed data may open again; an existing session needs no exchange.
        fresh = signed_telegram(stamp=int(time.time())+1)
        fresh_token, _ = restarted.telegram_login(fresh, second, self.binding, token)
        with self.assertRaises(ApiError):
            restarted.session(token)
        self.assertEqual(restarted.session(fresh_token)[0], self.app.session(fresh_token)[0])

    def test_telegram_конкурентный_challenge_в_разных_процессах(self):
        raw = signed_telegram(404)
        challenge = self.runtime.telegram_challenge(self.binding)
        args = (str(self.path), raw, challenge, self.binding)
        with multiprocessing.get_context('spawn').Pool(3) as pool:
            results = pool.map(exchange_worker, [args]*3)
        self.assertEqual(sum(isinstance(r,str) for r in results), 1)
        self.assertEqual(results.count(409), 2)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_proofs').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM channel_links WHERE channel_user_id='404'").fetchone()[0], 1)

    def test_telegram_истёкший_challenge_отклоняется(self):
        challenge = self.runtime.telegram_challenge(self.binding)
        with patch('xblago.integrations.identity.time.time', return_value=int(time.time())+301), self.assertRaises(ApiError):
            self.app.telegram_login(signed_telegram(), challenge, self.binding)

    def test_настоящая_регистрация_и_подпись_webauthn_ротация_и_replay(self):
        authenticator, replacement, _ = self.register_passkey()
        with self.assertRaises(ApiError):
            self.app.session(self.token)
        self.assertEqual(self.app.session(replacement)[0], self.uid)
        options = self.runtime.passkey_options(self.binding)
        payload = {'challenge':options['challenge'], 'credential':authenticator.assertion(options)}
        token, _ = self.runtime.passkey_verify(payload, self.binding, replacement)
        self.assertEqual(self.app.session(token)[0], self.uid)
        with self.assertRaises(ApiError):
            self.app.session(replacement)
        with self.assertRaises(ApiError):
            self.runtime.passkey_verify(payload, self.binding)
        self.assertEqual(WebApp(SQLiteBlagoStore(self.path)).session(token)[0], self.uid)

    def test_неверные_signature_origin_rp_uv_handle_и_cross_origin(self):
        authenticator, _, _ = self.register_passkey()
        for mutation in ('signature','origin','rp','uv','handle','cross_origin'):
            with self.subTest(mutation=mutation):
                options = self.runtime.passkey_options(self.binding)
                kwargs = {'origin':'https://evil.test'} if mutation=='origin' else {'rp':'evil.test'} if mutation=='rp' else {'flags':1} if mutation=='uv' else {}
                credential = authenticator.assertion(options, **kwargs)
                if mutation=='signature':
                    credential['response']['signature'] = b64(b'invalid-signature')
                elif mutation=='handle':
                    credential['response']['userHandle'] = b64(b'wrong-user')
                elif mutation=='cross_origin':
                    from webauthn.helpers import base64url_to_bytes
                    client = json.loads(base64url_to_bytes(credential['response']['clientDataJSON']))
                    client['crossOrigin'] = True
                    credential['response']['clientDataJSON'] = b64(json.dumps(client).encode())
                before = self.snapshot()
                with self.assertRaises(ApiError):
                    self.runtime.passkey_verify({'challenge':options['challenge'],'credential':credential}, self.binding)
                self.assertEqual(before, self.snapshot())

    def test_неверная_регистрация_и_cross_user_challenge(self):
        options = self.runtime.passkey_options(self.binding, self.token, register=True)
        authenticator = TestAuthenticator()
        payload = {'challenge':options['challenge'],'credential':authenticator.registration(options, origin='https://evil.test')}
        with self.assertRaises(ApiError):
            self.runtime.passkey_verify(payload, self.binding, self.token, register=True)
        foreign, _ = self.app.register('foreign','test-long-password','test-invite')
        payload['credential'] = authenticator.registration(options)
        with self.assertRaises(ApiError):
            self.runtime.passkey_verify(payload, self.binding, foreign, register=True)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM passkey_credentials').fetchone()[0], 0)

    def test_конкурентное_использование_webauthn_challenge(self):
        authenticator, _, _ = self.register_passkey()
        options = self.runtime.passkey_options(self.binding)
        payload = {'challenge':options['challenge'], 'credential':authenticator.assertion(options)}
        def verify(_):
            try:
                return WebApp(SQLiteBlagoStore(self.path)).identity.passkey_verify(payload, self.binding)[0]
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(verify, range(3)))
        self.assertEqual(sum(isinstance(r,str) for r in results), 1)
        self.assertEqual(results.count(409), 2)

    def test_удаление_последнего_метода_запрещено_восстановление_через_passkey(self):
        with self.assertRaises(ApiError):
            self.runtime.remove_method(self.token, 'password', 'password')
        authenticator, _, _ = self.register_passkey()
        options = self.runtime.passkey_options(self.binding)
        token, _ = self.runtime.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)}, self.binding)
        replacement, _ = self.runtime.remove_method(token, 'password', 'password')
        self.assertEqual(self.app.session(replacement)[0], self.uid)
        with self.assertRaises(ApiError):
            self.app.login('person', 'test-long-password')
        with self.assertRaises(ApiError):
            self.runtime.remove_method(replacement, 'passkey', b64(authenticator.cid))

    def test_удаление_чужого_passkey_не_меняет_данные(self):
        authenticator, token, _ = self.register_passkey()
        foreign, _ = self.app.register('foreign', 'test-long-password', 'test-invite')
        before = self.snapshot()
        with self.assertRaises(ApiError):
            self.runtime.remove_method(foreign, 'passkey', b64(authenticator.cid))
        self.assertEqual(before, self.snapshot())
        replacement, _ = self.runtime.remove_method(token, 'passkey', b64(authenticator.cid))
        self.assertEqual(self.runtime.methods(replacement), [{'kind':'password','id':'password'}])

    def test_account_merge_все_новые_fk_память_история_и_отзыв_сессий(self):
        source = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        source_token = self.runtime.new_session(source, 'telegram')[0]
        authenticator, source_token, _ = self.register_passkey(source_token)
        self.store.memory_for_user(source).set('city','Казань')
        self.store.memory_for_user(source).save_semantic(SemanticMemoryRecord('', 'skill:bicycle', 'ремонт велосипеда', 'skill', 'fixture', 'confirmed', 1.0, '', ''))
        self.store.append_history(source, 'user', 'История Telegram')
        asset = self.app.create_asset(source, {'title':'Велосипед','asset_type':'tool'})
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO user_devices VALUES ('device', ?, NULL, 1, NULL)", (source,))
            db.execute('UPDATE passkey_credentials SET device_id=? WHERE blago_user_id=?', ('device',source))
            db.execute("INSERT INTO consent_records VALUES ('consent', ?, 'device_label', '1', 'granted', 1)", (source,))
            db.execute("INSERT INTO security_signals VALUES ('signal', ?, 'failed_auth', 'pending', 1, 2)", (source,))
            db.execute("INSERT INTO activity_events VALUES ('event', ?, 'login', 'web', 1, 2)", (source,))
        code = self.app.new_link_code(self.uid, self.token)
        self.assertEqual(self.app.redeem_telegram_link(source, code), self.uid)
        for old in (self.token, source_token):
            with self.assertRaises(ApiError):
                self.app.session(old)
        self.assertEqual(self.store.resolve_telegram_user(TelegramIdentity(101,101,'private')), self.uid)
        self.assertEqual(self.store.all_memory(self.uid)['city'],'Казань')
        self.assertEqual(self.store.recent_history(self.uid,10)[0].text,'История Telegram')
        self.assertEqual(self.app.assets(self.uid)[0]['id'],asset['id'])
        with self.store._connect() as db:
            for table in ('user_devices','passkey_credentials','consent_records','security_signals','activity_events'):
                self.assertEqual(db.execute(f'SELECT blago_user_id FROM {table}').fetchone()[0],self.uid)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        # Existing source authenticator keeps its immutable userHandle after merge.
        options = self.runtime.passkey_options(self.binding)
        token, _ = self.runtime.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)}, self.binding)
        self.assertEqual(self.app.session(token)[0],self.uid)

    def test_merge_экономический_конфликт_откатывает_всё(self):
        source = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        request = self.app.create_payment_request(self.uid, {'title':'Счёт','amount_minor':100,'currency':'RUB','payment_purpose':'Ремонт'})
        offers = [self.app.create_resource_offer(uid, {'title':'Помощь','offer_type':'external_payment','visibility':'community','max_amount_minor':1000,'currency':'RUB'}) for uid in (source,self.uid)]
        with self.store._locked_transaction() as db:
            for number, uid in enumerate((source,self.uid)):
                db.execute('INSERT INTO blago_payment_request_offers VALUES (?,?,?,?,?,?,?)', (str(number),request['id'],offers[number]['id'],uid,'offered','now','now'))
        code = self.app.new_link_code(self.uid, self.token)
        before = self.snapshot()
        with self.assertRaises(ApiError) as error:
            self.app.redeem_telegram_link(source, code)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.app.session(self.token)[0],self.uid)

    def test_backup_и_перезапуск_сохраняют_защиту_от_replay(self):
        raw = signed_telegram()
        challenge = self.runtime.telegram_challenge(self.binding)
        token, _ = self.app.telegram_login(raw,challenge,self.binding)
        backup = self.store.backup_database(self.path.parent/'backups')
        restored = WebApp(SQLiteBlagoStore(backup))
        self.assertEqual(restored.session(token)[0],self.app.session(token)[0])
        self.assertFalse(schema.migrate_identity_runtime(backup))
        with self.assertRaises(ApiError):
            restored.telegram_login(raw, challenge, self.binding)
        with restored.store._connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('PRAGMA integrity_check')],[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_необязательная_активность_не_собирается(self):
        self.app.login('person','test-long-password')
        self.runtime.sessions(self.token)
        self.runtime.methods(self.token)
        with self.store._connect() as db:
            for table in ('activity_events','security_signals','user_devices','consent_records'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_восстановление_забытого_пароля_через_ранее_привязанный_ключ(self):
        authenticator, _, _ = self.register_passkey()
        options = self.runtime.passkey_options(self.binding)
        token, _ = self.runtime.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)}, self.binding)
        replacement, _ = self.runtime.set_password(token, 'person', 'new-recovered-password')
        with self.assertRaises(ApiError):
            self.app.login('person', 'test-long-password')
        self.assertEqual(self.app.session(replacement)[0],self.uid)
        self.assertEqual(self.app.session(self.app.login('person','new-recovered-password')[0])[0],self.uid)

    def test_отвязка_telegram_только_после_другого_подтверждения(self):
        source = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        self.app.redeem_telegram_link(source,self.app.new_link_code(self.uid,self.token))
        token, _ = self.app.login('person','test-long-password')
        telegram = next(m for m in self.runtime.methods(token) if m['kind']=='telegram')
        replacement, _ = self.runtime.remove_method(token, telegram['kind'], telegram['id'])
        self.assertEqual(self.runtime.methods(replacement),[{'kind':'password','id':'password'}])
        self.assertNotEqual(self.store.resolve_telegram_user(TelegramIdentity(101,101,'private')), self.uid)

    def test_неизвестная_fk_таблица_блокирует_merge_без_потерь(self):
        source = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        with self.store._locked_transaction() as db:
            db.execute('CREATE TABLE future_data (owner TEXT REFERENCES users(blago_user_id))')
            db.execute('INSERT INTO future_data VALUES (?)',(source,))
        code = self.app.new_link_code(self.uid,self.token)
        before = self.snapshot()
        with self.assertRaises(ApiError):
            self.app.redeem_telegram_link(source,code)
        self.assertEqual(before,self.snapshot())

    def test_истёкший_webauthn_challenge_и_подмена_binding(self):
        authenticator, _, _ = self.register_passkey()
        options = self.runtime.passkey_options(self.binding)
        payload = {'challenge':options['challenge'],'credential':authenticator.assertion(options)}
        with self.assertRaises(ApiError):
            self.runtime.passkey_verify(payload, 'foreign-browser')
        with patch('xblago.integrations.identity.time.time', return_value=int(time.time())+301), self.assertRaises(ApiError):
            self.runtime.passkey_verify(payload,self.binding)

    def test_повторный_counter_отклоняется_без_автоматического_слияния(self):
        authenticator, _, _ = self.register_passkey()
        first = self.runtime.passkey_options(self.binding)
        self.runtime.passkey_verify({'challenge':first['challenge'],'credential':authenticator.assertion(first)},self.binding)
        second = self.runtime.passkey_options(self.binding)
        with self.assertRaises(ApiError):
            self.runtime.passkey_verify({'challenge':second['challenge'],'credential':authenticator.assertion(second)},self.binding)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],1)

    def test_экономические_события_после_успешного_merge_сохраняются(self):
        source = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        helper = self.store.resolve_telegram_user(TelegramIdentity(202,202,'private'))
        self.app.create_resource_offer(helper, {'title':'Помощь','offer_type':'external_payment','visibility':'community','max_amount_minor':1000,'currency':'RUB'})
        need = self.app.create_item('needs',source,{'title':'Ремонт','visibility':'community'})
        request = self.app.create_payment_request(source,{'need_id':need['id'],'title':'Ремонт','amount_minor':100,'currency':'RUB','payment_purpose':'Ремонт'})
        self.app.offer_payment_request(source,request['id'])
        offer = self.app.incoming_payment_offers(helper)[0]
        self.app.accept_payment_offer(helper,offer['id'])
        self.app.simulate_payment(helper,request['id'],{'note':'Синтетическая симуляция'})
        self.app.confirm_fulfillment(source,request['id'],{'note':'Подтверждено','xblago_rate':1,'exchange_rate_source':'test'})
        before = self.app.economy_events(source,request['id'])
        self.app.redeem_telegram_link(source,self.app.new_link_code(self.uid,self.token))
        after = self.app.economy_events(self.uid,request['id'])
        # Only the explicit account owner substitution may change economic records.
        expected = json.loads(json.dumps(before).replace(source,self.uid))
        self.assertEqual(expected,after)
        self.assertEqual(self.app.payment_request(self.uid,request['id'])['status'],'completed')

    def test_http_telegram_cookie_csrf_сессии_и_webauthn(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
        server.blago_web_app = self.app
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        root = f'http://127.0.0.1:{server.server_port}'
        jar = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        def request(path, method='GET', payload=None, csrf=None, origin=None):
            headers = {'Origin':origin or root}
            if csrf:
                headers['X-CSRF-Token']=csrf
            data = None
            if payload is not None:
                data=json.dumps(payload).encode()
                headers['Content-Type']='application/json'
            try:
                with opener.open(Request(root+'/api/v1/'+path,data=data,headers=headers,method=method),timeout=5) as result:
                    return result.status,json.loads(result.read())
            except HTTPError as error:
                return error.code,json.loads(error.read())
        self.assertEqual(request('auth/capabilities')[1],{'secure_identity':True,'passkeys':True})
        self.assertEqual(request('auth/telegram/challenge','POST',{},origin='https://evil.test')[0],403)
        challenge = request('auth/telegram/challenge','POST',{})[1]['challenge']
        raw = signed_telegram()
        status, data = request('auth/telegram','POST',{'init_data':raw,'challenge':challenge})
        self.assertEqual(status,200)
        csrf = data['csrf']
        token = next(c.value for c in jar if c.name=='blago_session')
        self.assertEqual(request('me')[0],200)

        self.assertEqual(request('auth/sessions/revoke','POST',{'others':True})[0],403)
        self.assertEqual(request('auth/sessions')[0],200)
        self.assertEqual(request('auth/telegram','POST',{'init_data':raw,'challenge':challenge})[0],409)
        status, options = request('auth/passkeys/register/options','POST',{},csrf)
        self.assertEqual(status,200)
        authenticator = TestAuthenticator()
        status, data = request('auth/passkeys/register/verify','POST',{'challenge':options['challenge'],'credential':authenticator.registration(options)},csrf)
        self.assertEqual(status,200)
        with self.assertRaises(ApiError):
            self.app.session(token)
        csrf = data['csrf']
        status, options = request('auth/passkeys/authenticate/options','POST',{})
        self.assertEqual(status,200)
        status, data = request('auth/passkeys/authenticate/verify','POST',{'challenge':options['challenge'],'credential':authenticator.assertion(options)})
        self.assertEqual(status,200)
        self.assertEqual(request('auth/sessions/revoke','POST',{'others':True},data['csrf'])[0],200)
        # Restarted app on the same DB accepts the cookie and has the same principal.
        server.blago_web_app = WebApp(SQLiteBlagoStore(self.path))
        self.assertEqual(request('me')[0],200)

    def test_restore_сбрасывает_сессии_challenge_и_карантин_telegram(self):
        authenticator, token, _ = self.register_passkey()
        raw = signed_telegram()
        challenge = self.runtime.telegram_challenge(self.binding)
        self.app.telegram_login(raw,challenge,self.binding)
        backup = self.store.backup_database(self.path.parent/'restore-copy')
        schema.invalidate_restored_authentication(backup)
        restored = WebApp(SQLiteBlagoStore(backup))
        with self.assertRaises(ApiError):
            restored.session(token)
        exchange = restored.identity.telegram_challenge(self.binding)
        with self.assertRaises(ApiError) as error:
            restored.telegram_login(signed_telegram(stamp=int(time.time())+1),exchange,self.binding)
        self.assertEqual(error.exception.status,503)
        options = restored.identity.passkey_options(self.binding)
        replacement, _ = restored.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)},self.binding)
        self.assertEqual(restored.session(replacement)[0],self.uid)

    def test_telegram_bot_после_перезапуска_и_блокировки_участника(self):
        bot = TelegramBot(None,self.path.parent,store=self.store)
        update = {'update_id':1,'message':{'from':{'id':101},'chat':{'id':101,'type':'private'},'text':'Запомни: мой город Казань'}}
        first = bot.replies_for_update(update)
        restarted = TelegramBot(None,self.path.parent,store=SQLiteBlagoStore(self.path))
        self.assertEqual(restarted.replies_for_update(update),first)
        uid = self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        before = self.store.all_memory(uid)
        with self.store._locked_transaction() as db:
            db.execute("UPDATE users SET status='blocked' WHERE blago_user_id=?",(uid,))
        update = copy.deepcopy(update)
        update['update_id']=2
        update['message']['text']='Забудь город'
        self.assertIn('приостановлен',restarted.replies_for_update(update)[0]['text'])
        self.assertEqual(before,self.store.all_memory(uid))

    def test_смена_пароля_не_продлевает_свежесть_подтверждения(self):
        now = int(time.time())
        with patch('xblago.integrations.identity.time.time',return_value=now+250):
            replacement, _ = self.runtime.set_password(self.token,'person','new-strong-password')
        with patch('xblago.integrations.identity.time.time',return_value=now+301), self.assertRaises(ApiError) as error:
            self.runtime.require_fresh(replacement)
        self.assertEqual(error.exception.status,403)

    def test_конфликт_имени_при_восстановлении_не_меняет_другой_аккаунт(self):
        self.app.register('foreign','test-long-password','test-invite')
        before = self.snapshot()
        with self.assertRaises(ApiError) as error:
            self.runtime.set_password(self.token,'foreign','new-strong-password')
        self.assertEqual(error.exception.status,409)
        self.assertEqual(before,self.snapshot())

    def test_лимит_ключей_и_неправильный_clientdata_отклоняются(self):
        with self.store._locked_transaction() as db:
            for i in range(20):
                db.execute('INSERT INTO passkey_credentials VALUES (?,?,NULL,?,?,0,0,0,1,NULL)',
                           (bytes([i+1]),self.uid,b'fixture-only-key',b'fixture-only-handle'))
        with self.assertRaises(ApiError) as error:
            self.runtime.passkey_options(self.binding,self.token,register=True)
        self.assertEqual(error.exception.status,409)
        options = self.runtime.passkey_options(self.binding)
        with self.assertRaises(ApiError) as error:
            self.runtime.passkey_verify({'challenge':options['challenge'],'credential':{'response':{'clientDataJSON':b64(b'[]')}}},self.binding)
        self.assertEqual(error.exception.status,400)
