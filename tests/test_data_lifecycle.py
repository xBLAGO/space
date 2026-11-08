"""Детерминированные synthetic lifecycle, restore, concurrency и crash tests."""
import concurrent.futures
import errno
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.core import lifecycle_schema as schema
from xblago.core.data_lifecycle import sweep,managed_backup,retire_backup,RetentionBlocked
from xblago.core.context_fencing import Generation,ContextConflict
from xblago.core.privacy_maintenance import restore
from xblago.core.privacy_control import PrivacyControl
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.help_service import HelpService
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import WebApp,ApiError


def crash_retention(config):
    os.environ.update(config)
    original=PrivacyControl.flush
    def stop(c):
        pending=c.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='lifecycle_delete'").fetchone()
        original(c)
        if pending:os._exit(81)
    with patch.object(PrivacyControl,'flush',stop):sweep(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3'))


def retire_worker(config,connection):
    os.environ.update(config)
    try:connection.send(sweep(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3'))['blago_needs'])
    finally:connection.close()


class DataLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=create(Path(self.tmp.name)/'new',help_enabled=True,context_fencing=True,lifecycle=True)
        self.cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-life'
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.path=Path(self.cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3';self.app=WebApp(preflight(self.path));self.store=self.app.store
        self.a=self.app.register('synthetic_life_a','synthetic-password-only','synthetic-life')[0]
        self.b=self.app.register('synthetic_life_b','synthetic-password-only','synthetic-life')[0]
        self.uid=self.app.session(self.a)[0];self.h=HelpService(self.app)
    def integrity(self,store=None):
        with (store or self.store)._connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def aged_need(self,uid=None):
        item=self.app.create_item('needs',uid or self.uid,{'title':'Синтетический личный текст'})
        with self.store._locked_transaction() as db:db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",(item['id'],))
        return item['id']
    def backup(self):return self.store.backup_database()
    def action(self,token,rid,action,args=None):
        p=self.h.prepare(token,rid,self.h.get(token,rid)['version'],action,args or {});return self.h.commit(token,p['challenge'],'ПОДТВЕРЖДАЮ')
    def help_cycle(self,days=100,completed=True):
        bc=self.h.code(self.b)['code']
        with patch('time.time',return_value=int(time.time())-days*86400):
            rid=self.h.draft(self.a,'Синтетический Help private text',[bc],secrets.token_hex(16))['id'];self.action(self.a,rid,'publish')
            if completed:
                self.action(self.b,rid,'offer',{'note':'Синтетический helper note'})
                oid=self.h.get(self.a,rid)['offers'][0]['id'];self.action(self.a,rid,'accept',{'offer_id':oid})
                self.action(self.a,rid,'confirm');self.action(self.b,rid,'confirm')
        return rid
    def test_пустая_и_существующая_БД_повторная_миграция(self):
        self.assertFalse(schema.migrate_lifecycle(self.path))
        cfg=create(Path(self.tmp.name)/'before',help_enabled=True,context_fencing=True)
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));token=app.register('before_life','synthetic-password-only','synthetic-life')[0];uid=app.session(token)[0]
            app.chat(uid,'запомни city = Синтетический город');before=app.history(uid)
            self.assertTrue(schema.migrate_lifecycle(app.store.path));self.assertFalse(schema.migrate_lifecycle(app.store.path))
            self.assertEqual(app.history(uid),before);self.assertEqual(app.store.all_memory(uid)['city'],'Синтетический город')
    def test_DDL_ошибка_откатывается(self):
        cfg=create(Path(self.tmp.name)/'bad',help_enabled=True,context_fencing=True)
        with patch.dict(os.environ,cfg):
            path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
            with patch.object(schema,'DDL',schema.DDL+('INVALID SQL',)):
                with self.assertRaises(sqlite3.Error):schema.migrate_lifecycle(path)
            with preflight(path)._connect() as db:self.assertFalse(schema.present(db))
    def test_старые_needs_resources_удалены_и_не_возвращаются(self):
        rid=self.aged_need();backup=self.backup();self.assertEqual(sweep(self.store)['blago_needs'],1)
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM blago_needs WHERE id=?',(rid,)).fetchone()[0],0)
        self.integrity(recovered)
    def test_повтор_sweep_без_повторных_удалений(self):
        self.aged_need();sweep(self.store);self.assertEqual(sweep(self.store)['blago_needs'],0)
    def test_не_удалять_актуальные_данные_и_чужие_новые_записи(self):
        self.aged_need();other=self.app.session(self.b)[0];new=self.app.create_item('needs',other,{'title':'Актуальная синтетическая запись'})
        self.store.set_memory(self.uid,'city','Синтетический город');sweep(self.store)
        self.assertEqual(self.app.rows('needs',other)[0]['id'],new['id']);self.assertIn('city',self.store.all_memory(self.uid))
    def test_архив_памяти_не_стирает_действующего_потомка_после_restore(self):
        self.app.chat(self.uid,'Я предпочитаю чай');self.app.chat(self.uid,'Раньше я любил чай, теперь предпочитаю кофе')
        with self.store._locked_transaction() as db:db.execute("UPDATE semantic_memory SET updated_at='2000-01-01T00:00:00+00:00' WHERE status='superseded'")
        backup=self.backup();self.assertGreater(sweep(self.store)['semantic_archives'],0)
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        self.assertEqual(recovered.all_memory(self.uid)['preference:drink'],'кофе');self.integrity(recovered)
    def test_пара_истории_и_receipt_plaintext_не_восстанавливаются(self):
        self.app.chat(self.uid,'Привет')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00'")
        self.store.begin_update('telegram','life-1',self.uid);self.store.mark_update_processed('telegram','life-1',[{'chat_id':1,'text':'Синтетический cached plaintext'}])
        backup=self.backup();self.assertEqual(sweep(self.store)['history'],2)
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        self.assertEqual(recovered.recent_history(self.uid,100),())
        with recovered._connect() as db:self.assertIsNone(db.execute("SELECT response_json FROM events WHERE external_update_id='life-1'").fetchone()[0])
    def test_receipt_TTL_оставляет_dedupe(self):
        self.store.begin_update('telegram','life-2',self.uid);self.store.mark_update_processed('telegram','life-2',[{'chat_id':1,'text':'Синтетический cached text'}])
        with self.store._locked_transaction() as db:db.execute("UPDATE events SET updated_at='2000-01-01T00:00:00+00:00'")
        backup=self.backup();self.assertEqual(sweep(self.store)['receipt_bodies'],1)
        self.assertFalse(self.store.begin_update('telegram','life-2',self.uid))
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertIsNone(db.execute("SELECT response_json FROM events WHERE external_update_id='life-2'").fetchone()[0])
    def test_Help_минимизация_без_текста_времени_участников(self):
        rid=self.help_cycle();backup=self.backup();counts=sweep(self.store);self.assertEqual(counts['help_retired'],1)
        with self.store._connect() as db:
            evidence=dict(db.execute('SELECT * FROM lifecycle_help_evidence').fetchone())
            self.assertEqual(set(evidence),{'target_tag','outcome','event_count','protocol'});self.assertEqual(evidence['outcome'],'mutual_confirmation_recorded')
            self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],0)
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],0)
        self.assertNotIn('private text',json.dumps(evidence));self.integrity(recovered)
    def test_незавершённый_Help_не_превращается_в_успех(self):
        self.help_cycle(days=40,completed=False);sweep(self.store)
        with self.store._connect() as db:self.assertEqual(db.execute('SELECT outcome FROM lifecycle_help_evidence').fetchone()[0],'incomplete')
    def test_account_delete_Help_не_содержит_ссылок_на_людей(self):
        rid=self.help_cycle(days=0);privacy=PrivacyService(self.app);p=privacy.preview(self.a);backup=self.backup()
        self.assertTrue(privacy.delete(self.a,p['challenge'],'УДАЛИТЬ')['deleted'])
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT outcome FROM lifecycle_help_evidence').fetchone()[0],'privacy_removed')
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertEqual(db.execute("SELECT status FROM users WHERE blago_user_id=?",(self.uid,)).fetchone()[0],'deleted')
    def test_отзыв_согласия_стирает_activity_и_запрещает_возврат(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO consent_records VALUES ('c1',?,'optional_activity','synthetic','granted',1)",(self.uid,))
            db.execute("INSERT INTO activity_events VALUES ('activity1',?,'login','web',1,9999999999)",(self.uid,))
        backup=self.backup();PrivacyService(self.app).withdraw_consent(self.a,'optional_activity')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._locked_transaction() as db:db.execute("INSERT INTO activity_events VALUES ('activity2',?,'login','web',1,9999999999)",(self.uid,))
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM activity_events').fetchone()[0],0)
    def test_отзыв_device_label_не_удаляет_способ_входа(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO consent_records VALUES ('labelc',?,'device_label','synthetic','granted',1)",(self.uid,))
            db.execute("INSERT INTO user_devices VALUES ('device1',?,'Синтетическая подпись',1,NULL)",(self.uid,))
        backup=self.backup();PrivacyService(self.app).withdraw_consent(self.a,'device_label')
        self.app.session(self.a)
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertIsNone(db.execute('SELECT label FROM user_devices').fetchone()[0])
    def test_export_не_возвращает_retired_Help_или_чужой_текст(self):
        self.help_cycle();sweep(self.store);token=self.app.login('synthetic_life_a','synthetic-password-only')[0]
        data=PrivacyService(self.app).export(token);encoded=json.dumps(data)
        self.assertNotIn('helper note',encoded);self.assertNotIn('private text',encoded);self.assertNotIn('lifecycle_help_evidence',data['tables'])
    def test_удаление_generation_ticket_инвалидирует_pending(self):
        g=Generation(self.store,self.uid)
        with self.store._locked_transaction() as db:db.execute('UPDATE lifecycle_generation_times SET created_at=0 WHERE generation_id=?',(g.id,))
        backup=self.backup();self.assertGreater(sweep(self.store)['generations'],0)
        with self.assertRaises(ContextConflict):g.commit()
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM context_generations WHERE generation_id=?',(g.id,)).fetchone()[0],0)
    def test_retention_во_время_LLM_сохраняет_G09(self):
        self.app.chat(self.uid,'Привет')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00'")
        app=self.app
        class Provider:
            def generate(self,context):sweep(preflight(app.store.path));return 'Старый ответ.'
        with patch('xblago.integrations.web.build_chat_provider',return_value=Provider()):
            with self.assertRaises(ContextConflict):self.app.chat(self.uid,'Продолжим?')
        self.assertEqual(self.app.history(self.uid),[])
    def test_retired_ID_нельзя_вставить_снова(self):
        rid=self.aged_need();sweep(self.store)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._locked_transaction() as db:db.execute("INSERT INTO blago_needs VALUES (?,?,?,'','private','open','2030-01-01')",(rid,self.uid,'Повтор'))
    def test_потеря_tombstone_вызывает_fail_closed(self):
        self.aged_need();sweep(self.store)
        with self.store._connect() as db:db.execute('DELETE FROM lifecycle_holds');db.commit()
        with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_future_clock_и_подмена_политики(self):
        with self.assertRaises(ValueError):sweep(self.store,now=int(time.time())+1000)
        with self.store._connect() as db:db.execute("UPDATE lifecycle_policy SET policy_json='{}'");db.commit()
        with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_missing_witness_ничего_не_удаляет(self):
        self.aged_need()
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(Path(self.tmp.name)/'missing')}):
            with self.assertRaises(JournalUnavailable):sweep(self.store)
        self.assertEqual(len(self.app.rows('needs',self.uid)),1)
    def test_сбой_journal_rollback_без_ложного_успеха(self):
        self.aged_need()
        with patch('xblago.core.security_journal.SecurityJournal.append',side_effect=JournalUnavailable('synthetic fsync failure')):
            with self.assertRaises(JournalUnavailable):sweep(self.store)
        self.assertEqual(len(self.app.rows('needs',self.uid)),1)
    def test_crash_после_journal_до_SQL_replay_удаления(self):
        self.aged_need();p=multiprocessing.get_context('spawn').Process(target=crash_retention,args=(self.cfg,));p.start();p.join(20)
        if p.is_alive():p.kill();p.join();self.fail('worker hung')
        self.assertEqual(p.exitcode,81)
        with preflight(self.path)._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM blago_needs').fetchone()[0],0)
    def test_два_процесса_retention_не_дублируют_эффект(self):
        self.aged_need();ctx=multiprocessing.get_context('spawn');workers=[]
        try:
            for _ in range(2):
                parent,child=ctx.Pipe();p=ctx.Process(target=retire_worker,args=(self.cfg,child));p.start();child.close();workers.append((p,parent))
            values=[]
            for p,pipe in workers:self.assertTrue(pipe.poll(30));values.append(pipe.recv());p.join(10);self.assertEqual(p.exitcode,0)
            self.assertEqual(sum(values),1)
        finally:
            for p,pipe in workers:
                pipe.close()
                if p.is_alive():p.kill();p.join()
    def test_backup_retirement_и_физическое_удаление_только_учтённой_копии(self):
        backup=managed_backup(self.store);path=Path(backup['path']);retire_backup(self.store,backup['backup_id'])
        with self.assertRaises(JournalUnavailable):restore(path,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        self.assertTrue(path.exists());self.assertTrue(retire_backup(self.store,backup['backup_id'],unlink=True)['file_removed']);self.assertFalse(path.exists())
    def test_backup_подмена_байтов_не_удаляется_вслепую(self):
        backup=managed_backup(self.store);path=Path(backup['path']);path.write_bytes(b'synthetic wrong file')
        with self.assertRaises(ValueError):retire_backup(self.store,backup['backup_id'],unlink=True)
        self.assertTrue(path.exists())
    def test_старый_backup_до_migration_требует_явной_миграции(self):
        cfg=create(Path(self.tmp.name)/'before-life',help_enabled=True,context_fencing=True)
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));t=app.register('old_life','synthetic-password-only','synthetic-life')[0];uid=app.session(t)[0]
            item=app.create_item('needs',uid,{'title':'Синтетическая запись'})
            with app.store._locked_transaction() as db:db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00'")
            backup=app.store.backup_database();schema.migrate_lifecycle(app.store.path);sweep(app.store)
            with self.assertRaises(JournalUnavailable):preflight(backup)
            schema.migrate_lifecycle(backup)
            with preflight(backup)._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM blago_needs').fetchone()[0],0)
    def test_повторная_регистрация_не_возвращает_старую_память(self):
        self.store.set_memory(self.uid,'city','Старый синтетический город');p=PrivacyService(self.app).preview(self.a)
        PrivacyService(self.app).delete(self.a,p['challenge'],'УДАЛИТЬ')
        token=self.app.register('synthetic_life_a','synthetic-password-only','synthetic-life')[0];uid=self.app.session(token)[0]
        self.assertNotEqual(uid,self.uid);self.assertEqual(self.store.all_memory(uid),{});self.assertEqual(self.app.history(uid),[])
        self.integrity()
    def test_экономические_зависимости_не_удаляются_по_догадке(self):
        self.aged_need()
        self.app.create_payment_request(self.uid,{'title':'Синтетическая legacy запись','payment_purpose':'Только тест legacy metadata','amount_minor':100,'currency':'RUB'})
        with self.assertRaises(RetentionBlocked):sweep(self.store)
        self.assertEqual(len(self.app.rows('needs',self.uid)),1)
        with self.assertRaises(ApiError):PrivacyService(self.app).export(self.a)
    def test_неоднозначный_legacy_merge_блокирует_retention(self):
        uid=self.app.session(self.b)[0]
        with self.store._locked_transaction() as db:db.execute("UPDATE users SET status='merged' WHERE blago_user_id=?",(uid,))
        self.aged_need()
        with self.assertRaises(RetentionBlocked):sweep(self.store)
        self.assertEqual(len(self.app.rows('needs',self.uid)),1)
    def test_старый_context_checksum_и_идемпотентность_сохранены(self):
        from xblago.core.context_schema import migrate_context,CHECKSUM
        self.assertEqual(CHECKSUM,'79aed28a7444a8a4758d8133033dd636dee6a3a214c877faba338da315434aa0')
        self.assertFalse(migrate_context(self.path))
    def test_symlink_backup_directory_не_перенаправляет_удаление(self):
        backup=managed_backup(self.store);root=self.path.parent/'backups';moved=self.path.parent/'retained-backups';root.rename(moved);root.symlink_to(moved,target_is_directory=True)
        try:
            with self.assertRaises(ValueError):retire_backup(self.store,backup['backup_id'],unlink=True)
            self.assertTrue(Path(backup['path']).exists())
        finally:root.unlink();moved.rename(root)
    def test_HTTP_consent_требует_CSRF_и_не_принимает_чужой_uid(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        import threading
        from xblago.integrations.web import BlagoWebHandler
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=self.app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def request(body,csrf=True):
                headers={'Content-Type':'application/json','Cookie':'blago_session='+self.a}
                if csrf:headers['X-CSRF-Token']=self.app.session(self.a)[1]
                req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/v1/privacy/consent/withdraw',data=json.dumps(body).encode(),headers=headers)
                try:
                    with urlopen(req,timeout=10) as r:return r.status,json.load(r)
                except HTTPError as e:return e.code,json.load(e)
            self.assertEqual(request({'purpose':'optional_activity'},False)[0],403)
            self.assertEqual(request({'purpose':'optional_activity','blago_user_id':'other'})[0],400)
            self.assertEqual(request({'purpose':'optional_activity'})[0],200)
            with self.store._connect() as db:self.assertEqual(db.execute('SELECT DISTINCT blago_user_id FROM consent_records').fetchone()[0],self.uid)
        finally:server.shutdown();server.server_close();thread.join(5)
    def test_consent_withdrawal_инвалидирует_pending_generation(self):
        g=Generation(self.store,self.uid);PrivacyService(self.app).withdraw_consent(self.a,'optional_activity')
        with self.assertRaises(ContextConflict):g.commit()
    def test_истёкшее_activity_не_возвращается_после_backup(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO consent_records VALUES ('ttlgrant',?,'optional_activity','synthetic','granted',1)",(self.uid,))
            db.execute("INSERT INTO activity_events VALUES ('expiredactivity',?,'login','web',1,2)",(self.uid,))
        backup=self.backup();sweep(self.store);restored=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with restored._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM activity_events').fetchone()[0],0)
    def test_pending_WebAuthn_удаляется_каскадом_по_TTL(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO auth_challenges VALUES (?,?,? ,?,'localhost','http://localhost',1,2,NULL)",('a'*64,self.uid,'register_passkey','b'*64))
            db.execute('INSERT INTO webauthn_pending VALUES (?,?)',('a'*64,b'synthetic-handle'))
        sweep(self.store)
        with self.store._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM webauthn_pending').fetchone()[0],0)
        self.integrity()
    def test_retention_на_границе_срока_удаляет_обе_реплики(self):
        self.app.chat(self.uid,'Привет')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00' WHERE role='user'")
        self.assertEqual(sweep(self.store)['history'],2);self.assertEqual(self.app.history(self.uid),[])
    def test_неясные_границы_legacy_истории_не_угадываются(self):
        self.store.append_history(self.uid,'assistant','Синтетическая одиночная старая реплика')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(RetentionBlocked):sweep(self.store)
        self.assertEqual(len(self.app.history(self.uid)),1)
    def test_сертифицированный_backup_до_Help_context_восстанавливается_без_грантов(self):
        cfg=create(Path(self.tmp.name)/'before-all')
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));t=app.register('before_all','synthetic-password-only','synthetic-life')[0];uid=app.session(t)[0]
            backup=app.store.backup_database();schema.migrate_lifecycle(app.store.path)
            app.store.set_memory(uid,'city','Синтетический удаляемый город');app.store.delete_memory(uid,'city')
            with self.assertRaises(JournalUnavailable):preflight(backup)
            schema.migrate_lifecycle(backup);recovered=restore(backup,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
            self.assertNotIn('city',recovered.all_memory(uid));self.assertEqual(recovered.recent_history(uid,100),())
            self.integrity(recovered)
    def test_старые_отозванные_credentials_и_devices_не_восстанавливаются(self):
        with self.store._locked_transaction() as db:
            db.execute("INSERT INTO user_devices VALUES ('retireddev',?,NULL,1,2)",(self.uid,))
            db.execute("INSERT INTO passkey_credentials VALUES (?,?,?, ?,?,0,0,0,1,2)",(b'synthetic-cid',self.uid,'retireddev',b'synthetic-cose',b'synthetic-handle'))
        backup=self.backup();counts=sweep(self.store);self.assertEqual(counts['revoked_credentials'],1);self.assertEqual(counts['revoked_devices'],1)
        restored=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with restored._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM passkey_credentials').fetchone()[0],0)
    def test_действующий_passkey_не_удаляется_автоматически(self):
        with self.store._locked_transaction() as db:db.execute("INSERT INTO passkey_credentials VALUES (?,?,NULL,?,?,0,0,0,1,NULL)",(b'active-synthetic',self.uid,b'synthetic-cose',b'synthetic-handle'))
        self.assertEqual(sweep(self.store)['revoked_credentials'],0)
        with self.store._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM passkey_credentials').fetchone()[0],1)
