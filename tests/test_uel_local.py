"""Stateful UEL assertions on disposable, guarded SQLite."""
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.help_readiness_lab import initialize
from xblago.core import uel_schema, uel_local
from xblago.core.asset_registry_schema import migrate_registry
from xblago.core.context_fencing import ContextConflict, Generation
from xblago.core.data_lifecycle import sweep
from xblago.core.integration import preflight
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable, SecurityJournal
from xblago.domain.asset_registry import AssetRegistry
from xblago.domain.help_service import HelpService
from xblago.domain.local_ledger import LocalLedger
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import ApiError, WebApp

PASSWORD = 'synthetic-password-only'
INVITE = 'synthetic-help011-only'
KEY = 'synthetic-uel-asset-key-001'
CONFIRM = 'ПОДТВЕРЖДАЮ'


def body(**kwargs):
    return dict({'label':'SYNTHETIC_PRIVATE_ASSET_TITLE','category':'computer','quantity':'2',
                 'available':'0','unit':'item','right':'OWNER','share_consent':False,
                 'audience':'PRIVATE','conditions':'ON_REQUEST'}, **kwargs)


def worker(cfg, token, challenge, phase, pipe=None):
    os.environ.update(cfg)
    service = AssetRegistry(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
    original = PrivacyControl.flush

    def flush(control):
        pending = control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='uel_local'").fetchone()
        if pending and phase == 'before_journal':
            os._exit(71)
        original(control)
        if pending and phase == 'after_journal':
            os._exit(72)

    if pipe:
        pipe.send('ready')
        pipe.recv()
    try:
        with patch.object(PrivacyControl, 'flush', flush):
            result = service.commit(token, challenge, CONFIRM)
        if phase == 'after_commit':
            os._exit(73)
        if pipe:
            pipe.send({'ok':result})
    except ApiError as exc:
        if pipe:
            pipe.send({'denied':exc.status})
        else:
            raise
    finally:
        if pipe:
            pipe.close()


class LocalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'new'
        self.cfg = initialize(self.root)
        env = patch.dict(os.environ, self.cfg)
        env.start()
        self.addCleanup(env.stop)
        self.path = self.root/'app/blago.sqlite3'
        self.journal = Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])
        migrate_registry(self.path)
        self.cfg['XBLAGO_ASSET_REGISTRY_ENABLED'] = 'true'
        os.environ.update(self.cfg)
        self.app = WebApp(preflight(self.path))
        self.a, self.b, self.c = [self.app.register('uel_synthetic_'+name,PASSWORD,INVITE)[0]
                                  for name in ('owner','helper','outsider')]
        self.pre_uel = self.app.store.backup_database(Path(self.tmp.name)/'before-uel')
        self.assertTrue(uel_schema.migrate_local(self.path))
        self.cfg['XBLAGO_UEL_LOCAL_ENABLED'] = 'true'
        os.environ.update(self.cfg)
        self.app = WebApp(preflight(self.path))
        self.r, self.h, self.l = AssetRegistry(self.app), HelpService(self.app), LocalLedger(self.app)

    def records(self):
        with self.app.store._locked_transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]

    def asset(self, token=None, **kwargs):
        token = token or self.a
        return self.r.draft(token,body(**kwargs),KEY)['id']

    def preview(self, aid, version=1, **kwargs):
        return self.r.prepare(self.a,aid,version,body(**kwargs))['challenge']

    def publish(self):
        aid = self.asset()
        challenge = self.preview(aid,available='1',share_consent=True,audience='LAB_COMMUNITY')
        self.r.commit(self.a,challenge,CONFIRM)
        return aid

    def request(self):
        return self.h.draft(self.a,'Мне нужна помощь с синтетическим компьютером',
                            [self.h.code(self.b)['code']],'synthetic-uel-help-key-001')['id']

    def act(self, token, rid, action, args=None):
        preview = self.h.prepare(token,rid,self.h.get(token,rid)['version'],action,args or {})
        return self.h.commit(token,preview['challenge'],CONFIRM)

    def complete(self):
        rid = self.request()
        self.act(self.a,rid,'publish')
        self.act(self.b,rid,'offer',{'note':'Синтетическая помощь'})
        self.act(self.a,rid,'accept',{'offer_id':self.h.get(self.a,rid)['offers'][0]['id']})
        self.act(self.a,rid,'confirm')
        self.assertEqual(self.l.balance(self.a)['fulfilled_occurrences'],0)
        self.act(self.b,rid,'confirm')
        return rid

    def integrity(self):
        with self.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            uel_local.check(db,PrivacyControl.open(db))

    def spawn(self, challenge, phase, expected):
        ctx = multiprocessing.get_context('spawn')
        proc = ctx.Process(target=worker,args=(self.cfg,self.a,challenge,phase))
        proc.start()
        try:
            proc.join(30)
            self.assertEqual(proc.exitcode,expected)
        finally:
            if proc.is_alive():
                proc.kill()
                proc.join()

    def parallel(self, challenges):
        ctx = multiprocessing.get_context('spawn')
        jobs = []
        try:
            for challenge in challenges:
                parent, child = ctx.Pipe()
                proc = ctx.Process(target=worker,args=(self.cfg,self.a,challenge,'normal',child))
                proc.start()
                child.close()
                jobs.append((proc,parent))
            for _, pipe in jobs:
                self.assertTrue(pipe.poll(30))
                self.assertEqual(pipe.recv(),'ready')
            for _, pipe in jobs:
                pipe.send('go')
            results = []
            for proc, pipe in jobs:
                self.assertTrue(pipe.poll(30))
                results.append(pipe.recv())
                proc.join(10)
                self.assertEqual(proc.exitcode,0)
            return results
        finally:
            for proc, pipe in jobs:
                pipe.close()
                if proc.is_alive():
                    proc.kill()
                    proc.join()

    def test_repeat_migration_and_integrity(self):
        self.assertFalse(uel_schema.migrate_local(self.path))
        self.assertEqual(self.records(),[])
        self.integrity()

    def test_flag_off_hides_api_but_preserves_installed_capture(self):
        with patch.dict(os.environ,{'XBLAGO_UEL_LOCAL_ENABLED':'false'}):
            with self.assertRaises(ApiError):
                self.l.balance(self.a)
            self.asset()
        self.assertEqual(len(self.records()),1)

    def test_installed_ledger_refuses_non_synthetic_mode(self):
        self.asset()
        with patch.dict(os.environ,{'XBLAGO_INTEGRATION_MODE':'production','XBLAGO_UEL_LOCAL_ENABLED':'false'}):
            with self.assertRaises(JournalUnavailable):
                with self.app.store._locked_transaction():
                    pass

    def test_requires_exact_complete_baseline(self):
        empty = Path(self.tmp.name)/'empty.sqlite3'
        sqlite3.connect(empty).close()
        with self.assertRaises((ValueError,JournalUnavailable)):
            uel_schema.migrate_local(empty)

    def test_migration_ddl_failure_rolls_back(self):
        with patch.object(uel_schema,'DDL',uel_schema.DDL+('INVALID SQL',)):
            with self.assertRaises(sqlite3.Error):
                uel_schema.migrate_local(self.pre_uel)
        with sqlite3.connect(self.pre_uel) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'uel_%'").fetchone()[0],0)

    def test_explicit_current_baseline_and_legacy_exclusion(self):
        with patch.dict(os.environ,{'XBLAGO_UEL_LOCAL_ENABLED':'false'}):
            baseline = WebApp(preflight(self.pre_uel))
            uid = baseline.session(self.a)[0]
            baseline.create_asset(uid,{'title':'Synthetic unmapped legacy asset'})
            r = AssetRegistry(baseline)
            aid = r.draft(self.a,body(),KEY)['id']
            q = r.prepare(self.a,aid,1,body())
            r.commit(self.a,q['challenge'],CONFIRM)
            with self.assertRaises(JournalUnavailable):
                uel_schema.migrate_local(self.pre_uel)
            self.assertTrue(uel_schema.migrate_local(self.pre_uel,allow_baseline=True))
            self.assertFalse(uel_schema.migrate_local(self.pre_uel,allow_baseline=True))
        ledger = LocalLedger(WebApp(preflight(self.pre_uel)))
        result = ledger.balance(self.a)
        self.assertEqual(result['registered_asset_claims'],1)
        self.assertEqual(result['baseline_current_objects'],1)
        self.assertEqual(result['historical_coverage'],'UNKNOWN')
        with sqlite3.connect(self.pre_uel) as db:
            self.assertEqual(db.execute('SELECT source_version,coverage FROM uel_events').fetchall(),[(2,'BASELINE_CURRENT')])

    def test_source_rows_and_every_indexed_revision_match(self):
        self.publish()
        self.complete()
        with self.app.store._locked_transaction() as db:
            c = PrivacyControl.open(db)
            live = uel_local.sources(db,c)
            for (kind,obj), source in live.items():
                rows = db.execute('SELECT * FROM uel_events WHERE source_kind=? AND object_ref=? ORDER BY source_version',(kind,obj)).fetchall()
                self.assertEqual([r['source_version'] for r in rows],[r['version'] for r in source['rows']])
                for record, original in zip(rows,source['rows']):
                    self.assertEqual((record['source_ref'],record['payload_ref']),uel_local.row_refs(kind,original,c.journal))
                self.assertEqual(rows[-1]['source_state'],source['state'])
        self.integrity()

    def test_causal_links_and_global_sequence(self):
        aid = self.publish()
        self.request()
        self.r.revoke(self.a,aid)
        records = self.records()
        self.assertEqual([x['sequence'] for x in records],list(range(1,len(records)+1)))
        self.assertEqual(records[-1]['previous'],records[-2]['record_ref'])
        self.assertEqual(records[-1]['causal_ref'],records[1]['record_ref'])

    def test_draft_and_commit_retries_keep_one_observation(self):
        aid = self.asset()
        self.assertEqual(aid,self.asset())
        q = self.preview(aid)
        receipt = self.r.commit(self.a,q,CONFIRM)
        self.assertEqual(receipt,self.r.commit(self.a,q,CONFIRM))
        self.assertEqual(len(self.records()),2)

    def test_prepare_and_read_do_not_append(self):
        aid = self.asset()
        before = self.records()
        self.preview(aid)
        self.assertEqual(self.l.balance(self.a),self.l.balance(self.a))
        self.assertEqual(before,self.records())

    def test_duplicate_commit_from_three_workers(self):
        q = self.preview(self.asset())
        replies = self.parallel([q]*3)
        self.assertTrue(all(r==replies[0] for r in replies))
        self.assertIn('ok',replies[0])
        self.assertEqual(len(self.records()),2)

    def test_competing_revisions_from_two_workers(self):
        aid = self.asset()
        results = self.parallel([self.preview(aid,quantity='3'),self.preview(aid,quantity='4')])
        self.assertEqual(sum('ok' in r for r in results),1)
        self.assertEqual(sum(r.get('denied')==409 for r in results),1)
        self.assertEqual(len(self.records()),2)

    def test_abort_rolls_back_asset_and_index(self):
        with patch.object(uel_local,'append',side_effect=OSError('synthetic write error')):
            with self.assertRaises(OSError):
                self.asset()
        self.assertEqual(self.r.feed(self.a)['items'],[])
        self.assertEqual(self.records(),[])

    def test_abort_rolls_back_help_and_index(self):
        with patch.object(uel_local,'append',side_effect=OSError('synthetic write error')):
            with self.assertRaises(OSError):
                self.request()
        self.assertEqual(self.h.feed(self.a)['items'],[])
        self.assertEqual(self.records(),[])

    def test_crash_before_journal_rolls_back_both(self):
        aid = self.asset()
        q = self.preview(aid)
        self.spawn(q,'before_journal',71)
        self.assertEqual(len(self.records()),1)
        self.assertEqual(self.r.feed(self.a)['items'][0]['version'],1)
        self.r.commit(self.a,q,CONFIRM)
        self.assertEqual(len(self.records()),2)

    def test_crash_after_journal_recovers_header_without_grant(self):
        aid = self.asset()
        q = self.preview(aid,audience='LAB_COMMUNITY',available='1',share_consent=True)
        self.spawn(q,'after_journal',72)
        self.assertEqual(self.l.balance(self.b)['registered_asset_claims'],0)
        records = self.records()
        self.assertEqual([r['phase'] for r in records],['OBSERVED','OBSERVED','RETIRED'])
        self.assertIsNone(records[-1]['payload_ref'])
        with self.assertRaises(ApiError):
            self.r.commit(self.a,q,CONFIRM)

    def test_crash_after_commit_retry_keeps_original_receipt(self):
        q = self.preview(self.asset())
        self.spawn(q,'after_commit',73)
        self.assertEqual(self.r.commit(self.a,q,CONFIRM)['version'],2)
        self.assertEqual(len(self.records()),2)

    def test_restart_determinism(self):
        self.publish()
        self.complete()
        restarted = LocalLedger(WebApp(preflight(self.path)))
        self.assertEqual(self.l.balance(self.a),restarted.balance(self.a))

    def test_asset_resource_and_help_occurrence_no_double_count(self):
        self.publish()
        rid = self.complete()
        result = self.l.balance(self.b)
        self.assertEqual(result['registered_asset_claims'],1)
        self.assertEqual(result['categories']['computer:item']['stock_total'],'2')
        self.assertEqual(result['categories']['computer:item']['available_total'],'1')
        self.assertEqual(result['needs_visible'],1)
        self.assertEqual(result['fulfilled_occurrences'],1)
        self.assertIsNone(result['flow_btc'])
        self.act(self.a,rid,'dispute')
        self.assertEqual(self.l.balance(self.b)['fulfilled_occurrences'],0)
        self.assertEqual(self.l.balance(self.b)['disputed'],1)

    def test_unknown_amount_remains_unknown(self):
        aid = self.asset(quantity=None,available=None)
        q = self.preview(aid,quantity=None,available=None,audience='LAB_COMMUNITY',share_consent=True)
        self.r.commit(self.a,q,CONFIRM)
        r = self.l.balance(self.b)
        self.assertEqual(r['coverage'],'PARTIAL')
        self.assertIsNone(r['categories']['computer:item']['stock_total'])
        self.assertIsNone(r['categories']['computer:item']['available_total'])
        self.assertIsNone(r['stock_btc'])

    def test_private_audience_and_fresh_session(self):
        self.asset()
        self.request()
        self.assertEqual(self.l.observations(self.c)['items'],[])
        with self.assertRaises(ApiError):
            self.l.observations(self.app.session(self.a)[0])
        new = self.app.login('uel_synthetic_owner',PASSWORD,old_token=self.a)[0]
        with self.assertRaises(ApiError):
            self.l.balance(self.a)
        self.assertEqual(len(self.l.observations(new)['items']),2)

    def test_no_plaintext_or_control_references_in_user_view(self):
        uid = self.app.session(self.a)[0]
        self.app.store.set_memory(uid,'city','SYNTHETIC_PRIVATE_MEMORY')
        self.publish()
        self.complete()
        records = self.records()
        c = SecurityJournal(self.journal)
        journal_payloads = [json.loads(row[2]) for row in c.read()[0] if json.loads(row[2])['kind']=='uel_local']
        public = json.dumps(self.l.observations(self.a),ensure_ascii=False)
        encoded = json.dumps([records,journal_payloads,public],ensure_ascii=False)
        for forbidden in (uid,self.a,PASSWORD,'SYNTHETIC_PRIVATE_MEMORY','SYNTHETIC_PRIVATE_ASSET_TITLE','Синтетическая помощь'):
            self.assertNotIn(forbidden,encoded)
        for field in ('source_ref','record_ref','payload_ref','object_ref','sequence'):
            self.assertNotIn('"'+field+'"',public)
        self.assertEqual(self.l.observations(self.b)['items'][0]['claim']['evidence'],'SELF_DECLARED')

    def test_append_only_including_replace(self):
        self.asset()
        with sqlite3.connect(self.path) as db:
            for sql in ('DELETE FROM uel_events','UPDATE uel_events SET source_version=2',
                        'INSERT OR REPLACE INTO uel_events SELECT * FROM uel_events'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
        self.assertEqual(len(self.records()),1)

    def test_missing_retained_index_frontier_blocks_projection(self):
        self.asset()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE uel_state SET head=?",('0'*64,))
        result = self.l.balance(self.a)
        self.assertEqual(result['integrity'],'UNKNOWN_RECOVERY')
        self.assertIsNone(result['balance'])

    def test_sql_drift_blocks_all_guarded_writes(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TRIGGER uel_append_only_delete')
        self.assertEqual(self.l.balance(self.a)['integrity'],'UNKNOWN_RECOVERY')
        with self.assertRaises(JournalUnavailable):
            self.asset()

    def test_missing_help_source_without_hold_is_not_retirement(self):
        rid = self.request()
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM help_requests WHERE id=?',(rid,))
        self.assertEqual(self.l.balance(self.a)['integrity'],'UNKNOWN_RECOVERY')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM uel_events WHERE phase='RETIRED'").fetchone()[0],0)

    def test_forged_help_hold_cannot_hide_unscrubbed_payload(self):
        rid = self.request()
        journal = SecurityJournal(self.journal)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO help_holds VALUES (?,?)',(journal.tag('help-request',rid),'synthetic invalid hold'))
        self.assertEqual(self.l.balance(self.a)['integrity'],'UNKNOWN_RECOVERY')

    def test_witness_loss_returns_unknown_without_numbers(self):
        path = Path(self.cfg['XBLAGO_JOURNAL_WITNESS_DIR'])
        held = path.with_name('held')
        path.rename(held)
        try:
            result = self.l.balance(self.a)
            self.assertEqual(result['integrity'],'UNKNOWN_RECOVERY')
            self.assertIsNone(result['balance'])
        finally:
            held.rename(path)

    def test_old_backup_after_asset_erase_keeps_no_payload(self):
        aid = self.publish()
        backup = self.app.store.backup_database(Path(self.tmp.name)/'erase-copy')
        self.r.erase(self.a,aid)
        restored = WebApp(restore(backup,self.journal))
        with self.assertRaises(ApiError):
            LocalLedger(restored).observations(self.a)
        token = restored.login('uel_synthetic_owner',PASSWORD)[0]
        self.assertEqual(LocalLedger(restored).observations(token)['items'],[])
        with restored.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM assetreg_versions').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM uel_events WHERE phase='RETIRED' AND payload_ref IS NULL").fetchone()[0],1)

    def test_pre_migration_backup_requires_install_and_replays_only_headers(self):
        aid = self.publish()
        self.r.erase(self.a,aid)
        with self.assertRaises(JournalUnavailable):
            restore(self.pre_uel,self.journal)
        self.assertTrue(uel_schema.migrate_local(self.pre_uel))
        restored = WebApp(restore(self.pre_uel,self.journal))
        token = restored.login('uel_synthetic_owner',PASSWORD)[0]
        self.assertEqual(LocalLedger(restored).balance(token)['registered_asset_claims'],0)
        with restored.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM uel_events').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM blago_assets').fetchone()[0],0)

    def test_account_deletion_and_restore_remove_access(self):
        self.publish()
        self.complete()
        backup = self.app.store.backup_database(Path(self.tmp.name)/'account-copy')
        p = PrivacyService(self.app)
        q = p.preview(self.a)
        p.delete(self.a,q['challenge'],'УДАЛИТЬ')
        self.assertEqual(self.l.observations(self.b)['items'],[])
        self.assertEqual(sum(x['phase']=='RETIRED' for x in self.records()),2)
        restored = WebApp(restore(backup,self.journal))
        token = restored.login('uel_synthetic_helper',PASSWORD)[0]
        self.assertEqual(LocalLedger(restored).observations(token)['items'],[])
        with self.assertRaises(ApiError):
            restored.login('uel_synthetic_owner',PASSWORD)

    def test_retention_cascades_without_copying_old_payload(self):
        old = int(time.time())-91*86400
        with patch('xblago.domain.asset_registry.time.time',return_value=old):
            token = self.app.register('uel_synthetic_old',PASSWORD,INVITE)[0]
            self.asset(token)
        self.assertEqual(sweep(self.app.store)['blago_assets'],1)
        self.assertEqual(self.records()[-1]['phase'],'RETIRED')

    def test_g09_owner_generation_fenced_by_existing_source_change(self):
        aid = self.publish()
        g = Generation(self.app.store,self.app.session(self.a)[0])
        self.r.revoke(self.a,aid)
        with self.assertRaises(ContextConflict):
            g.commit()
        self.assertEqual(self.l.balance(self.b)['registered_asset_claims'],0)

    def test_help_completion_receipt_replay_has_one_effect(self):
        rid = self.request()
        self.act(self.a,rid,'publish')
        self.act(self.b,rid,'offer',{'note':'Синтетическая помощь'})
        self.act(self.a,rid,'accept',{'offer_id':self.h.get(self.a,rid)['offers'][0]['id']})
        self.act(self.a,rid,'confirm')
        preview = self.h.prepare(self.b,rid,5,'confirm',{})
        receipt = self.h.commit(self.b,preview['challenge'],CONFIRM)
        self.assertEqual(self.h.commit(self.b,preview['challenge'],CONFIRM),receipt)
        self.assertEqual(self.l.balance(self.a)['fulfilled_occurrences'],1)
        self.assertEqual(len(self.records()),6)

    def test_positive_write_outside_shadow_is_not_backfilled(self):
        self.asset()
        # Simulate an offline incompatible writer leaving a source commit with
        # no matching index. The next reader must preserve the mismatch.
        with patch.object(uel_local,'synchronize',return_value=False):
            self.r.draft(self.a,body(),'synthetic-uel-unindexed-01')
        self.assertEqual(self.l.balance(self.a)['integrity'],'UNKNOWN_RECOVERY')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM uel_events').fetchone()[0],1)
        with self.assertRaises(JournalUnavailable):
            uel_schema.migrate_local(self.path,allow_baseline=True)
