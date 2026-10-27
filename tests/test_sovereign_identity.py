"""07.1 isolated synthetic keys/users; no chain, wallet, real biometric or LLM I/O."""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sqlite3
import tempfile
import time
import threading
import subprocess
import sys
import urllib.request
import urllib.error
import hashlib
import hmac
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.pilot_fixture import fixture
from test_identity_runtime import TestAuthenticator
from xblago.core import sovereign_schema
from xblago.core.sovereign import SovereignIdentity,POLICY,b64,decode,verify_independent_proof,SolanaWalletInterface
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore,cleanup
from xblago.core.security_journal import SecurityJournal,JournalUnavailable
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import WebApp,ApiError,BlagoWebHandler


def create_worker(args):
    path,token=args
    return SovereignIdentity(WebApp(SQLiteBlagoStore(Path(path)))).create(token,'CREATE_TEST_IDENTITY',POLICY)['identity_id']


def complete_worker(args):
    path,token,challenge,signature=args
    try:
        return SovereignIdentity(WebApp(SQLiteBlagoStore(Path(path)))).complete(token,challenge,new_key_signature=signature,confirmation='CONFIRM_BOOTSTRAP')['ok']
    except ApiError as exc:
        return exc.status


class SovereignIdentityTests(unittest.TestCase):
    def setUp(self):
        self.ctx=fixture(origin='https://blago.test',rp_id='blago.test');self.f=self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__,None,None,None)
        self.disabled=patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false','XBLAGO_IDENTITY_CORE_MODE':'isolated_test'})
        self.disabled.start();self.addCleanup(self.disabled.stop)
        self.store,self.app=self.f['store'],self.f['app']
        self.path,self.journal=self.f['path'],self.f['journal']
        sovereign_schema.migrate_sovereign(self.path)
        self.flag=patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'true'});self.flag.start();self.addCleanup(self.flag.stop)
        self.token,self.csrf=self.app.register('person','synthetic-long-password','synthetic-pilot-invite')
        self.uid=self.app.session(self.token)[0]
        self.core=SovereignIdentity(self.app)

    def create(self,token=None):
        return self.core.create(token or self.token,'CREATE_TEST_IDENTITY',POLICY)

    def bootstrap(self):
        self.create();key=Ed25519PrivateKey.generate()
        challenge=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        result=self.core.complete(self.token,challenge['challenge'],new_key_signature=b64(key.sign(decode(challenge['message']))),confirmation='CONFIRM_BOOTSTRAP')
        return key,result['controller_id']

    def run_action(self,action,key,cid,new=None,target=None,role='owner'):
        challenge=self.core.challenge(self.token,action,authorizer=cid,
            new_public_key=b64(new.public_key().public_bytes_raw()) if new else None,target=target,role=role)
        return self.core.complete(self.token,challenge['challenge'],signature=b64(key.sign(decode(challenge['message']))),
            new_key_signature=b64(new.sign(decode(challenge['message']))) if new else None,confirmation='CONFIRM_'+action.upper())

    def test_default_off_and_explicit_test_mode(self):
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':''}),self.assertRaises(ApiError) as error:
            self.create()
        self.assertEqual(error.exception.status,404)
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_MODE':'production'}),self.assertRaises(ApiError):
            self.create()

    def test_identity_core_requires_pilot_lineage_even_without_global_pilot_flag(self):
        from xblago.core.pilot_schema import DDL
        with sqlite3.connect(self.path) as db:
            for sql in reversed(DDL):
                db.execute(f'DROP {"TRIGGER" if sql.startswith("CREATE TRIGGER") else "TABLE"} {sql.split()[2]}')
        with patch.dict(os.environ,{'XBLAGO_PILOT_MODE':'false'}),self.assertRaises(JournalUnavailable):
            self.create()

    def test_idempotent_random_identity_and_explicit_consent(self):
        with self.assertRaises(ApiError): self.core.create(self.token,'yes',POLICY)
        one=self.create();self.assertEqual(one,self.create());self.assertEqual(len(one['identity_id']),64)
        self.assertNotEqual(one['identity_id'],self.uid)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sovereign_identities').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM identity_consents').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM identity_events').fetchone()[0],1)

    def test_concurrent_create_separate_processes(self):
        with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(create_worker,[(str(self.path),self.token)]*4))
        self.assertEqual(len(set(ids)),1)

    def test_concurrent_challenge_atomic_consume(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        args=(str(self.path),self.token,q['challenge'],b64(key.sign(decode(q['message']))))
        with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(complete_worker,[args,args]))
        self.assertEqual(results.count(True),1);self.assertEqual(results.count(409),1)

    def test_independent_proof_contains_no_internal_ids_or_memory(self):
        key,cid=self.bootstrap();root=self.core.inspect(self.token)
        self.store.memory_for_user(self.uid).set('city','Тайный синтетический город')
        q=self.core.challenge(self.token,'prove',authorizer=cid)
        expected=json.loads(decode(q['message']))['nonce']
        result=self.core.complete(self.token,q['challenge'],signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_PROVE')['proof']
        message=decode(result['message']).decode()
        for secret in (self.uid,root['identity_id'],cid,'Тайный синтетический город'):
            self.assertNotIn(secret,message)
        pending={expected}
        def consume(nonce,audience):
            if nonce not in pending: return False
            pending.remove(nonce);return True
        self.assertTrue(verify_independent_proof(result['public_key'],result['signature'],result['message'],audience='https://blago.test',expected_nonce=expected,consume_nonce=consume)['key_possession'])
        with self.assertRaises(ApiError):
            verify_independent_proof(result['public_key'],result['signature'],result['message'],audience='https://blago.test',expected_nonce=expected,consume_nonce=consume)
        with self.assertRaises(ApiError):
            verify_independent_proof(result['public_key'],result['signature'],result['message'],audience='https://other.test',expected_nonce=expected,consume_nonce=consume)
        self.store.clear_memory(self.uid)
        self.assertTrue(self.run_action('prove',key,cid)['ok'])

    def test_bootstrap_replay_rejected(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        payload=dict(new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')
        self.core.complete(self.token,q['challenge'],**payload)
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],**payload)

    def test_invalid_signature_does_not_consume_or_modify(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        with self.assertRaises(ApiError):
            self.core.complete(self.token,q['challenge'],new_key_signature=b64(b'\0'*64),confirmation='CONFIRM_BOOTSTRAP')
        self.assertEqual(self.core.inspect(self.token)['controllers'],[])
        self.assertTrue(self.core.complete(self.token,q['challenge'],new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')['ok'])

    def test_weak_ed25519_identity_point_rejected(self):
        self.create()
        for key in (b'\0'*32,b'\1'+b'\0'*31):
            with self.subTest(key=b64(key)),self.assertRaises(ApiError):
                self.core.challenge(self.token,'bootstrap',new_public_key=b64(key))

    def test_wrong_session_and_other_account_cannot_use_challenge(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        other,_=self.app.register('other','synthetic-long-password','synthetic-pilot-invite');self.create(other)
        sig=b64(key.sign(decode(q['message'])))
        with self.assertRaises(ApiError): self.core.complete(other,q['challenge'],new_key_signature=sig,confirmation='CONFIRM_BOOTSTRAP')
        second,_=self.app.login('person','synthetic-long-password')
        with self.assertRaises(ApiError): self.core.complete(second,q['challenge'],new_key_signature=sig,confirmation='CONFIRM_BOOTSTRAP')

    def test_expiry_confirmation_and_lost_session(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        sig=b64(key.sign(decode(q['message'])))
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],new_key_signature=sig,confirmation='CONFIRM_ROTATE')
        with patch('time.time',return_value=int(time.time())+121),self.assertRaises(ApiError):
            self.core.complete(self.token,q['challenge'],new_key_signature=sig,confirmation='CONFIRM_BOOTSTRAP')
        self.app.logout(self.token)
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],new_key_signature=sig,confirmation='CONFIRM_BOOTSTRAP')

    def test_fresh_auth_required(self):
        with self.store._locked_transaction() as db:
            db.execute('UPDATE web_sessions SET authenticated_at=0')
        with self.assertRaises(ApiError) as error: self.create()
        self.assertEqual(error.exception.status,403)

    def test_rotation_requires_old_authority_and_new_possession(self):
        key,cid=self.bootstrap();new=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'rotate',authorizer=cid,new_public_key=b64(new.public_key().public_bytes_raw()),target=cid)
        msg=decode(q['message'])
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],signature=b64(new.sign(msg)),new_key_signature=b64(new.sign(msg)),confirmation='CONFIRM_ROTATE')
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],signature=b64(key.sign(msg)),new_key_signature=b64(key.sign(msg)),confirmation='CONFIRM_ROTATE')
        result=self.core.complete(self.token,q['challenge'],signature=b64(key.sign(msg)),new_key_signature=b64(new.sign(msg)),confirmation='CONFIRM_ROTATE')
        with self.assertRaises(ApiError): self.run_action('prove',key,cid)
        self.assertTrue(self.run_action('prove',new,result['controller_id'])['ok'])

    def test_proof_only_controller_cannot_rotate_or_add(self):
        key,cid=self.bootstrap();proof_key=Ed25519PrivateKey.generate()
        proof=self.run_action('add',key,cid,new=proof_key,role='proof')['controller_id']
        self.assertTrue(self.run_action('prove',proof_key,proof)['ok'])
        with self.assertRaises(ApiError): self.core.challenge(self.token,'add',authorizer=proof,new_public_key=b64(Ed25519PrivateKey.generate().public_key().public_bytes_raw()))

    def test_revoked_key_and_password_cannot_rebootstrap(self):
        key,cid=self.bootstrap();self.run_action('revoke',key,cid,target=cid)
        self.assertFalse(self.core.capabilities(self.token)['key_control_configured'])
        self.assertEqual(self.core.inspect(self.token)['status'],'suspended')
        with self.assertRaises(ApiError): self.run_action('prove',key,cid)
        with self.assertRaises(ApiError): self.core.challenge(self.token,'bootstrap',new_public_key=b64(Ed25519PrivateKey.generate().public_key().public_bytes_raw()))

    def test_withdrawal_is_denial_not_new_authority(self):
        self.bootstrap();self.core.withdraw(self.token,'WITHDRAW_TEST_IDENTITY')
        self.assertEqual(self.core.inspect(self.token)['status'],'revoked')
        self.assertEqual(self.create()['status'],'revoked')
        with self.assertRaises(ApiError): self.core.challenge(self.token,'bootstrap',new_public_key=b64(Ed25519PrivateKey.generate().public_key().public_bytes_raw()))

    def test_version_change_invalidates_other_pending_actions(self):
        key,cid=self.bootstrap();q=self.core.challenge(self.token,'prove',authorizer=cid)
        self.run_action('add',key,cid,new=Ed25519PrivateKey.generate())
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_PROVE')

    def test_tampered_command_cannot_reinterpret_signed_proof_role(self):
        from xblago.core.security_journal import canonical
        key,cid=self.bootstrap();new=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'add',authorizer=cid,new_public_key=b64(new.public_key().public_bytes_raw()),role='proof')
        handle=hashlib.sha256(q['challenge'].encode()).hexdigest()
        with self.store._locked_transaction() as db:
            row=db.execute('SELECT payload FROM sovereign_challenges WHERE challenge_hash=?',(handle,)).fetchone()
            command=json.loads(row[0]);command['role']='owner'
            db.execute('UPDATE sovereign_challenges SET payload=? WHERE challenge_hash=?',(canonical(command),handle))
        with self.assertRaises(ApiError):
            self.core.complete(self.token,q['challenge'],signature=b64(key.sign(decode(q['message']))),new_key_signature=b64(new.sign(decode(q['message']))),confirmation='CONFIRM_ADD')
        self.assertEqual(len(self.core.inspect(self.token)['controllers']),1)

    def test_cross_user_controller_and_database_ownership(self):
        key,cid=self.bootstrap();other,_=self.app.register('other','synthetic-long-password','synthetic-pilot-invite')
        other_uid=self.app.session(other)[0];other_root=self.create(other)
        with self.assertRaises(ApiError): self.core.challenge(other,'prove',authorizer=cid)
        with self.assertRaises(sqlite3.IntegrityError),self.store._locked_transaction() as db:
            db.execute("INSERT INTO identity_controllers VALUES ('bad',?,?,'Ed25519',?,'owner','active',1,NULL)",
                       (other_root['identity_id'],self.uid,key.public_key().public_bytes_raw()))
        self.assertEqual(self.store.all_memory(other_uid),{})

    def test_revoke_replayed_even_when_feature_disabled(self):
        key,cid=self.bootstrap();backup=self.store.backup_database(self.f['root']/'copies')
        self.run_action('revoke',key,cid,target=cid)
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
            recovered=restore(backup,self.journal)
        app=WebApp(recovered);token,_=app.login('person','synthetic-long-password')
        core=SovereignIdentity(app)
        self.assertEqual(core.inspect(token)['controllers'][0]['status'],'revoked')
        self.assertFalse(core.capabilities(token)['key_control_configured'])
        with self.assertRaises(ApiError): core.challenge(token,'prove',authorizer=cid)
        restore(backup,self.journal)

    def test_last_owner_revoke_suspends_remaining_proof_key_after_restore(self):
        key,cid=self.bootstrap();proof_key=Ed25519PrivateKey.generate()
        proof=self.run_action('add',key,cid,new=proof_key,role='proof')['controller_id']
        backup=self.store.backup_database(self.f['root']/'copies')
        self.run_action('revoke',key,cid,target=cid)
        recovered=WebApp(restore(backup,self.journal));token,_=recovered.login('person','synthetic-long-password')
        core=SovereignIdentity(recovered)
        self.assertEqual(core.create(token,'CREATE_TEST_IDENTITY',POLICY)['status'],'suspended')
        with self.assertRaises(ApiError): core.challenge(token,'prove',authorizer=proof)

    def test_missing_new_controller_and_identity_hold_instead_of_regrant(self):
        before_create=self.store.backup_database(self.f['root']/'pre-create')
        key,cid=self.bootstrap();identity=self.core.inspect(self.token)['identity_id']
        before_add=self.store.backup_database(self.f['root']/'pre-add')
        self.run_action('add',key,cid,new=Ed25519PrivateKey.generate())
        for backup in (before_create,before_add):
            recovered=restore(backup,self.journal);app=WebApp(recovered)
            token,_=app.login('person','synthetic-long-password');core=SovereignIdentity(app)
            self.assertTrue(core.capabilities(token)['recovery_required'])
            with self.assertRaises(ApiError): core.create(token,'CREATE_TEST_IDENTITY',POLICY)
            with self.assertRaises(ApiError): core.challenge(token,'prove',authorizer=cid)
        fresh,_=self.app.login('person','synthetic-long-password')
        self.assertEqual(self.core.inspect(fresh)['identity_id'],identity)

    def test_consumed_proof_replayed_after_snapshot_open(self):
        key,cid=self.bootstrap();q=self.core.challenge(self.token,'prove',authorizer=cid)
        backup=self.store.backup_database(self.f['root']/'copies')
        args=dict(signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_PROVE')
        self.core.complete(self.token,q['challenge'],**args)
        # Direct guarded open, not only restore(), must reconcile consumed proof.
        core=SovereignIdentity(WebApp(SQLiteBlagoStore(backup)))
        with self.assertRaises(ApiError): core.complete(self.token,q['challenge'],**args)

    def test_account_deletion_purges_all_identity_rows_and_replays(self):
        self.bootstrap();backup=self.store.backup_database(self.f['root']/'copies')
        privacy=PrivacyService(self.app);preview=privacy.preview(self.token)
        self.assertTrue(preview['eligible']);privacy.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        for store in (self.store,restore(backup,self.journal)):
            with store._connect() as db:
                for table in sovereign_schema.TABLES:
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        with self.assertRaises(ApiError): self.core.inspect(self.token)

    def test_existing_telegram_binding_noop_and_merge_refusal_atomic(self):
        self.bootstrap()
        same=self.app.new_link_code(self.uid,self.token)
        self.assertEqual(self.app.redeem_telegram_link(self.uid,same),self.uid)
        self.token,_=self.app.login('person','synthetic-long-password')
        source=self.store.resolve_telegram_user(TelegramIdentity(2000000001,2000000001,'private'))
        self.store.memory_for_user(source).set('city','Синтетический город')
        code=self.app.new_link_code(self.uid,self.token)
        with self.assertRaises(ApiError): self.app.redeem_telegram_link(source,code)
        self.assertEqual(self.store.resolve_telegram_user(TelegramIdentity(2000000001,2000000001,'private')),source)
        self.assertEqual(self.store.all_memory(source),{'city':'Синтетический город'})

    def test_bio_uses_existing_verified_passkey_not_biometric_material(self):
        self.bootstrap();authenticator=TestAuthenticator();binding='synthetic-webauthn-binding'
        options=self.app.identity.passkey_options(binding,self.token,register=True)
        self.token,_=self.app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.registration(options)},binding,self.token,register=True)
        self.assertFalse(self.core.capabilities(self.token)['local_user_verification'])
        options=self.app.identity.passkey_options(binding)
        self.token,_=self.app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)},binding)
        caps=self.core.capabilities(self.token)
        self.assertTrue(caps['local_user_verification']);self.assertFalse(caps['unique_human_verified'])
        self.assertNotIn('identity_id',caps);self.assertNotIn('controllers',caps)

    def test_export_and_journal_do_not_publish_keys_or_memory(self):
        key,cid=self.bootstrap();root=self.core.inspect(self.token)['identity_id']
        self.store.memory_for_user(self.uid).set('city','Частная память')
        exported=PrivacyService(self.app).export(self.token)
        self.assertIn('sovereign_identities',exported['tables'])
        self.assertNotIn('public_key',str(exported['tables']['identity_controllers']))
        rows,_=SecurityJournal(self.journal).read();text=str([r[2] for r in rows])
        for secret in (self.uid,root,cid,b64(key.public_key().public_bytes_raw()),'Частная память'):
            self.assertNotIn(secret,text)

    def test_wallet_interface_has_no_live_operations(self):
        adapter=SolanaWalletInterface()
        with self.assertRaises(NotImplementedError): adapter.verify(None,None,None)
        with self.assertRaises(NotImplementedError): adapter.verification_message(None,None)

    def test_agent_reader_is_boolean_only_uncached_and_session_authorized(self):
        from xblago.agents.personal_assistant import PersonalAssistantAgent
        self.bootstrap()
        agent=PersonalAssistantAgent(self.store.memory_for_user(self.uid),identity_capabilities_reader=lambda:self.core.capabilities(self.token))
        caps=agent.identity_capabilities()
        self.assertTrue(caps['key_control_configured']);self.assertTrue(all(type(v) is bool for v in caps.values()))
        agent._identity_capabilities_reader=lambda:{'private_key':'forbidden synthetic value'}
        with self.assertRaises(ValueError): agent.identity_capabilities()
        agent._identity_capabilities_reader=lambda:self.core.capabilities(self.token)
        self.app.logout(self.token)
        with self.assertRaises(ApiError): agent.identity_capabilities()

    def test_web_agent_receives_only_reader_for_matching_session(self):
        from xblago.agents.personal_assistant import PersonalAssistantAgent
        from xblago.core.messages import AgentResponse
        self.bootstrap();seen=[]
        def respond(agent,message):
            seen.append(agent.identity_capabilities())
            return AgentResponse('Синтетический ответ.')
        with patch.object(PersonalAssistantAgent,'respond',respond):
            self.assertEqual(self.app.chat(self.uid,'Обычный разговор',token=self.token),'Синтетический ответ.')
            self.assertTrue(seen[0]['available'])
            with self.assertRaises(ApiError): self.app.chat('another-user','Обычный разговор',token=self.token)

    def test_migration_idempotency_and_reject_empty_database(self):
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
            self.assertFalse(sovereign_schema.migrate_sovereign(self.path))
            empty=self.f['root']/'empty.sqlite3';empty.touch()
            with self.assertRaises(ValueError): sovereign_schema.migrate_sovereign(empty)
            self.assertEqual(empty.stat().st_size,0)

    def test_empty_rollback_and_transactional_migration_failure(self):
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
            self.assertTrue(sovereign_schema.migrate_sovereign(self.path,rollback_empty=True))
            with patch.object(sovereign_schema,'DDL',sovereign_schema.DDL+['SELECT synthetic_missing_function()']),self.assertRaises(sqlite3.OperationalError):
                sovereign_schema.migrate_sovereign(self.path)
            with self.store._connect() as db:
                self.assertFalse(set(sovereign_schema.NAMES)&{r[0] for r in db.execute('SELECT name FROM sqlite_master')})
            self.assertTrue(sovereign_schema.migrate_sovereign(self.path))
        self.create()
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}),self.assertRaises(ValueError):
            sovereign_schema.migrate_sovereign(self.path,rollback_empty=True)

    def test_schema_drift_closes_access(self):
        self.create()
        with sqlite3.connect(self.path) as db: db.execute('DROP TRIGGER sovereign_controller_immutable')
        with self.assertRaises(JournalUnavailable): self.core.inspect(self.token)

    def test_fsync_failure_cannot_grant_controller(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        with patch('xblago.core.security_journal.os.fsync',side_effect=OSError('synthetic disk failure')),self.assertRaises(JournalUnavailable):
            self.core.complete(self.token,q['challenge'],new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')
        # If the critical intent reached durable storage before failure, recovery
        # may suspend service, but must never recreate missing key material.
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM identity_controllers').fetchone()[0],0)

    def test_challenge_ttl_cleanup_preserves_core(self):
        self.create();key=Ed25519PrivateKey.generate()
        self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        with self.store._locked_transaction() as db:
            db.execute('UPDATE sovereign_challenges SET created_at=created_at-200,expires_at=expires_at-200')
        self.assertEqual(cleanup(self.store)['sovereign_challenges'],1)
        self.assertEqual(self.core.inspect(self.token)['status'],'pending')

    def test_http_csrf_origin_uid_spoof_and_limited_agent_capability(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=self.app
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            def request(path,body=None,csrf=None,origin='https://blago.test',token=None):
                headers={'Cookie':'blago_session='+(self.token if token is None else token),'Origin':origin,'Content-Type':'application/json'}
                if csrf: headers['X-CSRF-Token']=csrf
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/identity-core/'+path,
                    data=json.dumps(body).encode() if body is not None else None,headers=headers)
                try:
                    with urllib.request.urlopen(req) as response: return response.status,json.load(response)
                except urllib.error.HTTPError as exc:
                    return exc.code,json.load(exc)
            payload={'confirm':'CREATE_TEST_IDENTITY','policy_version':POLICY}
            self.assertEqual(request('create',payload)[0],403)
            self.assertEqual(request('create',payload,csrf=self.csrf,origin='https://evil.test')[0],403)
            self.assertEqual(request('create',{**payload,'blago_user_id':'someone_else'},csrf=self.csrf)[0],400)
            self.assertEqual(request('create',payload,csrf=self.csrf,token='missing-session')[0],401)
            self.assertEqual(request('create',payload,csrf=self.csrf)[0],200)
            status,cap=request('capabilities')
            self.assertEqual(status,200);self.assertNotIn('identity_id',cap);self.assertNotIn('public_key',cap)
            self.assertFalse(cap['unique_human_verified'])
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)

    def test_pre07_backup_cannot_be_relabelled_or_served(self):
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
            sovereign_schema.migrate_sovereign(self.path,rollback_empty=True)
            backup=self.store.backup_database(self.f['root']/'pre07')
            sovereign_schema.migrate_sovereign(self.path)
        self.bootstrap()
        with self.assertRaises(JournalUnavailable): restore(backup,self.journal)
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}),self.assertRaises(JournalUnavailable):
            sovereign_schema.migrate_sovereign(backup)

    def test_process_crash_after_journal_before_commit_denies_missing_grant(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        code='''import os,sys
from pathlib import Path
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import WebApp
from xblago.core.sovereign import SovereignIdentity
from xblago.core.privacy_control import PrivacyControl
core=SovereignIdentity(WebApp(SQLiteBlagoStore(Path(sys.argv[1]))))
original=PrivacyControl.flush
def crash(control):
    original(control)
    os._exit(74)
PrivacyControl.flush=crash
core.complete(sys.argv[2],sys.argv[3],new_key_signature=sys.argv[4],confirmation='CONFIRM_BOOTSTRAP')
'''
        result=subprocess.run([sys.executable,'-c',code,str(self.path),self.token,q['challenge'],b64(key.sign(decode(q['message'])))],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,74,result.stderr)
        self.assertTrue(self.core.capabilities(self.token)['recovery_required'])
        with self.assertRaises(ApiError): self.core.complete(self.token,q['challenge'],new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM identity_controllers').fetchone()[0],0)

    def test_original_server_loss_recover_identity_on_separate_instance(self):
        from xblago.core.journal_custody import export_bundle,recover_bundle
        key,cid=self.bootstrap();identity=self.core.inspect(self.token)['identity_id']
        self.store.memory_for_user(self.uid).set('city','Синтетическая Казань')
        backup=self.store.backup_database(self.f['root']/'offsite')
        wrap=self.f['root']/'escrow.key';wrap.write_bytes(os.urandom(32));wrap.chmod(0o600)
        package=self.f['root']/'encrypted-journal.json'
        export_bundle(SecurityJournal(self.journal),package,wrap)
        shutil.rmtree(self.journal)
        for path in self.path.parent.glob(self.path.name+'*'): path.unlink()
        server_b=self.f['root']/'server-b';server_b.mkdir(mode=0o700)
        journal=server_b/'journal';recover_bundle(package,journal,wrap,self.f['witness'])
        new_path=server_b/'app.sqlite3';shutil.copy2(backup,new_path)
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(journal)}):
            app=WebApp(restore(new_path,journal));token,_=app.login('person','synthetic-long-password')
            core=SovereignIdentity(app)
            self.assertEqual(core.inspect(token)['identity_id'],identity)
            self.assertEqual(app.store.all_memory(self.uid),{'city':'Синтетическая Казань'})
            q=core.challenge(token,'prove',authorizer=cid)
            self.assertTrue(core.complete(token,q['challenge'],signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_PROVE')['ok'])
            with app.store._connect() as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_telegram_mini_app_web_and_memory_share_existing_id(self):
        from xblago.integrations.telegram import TelegramBot
        identity=TelegramIdentity(2000000001,2000000001,'private')
        source=self.store.resolve_telegram_user(identity)
        self.app.redeem_telegram_link(source,self.app.new_link_code(self.uid,self.token))
        self.token,_=self.app.login('person','synthetic-long-password')
        self.bootstrap();root=self.core.inspect(self.token)['identity_id']
        fields={'user':json.dumps({'id':identity.telegram_user_id}),'auth_date':str(int(time.time()))}
        secret=hmac.new(b'WebAppData',b'123:synthetic-pilot-bot',hashlib.sha256).digest()
        fields['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
        binding='synthetic-mini-app-binding';challenge=self.app.identity.telegram_challenge(binding)
        mini_token,_=self.app.telegram_login(urlencode(fields),challenge,binding)
        self.assertEqual(self.core.inspect(mini_token)['identity_id'],root)
        bot=TelegramBot(None,self.f['root'],store=self.store)
        update={'update_id':501,'message':{'from':{'id':identity.telegram_user_id},'chat':{'id':identity.chat_id,'type':'private'},'text':'Меня зовут Тестовый Участник'}}
        reply=bot.replies_for_update(update);self.assertEqual(bot.replies_for_update(update),reply)
        self.assertEqual(self.store.all_memory(self.uid),{'name':'Тестовый Участник'})
        self.assertEqual(len(self.app.history(self.uid)),2)
        self.assertEqual(self.core.inspect(mini_token)['identity_id'],root)

    def test_cross_user_passkey_reference_and_mainnet_rows_rejected(self):
        root=self.create()['identity_id'];other,_=self.app.register('other','synthetic-long-password','synthetic-pilot-invite')
        other_uid=self.app.session(other)[0]
        authenticator=TestAuthenticator();binding='synthetic-passkey-binding'
        options=self.app.identity.passkey_options(binding,other,register=True)
        self.app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.registration(options)},binding,other,register=True)
        with self.store._connect() as db:
            credential=db.execute('SELECT credential_id FROM passkey_credentials WHERE blago_user_id=?',(other_uid,)).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError),self.store._locked_transaction() as db:
            db.execute("INSERT INTO identity_credentials VALUES ('wrong-owner',?,?,'passkey_reference',?,0)",(root,self.uid,credential))
        with self.assertRaises(sqlite3.IntegrityError),self.store._locked_transaction() as db:
            db.execute("INSERT INTO identity_wallet_links VALUES ('bad',?,?,'solana','mainnet','synthetic-address','prove','unverified',0,NULL)",(root,self.uid))

    def test_controller_write_failure_rolls_back_before_journal_flush(self):
        self.create();key=Ed25519PrivateKey.generate()
        q=self.core.challenge(self.token,'bootstrap',new_public_key=b64(key.public_key().public_bytes_raw()))
        with self.store._locked_transaction() as db:
            db.execute("CREATE TRIGGER synthetic_failure BEFORE UPDATE OF version ON sovereign_identities BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(ApiError):
            self.core.complete(self.token,q['challenge'],new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')
        self.assertEqual(self.core.inspect(self.token)['controllers'],[])
        self.assertEqual(self.core.inspect(self.token)['version'],1)
        with self.store._locked_transaction() as db: db.execute('DROP TRIGGER synthetic_failure')
        self.assertTrue(self.core.complete(self.token,q['challenge'],new_key_signature=b64(key.sign(decode(q['message']))),confirmation='CONFIRM_BOOTSTRAP')['ok'])

    def test_existing_data_preserved_and_downgrade_denied_after_purge(self):
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
            sovereign_schema.migrate_sovereign(self.path,rollback_empty=True)
            self.store.memory_for_user(self.uid).set('city','Сохраняемый город')
            self.store.append_history(self.uid,'user','Существующий диалог')
            tables=('users','profiles','channel_links','memory_facts','semantic_memory','dialogue_turns','web_credentials','web_sessions')
            with self.store._connect() as db: before={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
            sovereign_schema.migrate_sovereign(self.path)
            with self.store._connect() as db: after={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
            self.assertEqual(before,after)
        self.bootstrap();privacy=PrivacyService(self.app);preview=privacy.preview(self.token)
        privacy.delete(self.token,preview['challenge'],'УДАЛИТЬ')
        with patch.dict(os.environ,{'XBLAGO_IDENTITY_CORE_ENABLED':'false'}),self.assertRaises(ValueError):
            sovereign_schema.migrate_sovereign(self.path,rollback_empty=True)
