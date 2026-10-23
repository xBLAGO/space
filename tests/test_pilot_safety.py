"""05.4: independent-review regressions, patched SQLite and off-instance recovery."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_privacy as fixtures
from xblago.core import sqlite_safety
from xblago.core.journal_custody import export_bundle,recover_bundle
from xblago.core.journal_witness import initialize as initialize_witness
from xblago.core.pilot_schema import migrate_pilot
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import SecurityJournal,JournalUnavailable
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import WebApp,ApiError


class PilotSafetyTests(unittest.TestCase):
    def setUp(self):
        fixtures.PrivacyTests.setUp(self)
        self.assertTrue(migrate_pilot(self.path))
    backup=fixtures.PrivacyTests.backup
    reopened=fixtures.PrivacyTests.reopened

    def witness(self):
        directory=self.root/'independent-witness'
        initialize_witness(SecurityJournal(self.directory),directory)
        return directory

    def wrapping_key(self):
        path=self.root/'escrow.key'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(os.urandom(32))
        return path

    def test_actual_python_sqlite_is_verified(self):
        self.assertTrue(sqlite_safety.require_safe_sqlite()['wal_reset_fix_verified'])

    def test_unpatched_sqlite_prevents_pilot_before_database_creation(self):
        path=self.root/'must-not-exist.sqlite3'
        with patch.dict(os.environ,{'XBLAGO_PILOT_MODE':'true'}),patch.object(sqlite_safety,'runtime_evidence',return_value={'wal_reset_fix_verified':False}):
            with self.assertRaises(sqlite_safety.UnsafeSQLite):
                SQLiteBlagoStore(path)
        self.assertFalse(path.exists())

    def test_migration_repeat_and_uncovered_pilot_backup_rejected(self):
        self.assertFalse(migrate_pilot(self.path))
        backup=self.backup()
        with sqlite3.connect(backup) as db:
            for kind,name in db.execute("SELECT type,name FROM sqlite_master WHERE name LIKE 'pilot_%' AND sql IS NOT NULL").fetchall():
                db.execute(f'DROP {kind} "{name}"')
        witness=self.witness()
        with patch.dict(os.environ,{'XBLAGO_PILOT_MODE':'true','XBLAGO_JOURNAL_WITNESS_DIR':str(witness)}),self.assertRaises(JournalUnavailable):
            self.reopened(backup)

    def test_old_value_update_delete_does_not_resurrect(self):
        self.store.memory_for_user(self.uid).set('city','Старый город')
        backup=self.backup()
        self.store.memory_for_user(self.uid).set('city','Новый город')
        self.store.delete_memory(self.uid,'city')
        self.assertEqual(self.reopened(backup).store.all_memory(self.uid),{})

    def test_exact_merge_lineage_with_changed_occupied_keys(self):
        source=self.store.resolve_telegram_user(TelegramIdentity(101,101,'private'))
        self.store.memory_for_user(self.uid).set('city','Город A')
        self.store.memory_for_user(source).set('city','Город B')
        self.store.append_history(source,'user','История до объединения')
        backup=self.backup()
        # This allocation did not exist in the backup: recomputing merge would choose .city.
        self.store.memory_for_user(self.uid).set('telegram.city','Другая запись')
        self.app.redeem_telegram_link(source,self.app.new_link_code(self.uid,self.token))
        token,_=self.app.login('person','long-test-password')
        self.assertEqual(self.store.all_memory(self.uid)['telegram.city.2'],'Город B')
        self.store.delete_memory(self.uid,'telegram.city.2')
        self.store.clear_history(self.uid)
        recovered=self.reopened(backup)
        token,_=recovered.login('person','long-test-password')
        exported=PrivacyService(recovered).export(token)
        raw=json.dumps(exported,ensure_ascii=False)
        self.assertNotIn('Город B',raw)
        self.assertNotIn('История до объединения',raw)
        self.assertIn('Город A',raw)

    def test_lineage_does_not_match_another_participant_by_value(self):
        other,_=self.app.register('other','other-long-password','synthetic-invite')
        peer=self.app.session(other)[0]
        for uid in (self.uid,peer):
            self.store.memory_for_user(uid).set('city','Одинаковый город')
        backup=self.backup()
        self.store.memory_for_user(self.uid).set('city','Изменение')
        self.store.delete_memory(self.uid,'city')
        recovered=self.reopened(backup)
        self.assertEqual(recovered.store.all_memory(self.uid),{})
        self.assertEqual(recovered.store.all_memory(peer),{'city':'Одинаковый город'})

    def test_re_remember_after_delete_is_not_removed_by_old_intent(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        self.store.delete_memory(self.uid,'city')
        self.store.memory_for_user(self.uid).set('city','Казань')
        backup=self.backup()
        self.assertEqual(self.reopened(backup).store.all_memory(self.uid),{'city':'Казань'})

    def test_lineage_cycle_and_delete_sequence_boundary(self):
        control=object.__new__(PrivacyControl)
        def row(seq,before,after):
            return (seq,'id',json.dumps({'kind':'memory_lineage','owner':'o','target':before,'state':after,'recorded_at':1}),'prev','mac')
        control.rows=[row(1,'a','b'),row(2,'b','a'),row(4,'future','b')]
        control.replay_seq=3
        self.assertEqual(control._lineage_targets('memory_lineage','b'),['a','b'])

    def test_history_cache_is_scrubbed_but_dedupe_survives_restore(self):
        self.store.append_history(self.uid,'user','Удаляемая переписка')
        self.store.begin_update('telegram','cached-history',self.uid)
        self.store.mark_update_processed('telegram','cached-history',[{'chat_id':101,'text':'Удаляемая переписка'}])
        backup=self.backup()
        self.store.clear_history(self.uid)
        self.assertFalse(self.store.cached_update_replies('telegram','cached-history'))
        recovered=self.reopened(backup)
        self.assertFalse(recovered.store.cached_update_replies('telegram','cached-history'))
        self.assertFalse(recovered.store.begin_update('telegram','cached-history',self.uid))

    def test_signed_negative_bool_and_float_anchor_rejected(self):
        journal=SecurityJournal(self.directory)
        rows,chain=journal.read()
        for seq in (-1,True,0.0):
            journal._write_anchor(seq,chain[-1])
            with self.assertRaises(JournalUnavailable):
                journal.read()
        journal._write_anchor(len(rows),chain[-1])

    def test_lock_open_failure_is_normalized(self):
        journal=SecurityJournal(self.directory)
        original=os.open
        def fail(path,*args,**kwargs):
            if Path(path).name=='journal.lock':
                raise PermissionError('synthetic lock permission failure')
            return original(path,*args,**kwargs)
        with patch('os.open',side_effect=fail),self.assertRaises(JournalUnavailable):
            journal.read()

    def test_fsync_failure_leaves_durable_intent_fail_closed(self):
        self.store.memory_for_user(self.uid).set('city','Казань')
        with patch('os.fsync',side_effect=OSError('synthetic full disk')),self.assertRaises(JournalUnavailable):
            self.store.delete_memory(self.uid,'city')
        self.assertEqual(self.reopened(self.path).store.all_memory(self.uid),{})

    def test_witness_detects_rollback_of_journal_and_local_head(self):
        witness=self.witness()
        snapshot=self.root/'old-control-plane'
        shutil.copytree(self.directory,snapshot)
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness)}):
            self.app.logout(self.token)
            with self.assertRaises(JournalUnavailable):
                SecurityJournal(snapshot).read()

    def test_unavailable_witness_prevents_acknowledgement(self):
        witness=self.witness()
        before=self.store.all_memory(self.uid)
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness/'missing')}),self.assertRaises(JournalUnavailable):
            self.app.logout(self.token)
        self.assertEqual(self.store.all_memory(self.uid),before)
        self.assertTrue(self.app.session(self.token))

    def test_visible_checkpoint_after_fsync_failure_is_resynced_before_retry_ack(self):
        witness=self.witness()
        import xblago.core.journal_witness as module
        original=module._sync_dir
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness)}):
            calls=0
            def fail_once(directory):
                nonlocal calls
                if Path(directory).resolve()==witness.resolve():
                    calls+=1
                    # read() at append entry first verifies the existing checkpoint;
                    # fail the later fsync of the newly published high-water mark.
                    if calls==3:
                        raise OSError('synthetic witness directory fsync failure')
                return original(directory)
            with patch.object(module,'_sync_dir',side_effect=fail_once),self.assertRaises(JournalUnavailable):
                self.app.logout(self.token)
            with sqlite3.connect(self.directory/'journal.sqlite3') as journal_db:
                self.assertEqual(journal_db.execute('SELECT count(*) FROM entries').fetchone()[0],1)
            with patch.object(module,'_sync_dir',wraps=original) as retry:
                SecurityJournal(self.directory).read()
            self.assertTrue(any(Path(call.args[0]).resolve()==witness.resolve() for call in retry.call_args_list))

    def test_stale_encrypted_package_wrong_key_and_tampering_rejected(self):
        witness=self.witness()
        key=self.wrapping_key()
        package=self.root/'old-package.json'
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness)}):
            export_bundle(SecurityJournal(self.directory),package,key)
            self.app.logout(self.token)
            with self.assertRaises(JournalUnavailable):
                recover_bundle(package,self.root/'stale',key,witness)
        badkey=self.root/'wrong.key'
        badkey.write_bytes(os.urandom(32));badkey.chmod(0o600)
        with self.assertRaises(JournalUnavailable):
            recover_bundle(package,self.root/'wrong',badkey,witness)
        envelope=json.loads(package.read_text());envelope['ciphertext']='AAAA'
        package.write_text(json.dumps(envelope))
        with self.assertRaises(JournalUnavailable):
            recover_bundle(package,self.root/'tampered',key,witness)
        self.assertFalse((self.root/'stale').exists())
        self.assertFalse((self.root/'wrong').exists())

    def test_lost_server_recovery_in_separate_instance(self):
        witness=self.witness()
        key=self.wrapping_key()
        self.store.memory_for_user(self.uid).set('city','Удаляемый город')
        bob,_=self.app.register('survivor','survivor-long-password','synthetic-invite')
        bob_uid=self.app.session(bob)[0]
        self.store.memory_for_user(bob_uid).set('city','Сохраняемый город')
        backup=self.backup()
        package=self.root/'dr-package.json'
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(witness)}):
            preview=self.service.preview(self.token)
            self.service.delete(self.token,preview['challenge'],'УДАЛИТЬ')
            export_bundle(SecurityJournal(self.directory),package,key)
        # Remove the primary journal and app DB, including key/head. Only independent custody survives.
        shutil.rmtree(self.directory)
        for path in self.path.parent.glob(self.path.name+'*'):
            path.unlink()
        server_b=self.root/'server-b';server_b.mkdir(mode=0o700)
        new_journal=server_b/'journal'
        recover_bundle(package,new_journal,key,witness)
        recovered_path=server_b/'app.sqlite3'
        shutil.copy2(backup,recovered_path)
        env=dict(os.environ,XBLAGO_SECURITY_JOURNAL_DIR=str(new_journal),XBLAGO_JOURNAL_WITNESS_DIR=str(witness))
        code='''from pathlib import Path
import os,sys
from xblago.core.privacy_maintenance import restore
from xblago.integrations.web import WebApp,ApiError
s=restore(Path(sys.argv[1]),Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
os.environ['XBLAGO_PILOT_MODE']='true'
a=WebApp(s); token,_=a.login('survivor','survivor-long-password'); uid=a.session(token)[0]
assert a.store.all_memory(uid)=={'city':'Сохраняемый город'}
with a.store._connect() as db:
    assert db.execute("SELECT count(*) FROM users WHERE status='deleted'").fetchone()[0]==1
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
print('independent-instance recovery: deletion replayed, survivor authenticated, FK clean')
'''
        result=subprocess.run([sys.executable,'-c',code,str(recovered_path)],env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('independent-instance recovery',result.stdout)
