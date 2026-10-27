"""07.2 isolated recovery ceremony and independently verifiable evidence.

This module NEVER changes Identity Core authority. A ready ceremony is not a
recovered identity. The missing independent commit/notification infrastructure
is an explicit runtime gate, not a permissive callback or an admin override.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass

from xblago.core.security_journal import canonical, JournalUnavailable
from xblago.core.sovereign import b64, decode, verify_signature
from xblago.integrations.web import ApiError

PROTOCOL = 'blago-recovery-ceremony-v1'
TTL = 120
MIN_DELAY = 3600
MAX_DELAY = 30 * 86400


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def enabled():
    return os.getenv('XBLAGO_IDENTITY_RECOVERY_ENABLED', '').lower() in ('1', 'true', 'yes')


class RecoveryBlocked(JournalUnavailable):
    """Insufficient independent, current evidence. Never fall back to a snapshot."""


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise RecoveryBlocked('Invalid recovery integer')


def _keys(values, low, high):
    if not isinstance(values, list) or not low <= len(values) <= high:
        raise RecoveryBlocked('Invalid recovery participants')
    for value in values:
        decode(value, 32)
        # Point validation is performed again when verifying every signature.
        from nacl.bindings import crypto_core_ed25519_is_valid_point
        if not crypto_core_ed25519_is_valid_point(decode(value, 32)):
            raise RecoveryBlocked('Invalid recovery public key')
    if len(set(values)) != len(values):
        raise RecoveryBlocked('Repeated recovery participant')


@dataclass(frozen=True)
class RecoveryPolicy:
    """Pinned out of band BEFORE loss. Public keys are not proof of independence."""
    binding: str
    owners: tuple[str, ...]
    guardians: tuple[str, ...]
    threshold: int
    delay: int
    witnesses: tuple[str, ...]

    def document(self):
        return {'protocol': PROTOCOL, 'binding': self.binding,
                'owners': list(self.owners), 'guardians': list(self.guardians),
                'threshold': self.threshold, 'delay': self.delay,
                'witnesses': list(self.witnesses)}

    def validate(self):
        decode(self.binding, 32)  # Random private ceremony handle, not a person hash.
        _keys(list(self.owners), 1, 8)
        _keys(list(self.guardians), 3, 16)
        _keys(list(self.witnesses), 4, 4)
        _integer(self.threshold, 2, len(self.guardians))
        _integer(self.delay, MIN_DELAY, MAX_DELAY)
        all_keys = self.owners + self.guardians + self.witnesses
        if len(set(all_keys)) != len(all_keys):
            raise RecoveryBlocked('Owner, guardian and witness roles must be disjoint')

    @property
    def digest(self):
        return fingerprint(self.document())


def _signatures(message, signatures, permitted, threshold):
    if not isinstance(signatures, dict) or len(signatures) > 32:
        raise RecoveryBlocked('Invalid signature set')
    if not set(signatures) <= set(permitted):
        raise RecoveryBlocked('Signer has no authority')
    if len(signatures) < threshold:
        raise RecoveryBlocked('Recovery signature threshold not met')
    for key, signature in signatures.items():
        try:
            verify_signature(decode(key, 32), signature, canonical(message).encode())
        except ApiError as exc:
            raise RecoveryBlocked('Invalid recovery signature') from exc


def fresh_checkpoint(policy, head, nonce, receipts):
    """3/4 matching signed reads; caller pins nonce before contacting witnesses.

    Each external witness must independently retain the latest COMMITTED head.
    This verifier cannot turn app-local copies into independent witnesses.
    Time is supplied by signed witness clocks, never by an application clock.
    """
    policy.validate()
    decode(nonce, 32)
    if not isinstance(head, str) or len(head) != 64 or any(c not in '0123456789abcdef' for c in head):
        raise RecoveryBlocked('Invalid checkpoint head')
    if not isinstance(receipts, list) or not 3 <= len(receipts) <= 4:
        raise RecoveryBlocked('Independent checkpoint quorum unavailable')
    seen, stamps = set(), []
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {'key', 'body', 'signature'}:
            raise RecoveryBlocked('Invalid checkpoint receipt')
        key, body = receipt['key'], receipt['body']
        if key not in policy.witnesses or key in seen:
            raise RecoveryBlocked('Untrusted or repeated checkpoint witness')
        if not isinstance(body, dict) or set(body) != {'protocol', 'policy', 'head', 'nonce', 'time'}:
            raise RecoveryBlocked('Invalid checkpoint context')
        if body['protocol'] != PROTOCOL + '/checkpoint' or body['policy'] != policy.digest or body['head'] != head or body['nonce'] != nonce:
            raise RecoveryBlocked('Stale or mismatched checkpoint')
        _integer(body['time'], 0, 2**63-1)
        _signatures(body, {key: receipt['signature']}, policy.witnesses, 1)
        seen.add(key)
        stamps.append(body['time'])
    if max(stamps) - min(stamps) > 30:
        raise RecoveryBlocked('Independent clocks disagree')
    # Slowest honest witness: conservative TTL and delay evaluation.
    return min(stamps), max(stamps)


class RecoveryCeremony:
    """Replayable threshold state machine. No sessions, administrators or LLMs.

    Journal is the original 05.4 SecurityJournal with an independent witness.
    It contains opaque ceremony hashes only. Signed transcript storage is a
    separate private artefact, not an application database or public event log.
    A lost transcript cannot be recreated from HMAC tags and must fail closed.
    """
    def __init__(self, policy, journal, transcript=()):
        if not enabled() or os.getenv('XBLAGO_IDENTITY_CORE_MODE') != 'isolated_test':
            raise RecoveryBlocked('Recovery ceremony requires explicit isolated_test flag; disabled by default')
        policy.validate()
        if journal.witness_directory is None:
            raise RecoveryBlocked('Independent 05.4 witness required')
        self.policy, self.journal = policy, journal
        encoded = canonical(list(transcript))
        if len(encoded) > 2_000_000 or len(transcript) > 1000:
            raise RecoveryBlocked('Recovery evidence exceeds isolated protocol limits')
        self.records = json.loads(encoded)
        self._replay()
        self._check_journal()

    def _replay(self):
        self.head = self.policy.digest
        self.state = 'unconfigured'
        self.pending = None
        self.used = set()
        self.last_time = 0
        for record in self.records:
            self._apply(record)

    def _check_journal(self):
        rows, _ = self.journal.read()
        expected = []
        owner = self.journal.tag('recovery-ceremony', self.policy.digest)
        for row in rows:
            item = json.loads(row[2])
            if item.get('kind') != 'recovery_ceremony_evidence':
                raise RecoveryBlocked('Use a dedicated evidence journal; application privacy journal is not a ceremony store')
            if item.get('kind') == 'recovery_ceremony_evidence' and item.get('owner') == owner:
                expected.append(item.get('target'))
        actual = [fingerprint(record) for record in self.records]
        if expected != actual:
            raise RecoveryBlocked('Transcript missing, rolled back or ahead of independent journal')

    def proposal(self, action, *, at, candidate=None):
        _integer(at, 0, 2**63-1)
        message = {'protocol': PROTOCOL, 'policy': self.policy.digest,
                   'parent': self.head, 'action': action, 'nonce': b64(secrets.token_bytes(32)),
                   'issued_at': at, 'expires_at': at + TTL, 'candidate': candidate}
        self._context(message)
        return message

    def _context(self, message):
        if not isinstance(message, dict) or set(message) != {'protocol', 'policy', 'parent', 'action', 'nonce', 'issued_at', 'expires_at', 'candidate'}:
            raise RecoveryBlocked('Invalid signed recovery command')
        if message['protocol'] != PROTOCOL or message['policy'] != self.policy.digest or message['parent'] != self.head:
            raise RecoveryBlocked('Recovery context changed or challenge replayed')
        decode(message['nonce'], 32)
        if message['nonce'] in self.used:
            raise RecoveryBlocked('Recovery challenge already used')
        _integer(message['issued_at'], 0, 2**63-1)
        if type(message['expires_at']) is not int or message['expires_at'] != message['issued_at'] + TTL:
            raise RecoveryBlocked('Invalid challenge lifetime')
        action = message['action']
        if action not in ('configure', 'start', 'ready', 'cancel', 'contest', 'retire'):
            raise RecoveryBlocked('Unknown recovery action')
        candidate = message['candidate']
        if action == 'start':
            _keys([candidate], 1, 1)
            if candidate in self.policy.owners + self.policy.guardians + self.policy.witnesses:
                raise RecoveryBlocked('Recovery requires a new independent owner key')
        elif candidate is not None:
            raise RecoveryBlocked('Unexpected candidate key')

    def _apply(self, record):
        if not isinstance(record, dict) or set(record) != {'message', 'signatures', 'checkpoint_nonce', 'receipts', 'notices'}:
            raise RecoveryBlocked('Invalid recovery transcript record')
        message = record['message']
        self._context(message)
        low, high = fresh_checkpoint(self.policy, self.head, record['checkpoint_nonce'], record['receipts'])
        if low < self.last_time:
            raise RecoveryBlocked('Independent checkpoint time rolled back')
        if not message['issued_at'] <= low <= high <= message['expires_at']:
            raise RecoveryBlocked('Expired or future recovery command')
        action, sigs = message['action'], record['signatures']
        owners, guardians = self.policy.owners, self.policy.guardians
        if self.state in ('ready', 'retired'):
            raise RecoveryBlocked('Ceremony is terminal; no authority was changed')
        if action == 'configure':
            if self.state != 'unconfigured':
                raise RecoveryBlocked('Recovery policy already fixed')
            _signatures(message, sigs, owners + guardians, len(owners) + len(guardians))
            self.state = 'idle'
        elif action == 'start':
            if self.state != 'idle':
                raise RecoveryBlocked('Recovery already pending or not configured')
            candidate = message['candidate']
            _signatures(message, sigs, guardians + (candidate,), self.policy.threshold + 1)
            if candidate not in sigs:
                raise RecoveryBlocked('Candidate proof of possession required')
            # Durable independent inbox receipts, signed over this exact request.
            notices = record['notices']
            if not isinstance(notices, list) or len(notices) != len(owners):
                raise RecoveryBlocked('Every current owner requires a durable notice')
            for owner, notice in zip(owners, notices):
                body = {'protocol': PROTOCOL + '/notice', 'policy': self.policy.digest,
                        'request': fingerprint(message), 'owner': owner}
                _signatures(body, notice, self.policy.witnesses, 3)
            self.pending = {'candidate': candidate, 'started': high,
                            'not_before': high + self.policy.delay, 'request': fingerprint(message)}
            self.state = 'pending'
        elif action == 'ready':
            if self.state != 'pending' or low < self.pending['not_before'] or high > self.pending['not_before'] + 7*86400:
                raise RecoveryBlocked('Recovery delay not elapsed or request expired')
            candidate = self.pending['candidate']
            _signatures(message, sigs, guardians + (candidate,), self.policy.threshold + 1)
            if candidate not in sigs:
                raise RecoveryBlocked('Fresh candidate proof required')
            self.state = 'ready'  # Evidence only. Deliberately not "recovered".
        elif action in ('contest', 'cancel', 'retire'):
            if self.state == 'unconfigured':
                raise RecoveryBlocked('Recovery policy is not configured')
            if action != 'retire' and self.state != 'pending':
                raise RecoveryBlocked('No pending recovery to stop')
            if action == 'cancel' and isinstance(sigs, dict) and set(sigs) <= set(guardians):
                _signatures(message, sigs, guardians, self.policy.threshold)
            else:
                _signatures(message, sigs, owners, 1)
            self.state = 'retired' if action == 'retire' else 'idle'
            self.pending = None
        if action != 'start' and record['notices'] != []:
            raise RecoveryBlocked('Unexpected notice payload')
        self.last_time = low
        self.used.add(message['nonce'])
        self.head = fingerprint(record)

    @contextmanager
    def _serialized(self):
        # Local ceremony CAS only; this is not distributed consensus. The file
        # is in the dedicated 0700 evidence journal, outside app SQLite backups.
        import fcntl
        fd = None
        try:
            fd = os.open(self.journal.directory/'ceremony.lock',
                os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError as exc:
            raise RecoveryBlocked('Recovery evidence lock unavailable') from exc
        finally:
            if fd is not None:
                os.close(fd)

    def submit(self, message, signatures, *, checkpoint_nonce, receipts, notices=None):
        with self._serialized():
            return self._submit(message, signatures, checkpoint_nonce=checkpoint_nonce,
                                receipts=receipts, notices=notices)

    def _submit(self, message, signatures, *, checkpoint_nonce, receipts, notices=None):
        """Write-ahead evidence. Caller must durably save returned transcript.

        Crash after journal append but before saving transcript intentionally locks
        this ceremony; caller retains signed command for exact reconciliation.
        Local writers compare the retained parent under a POSIX process lock.
        Distributed single-successor commit is NOT implemented or claimed here.
        """
        self._check_journal()
        record = json.loads(canonical({'message': message, 'signatures': signatures,
            'checkpoint_nonce': checkpoint_nonce, 'receipts': receipts, 'notices': [] if notices is None else notices}))
        # Validate on an isolated object before touching either durable state.
        trial = object.__new__(type(self))
        trial.policy, trial.records = self.policy, self.records + [record]
        trial._replay()
        payload = canonical({'kind': 'recovery_ceremony_evidence',
            'owner': self.journal.tag('recovery-ceremony', self.policy.digest),
            'target': fingerprint(record)})
        self.journal.append([(fingerprint(record), payload)])
        self.records = trial.records
        self._replay()
        self._check_journal()
        return json.loads(canonical(self.records))

    def activate_identity(self, *args, **kwargs):
        raise RecoveryBlocked('Independent atomic authority commit and delivery infrastructure unavailable; no Identity Core rights changed')


def require_runtime_recovery():
    if not enabled():
        raise ApiError(404, 'Identity recovery is disabled')
    raise ApiError(503, 'Identity recovery authority activation is unavailable; isolated evidence protocol only')
