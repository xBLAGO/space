"""Help 01.1: только синтетические crash, DR, ACL и несколько процессов."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import unittest
from unittest.mock import patch

import tests.test_help01 as base
from xblago.core.integration import preflight
from xblago.core.journal_custody import export_bundle, recover_bundle
from xblago.core.privacy_maintenance import restore, cleanup
from xblago.core.security_journal import SecurityJournal, JournalUnavailable
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp, ApiError


def crash_worker(config, token, challenge, phase):
    os.environ.update(config)
    from xblago.core.privacy_control import PrivacyControl
    service = HelpService(WebApp(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
    if phase == 'before_journal':
        original = PrivacyControl.flush
        def crash(control):
            if control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='help_state'").fetchone():
                os._exit(74)
            return original(control)
        with patch.object(PrivacyControl, 'flush', crash):
            service.commit(token, challenge, 'ПОДТВЕРЖДАЮ')
    else:
        service.commit(token, challenge, 'ПОДТВЕРЖДАЮ')
        os._exit(75)  # committed; no result delivered to caller


def duplicate_worker(config, token, challenge, connection):
    os.environ.update(config)
    try:
        service = HelpService(WebApp(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        connection.send(service.commit(token, challenge, 'ПОДТВЕРЖДАЮ'))
    finally:
        connection.close()


class HelpReadinessTests(unittest.TestCase):
    setUp = base.Help01Tests.setUp
    draft = base.Help01Tests.draft
    prepare = base.Help01Tests.prepare
    action = base.Help01Tests.action
    offered = base.Help01Tests.offered
    accepted = base.Help01Tests.accepted

    def check_integrity(self, store=None):
        with (store or self.app.store)._connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0], 2)

    def test_аналитика_не_возвращает_финансы_и_малую_группу(self):
        uid = self.app.session(self.a)[0]
        self.assertEqual(set(self.app.analytics(uid)), {'needs','open_needs','resources','memory_facts'})
        self.assertEqual(self.app.analytics(uid, True), {'participants':None,'published_needs':None,'published_resources':None})
        for name in ('fourth', 'fifth'):
            self.app.register(name, 'synthetic-password-only', 'synthetic-help-invite')
        public = self.app.analytics(uid, True)
        self.assertEqual(public, {'participants':5,'published_needs':None,'published_resources':None})
        self.assertNotIn('external_fiat_demand_minor', public)

    def test_пять_аккаунтов_не_раскрывают_единственного_автора(self):
        uid=self.app.session(self.a)[0]
        for name in ('fourth','fifth'):
            self.app.register(name,'synthetic-password-only','synthetic-help-invite')
        self.app.create_item('needs',uid,{'title':'Синтетическая потребность','visibility':'community'})
        self.assertIsNone(self.app.analytics(uid,True)['published_needs'])
        with self.app.store._connect() as db:ids=[r[0] for r in db.execute("SELECT blago_user_id FROM users WHERE status='active'")]
        for participant in ids:
            if participant!=uid:self.app.create_item('needs',participant,{'title':'Синтетическая потребность','visibility':'community'})
        self.assertEqual(self.app.analytics(uid,True)['published_needs'],5)

    def test_отозванная_сессия_не_исполняет_подтверждение(self):
        rid = self.draft(); preview = self.prepare(self.a, rid, 'publish')
        self.app.logout(self.a)
        with self.assertRaises(ApiError):
            self.h.commit(self.a, preview['challenge'], 'ПОДТВЕРЖДАЮ')
        self.a = self.app.login('synthetic_author','synthetic-password-only')[0]
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT')

    def test_повтор_просроченной_квитанции_не_даёт_новых_прав(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        receipt=self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        with patch('xblago.domain.help_service.time.time',return_value=20000000000):
            # Even if an old content-free receipt is returned, no action is repeated.
            try:self.assertEqual(self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ'),receipt)
            except ApiError:pass  # session expiry may deny the retry first
        self.assertEqual(len(self.h.get(self.a,rid)['events']),2)

    def test_аккаунт_удалён_до_commit_нет_публикации(self):
        from xblago.integrations.privacy import PrivacyService
        rid=self.draft();p=self.prepare(self.a,rid,'publish');privacy=PrivacyService(self.app)
        intent=privacy.preview(self.a);privacy.delete(self.a,intent['challenge'],'УДАЛИТЬ')
        with self.assertRaises(ApiError):self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.h.feed(self.b)['items'],[])
        self.check_integrity()

    def test_истёкший_preview_очищается_без_удаления_истории(self):
        rid=self.draft();self.prepare(self.a,rid,'publish')
        with self.app.store._locked_transaction() as db:
            db.execute('UPDATE help_challenges SET expires_at=0')
        result=cleanup(self.app.store)
        self.assertEqual(result['help_challenges'],1)
        self.assertEqual(len(self.h.get(self.a,rid)['events']),1)
        self.check_integrity()

    def test_потеря_ключа_не_создаёт_новый_journal(self):
        rid=self.draft();key=Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])/'key.json';held=key.with_suffix('.held')
        key.rename(held)
        try:
            with self.assertRaises(JournalUnavailable):self.h.get(self.a,rid)
            self.assertFalse(key.exists())
        finally:held.rename(key)
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT')

    def test_witness_недоступен_нет_чтения_или_успешной_записи(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        directory=Path(self.cfg['XBLAGO_JOURNAL_WITNESS_DIR']);offline=directory.with_name('offline-witness');directory.rename(offline)
        try:
            for operation in (lambda:self.h.get(self.b,rid),lambda:self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')):
                with self.assertRaises(JournalUnavailable):operation()
            self.assertFalse(directory.exists())
        finally:offline.rename(directory)
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT')

    def test_полный_диск_при_fsync_не_даёт_ACK(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        with patch('xblago.core.journal_witness.os.fsync',side_effect=OSError(errno.ENOSPC,'synthetic disk full')):
            with self.assertRaises(JournalUnavailable):self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT');self.check_integrity()

    def test_авария_до_journal_откатывает_WAL(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        proc=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.cfg,self.a,p['challenge'],'before_journal'))
        proc.start();proc.join(20)
        if proc.is_alive():proc.kill();proc.join();self.fail('crash worker hung')
        self.assertEqual(proc.exitcode,74)
        restarted=HelpService(WebApp(preflight(self.path)))
        self.assertEqual(restarted.get(self.a,rid)['state'],'DRAFT');self.check_integrity()

    def test_авария_после_SQL_commit_потерянный_ACK_не_повторяет_событие(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        proc=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.cfg,self.a,p['challenge'],'after_commit'))
        proc.start();proc.join(20)
        if proc.is_alive():proc.kill();proc.join();self.fail('crash worker hung')
        self.assertEqual(proc.exitcode,75)
        restarted=HelpService(WebApp(preflight(self.path)))
        receipt=restarted.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(receipt['state'],'PUBLISHED')
        self.assertEqual(len(restarted.get(self.a,rid)['events']),2);self.check_integrity()

    def test_три_процесса_повторяют_одно_подтверждение(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish');ctx=multiprocessing.get_context('spawn');workers=[]
        try:
            for _ in range(3):
                parent,child=ctx.Pipe();proc=ctx.Process(target=duplicate_worker,args=(self.cfg,self.a,p['challenge'],child));proc.start();child.close();workers.append((proc,parent))
            receipts=[]
            for proc,pipe in workers:
                self.assertTrue(pipe.poll(30));receipts.append(pipe.recv());proc.join(10);self.assertEqual(proc.exitcode,0)
            self.assertTrue(all(r==receipts[0] for r in receipts))
            self.assertEqual(len(self.h.get(self.a,rid)['events']),2);self.check_integrity()
        finally:
            for proc,pipe in workers:
                pipe.close()
                if proc.is_alive():proc.kill();proc.join()

    def test_DR_новый_экземпляр_повторяет_удаление_памяти_и_отзыв(self):
        uid=self.app.session(self.a)[0];self.app.chat(uid,'запомни город = Синтетический город')
        rid=self.draft();self.action(self.a,rid,'publish');backup=self.app.store.backup_database()
        self.app.delete_memory(uid,'город');self.action(self.a,rid,'withdraw')
        # Separate directories simulate custody domains; NOT independent hosts/IAM.
        escrow=Path(self.tmp.name)/'escrow';escrow.mkdir(mode=0o700)
        key=escrow/'wrap.key';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
        package=escrow/'journal.aesgcm';export_bundle(SecurityJournal(Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])),package,key)
        retained=Path(self.tmp.name)/'retained-copy';shutil.copytree(self.cfg['XBLAGO_JOURNAL_WITNESS_DIR'],retained)
        recovered=Path(self.tmp.name)/'recovered-control';recover_bundle(package,recovered,key,retained)
        # Existing database is an OLD offline application backup, new instance/config.
        with patch.dict(os.environ,{'XBLAGO_SECURITY_JOURNAL_DIR':str(recovered),'XBLAGO_JOURNAL_WITNESS_DIR':str(retained)}):
            store=restore(backup,recovered);app=WebApp(store);h=HelpService(app)
            with self.assertRaises(ApiError):app.session(self.a)
            token=app.login('synthetic_author','synthetic-password-only')[0]
            self.assertEqual(app.session(token)[0],uid)
            self.assertNotIn('город',store.all_memory(uid));self.assertEqual(len(app.history(uid)),2)
            self.assertEqual(h.get(token,rid)['state'],'WITHDRAWN');self.assertEqual(h.get(token,rid)['text'],'')
            self.check_integrity(store)

    def test_старый_DR_и_потерянный_witness_не_выдают_права(self):
        rid=self.draft();escrow=Path(self.tmp.name)/'escrow';escrow.mkdir(mode=0o700)
        key=escrow/'key';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
        package=escrow/'old';export_bundle(SecurityJournal(Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])),package,key)
        self.action(self.a,rid,'publish');self.action(self.a,rid,'withdraw')
        with self.assertRaises(JournalUnavailable):recover_bundle(package,escrow/'recovered',key,Path(self.cfg['XBLAGO_JOURNAL_WITNESS_DIR']))
        with self.assertRaises(JournalUnavailable):recover_bundle(package,escrow/'missing-control',key,escrow/'lost-witness')
        self.assertFalse((escrow/'recovered').exists());self.assertFalse((escrow/'missing-control').exists())

    def test_подмена_журнала_и_состояния_не_исправляется_автоматически(self):
        rid=self.draft()
        with self.app.store._connect() as db:
            db.execute('UPDATE help_requests SET text=? WHERE id=?',('Неподтверждённый текст',rid));db.commit()
        with self.assertRaises(JournalUnavailable):self.h.get(self.a,rid)

    def test_копия_без_живого_WAL_не_восстанавливает_публикацию(self):
        import sqlite3
        # Checkpoint actors/schema, then keep a reader so subsequent writes stay in WAL.
        with self.app.store._connect() as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        reader=sqlite3.connect(self.path)
        try:
            reader.execute('BEGIN');reader.execute('SELECT count(*) FROM help_requests').fetchone()
            rid=self.draft();self.action(self.a,rid,'publish')
            self.assertTrue(Path(str(self.path)+'-wal').exists())
            copy=Path(self.tmp.name)/'unsafe-main-only.sqlite3';shutil.copy2(self.path,copy)
        finally:reader.close()
        restored=preflight(copy)
        with restored._connect() as db:
            self.assertTrue(db.execute('SELECT 1 FROM help_holds').fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM help_requests WHERE id=?',(rid,)).fetchone()[0],0)
        self.check_integrity(restored)
