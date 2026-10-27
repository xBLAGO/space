"""07.2 synthetic protocol model. No live signers, notifications or authority grants."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xblago.core.identity_recovery import (
    RecoveryPolicy, RecoveryCeremony, RecoveryBlocked, fresh_checkpoint,
    require_runtime_recovery, fingerprint, PROTOCOL,
)
from xblago.core.security_journal import SecurityJournal, canonical, JournalUnavailable
from xblago.core.journal_witness import initialize
from xblago.core.sovereign import b64, SovereignIdentity, POLICY
from xblago.core.sovereign_schema import migrate_sovereign
from xblago.integrations.web import ApiError, WebApp
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.privacy import PrivacyService
from xblago.core.privacy_maintenance import restore
from tools.pilot_fixture import fixture


def restore_worker(args):
    policy, journal, witness, records = args
    document = dict(policy)
    document.pop('protocol')
    for field in ('owners', 'guardians', 'witnesses'):
        document[field] = tuple(document[field])
    engine = RecoveryCeremony(RecoveryPolicy(**document),
        SecurityJournal(Path(journal), witness_directory=Path(witness)), records)
    return engine.state, engine.head


def submit_worker(args):
    policy, journal, witness, command = args
    document = dict(policy)
    document.pop('protocol')
    for field in ('owners', 'guardians', 'witnesses'):
        document[field] = tuple(document[field])
    try:
        engine = RecoveryCeremony(RecoveryPolicy(**document),
            SecurityJournal(Path(journal), witness_directory=Path(witness)))
        message, signatures, kwargs = command
        return engine.submit(message, signatures, **kwargs)
    except JournalUnavailable:
        return None


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blago-recovery-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'XBLAGO_IDENTITY_RECOVERY_ENABLED': 'true',
            'XBLAGO_IDENTITY_CORE_MODE': 'isolated_test', 'XBLAGO_JOURNAL_WITNESS_DIR': '',
            'XBLAGO_PILOT_MODE': 'false'})
        self.env.start(); self.addCleanup(self.env.stop)
        keys = [Ed25519PrivateKey.generate() for _ in range(10)]
        self.secrets = {b64(key.public_key().public_bytes_raw()): key for key in keys}
        pubs = list(self.secrets)
        self.owner, self.guardians, self.witnesses, self.candidate = pubs[0], pubs[1:4], pubs[4:8], pubs[8]
        self.policy = RecoveryPolicy(b64(secrets.token_bytes(32)), (self.owner,),
            tuple(self.guardians), 2, 3600, tuple(self.witnesses))
        self.journal = SecurityJournal.initialize(self.root/'control')
        initialize(self.journal, self.root/'witness')
        self.journal = SecurityJournal(self.root/'control', witness_directory=self.root/'witness')
        self.ceremony = RecoveryCeremony(self.policy, self.journal)
        self.now = 1000000

    def sign(self, message, keys):
        return {key: b64(self.secrets[key].sign(canonical(message).encode())) for key in keys}

    def checkpoint(self, head=None, at=None, nonce=None):
        nonce = nonce or b64(secrets.token_bytes(32))
        body = {'protocol': PROTOCOL+'/checkpoint', 'policy': self.policy.digest,
                'head': head or self.ceremony.head, 'nonce': nonce, 'time': at or self.now}
        return nonce, [{'key': key, 'body': dict(body), 'signature': self.sign(body, [key])[key]}
                       for key in self.witnesses[:3]]

    def command(self, action, keys=None):
        candidate = self.candidate if action == 'start' else None
        message = self.ceremony.proposal(action, at=self.now, candidate=candidate)
        if keys is None:
            keys = ([self.owner]+self.guardians if action == 'configure' else
                    self.guardians[:2]+[self.candidate] if action in ('start', 'ready') else [self.owner])
        nonce, receipts = self.checkpoint()
        notices = []
        if action == 'start':
            body = {'protocol': PROTOCOL+'/notice', 'policy': self.policy.digest,
                    'request': fingerprint(message), 'owner': self.owner}
            notices = [self.sign(body, self.witnesses[:3])]
        return message, self.sign(message, keys), {'checkpoint_nonce': nonce, 'receipts': receipts, 'notices': notices}

    def submit(self, action, keys=None):
        message, signatures, kwargs = self.command(action, keys)
        return self.ceremony.submit(message, signatures, **kwargs)

    def pending(self):
        self.submit('configure'); self.submit('start')

    def test_threshold_delay_and_fresh_approval_evidence_only(self):
        self.pending()
        with self.assertRaises(RecoveryBlocked): self.submit('ready')
        self.now += 3600
        self.submit('ready')
        self.assertEqual(self.ceremony.state, 'ready')
        with self.assertRaises(RecoveryBlocked): self.ceremony.activate_identity()
        with self.assertRaises(RecoveryBlocked): self.submit('ready')

    def test_policy_configuration_requires_owner_and_every_guardian_acceptance(self):
        for keys in ([self.owner], self.guardians, [self.owner]+self.guardians[:2]):
            with self.subTest(keys=keys), self.assertRaises(RecoveryBlocked): self.submit('configure', keys)
        self.assertEqual(self.ceremony.state, 'unconfigured')
        self.assertEqual(len(self.journal.read()[0]), 0)

    def test_policy_bounds_role_overlap_and_duplicate_keys(self):
        from dataclasses import replace
        invalid = [replace(self.policy, threshold=1), replace(self.policy, threshold=True),
            replace(self.policy, threshold=4), replace(self.policy, delay=0),
            replace(self.policy, guardians=(self.guardians[0],)*3),
            replace(self.policy, owners=(self.guardians[0],)), replace(self.policy, witnesses=tuple(self.witnesses[:2])),
            replace(self.policy, owners=(b64(b'\x01'+b'\0'*31),))]
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises((RecoveryBlocked, ApiError)): policy.validate()

    def test_compromised_one_guardian_and_candidate_cannot_recover(self):
        self.submit('configure')
        with self.assertRaises(RecoveryBlocked): self.submit('start', self.guardians[:1]+[self.candidate])
        self.assertEqual(self.ceremony.state, 'idle')

    def test_candidate_possession_and_unknown_signer(self):
        self.submit('configure')
        for keys in (self.guardians, self.guardians[:2]+[list(self.secrets)[9]]):
            with self.assertRaises(RecoveryBlocked): self.submit('start', keys)

    def test_session_password_admin_telegram_and_memory_are_not_signers(self):
        message, _, kwargs = self.command('configure')
        for authority in ('admin', 'telegram', 'password', 'memory', 'blago_user_id'):
            with self.subTest(authority=authority), self.assertRaises(RecoveryBlocked):
                self.ceremony.submit(message, {authority: 'session-token'}, **kwargs)

    def test_invalid_signature_and_signed_context_substitution(self):
        for field, value in [('policy', '0'*64), ('parent', '1'*64), ('action', 'retire'), ('expires_at', self.now+9999)]:
            message, signatures, kwargs = self.command('configure')
            message[field] = value
            with self.subTest(field=field), self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)
        message, signatures, kwargs = self.command('configure')
        signatures[self.owner] = b64(b'\0'*64)
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)

    def test_replay_and_parent_invalidation(self):
        message, signatures, kwargs = self.command('configure')
        self.ceremony.submit(message, signatures, **kwargs)
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)

    def test_owner_contest_cancels_pending_request(self):
        self.pending()
        old = self.command('ready')
        self.submit('contest')
        self.now += 3600
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(old[0], old[1], **old[2])
        self.assertIsNone(self.ceremony.pending)
        self.assertEqual(self.ceremony.state, 'idle')

    def test_threshold_cancel_and_owner_retirement(self):
        self.pending()
        with self.assertRaises(RecoveryBlocked): self.submit('cancel', self.guardians[:1])
        self.submit('cancel', self.guardians[:2])
        self.submit('retire')
        with self.assertRaises(RecoveryBlocked): self.submit('start')

    def test_foreign_and_old_owner_role_not_automatically_authorised(self):
        self.pending()
        with self.assertRaises(RecoveryBlocked): self.submit('contest', [self.candidate])
        with self.assertRaises(RecoveryBlocked): self.submit('retire', self.guardians)

    def test_no_notice_or_insufficient_delivery_quorum(self):
        self.submit('configure')
        for notices in ([], [{}]):
            message, signatures, kwargs = self.command('start')
            kwargs['notices'] = notices
            with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)

    def test_notice_bound_to_exact_request(self):
        self.submit('configure')
        _, _, old = self.command('start')
        message, signatures, kwargs = self.command('start')
        kwargs['notices'] = old['notices']
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)

    def test_missing_duplicate_or_forged_checkpoint_witness(self):
        nonce, receipts = self.checkpoint()
        invalid = [receipts[:2], [receipts[0]]*3, copy.deepcopy(receipts)]
        invalid[-1][0]['signature'] = b64(b'\0'*64)
        for values in invalid:
            with self.assertRaises(RecoveryBlocked): fresh_checkpoint(self.policy, self.ceremony.head, nonce, values)

    def test_checkpoint_nonce_head_scope_and_clock_disagreement(self):
        nonce, receipts = self.checkpoint()
        for head, other_nonce in [('0'*64, nonce), (self.ceremony.head, b64(secrets.token_bytes(32)))]:
            with self.assertRaises(RecoveryBlocked): fresh_checkpoint(self.policy, head, other_nonce, receipts)
        receipt = receipts[0]
        receipt['body']['time'] += 31
        receipt['signature'] = self.sign(receipt['body'], [receipt['key']])[receipt['key']]
        with self.assertRaises(RecoveryBlocked): fresh_checkpoint(self.policy, self.ceremony.head, nonce, receipts)

    def test_app_clock_fast_forward_cannot_skip_delay(self):
        self.pending()
        message, signatures, kwargs = self.command('ready')
        message['issued_at'] += 3600; message['expires_at'] += 3600
        signatures = self.sign(message, self.guardians[:2]+[self.candidate])
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)

    def test_signed_control_clock_rollback_is_rejected(self):
        self.submit('configure')
        self.now -= 1
        with self.assertRaises(RecoveryBlocked): self.submit('start')
        self.assertEqual(self.ceremony.state, 'idle')

    def test_expired_command_and_request(self):
        message, signatures, kwargs = self.command('configure')
        kwargs['checkpoint_nonce'], kwargs['receipts'] = self.checkpoint(at=self.now+121)
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)
        self.pending(); self.now += 3600+7*86400+1
        with self.assertRaises(RecoveryBlocked): self.submit('ready')

    def test_restart_and_exact_transcript_restore(self):
        self.pending()
        records = json.loads(canonical(self.ceremony.records))
        restored = RecoveryCeremony(self.policy, self.journal, records)
        self.assertEqual(restored.head, self.ceremony.head)
        self.assertEqual(restored.pending, self.ceremony.pending)
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal, records[:1])
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal, [])

    def test_separate_process_restore_public_transcript_without_human_private_keys(self):
        self.pending()
        args = (self.policy.document(), str(self.root/'control'), str(self.root/'witness'), self.ceremony.records)
        import multiprocessing
        with concurrent.futures.ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn')) as pool:
            self.assertEqual(pool.submit(restore_worker, args).result(timeout=30), ('pending', self.ceremony.head))

    def test_old_journal_snapshot_detected_by_retained_witness(self):
        shutil.copytree(self.root/'control', self.root/'old-control')
        self.pending()
        old = SecurityJournal(self.root/'old-control', witness_directory=self.root/'witness')
        with self.assertRaises(JournalUnavailable): RecoveryCeremony(self.policy, old)

    def test_lost_witness_or_key_no_reinitialisation(self):
        self.submit('configure')
        os.rename(self.root/'witness', self.root/'witness-lost')
        with self.assertRaises(JournalUnavailable): self.submit('start')
        os.rename(self.root/'witness-lost', self.root/'witness')
        (self.root/'control'/'key.json').unlink()
        with self.assertRaises(JournalUnavailable): SecurityJournal(self.root/'control', witness_directory=self.root/'witness')

    def test_crash_after_append_before_transcript_persistence_blocks_restore(self):
        self.submit('configure')
        old = copy.deepcopy(self.ceremony.records)
        self.submit('start')
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal, old)
        self.assertEqual(RecoveryCeremony(self.policy, self.journal, self.ceremony.records).state, 'pending')

    def test_filesystem_failure_never_advances_local_state(self):
        with patch.object(self.journal, 'append', side_effect=JournalUnavailable('synthetic fsync failure')):
            with self.assertRaises(JournalUnavailable): self.submit('configure')
        self.assertEqual(self.ceremony.state, 'unconfigured')
        self.assertEqual(self.ceremony.records, [])

    def test_stale_parallel_instance_and_duplicate_proposals(self):
        other = RecoveryCeremony(self.policy, self.journal)
        self.submit('configure')
        message = other.proposal('configure', at=self.now)
        nonce, receipts = self.checkpoint(head=other.head)
        with self.assertRaises(RecoveryBlocked): other.submit(message, self.sign(message, [self.owner]+self.guardians), checkpoint_nonce=nonce, receipts=receipts)

    def test_simultaneous_identical_request_has_no_second_transition(self):
        message, signatures, kwargs = self.command('configure')
        def submit():
            local = RecoveryCeremony(self.policy, self.journal)
            return local.submit(message, signatures, **kwargs)
        def attempt():
            try: return submit()
            except JournalUnavailable: return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertTrue(any(results))
        self.assertEqual(len(self.journal.read()[0]), 1)

    def test_concurrent_conflicting_commands_fail_closed_without_journal_fork(self):
        local = RecoveryCeremony(self.policy, self.journal)
        commands = [self.command('configure'), self.command('configure')]
        def attempt(index):
            message, signatures, kwargs = commands[index]
            try:
                return (self.ceremony if index == 0 else local).submit(message, signatures, **kwargs)
            except JournalUnavailable:
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        winners = [result for result in results if result]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(self.journal.read()[0]), 1)
        self.assertEqual(RecoveryCeremony(self.policy, self.journal, winners[0]).state, 'idle')

    def test_compromised_server_hmac_key_cannot_forge_guardian_or_witness_signatures(self):
        # HMAC custody loss is outside 05.4 guarantees. It still cannot create
        # human signatures or satisfy an independently pinned checkpoint reader.
        self.assertEqual(len(self.journal.key), 32)
        message, signatures, kwargs = self.command('configure')
        signatures[self.owner] = b64(self.journal.key * 2)
        with self.assertRaises(RecoveryBlocked): self.ceremony.submit(message, signatures, **kwargs)
        nonce, receipts = self.checkpoint()
        receipts[0]['signature'] = b64(self.journal.key * 2)
        with self.assertRaises(RecoveryBlocked): fresh_checkpoint(self.policy, self.ceremony.head, nonce, receipts)
        # Full app compromise can lie about results. No test claims to prevent
        # that without an independent client and an atomic authority service.

    def test_transcript_tampering_and_cross_policy_isolation(self):
        self.pending()
        altered = copy.deepcopy(self.ceremony.records)
        altered[1]['message']['candidate'] = list(self.secrets)[9]
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal, altered)
        from dataclasses import replace
        other = replace(self.policy, binding=b64(secrets.token_bytes(32)))
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(other, self.journal, self.ceremony.records)

    def test_evidence_journal_cannot_be_used_without_independent_witness(self):
        with self.assertRaises(RecoveryBlocked):
            RecoveryCeremony(self.policy, SecurityJournal(self.root/'control'))

    def test_multiprocess_conflicting_commands_have_one_durable_successor(self):
        commands = [self.command('configure'), self.command('configure')]
        args = [(self.policy.document(), str(self.root/'control'), str(self.root/'witness'), command)
                for command in commands]
        import multiprocessing
        with concurrent.futures.ProcessPoolExecutor(2, mp_context=multiprocessing.get_context('spawn')) as pool:
            results = list(pool.map(submit_worker, args))
        winners = [result for result in results if result]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(self.journal.read()[0]), 1)
        self.assertEqual(RecoveryCeremony(self.policy, self.journal, winners[0]).state, 'idle')

    def test_sufficient_guardian_collusion_is_a_known_risk_not_runtime_permission(self):
        # A threshold coalition controlling a new candidate can create evidence.
        # This is a fundamental trust assumption, not Sybil/personhood protection.
        self.pending(); self.now += 3600; self.submit('ready')
        self.assertEqual(self.ceremony.state, 'ready')
        with self.assertRaises(RecoveryBlocked): self.ceremony.activate_identity()

    def test_flags_cannot_enable_authority_activation(self):
        with patch.dict(os.environ, {'XBLAGO_IDENTITY_RECOVERY_ENABLED': 'false'}):
            with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal)
            with self.assertRaises(ApiError) as error: require_runtime_recovery()
            self.assertEqual(error.exception.status, 404)
        with self.assertRaises(ApiError) as error: require_runtime_recovery()
        self.assertEqual(error.exception.status, 503)
        with patch.dict(os.environ, {'XBLAGO_IDENTITY_CORE_MODE': 'production'}):
            with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal)

    def test_journal_contains_no_keys_biography_or_signatures(self):
        self.pending()
        rows, _ = self.journal.read()
        text = canonical(rows)
        for key in self.secrets: self.assertNotIn(key, text)
        self.assertNotIn(self.policy.binding, text)
        self.assertNotIn('candidate', text)
        self.assertNotIn('signature', text)

    def test_dedicated_evidence_cannot_mix_with_application_journal(self):
        self.journal.append([('foreign', canonical({'kind': 'user_status', 'owner': 'synthetic'}))])
        with self.assertRaises(RecoveryBlocked): RecoveryCeremony(self.policy, self.journal)

    def test_identity_memory_economy_deletion_and_old_backup_remain_unchanged(self):
        with fixture() as f, patch.dict(os.environ, {'XBLAGO_IDENTITY_CORE_ENABLED': 'false'}):
            migrate_sovereign(f['path'])
            with patch.dict(os.environ, {'XBLAGO_IDENTITY_CORE_ENABLED': 'true'}):
                token, _ = f['app'].register('recovery_test', 'synthetic-long-password', 'synthetic-pilot-invite')
                uid = f['app'].session(token)[0]
                core = SovereignIdentity(f['app'])
                identity = core.create(token, 'CREATE_TEST_IDENTITY', POLICY)
                f['store'].memory_for_user(uid).set('city', 'Казань')
                before = f['store'].all_memory(uid)
                backup = f['store'].backup_database(f['root']/'backup')
                self.pending(); self.now += 3600; self.submit('ready')
                with self.assertRaises(RecoveryBlocked): self.ceremony.activate_identity(core, token)
                self.assertEqual(core.inspect(token)['identity_id'], identity['identity_id'])
                self.assertEqual(core.inspect(token)['controllers'], [])
                self.assertEqual(f['store'].all_memory(uid), before)
                self.assertEqual(f['app'].rows('needs', uid), [])
                self.assertEqual(f['app'].rows('resources', uid), [])
                core.withdraw(token, 'WITHDRAW_TEST_IDENTITY')
                restored = WebApp(restore(backup, f['journal']))
                replacement, _ = restored.login('recovery_test', 'synthetic-long-password')
                self.assertEqual(SovereignIdentity(restored).inspect(replacement)['status'], 'revoked')
                with self.assertRaises(RecoveryBlocked): self.ceremony.activate_identity()
                privacy = PrivacyService(restored)
                preview = privacy.preview(replacement)
                self.assertTrue(privacy.delete(replacement, preview['challenge'], 'УДАЛИТЬ')['deleted'])
                with self.assertRaises(ApiError): restored.session(replacement)
                with self.assertRaises(RecoveryBlocked): self.ceremony.activate_identity()
                with sqlite3.connect(backup) as db:
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
