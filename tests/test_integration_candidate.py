import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xblago.core.integration import preflight,IntegrationBlocked,migration_manifest,route_allowed
from xblago.core.security_journal import JournalUnavailable
from xblago.integrations.web import WebApp,ApiError
from xblago.core.privacy_maintenance import restore
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from tools.integration_fixture import create

class IntegrationCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'new';self.config=create(self.root)
        self.env=patch.dict(os.environ,self.config);self.env.start();self.addCleanup(self.env.stop)
        self.path=Path(self.config['XBLAGO_DATA_DIR'])/'blago.sqlite3'
    def test_missing_flags_no_schema_write(self):
        with patch.dict(os.environ,{'XBLAGO_REQUIRE_SECURE_IDENTITY':'false'}):
            with self.assertRaises(IntegrationBlocked):preflight(self.path)
    def test_missing_db_never_created(self):
        p=self.root/'absent.sqlite3'
        with self.assertRaises(IntegrationBlocked):preflight(p)
        self.assertFalse(p.exists())
    def test_no_auto_reinitialize(self):
        with self.assertRaises(FileExistsError):create(self.root)
    def test_missing_witness_fail_closed(self):
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(self.root/'missing')}):
            with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_missing_journal_fail_closed(self):
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(self.root/'missing')}):
            with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_experimental_and_live_provider_rejected(self):
        for field,value in [('XBLAGO_IDENTITY_CORE_ENABLED','true'),('XBLAGO_GENESIS_ENABLED','true'),('XBLAGO_IDENTITY_RECOVERY_ENABLED','true'),('XBLAGO_LLM_PROVIDER','hf')]:
            with patch.dict(os.environ,{field:value}):
                with self.assertRaises(IntegrationBlocked):preflight(self.path)
    def test_bad_manifest_no_repair(self):
        store=preflight(self.path)
        with store._connect() as db:
            db.execute("UPDATE identity_runtime_migrations SET checksum='wrong'");db.commit()
        with self.assertRaises(IntegrationBlocked):preflight(self.path)
    def test_restart_backup_revocation_and_memory(self):
        with patch.dict(os.environ,{'XBLAGO_WEB_INVITE_CODE':'synthetic-integration'}):
            app=WebApp(preflight(self.path));token,_=app.register('integration_user','synthetic-password-only','synthetic-integration');uid=app.session(token)[0]
            app.store.set_memory(uid,'city','Казань');app.chat(uid,'Я размышляю о взаимопомощи')
            backup=app.store.backup_database()
            app.store.delete_memory(uid,'city');app.identity.revoke(token,others=True)
            app.logout(token)
            restored=restore(backup,Path(self.config['XBLAGO_SECURITY_JOURNAL_DIR']))
            self.assertEqual(restored.all_memory(uid),{})
            with self.assertRaises(ApiError):WebApp(preflight(backup)).session(token)
            self.assertEqual(len(restored.recent_history(uid,100)),2)
    def test_manifest_and_nonfinancial_surface(self):
        self.assertEqual(len(migration_manifest()),4)
        for path in ('/api/v1/economy/payment-requests','/api/v1/economy/assets','/api/v1/identity-core/create','/api/v1/genesis/publish'):
            self.assertFalse(route_allowed(path))
        self.assertTrue(route_allowed('/api/v1/needs'))
    def test_workflow_no_push_deployment(self):
        text=Path('.github/workflows/huggingface-space.yml').read_text()
        self.assertNotIn('  push:',text);self.assertNotIn('pull_request:',text)
        for gate in ('inputs.release_sha == github.sha','vars.BLAGO_DEPLOYMENT_ENABLED','environment: production-release',"github.ref == 'refs/heads/main'"):
            self.assertIn(gate,text)
        self.assertNotIn('migrate_',text)

    def test_base_schema_loss_is_not_recreated(self):
        store=preflight(self.path)
        with store._connect() as db:
            db.execute('DROP TABLE dialogue_turns');db.commit()
        with self.assertRaises(IntegrationBlocked):preflight(self.path)
        with self.assertRaises(IntegrationBlocked):SQLiteBlagoStore(self.path)
        with self.assertRaises(IntegrationBlocked):WebApp(store)
        import sqlite3
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='dialogue_turns'").fetchone())

    def test_http_finance_disabled_without_writes(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from xblago.integrations.web import BlagoWebHandler
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
        server.blago_web_app=WebApp(preflight(self.path))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for suffix in ('/api/v1/economy/payment-requests','/api/v1/economy/assets'):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request('http://127.0.0.1:'+str(server.server_port)+suffix,data=b'{}',headers={'Content-Type':'application/json'}),timeout=5)
                self.assertEqual(caught.exception.code,404)
            with server.blago_web_app.store._connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0],0)
        finally:server.shutdown();server.server_close();worker.join(5)

class MainUpgradeIntegrationTests(unittest.TestCase):
    def test_exact_main_to_candidate_upgrade_and_repeat(self):
        import subprocess,sys,tarfile,io
        from xblago.core.identity_schema import migrate_identity_store
        from xblago.core.identity_runtime_schema import migrate_identity_runtime
        from xblago.core.privacy_schema import migrate_privacy
        from xblago.core.pilot_schema import migrate_pilot
        from xblago.core.security_journal import SecurityJournal
        from xblago.core.journal_witness import initialize
        import _sqlite3
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);old=root/'old';old.mkdir()
            archive=subprocess.check_output(['git','archive','3c218dc5757879c26b3aec520af3636b2d702715'])
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                for member in bundle.getmembers():
                    if member.isfile() and member.name.startswith('src/') and '..' not in Path(member.name).parts:
                        target=old/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(bundle.extractfile(member).read())
            path=root/'blago.sqlite3';metadata=root/'identity.json'
            program='''import json,sys
from pathlib import Path
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
s=SQLiteBlagoStore(Path(sys.argv[1]));u=s.resolve_telegram_user(TelegramIdentity(1000000001,1000000001,'private'))
s.set_memory(u,'city','Казань');s.append_history(u,'user','Синтетический main')
s.begin_update('telegram','integration01',u);s.mark_update_processed('telegram','integration01')
Path(sys.argv[2]).write_text(json.dumps({'uid':u}))
'''
            env=dict(os.environ,PYTHONPATH=str(old/'src')+os.pathsep+str(Path(_sqlite3.__file__).parent))
            subprocess.run([sys.executable,'-c',program,str(path),str(metadata)],env=env,check=True,capture_output=True,timeout=30)
            uid=json.loads(metadata.read_text())['uid']
            config={'XBLAGO_PILOT_MODE':'false','XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'','XBLAGO_INTEGRATION_MODE':'','XBLAGO_LLM_PROVIDER':'local'}
            with patch.dict(os.environ,config):
                store=SQLiteBlagoStore(path);WebApp(store)
                self.assertEqual(store.all_memory(uid),{'city':'Казань'})
                self.assertEqual(len(store.recent_history(uid,10)),1)
                self.assertFalse(store.begin_update('telegram','integration01',uid))
                self.assertEqual(store.resolve_telegram_user(TelegramIdentity(1000000001,1000000001,'private')),uid)
                self.assertTrue(migrate_identity_store(path));self.assertTrue(migrate_identity_runtime(path))
                journal=root/'journal';witness=root/'witness';SecurityJournal.initialize(journal)
                self.assertTrue(migrate_privacy(path,journal));os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal)
                self.assertTrue(migrate_pilot(path));initialize(SecurityJournal(journal),witness)
                self.assertFalse(migrate_identity_store(path));self.assertFalse(migrate_identity_runtime(path));self.assertFalse(migrate_privacy(path,journal));self.assertFalse(migrate_pilot(path))
                config.update(XBLAGO_PILOT_MODE='true',XBLAGO_REQUIRE_PRIVACY_JOURNAL='true',XBLAGO_REQUIRE_SECURE_IDENTITY='true',XBLAGO_INTEGRATION_MODE='synthetic',XBLAGO_SECURITY_JOURNAL_DIR=str(journal),XBLAGO_JOURNAL_WITNESS_DIR=str(witness))
                with patch.dict(os.environ,config):
                    current=preflight(path);backup=current.backup_database();current.delete_memory(uid,'city')
                    restored=restore(backup,journal)
                    self.assertEqual(preflight(backup).all_memory(uid),{})
                    self.assertEqual(len(restored.recent_history(uid,10)),1)

class ReleasePolicyTests(unittest.TestCase):
    def test_all_unapproved_events_refs_shas_fail_closed(self):
        from tools.release_guard import allowed
        sha='a'*40
        baseline=['workflow_dispatch','refs/heads/main',sha,sha,'DEPLOY','true']
        self.assertTrue(allowed(*baseline))
        for index,bad_values in enumerate([['push','pull_request',''],['refs/heads/integration/test','refs/pull/1/merge',''],['', 'xyz', 'a'*39],['', 'b'*40],['','deploy'],['','false','True']]):
            for bad in bad_values:
                args=list(baseline);args[index]=bad
                self.assertFalse(allowed(*args),args)
