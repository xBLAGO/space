"""Synthetic owner-private matching and durable local pull; no external operator claim."""
import json
import multiprocessing
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import tests.test_living_deeds as fixture
from xblago.core import agent_context_schema
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.living_deeds_agent import AgentLivingDeeds, LocalPullAdapter, FLAG, DELIVERY_FLAG
from xblago.integrations.web import WebApp, ApiError


def pull_worker(cfg, token, delivery, clock, pipe, phase=None):
    os.environ.update(cfg)
    with patch('time.time', return_value=clock):
        service = AgentLivingDeeds(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        if pipe:
            pipe.send('ready'); pipe.recv()
        if phase == 'after_journal':
            from xblago.core.privacy_control import PrivacyControl
            original = PrivacyControl.flush
            def flush(control):
                changed = control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='living_state'").fetchone()
                original(control)
                if changed: os._exit(81)
            with patch.object(PrivacyControl, 'flush', flush): service.receive(token, delivery)
        else:
            try:
                result = service.receive(token, delivery)
                if phase == 'after_commit': os._exit(82)
                pipe.send({'ok': result})
            except ApiError as exc:
                pipe.send({'denied': exc.status})


class AgentLivingTests(unittest.TestCase):
    def setUp(self):
        off = patch.dict(os.environ, {agent_context_schema.FLAG: 'false', FLAG: 'false', DELIVERY_FLAG: 'false'})
        off.start(); self.addCleanup(off.stop)
        self.f = fixture.LivingTests(); self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.assertTrue(agent_context_schema.migrate(self.f.path))
        self.f.cfg.update({agent_context_schema.FLAG: 'true', FLAG: 'true', DELIVERY_FLAG: 'true'})
        os.environ.update(self.f.cfg)
        self.f.app = WebApp(preflight(self.f.path))
        self.s = AgentLivingDeeds(self.f.app)
        self.f.s = self.s
        self.a, self.b, self.lena = self.f.a, self.f.b, self.f.lena

    def action(self, token, action, data):
        return self.f.run_action(token, action, data)

    def grant(self, permission, bridge=None, scope='MATCH_PRIVATE'):
        return self.action(self.a, 'grant_agent', {'agent': 'personal-agent', 'scope': scope,
            'permission_id': permission, 'bridge_id': bridge, 'expires_at': self.f.now + 200})

    def invitation(self):
        need, note, permission, bridge = self.f.bridge()
        self.action(self.a, 'request_consent', {'id': bridge, 'recipient': self.f.code(self.lena)})
        mandate = self.grant(permission, bridge, 'INVITE_ONCE')
        delivery = self.action(self.a, 'queue_invitation', {'mandate_id': mandate, 'bridge_id': bridge})
        return need, note, permission, bridge, mandate, delivery

    def test_flags_default_off_and_external_adapter_absent(self):
        with patch.dict(os.environ, {FLAG: '', DELIVERY_FLAG: ''}):
            with self.assertRaises(JournalUnavailable): self.s.view(self.a)
            with self.assertRaises(JournalUnavailable): self.s.receive(self.lena, 'x' * 36)

    def test_owner_memory_match_is_private_read_only_and_model_independent(self):
        _, _, p, _ = self.f.bridge()
        uid = self.f.app.session(self.a)[0]
        self.f.app.store.set_memory(uid, 'city', 'PRIVATE_PERM')
        m = self.grant(p)
        before = json.dumps(self.s.view(self.b), sort_keys=True)
        with self.f.app.store._locked_transaction() as db:
            count = db.execute('SELECT count(*) FROM living_versions').fetchone()[0]
        first = self.s.candidates(self.a, m, 'PRIVATE_PERM', model_label='model-a')
        second = self.s.candidates(self.a, m, 'PRIVATE_PERM', model_label='model-b')
        self.assertEqual(first, second); self.assertEqual(len(first['items']), 1)
        self.assertFalse(first['sent']); self.assertFalse(first['published'])
        self.assertEqual(before, json.dumps(self.s.view(self.b), sort_keys=True))
        self.assertNotIn('PRIVATE_PERM', before)
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM living_versions').fetchone()[0], count)
        with self.assertRaises(ApiError): self.s.candidates(self.b, m, 'PRIVATE_PERM')
        with self.assertRaises(ApiError): self.s.candidates(self.a, m, 'PRIVATE_PERM', agent_id='another-agent')

    def test_memory_correction_deletion_and_restart_no_stale_candidate(self):
        _, _, p, _ = self.f.bridge(); m = self.grant(p)
        uid = self.f.app.session(self.a)[0]
        self.f.app.store.set_memory(uid, 'city', 'PRIVATE_PERM')
        first = self.s.candidates(self.a, m, 'PRIVATE_PERM')['items'][0]
        self.f.app.store.set_memory(uid, 'city', 'PRIVATE_TOMSK')
        self.assertEqual(self.s.candidates(self.a, m, 'PRIVATE_PERM')['items'], [])
        self.f.app.delete_memory(uid, 'city')
        restarted = AgentLivingDeeds(WebApp(preflight(self.f.path)))
        self.assertEqual(restarted.candidates(self.a, m, 'PRIVATE_TOMSK')['items'], [])
        self.assertTrue(first['source_tag']); self.assertNotIn('note_from_memory', __import__('xblago.domain.living_deeds_agent', fromlist=['FIELDS']).FIELDS)

    def test_explicit_preview_minimal_payload_and_no_requester_leak(self):
        need, note, p, bridge = self.f.bridge()
        before = json.dumps(self.s.view(self.b), sort_keys=True)
        self.action(self.a, 'request_consent', {'id': bridge, 'recipient': self.f.code(self.lena)})
        preview = self.s.prepare(self.a, 'grant_agent', {'agent': 'personal-agent', 'scope': 'INVITE_ONCE',
            'permission_id': p, 'bridge_id': bridge, 'expires_at': self.f.now + 200}, self.f.key())
        review = preview['preview']['review']
        self.assertEqual(review['recipient'], self.f.code(self.lena))
        self.assertIn('AUTHOR_APPROVED', review['task']); self.assertNotIn('PRIVATE_NOTE', json.dumps(review))
        m = self.s.commit(self.a, preview['challenge'], fixture.CONFIRM)['id']
        d = self.action(self.a, 'queue_invitation', {'mandate_id': m, 'bridge_id': bridge})
        result = LocalPullAdapter(self.s).deliver(self.lena, d)
        self.assertEqual(set(result), {'format', 'receipt', 'task', 'referrer', 'contact_only', 'resource_available'})
        self.assertNotIn('PRIVATE_NOTE', json.dumps(result)); self.assertFalse(result['resource_available'])
        self.assertEqual(before, json.dumps(self.s.view(self.b), sort_keys=True))
        with self.assertRaises(ApiError): self.s.receive(self.b, d)

    def test_receipt_replay_restart_lost_ack_and_single_consumption(self):
        *_, bridge, m, d = self.invitation()
        first = self.s.receive(self.lena, d)
        again = AgentLivingDeeds(WebApp(preflight(self.f.path))).receive(self.lena, d)
        self.assertEqual(first, again)
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM living_versions WHERE record_id=?', (d,)).fetchone()[0], 2)
        with self.assertRaises(ApiError): self.action(self.a, 'queue_invitation', {'mandate_id': m, 'bridge_id': bridge})

    def test_decline_blocks_late_ack_and_neutral_requester_state(self):
        *_, bridge, m, d = self.invitation()
        before = json.dumps(self.s.view(self.b), sort_keys=True)
        self.s.receive(self.lena, d)
        self.action(self.lena, 'decline_invitation', {'id': bridge})
        with self.assertRaises(ApiError): self.s.receive(self.lena, d)
        self.assertEqual(before, json.dumps(self.s.view(self.b), sort_keys=True))
        with self.assertRaises(ApiError): self.grant('x' * 36, bridge, 'INVITE_ONCE')

    def test_revocation_expiry_cancel_and_permission_withdrawal_fail_closed(self):
        _, _, p, bridge, m, d = self.invitation()
        self.action(self.a, 'revoke_agent', {'id': m})
        with self.assertRaises(ApiError): self.s.receive(self.lena, d)
        m2 = self.grant(p, bridge, 'INVITE_ONCE')
        d2 = self.action(self.a, 'queue_invitation', {'mandate_id': m2, 'bridge_id': bridge})
        with patch('time.time', return_value=self.f.now + 201):
            with self.assertRaises(ApiError): self.s.receive(self.lena, d2)
        self.action(self.a, 'cancel_delivery', {'id': d2})
        with self.assertRaises(ApiError): self.s.receive(self.lena, d2)
        self.action(self.b, 'withdraw', {'id': p})
        with self.assertRaises(ApiError): self.grant(p, bridge, 'INVITE_ONCE')

    def test_silence_creates_private_no_contact_hold(self):
        _, note, p, bridge, _, d = self.invitation()
        with patch('time.time', return_value=self.f.now + 301):
            fresh = self.f.app.login('uel_synthetic_owner', fixture.PASSWORD)[0]
            self.action(fresh, 'close_silent_invitation', {'id': bridge})
            with self.assertRaises(ApiError): self.s.receive(self.lena, d)
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM living_contact_holds').fetchone()[0], 1)

    def test_erasure_cascades_queue_no_hash_reconstruction(self):
        _, note, _, _, _, d = self.invitation()
        self.action(self.a, 'erase', {'id': note})
        with self.assertRaises(ApiError): self.s.receive(self.lena, d)
        with self.f.app.store._locked_transaction() as db:
            self.assertIsNone(db.execute('SELECT id FROM living_records WHERE id=?', (d,)).fetchone())

    def test_old_backup_restores_no_permissions_and_unknown_recovery(self):
        *_, m, d = self.invitation()
        backup = self.f.app.store.backup_database(self.f.cp.root/'agent-backup')
        self.action(self.a, 'revoke_agent', {'id': m})
        app = WebApp(restore(backup, self.f.lab.journal))
        token = app.login('uel_synthetic_owner', fixture.PASSWORD)[0]
        with self.assertRaises(JournalUnavailable): AgentLivingDeeds(app).receive(token, d)

    def test_additive_agent_schema_repeated_migration_and_frozen_predecessors(self):
        self.assertFalse(agent_context_schema.migrate(self.f.path))
        from xblago.core import living_schema
        self.assertEqual(agent_context_schema.CHECKSUM, 'ef6d9ae1d6d2431f176717712aa029086d729d8b26e9c273440c42d6c78b8feb')
        with self.f.app.store._locked_transaction() as db:
            self.assertTrue(living_schema.validate(db, required=True))
            self.assertTrue(agent_context_schema.validate(db, mandatory=True))

    def test_generation_fencing_blocks_memory_delete_during_agent_response(self):
        from xblago.core.context_fencing import Generation, ContextConflict
        from xblago.core.messages import ConversationTurn
        uid = self.f.app.session(self.a)[0]
        self.f.app.store.set_memory(uid, 'city', 'PRIVATE_PERM')
        generation = Generation(self.f.app.store, uid)
        self.f.app.delete_memory(uid, 'city')
        generation.record((ConversationTurn('user', 'city?'), ConversationTurn('assistant', 'PRIVATE_PERM')))
        with self.assertRaises(ContextConflict): generation.commit()

    def test_two_processes_pull_one_durable_receipt(self):
        *_, d = self.invitation()
        context = multiprocessing.get_context('spawn'); children = []; pipes = []
        for _ in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=pull_worker, args=(self.f.cfg, self.lena, d, self.f.now, child))
            process.start(); children.append(process); pipes.append(parent)
        for pipe in pipes: self.assertEqual(pipe.recv(), 'ready')
        for pipe in pipes: pipe.send('go')
        results = [pipe.recv() for pipe in pipes]
        for process in children:
            process.join(30); self.assertEqual(process.exitcode, 0)
        self.assertEqual(results[0], results[1]); self.assertIn('ok', results[0])
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM living_versions WHERE record_id=?', (d,)).fetchone()[0], 2)

    def test_crash_after_commit_lost_ack_reuses_receipt(self):
        *_, d = self.invitation()
        process = multiprocessing.get_context('spawn').Process(target=pull_worker,
            args=(self.f.cfg, self.lena, d, self.f.now, None, 'after_commit'))
        process.start(); process.join(40); self.assertEqual(process.exitcode, 82)
        first = self.s.receive(self.lena, d)
        self.assertEqual(first, self.s.receive(self.lena, d))

    def test_crash_after_journal_before_sql_commit_no_success(self):
        *_, d = self.invitation()
        process = multiprocessing.get_context('spawn').Process(target=pull_worker,
            args=(self.f.cfg, self.lena, d, self.f.now, None, 'after_journal'))
        process.start(); process.join(40); self.assertEqual(process.exitcode, 81)
        with self.assertRaises(JournalUnavailable): self.s.receive(self.lena, d)

    def test_disk_failure_before_journal_returns_no_ack(self):
        from xblago.core import uel_local
        *_, d = self.invitation()
        with patch.object(uel_local, 'append', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): self.s.receive(self.lena, d)
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(json.loads(db.execute('SELECT state FROM living_records WHERE id=?', (d,)).fetchone()[0])['status'], 'QUEUED')
        self.assertTrue(self.s.receive(self.lena, d)['receipt'])

    def test_recipient_deletion_retires_delivery_without_exposing_note(self):
        from xblago.integrations.privacy import PrivacyService
        *_, d = self.invitation()
        privacy = PrivacyService(self.f.app); preview = privacy.preview(self.lena)
        privacy.delete(self.lena, preview['challenge'], 'УДАЛИТЬ')
        with self.f.app.store._locked_transaction() as db:
            self.assertIsNone(db.execute('SELECT id FROM living_records WHERE id=?', (d,)).fetchone())
        self.assertNotIn('PRIVATE_NOTE', json.dumps(self.s.view(self.b)))

    def test_retention_of_unanswered_invitation_preserves_suppression(self):
        *_, d = self.invitation()
        with patch('time.time', return_value=self.f.now + 301):
            fresh = self.f.app.login('uel_synthetic_owner', fixture.PASSWORD)[0]
            self.assertGreater(self.s.expire(fresh)['expired_records'], 0)
        with self.f.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM living_contact_holds').fetchone()[0], 1)
            self.assertIsNone(db.execute('SELECT id FROM living_records WHERE id=?', (d,)).fetchone())
