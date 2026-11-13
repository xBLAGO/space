"""Synthetic rollback, signed custody, crash and repair checks."""
import errno
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import tests.test_uel_local as fixture
from xblago.core import uel_checkpoint as cp
from xblago.core.privacy_control import PrivacyControl
from xblago.core.security_journal import JournalUnavailable, SecurityJournal
from xblago.core.uel_recovery import repair_index
from xblago.core.privacy_maintenance import restore
from xblago.domain.local_ledger import LocalLedger
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import ApiError, WebApp


def server_worker(cfg, directory, endpoint):
    os.environ.update(cfg)
    cp.serve(directory, endpoint)


def repair_worker(cfg, path, pipe, frozen_at):
    os.environ.update(cfg)
    with patch('time.time', return_value=frozen_at):
        pipe.send('ready')
        pipe.recv()
        try:
            pipe.send(repair_index(path))
        finally:
            pipe.close()


def custody_worker(cfg, directory, request, pipe):
    os.environ.update(cfg)
    pipe.send('ready')
    pipe.recv()
    try:
        response = cp.accept(directory, request)
        pipe.send({'accepted': response['body']['checkpoint']})
    except JournalUnavailable:
        pipe.send({'rejected': True})
    finally:
        pipe.close()


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.lab = fixture.LocalLedgerTests()
        self.addCleanup(self.lab.doCleanups)
        self.lab.setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix='uc-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stream = secrets.token_hex(32)
        self.source_key = self.root / 'source.key'
        source_public = cp.create_key(self.source_key)
        self.directory = self.root / 'custody'
        self.public = cp.initialize(self.directory, self.stream, source_public)
        self.endpoint = self.root / 'socket'
        self.config = {'stream': self.stream, 'custodian_key': self.public,
                       'source_key_file': str(self.source_key), 'endpoint': str(self.endpoint)}
        config_path = self.root / 'client.json'
        config_path.write_bytes(cp.encode(self.config))
        config_path.chmod(0o600)
        self.lab.cfg['XBLAGO_UEL_CHECKPOINT_CONFIG'] = str(config_path)
        os.environ.update(self.lab.cfg)
        self.start_server()
        self.addCleanup(self.stop_server)
        cp.enroll(SecurityJournal(self.lab.journal))

    def start_server(self):
        self.endpoint.unlink(missing_ok=True)
        self.server = multiprocessing.get_context('spawn').Process(
            target=server_worker, args=(self.lab.cfg, self.directory, self.endpoint))
        self.server.start()
        deadline = time.monotonic() + 15
        while not self.endpoint.exists():
            if not self.server.is_alive() or time.monotonic() > deadline:
                self.fail('Synthetic custodian did not start')
            time.sleep(0.02)

    def stop_server(self):
        if self.server.is_alive():
            self.server.terminate()
        self.server.join(10)
        self.endpoint.unlink(missing_ok=True)

    def receipts(self):
        with sqlite3.connect(self.directory / 'custody.sqlite3') as db:
            return [json.loads(row[0]) for row in db.execute('SELECT receipt FROM checkpoints ORDER BY generation')]

    def request(self):
        rows = SecurityJournal(self.lab.journal).read()[0]
        journal, uel = cp.proof(rows)
        return cp.signed({'profile': cp.PROFILE, 'stream': self.stream, 'nonce': secrets.token_hex(32),
                          'journal': journal, 'uel': uel}, cp.load_key(self.source_key), 'request')

    def snapshot(self):
        snapshot = self.root / 'old'
        snapshot.mkdir()
        with sqlite3.connect(self.lab.path) as source, sqlite3.connect(snapshot / 'app.sqlite3') as target:
            source.backup(target)
        shutil.copytree(self.lab.journal, snapshot / 'journal')
        shutil.copytree(Path(self.lab.cfg['XBLAGO_JOURNAL_WITNESS_DIR']), snapshot / 'witness')
        return snapshot

    def rollback(self, snapshot):
        for suffix in ('-wal', '-shm'):
            Path(str(self.lab.path) + suffix).unlink(missing_ok=True)
        shutil.copy2(snapshot / 'app.sqlite3', self.lab.path)
        for source, destination in ((snapshot / 'journal', self.lab.journal),
                                    (snapshot / 'witness', Path(self.lab.cfg['XBLAGO_JOURNAL_WITNESS_DIR']))):
            shutil.rmtree(destination)
            shutil.copytree(source, destination)

    def test_wall_clock_suspension_reproduces_both_original_errors(self):
        for operation in (self.lab.asset, self.lab.request):
            with patch('time.time', return_value=self.lab.frozen_at + 1801):
                with self.assertRaises(ApiError) as raised:
                    operation()
                self.assertEqual(raised.exception.status, 401)
        self.assertEqual(self.lab.records(), [])
        self.lab.asset()
        self.lab.request()
        self.assertEqual(len(self.lab.records()), 2)

    def test_session_freshness_still_expires_at_original_boundary(self):
        with patch('time.time', return_value=self.lab.frozen_at + 301):
            with self.assertRaises(ApiError) as raised:
                self.lab.asset()
            self.assertEqual(raised.exception.status, 403)

    def test_signed_prefix_and_source_headers(self):
        self.lab.publish()
        self.lab.complete()
        chain = self.receipts()
        current = cp.verify_chain(chain, self.public, self.stream)
        self.assertEqual(current['uel_seq'], len(self.lab.records()))
        self.assertGreater(current['journal_seq'], current['uel_seq'])
        self.assertNotIn('SYNTHETIC_PRIVATE_ASSET_TITLE', cp.canonical(chain))
        self.assertNotIn(self.lab.a, cp.canonical(chain))
        self.assertNotIn(str(self.lab.journal), cp.canonical(chain))

    def test_fresh_nonce_and_request_binding(self):
        first, second = self.request(), self.request()
        reply = cp.accept(self.directory, first)
        cp.verify_reply(reply, first, self.config)
        with self.assertRaises(JournalUnavailable):
            cp.verify_reply(reply, second, self.config)

    def test_wrong_key_domain_stream_and_changed_signature(self):
        receipt = self.receipts()[-1]
        other = cp.public(cp.Ed25519PrivateKey.generate())
        with self.assertRaises(JournalUnavailable):
            cp.verify_checkpoint(receipt, other, self.stream)
        with self.assertRaises(JournalUnavailable):
            cp.verify_checkpoint(receipt, self.public, secrets.token_hex(32))
        with self.assertRaises(JournalUnavailable):
            cp.verify_signed(receipt, self.public, 'reply')
        forged = json.loads(json.dumps(receipt))
        forged['body']['uel_seq'] += 1
        with self.assertRaises(JournalUnavailable):
            cp.verify_checkpoint(forged, self.public, self.stream)

    def test_unknown_source_key_cannot_advance(self):
        request = self.request()
        request = cp.signed(request['body'], cp.Ed25519PrivateKey.generate(), 'request')
        before = self.receipts()
        with self.assertRaises(JournalUnavailable):
            cp.accept(self.directory, request)
        self.assertEqual(self.receipts(), before)

    def test_bounded_and_strict_serialization(self):
        with self.assertRaises(JournalUnavailable):
            cp.root([cp.ZERO] * (cp.LIMIT + 1))
        for encoded in (b'{"a":1,"a":2}', b'{ "a": 1 }'):
            with self.assertRaises(JournalUnavailable):
                cp.decode(encoded)
        request = self.request()
        request['body']['journal'][0] = 'not-a-hash'
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        with self.assertRaises(JournalUnavailable):
            cp.accept(self.directory, request)

    def test_full_coordinated_local_rollback_is_detected(self):
        self.lab.publish()
        snapshot = self.snapshot()
        aid = self.lab.asset()
        self.lab.r.erase(self.lab.a, aid)
        retained = self.receipts()[-1]
        self.rollback(snapshot)
        # Original 05.4 local copies agree, reproducing the old rollback gap.
        with patch.object(cp, 'guard'):
            SecurityJournal(self.lab.journal).read()
        self.assertEqual(self.lab.l.balance(self.lab.a)['integrity'], 'UNKNOWN_RECOVERY')
        self.assertEqual(self.receipts()[-1], retained)
        with self.assertRaises(JournalUnavailable):
            repair_index(self.lab.path)

    def test_application_backup_only_replays_denials(self):
        aid = self.lab.publish()
        backup = self.lab.app.store.backup_database(self.root / 'app-copy')
        self.lab.r.erase(self.lab.a, aid)
        restored = WebApp(restore(backup, self.lab.journal))
        token = restored.login('uel_synthetic_owner', fixture.PASSWORD)[0]
        self.assertEqual(LocalLedger(restored).balance(token)['registered_asset_claims'], 0)

    def test_missing_custodian_cannot_ack_or_project(self):
        self.stop_server()
        with self.assertRaises(JournalUnavailable):
            self.lab.asset()
        self.assertEqual(self.lab.l.balance(self.lab.a)['integrity'], 'UNKNOWN_RECOVERY')
        self.start_server()
        self.lab.asset()

    def test_lost_config_does_not_disable_installed_policy(self):
        with patch.dict(os.environ, {'XBLAGO_UEL_CHECKPOINT_CONFIG': ''}):
            with self.assertRaises(JournalUnavailable):
                self.lab.asset()

    def test_missing_custody_store_is_never_recreated(self):
        self.stop_server()
        path = self.directory / 'custody.sqlite3'
        path.rename(self.directory / 'retained.sqlite3')
        with self.assertRaises(FileNotFoundError):
            cp.accept(self.directory, self.request_without_transport())
        self.assertFalse(path.exists())

    def request_without_transport(self):
        with patch.object(cp, 'guard'):
            rows = SecurityJournal(self.lab.journal).read()[0]
        journal, uel = cp.proof(rows)
        return cp.signed({'profile': cp.PROFILE, 'stream': self.stream, 'nonce': secrets.token_hex(32),
                          'journal': journal, 'uel': uel}, cp.load_key(self.source_key), 'request')

    def test_missing_signing_key_is_not_regenerated(self):
        request = self.request()
        (self.directory / 'signing.key').rename(self.directory / 'retained.key')
        with self.assertRaises(OSError):
            cp.accept(self.directory, request)
        self.assertFalse((self.directory / 'signing.key').exists())

    def test_checkpoint_chain_corruption_blocks(self):
        with sqlite3.connect(self.directory / 'custody.sqlite3') as db:
            db.execute("UPDATE checkpoints SET receipt='{}' WHERE generation=0")
        self.assertEqual(self.lab.l.balance(self.lab.a)['integrity'], 'UNKNOWN_RECOVERY')

    def test_contradictory_prefix_does_not_overwrite_checkpoint(self):
        request = self.request()
        request['body']['journal'][0] = '1' * 64
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        before = self.receipts()
        with self.assertRaises(JournalUnavailable):
            cp.accept(self.directory, request)
        self.assertEqual(before, self.receipts())

    def test_sql_write_failure_emits_no_receipt(self):
        request = self.request()
        request['body']['journal'].append('1' * 64)
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        before = self.receipts()

        class BrokenCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError('injected disk full at commit')

        connect = sqlite3.connect
        with patch.object(cp.sqlite3, 'connect', side_effect=lambda *a, **kw: connect(*a, **kw, factory=BrokenCommit)):
            with self.assertRaises(sqlite3.OperationalError):
                cp.accept(self.directory, request)
        self.assertEqual(before, self.receipts())

    def test_full_disk_open_emits_no_receipt(self):
        request = self.request()
        with patch.object(cp.sqlite3, 'connect', side_effect=OSError(errno.ENOSPC, 'injected')):
            with self.assertRaises(OSError):
                cp.accept(self.directory, request)

    def test_lost_ack_keeps_same_committed_checkpoint(self):
        request = self.request()
        request['body']['journal'].append('1' * 64)
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        original = cp.signed

        def fail_reply(body, key, domain):
            if domain == 'reply':
                raise OSError('injected lost ACK after commit')
            return original(body, key, domain)

        with patch.object(cp, 'signed', side_effect=fail_reply):
            with self.assertRaises(OSError):
                cp.accept(self.directory, request)
        committed = self.receipts()[-1]
        reply = cp.accept(self.directory, request)
        self.assertEqual(cp.verify_reply(reply, request, self.config), committed)

    def test_repair_state_and_headers_then_authorized_account_deletion(self):
        self.lab.publish()
        self.lab.complete()
        before = self.lab.records()
        backup = self.lab.app.store.backup_database(self.root / 'before-delete')
        with sqlite3.connect(self.lab.path) as db:
            db.execute('DROP TRIGGER uel_append_only_update')
            db.execute("UPDATE uel_events SET source_ref=? WHERE sequence=1", ('1' * 64,))
            db.execute("UPDATE uel_state SET head=?", ('2' * 64,))
        self.assertEqual(self.lab.l.balance(self.lab.a)['integrity'], 'UNKNOWN_RECOVERY')
        repair_index(self.lab.path)
        self.assertEqual(self.lab.records(), before)
        with self.assertRaises(ApiError):
            self.lab.l.observations('invalid-session')
        service = PrivacyService(self.lab.app)
        preview = service.preview(self.lab.a)
        service.delete(self.lab.a, preview['challenge'], 'УДАЛИТЬ')
        self.assertEqual(self.lab.l.observations(self.lab.b)['items'], [])
        restored = WebApp(restore(backup, self.lab.journal))
        with self.assertRaises(ApiError):
            restored.login('uel_synthetic_owner', fixture.PASSWORD)

    def test_repair_rollback_on_source_corruption(self):
        aid = self.lab.publish()
        with sqlite3.connect(self.lab.path) as db:
            db.execute("UPDATE uel_state SET head=?", ('1' * 64,))
            db.create_function('blago_replaying', 0, lambda: 1)
            db.create_function('blago_tag', -1, SecurityJournal(self.lab.journal).tag)
            db.execute('UPDATE assetreg_records SET version=version+1 WHERE asset_id=?', (aid,))
        with self.assertRaises(JournalUnavailable):
            repair_index(self.lab.path)
        with sqlite3.connect(self.lab.path) as db:
            self.assertEqual(db.execute('SELECT head FROM uel_state').fetchone()[0], '1' * 64)

    def test_repair_requires_live_evidence(self):
        self.lab.asset()
        self.stop_server()
        with self.assertRaises(JournalUnavailable):
            repair_index(self.lab.path)
        self.start_server()
        repair_index(self.lab.path)
        self.lab.integrity()

    def test_concurrent_repair_and_commit(self):
        aid = self.lab.asset()
        challenge = self.lab.preview(aid)
        ctx = multiprocessing.get_context('spawn')
        p1, c1 = ctx.Pipe()
        p2, c2 = ctx.Pipe()
        jobs = [
            ctx.Process(target=repair_worker, args=(self.lab.cfg, self.lab.path, c1, self.lab.frozen_at)),
            ctx.Process(target=fixture.worker, args=(self.lab.cfg, self.lab.a, challenge, 'normal', c2, self.lab.frozen_at)),
        ]
        try:
            for job in jobs:
                job.start()
            for pipe in (p1, p2):
                self.assertTrue(pipe.poll(30))
                self.assertEqual(pipe.recv(), 'ready')
            for pipe in (p1, p2):
                pipe.send('go')
            for pipe in (p1, p2):
                self.assertTrue(pipe.poll(30))
                self.assertIsInstance(pipe.recv(), dict)
            for job in jobs:
                job.join(10)
                self.assertEqual(job.exitcode, 0)
            self.assertEqual(len(self.lab.records()), 2)
            self.lab.integrity()
        finally:
            for pipe in (p1, p2, c1, c2):
                pipe.close()
            for job in jobs:
                if job.is_alive():
                    job.kill()
                    job.join()

    def test_concurrent_duplicate_commit_and_restart(self):
        aid = self.lab.asset()
        result = self.lab.parallel([self.lab.preview(aid)] * 3)
        self.assertTrue(all('ok' in row for row in result))
        current = self.receipts()[-1]
        self.stop_server()
        self.start_server()
        self.lab.integrity()
        self.assertEqual(self.receipts()[-1], current)

    def test_crash_source_after_journal_remains_non_grant(self):
        aid = self.lab.asset()
        self.lab.spawn(self.lab.preview(aid), 'after_journal', 72)
        self.lab.integrity()
        self.assertEqual(self.lab.l.balance(self.lab.b)['registered_asset_claims'], 0)

    def test_repair_crash_after_retained_audit_and_replay(self):
        self.lab.publish()
        with sqlite3.connect(self.lab.path) as db:
            db.execute('UPDATE uel_state SET head=?', ('1' * 64,))
        original = PrivacyControl.flush

        def crash(control):
            original(control)
            raise OSError('injected crash after repair journal commit')

        with patch.object(PrivacyControl, 'flush', new=crash):
            with self.assertRaises(OSError):
                repair_index(self.lab.path)
        with sqlite3.connect(self.lab.path) as db:
            self.assertEqual(db.execute('SELECT head FROM uel_state').fetchone()[0], '1' * 64)
        repair_index(self.lab.path)
        self.lab.integrity()
        audits = [json.loads(row[2]) for row in SecurityJournal(self.lab.journal).read()[0]
                  if json.loads(row[2])['kind'] == 'uel_index_repair']
        self.assertEqual(len(audits), 2)
        prior = self.receipts()[-1]
        repair_index(self.lab.path)
        self.assertEqual(self.receipts()[-1], prior)

    def test_lost_local_witness_cannot_be_repaired_from_checkpoint_alone(self):
        witness = Path(self.lab.cfg['XBLAGO_JOURNAL_WITNESS_DIR'])
        witness.rename(witness.with_name('missing-witness'))
        with self.assertRaises(JournalUnavailable):
            repair_index(self.lab.path)

    def test_missing_retainer_head_is_not_recreated(self):
        request = self.request()
        (self.directory / 'head.json').rename(self.directory / 'held-head.json')
        with self.assertRaises(FileNotFoundError):
            cp.accept(self.directory, request)
        self.assertFalse((self.directory / 'head.json').exists())

    def test_truncated_custody_database_rejected_by_retained_head(self):
        request = self.request()
        with sqlite3.connect(self.directory / 'custody.sqlite3') as db:
            db.execute('DELETE FROM checkpoints WHERE generation>0')
        with self.assertRaises(JournalUnavailable):
            cp.accept(self.directory, request)

    def test_crash_after_custody_commit_before_head(self):
        request = self.request()
        request['body']['journal'].append('1' * 64)
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        with patch.object(cp, '_publish_head', side_effect=OSError('injected fsync failure')):
            with self.assertRaises(OSError):
                cp.accept(self.directory, request)
        committed = self.receipts()[-1]
        response = cp.accept(self.directory, request)
        self.assertEqual(cp.verify_reply(response, request, self.config), committed)

    def custody_parallel(self, requests):
        ctx = multiprocessing.get_context('spawn')
        jobs = []
        try:
            for request in requests:
                parent, child = ctx.Pipe()
                process = ctx.Process(target=custody_worker, args=(self.lab.cfg, self.directory, request, child))
                process.start()
                child.close()
                jobs.append((process, parent))
            for _, pipe in jobs:
                self.assertTrue(pipe.poll(20))
                self.assertEqual(pipe.recv(), 'ready')
            for _, pipe in jobs:
                pipe.send('go')
            results = []
            for process, pipe in jobs:
                self.assertTrue(pipe.poll(20))
                results.append(pipe.recv())
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            return results
        finally:
            for process, pipe in jobs:
                pipe.close()
                if process.is_alive():
                    process.kill()
                    process.join()

    def test_custody_two_processes_same_request(self):
        request = self.request()
        request['body']['journal'].append('1' * 64)
        request = cp.signed(request['body'], cp.load_key(self.source_key), 'request')
        results = self.custody_parallel([request, request])
        self.assertEqual(results[0]['accepted'], results[1]['accepted'])
        self.assertEqual(self.receipts()[-1], results[0]['accepted'])

    def test_custody_concurrent_conflicting_extensions(self):
        first = self.request()
        second = json.loads(json.dumps(first))
        first['body']['journal'].append('1' * 64)
        second['body']['journal'].append('2' * 64)
        requests = [cp.signed(item['body'], cp.load_key(self.source_key), 'request') for item in (first, second)]
        results = self.custody_parallel(requests)
        self.assertEqual(sum('accepted' in item for item in results), 1)
        self.assertEqual(sum('rejected' in item for item in results), 1)

    def test_sql_generation_corruption_is_rejected_before_advance(self):
        request = self.request()
        with sqlite3.connect(self.directory / 'custody.sqlite3') as db:
            db.execute('UPDATE checkpoints SET generation=60000 WHERE generation=(SELECT max(generation) FROM checkpoints)')
        with self.assertRaises(JournalUnavailable):
            cp.accept(self.directory, request)

    def test_custodian_requires_verified_sqlite_before_storage_access(self):
        request = self.request()
        before = self.receipts()
        with patch.object(cp, 'require_safe_sqlite', side_effect=cp.UnsafeSQLite('synthetic rejected SQLite')):
            with self.assertRaises(cp.UnsafeSQLite):
                cp.accept(self.directory, request)
            with self.assertRaises(cp.UnsafeSQLite):
                cp.initialize(self.root / 'unsafe-copy', self.stream, cp.public(cp.load_key(self.source_key)))
        self.assertEqual(self.receipts(), before)
        self.assertFalse((self.root / 'unsafe-copy').exists())
