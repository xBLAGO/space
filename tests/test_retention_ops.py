"""Privacy01.4: synthetic operations, crash, concurrency and linkage attacks."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.help_readiness_lab import initialize as lab_init
from xblago.core.retention_ops import Runner, Schedule, initialize, OpsUnavailable, CHECKSUM
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.integrations.web import WebApp
from xblago.core.security_journal import SecurityJournal, JournalUnavailable
from xblago.domain.help_service import HelpService


def run_worker(root, pipe, mode):
    def held(store):
        pipe.send('LOCKED');pipe.recv()
        from xblago.core.data_lifecycle import managed_backup
        return managed_backup(store)
    def die():os._exit(84)
    r=Runner(root,enabled=True,backup=held if mode=='held' else None,after_cleanup=die if mode=='die' else None)
    if mode=='before':
        r.backup=lambda store:os._exit(83)
    try:pipe.send(r.tick())
    finally:pipe.close()


class RetentionOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'new';self.cfg=lab_init(self.root)
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.path=self.root/'app/blago.sqlite3';self.app=WebApp(preflight(self.path));self.store=self.app.store
        self.a=self.app.register('synthetic_ops_a','synthetic-password-only','synthetic-help011-only')[0];self.uid=self.app.session(self.a)[0]
        self.now=int(time.time());self.clock=lambda:self.now
        initialize(self.root,Schedule(interval=10,retry_seconds=2,max_attempts=2,overdue_seconds=20),clock=self.clock)
        self.r=Runner(self.root,enabled=True,clock=self.clock)
    def aged(self):
        row=self.app.create_item('needs',self.uid,{'title':'Синтетическая старая потребность'})
        with self.store._locked_transaction() as db:db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",(row['id'],))
        return row['id']
    def count(self):
        with self.store._locked_transaction() as db:return db.execute('SELECT count(*) FROM blago_needs').fetchone()[0]
    def ops(self,sql):
        with sqlite3.connect(self.r.path) as db:return db.execute(sql).fetchall()
    def spawn(self,mode):
        ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe();p=ctx.Process(target=run_worker,args=(str(self.root),child,mode));p.start();child.close()
        def stop():
            if p.is_alive():p.kill()
            p.join(10);parent.close()
        self.addCleanup(stop);return p,parent
    def test_OFF_по_умолчанию_не_пишет_ничего(self):
        self.aged();before=self.r.path.read_bytes()
        with patch.dict(os.environ,{'XBLAGO_RETENTION_SCHEDULER_ENABLED':'false'}):self.assertEqual(Runner(self.root).tick()['status'],'DISABLED')
        self.assertEqual(self.r.path.read_bytes(),before);self.assertEqual(self.count(),1)
    def test_нормальный_запуск_backup_очистка_и_интервал(self):
        self.aged();result=self.r.tick();self.assertEqual(result['status'],'SUCCESS');self.assertEqual(self.count(),0)
        self.assertEqual(self.r.tick()['status'],'WAITING');self.assertEqual(len(self.ops('SELECT * FROM ops_runs')),1)
        self.now+=10;self.assertEqual(self.r.tick()['status'],'SUCCESS');self.assertEqual(len(self.ops('SELECT * FROM ops_runs')),2)
    def test_задержка_создаёт_overdue_и_не_переименовывает_G04(self):
        self.now+=21;self.assertTrue(self.r.inspect()['overdue']);result=self.r.tick()
        self.assertTrue(result['overdue_before']);self.assertFalse(result['overdue']);self.assertEqual(result['G04'],'UNKNOWN')
    def test_ошибка_backup_не_запускает_очистку(self):
        self.aged();r=Runner(self.root,enabled=True,clock=self.clock,backup=lambda store:(_ for _ in ()).throw(OSError(errno.ENOSPC,'Секретный путь')))
        result=r.tick();self.assertEqual(result['status'],'FAILED');self.assertEqual(result['reason'],'STORAGE_FAILURE');self.assertEqual(self.count(),1)
        self.assertNotIn('Секретный',json.dumps(self.ops('SELECT * FROM ops_runs')))
    def test_ограниченные_retry_backoff_и_manual_reschedule(self):
        r=Runner(self.root,enabled=True,clock=self.clock,backup=lambda store:(_ for _ in ()).throw(OSError('private')))
        self.assertEqual(r.tick()['status'],'FAILED');self.assertEqual(r.tick()['status'],'WAITING')
        self.now+=2;self.assertEqual(r.tick()['status'],'FAILED');self.now+=30
        self.assertEqual(r.tick()['status'],'EXHAUSTED');self.assertTrue(r.inspect()['overdue']);self.assertEqual(len(self.ops('SELECT * FROM ops_runs')),2)
        self.assertTrue(r.reschedule()['overdue']);self.assertEqual(self.r.tick()['status'],'SUCCESS')
    def test_потеря_witness_до_запуска_fail_closed(self):
        self.aged();w=self.root/'witness';w.rename(self.root/'offline-witness')
        try:
            self.assertEqual(self.r.tick()['status'],'UNKNOWN_RECOVERY');self.assertEqual(self.ops('SELECT status FROM ops_runs'),[('BLOCKED',)])
        finally:(self.root/'offline-witness').rename(w)
        self.assertEqual(self.count(),1)
    def test_потеря_witness_после_commit_не_даёт_SUCCESS(self):
        self.aged();w=self.root/'witness'
        r=Runner(self.root,enabled=True,clock=self.clock,after_cleanup=lambda:w.rename(self.root/'offline-witness'))
        try:self.assertEqual(r.tick()['status'],'FAILED')
        finally:(self.root/'offline-witness').rename(w)
        self.assertEqual(self.count(),0);self.assertIsNone(self.ops('SELECT last_success FROM ops_state')[0][0])
        self.now+=3;self.assertEqual(self.r.tick()['status'],'SUCCESS')
    def test_два_процесса_не_исполняют_очистку_одновременно(self):
        self.aged();p,pipe=self.spawn('held');self.assertTrue(pipe.poll(30));self.assertEqual(pipe.recv(),'LOCKED')
        self.now+=1000;self.assertEqual(self.r.tick()['status'],'BUSY')
        pipe.send('release');self.assertTrue(pipe.poll(30));self.assertEqual(pipe.recv()['status'],'SUCCESS');p.join(10);self.assertEqual(p.exitcode,0)
        self.assertEqual(self.count(),0);self.assertEqual(len(self.ops('SELECT * FROM ops_runs')),1)
    def test_crash_до_очистки_после_durable_RUNNING(self):
        self.aged();p,pipe=self.spawn('before');p.join(30);self.assertEqual(p.exitcode,83)
        self.now=max(self.now,int(time.time())+1)
        self.assertEqual(self.count(),1);self.assertEqual(self.r.inspect()['status'],'INTERRUPTED')
        self.now=max(self.now+10,int(time.time())+10);self.assertEqual(self.r.tick()['status'],'SUCCESS')
        self.assertEqual(self.ops("SELECT count(*) FROM ops_runs WHERE status='INTERRUPTED'")[0][0],1)
    def test_crash_после_app_commit_не_откатывает_удаление(self):
        self.aged();backup=self.store.backup_database();p,pipe=self.spawn('die');p.join(30);self.assertEqual(p.exitcode,84)
        self.now=max(self.now,int(time.time())+1)
        self.assertEqual(self.count(),0);self.assertEqual(self.r.inspect()['status'],'INTERRUPTED')
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._locked_transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM blago_needs').fetchone()[0],0)
        self.now=max(self.now+10,int(time.time())+10);self.assertEqual(self.r.tick()['status'],'SUCCESS')
    def test_старый_app_backup_и_сохранённый_ops_не_возвращают_данные(self):
        self.aged();backup=self.store.backup_database();self.assertEqual(self.r.tick()['status'],'SUCCESS')
        recovered=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with recovered._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM blago_needs').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.r.inspect()['status'],'WAITING')
    def test_ops_старый_backup_не_восстанавливает_полномочия(self):
        old=self.r.path.read_bytes();self.aged();self.r.tick();self.r.path.write_bytes(old)
        self.assertEqual(self.r.inspect()['status'],'DUE');self.now+=21;self.assertTrue(self.r.inspect()['overdue']);self.assertEqual(self.count(),0)
    def test_потеря_ops_не_переинициализируется_молча(self):
        self.r.path.rename(self.r.path.with_suffix('.lost'))
        with self.assertRaises(OpsUnavailable):self.r.tick()
        with self.assertRaises(FileExistsError):initialize(self.root)
    def test_откат_часов_блокирует_операцию(self):
        self.now-=1;self.assertEqual(self.r.tick()['status'],'CLOCK_ROLLBACK');self.assertEqual(self.ops('SELECT status FROM ops_runs'),[('BLOCKED',)])
    def test_ошибка_фиксации_результата_не_даёт_ложного_SUCCESS(self):
        self.aged()
        def lose():self.r.path.rename(self.r.path.with_suffix('.offline'))
        r=Runner(self.root,enabled=True,clock=self.clock,after_cleanup=lose)
        with self.assertRaises(OpsUnavailable):r.tick()
        self.assertEqual(self.count(),0)
    def test_нет_реального_провайдера_и_нет_произвольного_корня(self):
        with self.assertRaises(FileNotFoundError):Runner(Path(self.tmp.name)/'absent')
        marker=self.root/'lab-only.json';data=json.loads(marker.read_text());data['config']['XBLAGO_LLM_PROVIDER']='hf';marker.write_text(json.dumps(data))
        with self.assertRaises(ValueError):Runner(self.root)
    def test_symlink_ops_не_открывает_чужой_файл(self):
        moved=self.root/'moved-ops';self.r.directory.rename(moved);self.r.directory.symlink_to(moved,target_is_directory=True)
        with self.assertRaises(OpsUnavailable):self.r.tick()
    def test_неверная_схема_оператора_не_ремонтируется(self):
        with sqlite3.connect(self.r.path) as db:db.execute("UPDATE ops_schema SET checksum='wrong'")
        with self.assertRaises(OpsUnavailable):self.r.tick()
    def test_операторский_TTL_не_удаляет_privacy_holds(self):
        self.aged();self.r.tick();self.now+=8*86400;self.r.tick()
        self.assertEqual(self.ops('SELECT count(*) FROM ops_runs')[0][0],1)
        with self.store._locked_transaction() as db:self.assertGreater(db.execute('SELECT count(*) FROM lifecycle_holds').fetchone()[0],0)
    def test_legacy_blocker_не_превращается_в_SUCCESS(self):
        self.store.append_history(self.uid,'assistant','Синтетическая старая одиночная реплика')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.r.tick()['reason'],'RETENTION_REVIEW_REQUIRED')
    def test_активная_память_и_Help_не_меняются(self):
        self.store.set_memory(self.uid,'city','Синтетический город');h=HelpService(self.app)
        b=self.app.register('synthetic_ops_b','synthetic-password-only','synthetic-help011-only')[0]
        rid=h.draft(self.a,'Синтетический запрос',[h.code(b)['code']],'ops-private-draft')['id']
        self.r.tick();self.assertEqual(h.get(self.a,rid)['state'],'DRAFT');self.assertIn('city',self.store.all_memory(self.uid))

    def test_чужой_ops_journal_не_принимается(self):
        with sqlite3.connect(self.r.path) as db:db.execute("UPDATE ops_binding SET journal_id='another' WHERE singleton=1")
        self.assertEqual(self.r.tick()['status'],'UNKNOWN_RECOVERY')

    def test_подмена_периодичности_требует_нового_подтверждения(self):
        with sqlite3.connect(self.r.path) as db:
            policy=json.loads(db.execute('SELECT policy FROM ops_binding').fetchone()[0]);policy['interval']=11
            from xblago.core.security_journal import canonical
            db.execute('UPDATE ops_binding SET policy=?',(canonical(policy),))
        self.assertEqual(self.r.tick()['status'],'UNKNOWN_RECOVERY')

    def test_персональные_строки_из_ошибочного_adapter_не_логируются(self):
        r=Runner(self.root,enabled=True,clock=self.clock,cleanup=lambda store:{'private':'Секретный синтетический текст'})
        self.assertEqual(r.tick()['status'],'FAILED');self.assertNotIn('Секретный',json.dumps(self.ops('SELECT * FROM ops_runs')))

    def test_ops_миграция_не_меняет_app_DDL_и_прежние_checksums(self):
        from xblago.core.lifecycle_schema import CHECKSUM as LIFE
        from xblago.core.context_schema import CHECKSUM as CONTEXT
        with self.store._locked_transaction() as db:before=db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
        self.r.tick()
        with self.store._locked_transaction() as db:after=db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
        self.assertEqual([tuple(r) for r in before],[tuple(r) for r in after])
        self.assertEqual(LIFE,'1549c5875e15571f35ae93c1f2535c42fcc690df65f04be29629ce3c2c89e02a')
        self.assertEqual(CONTEXT,'79aed28a7444a8a4758d8133033dd636dee6a3a214c877faba338da315434aa0')

    def test_истёкший_managed_backup_удаляется_после_durable_retirement(self):
        from xblago.core.data_lifecycle import managed_backup
        old=managed_backup(self.store)
        with self.store._locked_transaction() as db:db.execute('UPDATE lifecycle_backups SET expires_at=0 WHERE backup_id=?',(old['backup_id'],))
        self.assertEqual(self.r.tick()['status'],'SUCCESS');self.assertFalse(Path(old['path']).exists())
        with self.store._locked_transaction() as db:self.assertEqual(db.execute('SELECT state FROM lifecycle_backups WHERE backup_id=?',(old['backup_id'],)).fetchone()[0],'RETIRED')

    def test_scheduler_во_время_LLM_не_обходит_G09(self):
        from xblago.core.context_fencing import ContextConflict
        self.app.chat(self.uid,'Привет')
        with self.store._locked_transaction() as db:db.execute("UPDATE dialogue_turns SET created_at='2000-01-01T00:00:00+00:00'")
        runner=self.r
        class Provider:
            calls=0
            def generate(self,context):
                self.calls+=1
                if runner.tick()['status']!='SUCCESS':raise AssertionError('Synthetic cleanup failed')
                return 'Ответ из удалённого контекста.'
        provider=Provider()
        with patch('xblago.integrations.web.build_chat_provider',return_value=provider):
            with self.assertRaises(ContextConflict):self.app.chat(self.uid,'Продолжим?')
        self.assertEqual(provider.calls,1);self.assertEqual(self.app.history(self.uid),[])

    def test_ошибка_новой_ops_миграции_откатывает_DDL(self):
        from xblago.core import retention_ops
        other=Path(self.tmp.name)/'bad-ops';lab_init(other)
        with patch.object(retention_ops,'DDL',retention_ops.DDL+('INVALID SQL',)):
            with self.assertRaises(sqlite3.Error):initialize(other)
        with sqlite3.connect(other/'retention-ops/operations.sqlite3') as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
        with self.assertRaises(FileExistsError):initialize(other)


class HelpReidentificationTests(unittest.TestCase):
    def test_старый_backup_и_ключ_связывают_псевдонимный_target(self):
        from tools.retention_ops_lab import reidentification_probe
        result=reidentification_probe()
        self.assertTrue(result['key_and_old_backup_linkable'])
        self.assertTrue(result['side_information_singles_out_without_key'])
        self.assertTrue(result['ambiguous_metadata_not_unique'])
        self.assertFalse(result['anonymous'])
        self.assertTrue(result['no_personal_fields_in_summary'])
        self.assertTrue(result['personal_export_excludes_summary'])
