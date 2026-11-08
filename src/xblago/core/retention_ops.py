"""Explicit synthetic retention operator. Never imported by application startup.

Scheduling state is private operational metadata, NOT an authority or a replacement
for the original deletion journal/witness. No production mode is implemented.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from xblago.core.data_lifecycle import managed_backup, retire_backup, sweep, RetentionBlocked
from xblago.core.security_journal import JournalUnavailable, SecurityJournal, canonical
from xblago.core.sqlite_safety import require_safe_sqlite


VERSION = 'retention_operations_001'
DDL = (
    'CREATE TABLE ops_schema (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)',
    'CREATE TABLE ops_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), journal_id TEXT NOT NULL, policy TEXT NOT NULL, policy_tag TEXT NOT NULL)',
    """CREATE TABLE ops_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        first_due INTEGER NOT NULL, next_due INTEGER NOT NULL, last_success INTEGER, last_seen INTEGER NOT NULL,
        attempts INTEGER NOT NULL CHECK(attempts>=0), anchor_seq INTEGER NOT NULL,
        anchor_head TEXT NOT NULL)""",
    """CREATE TABLE ops_runs (run_id TEXT PRIMARY KEY, started INTEGER NOT NULL, finished INTEGER,
        status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCESS','FAILED','INTERRUPTED','RESCHEDULED','BLOCKED')),
        reason TEXT NOT NULL, counts TEXT NOT NULL)""",
)
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


@dataclass(frozen=True)
class Schedule:
    interval: int = 3600
    retry_seconds: int = 60
    max_attempts: int = 3
    overdue_seconds: int = 7200
    result_ttl: int = 604800

    def validate(self):
        if any(type(v) is not int for v in asdict(self).values()):
            raise ValueError('Integer schedule required')
        if not (1 <= self.interval <= 86400 and 1 <= self.retry_seconds <= 3600
                and 1 <= self.max_attempts <= 5 and self.interval <= self.overdue_seconds <= 604800
                and 86400 <= self.result_ttl <= 2592000):
            raise ValueError('Schedule bounds invalid')


class OpsUnavailable(RuntimeError):
    pass


def _private(path):
    if path.is_symlink() or not path.exists() or path.stat().st_mode & 0o077:
        raise OpsUnavailable('Unsafe operational state')


def _clock_value(clock):
    n = clock()
    if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 0:
        raise OpsUnavailable('Invalid clock')
    return int(n)


def _environment(root):
    # The existing marker restricts this operator to a NEW, local-provider lab.
    from tools.help_readiness_lab import configuration
    return configuration(root)


def _controls(cfg):
    j = SecurityJournal(Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']),
                        witness_directory=Path(cfg['XBLAGO_JOURNAL_WITNESS_DIR']))
    rows, chain = j.read()
    return j, rows, chain


def _safe_counts(value):
    top = {'history','semantic_archives','blago_needs','blago_resources','blago_assets',
           'blago_resource_offers','receipt_bodies','help_retired','generations','activity_unconsented',
           'labels_unconsented','revoked_credentials','revoked_devices','technical'}
    technical = {'auth_challenges','telegram_exchanges','telegram_proofs','privacy_delete_challenges',
                 'help_challenges','activity_events','security_signals','account_link_codes','web_sessions'}
    def valid(d, keys):
        return isinstance(d,dict) and set(d)<=keys and all(type(v) is int and v>=0 for v in d.values())
    if not isinstance(value,dict) or set(value)-top or not valid({k:v for k,v in value.items() if k!='technical'},top):
        raise ValueError('Only aggregate retention counters allowed')
    if 'technical' in value and not valid(value['technical'],technical):
        raise ValueError('Only aggregate technical counters allowed')
    return value


def initialize(root, schedule=None, *, clock=time.time):
    """Only explicit initialization, new operator directory; no application DDL."""
    from unittest.mock import patch
    from xblago.core.integration import preflight
    schedule = schedule or Schedule()
    schedule.validate()
    root = Path(root).resolve()
    cfg = _environment(root)
    with patch.dict(os.environ, cfg):
        require_safe_sqlite()
        preflight(root/'app'/'blago.sqlite3')
        j, rows, chain = _controls(cfg)
    directory = root/'retention-ops'
    directory.mkdir(mode=0o700, exist_ok=False)
    path = directory/'operations.sqlite3'
    now = _clock_value(clock)
    # Explicit versioned migration in the separate operator store, atomically.
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        for sql in DDL:
            db.execute(sql)
        db.execute('INSERT INTO ops_schema VALUES (?,?)', (VERSION, CHECKSUM))
        policy = canonical(asdict(schedule))
        db.execute('INSERT INTO ops_binding VALUES (1,?,?,?)', (j.journal_id, policy, j.tag('retention-policy', VERSION, policy)))
        db.execute('INSERT INTO ops_state VALUES (1,?,?,NULL,?,0,?,?)', (now, now, now, len(rows), chain[-1]))
    path.chmod(0o600)
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Runner:
    def __init__(self, root, *, enabled=None, clock=time.time, backup=None, cleanup=None, after_cleanup=None):
        self.root = Path(root).resolve()
        self.cfg = _environment(self.root)
        self.directory = self.root/'retention-ops'
        self.path = self.directory/'operations.sqlite3'
        self.clock = clock
        self.enabled = (os.getenv('XBLAGO_RETENTION_SCHEDULER_ENABLED', '').lower() == 'true') if enabled is None else enabled
        if type(self.enabled) is not bool:
            raise ValueError('Explicit boolean activation required')
        self.backup = backup or managed_backup
        self.cleanup = cleanup or sweep
        self.after_cleanup = after_cleanup

    @contextmanager
    def _lock(self):
        _private(self.directory)
        fd = os.open(self.directory/'runner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _private(self.directory/'runner.lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                # No expiring lease: paused old workers cannot overlap a replacement.
                yield True
        finally:
            os.close(fd)

    @contextmanager
    def _db(self):
        _private(self.path)
        require_safe_sqlite()
        with sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw', uri=True, timeout=5) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
            if actual != {sql.split()[2]: sql for sql in DDL}:
                raise OpsUnavailable('Operational schema drift')
            if [tuple(r) for r in db.execute('SELECT * FROM ops_schema')] != [(VERSION, CHECKSUM)]:
                raise OpsUnavailable('Operational migration mismatch')
            binding = db.execute('SELECT * FROM ops_binding WHERE singleton=1').fetchone()
            if not binding:
                raise OpsUnavailable('Operational binding missing')
            policy = Schedule(**json.loads(binding['policy']))
            policy.validate()
            if canonical(asdict(policy)) != binding['policy']:
                raise OpsUnavailable('Operational policy invalid')
            yield db, binding, policy

    def _verify(self, binding, state):
        j, rows, chain = _controls(self.cfg)
        if (j.journal_id != binding['journal_id'] or not hmac.compare_digest(binding['policy_tag'],j.tag('retention-policy',VERSION,binding['policy'])) or state['anchor_seq'] < 0
                or state['anchor_seq'] >= len(chain) or chain[state['anchor_seq']] != state['anchor_head']):
            raise JournalUnavailable('Operational control frontier unavailable')
        return rows, chain

    @staticmethod
    def _result(status, state=None, policy=None, now=None, **extra):
        overdue = bool(state is not None and now - (state['last_success'] if state['last_success'] is not None else state['first_due']) >= policy.overdue_seconds)
        return {'format': 'blago-retention-ops-1', 'status': status, 'overdue': overdue,
                'alerts': (['RETENTION_OVERDUE'] if overdue else []) + (['RETENTION_UNCONFIRMED'] if status in ('FAILED','INTERRUPTED','UNKNOWN_RECOVERY','EXHAUSTED','CLOCK_ROLLBACK') else []),
                'G04': 'UNKNOWN', 'real_pilot': 'NO-GO', 'synthetic_only': True, **extra}

    def inspect(self):
        if not self.enabled:
            return self._result('DISABLED')
        from unittest.mock import patch
        with patch.dict(os.environ, self.cfg), self._lock() as acquired:
            if not acquired:
                return self._result('BUSY', compliant=False)
            with self._db() as (db, binding, policy):
                s = db.execute('SELECT * FROM ops_state').fetchone()
                try:
                    self._verify(binding, s)
                except JournalUnavailable:
                    return self._result('UNKNOWN_RECOVERY', s, policy, _clock_value(self.clock), compliant=False)
                now = _clock_value(self.clock)
                running = db.execute("SELECT 1 FROM ops_runs WHERE status='RUNNING'").fetchone()
                status = 'CLOCK_ROLLBACK' if now < s['last_seen'] else 'INTERRUPTED' if running else 'EXHAUSTED' if s['attempts'] >= policy.max_attempts else 'WAITING' if now < s['next_due'] else 'DUE'
                return self._result(status, s, policy, now, last_success=s['last_success'], attempts=s['attempts'], compliant=False)

    def tick(self):
        if not self.enabled:
            return self._result('DISABLED')
        from unittest.mock import patch
        from xblago.core.integration import preflight
        with patch.dict(os.environ, self.cfg), self._lock() as acquired:
            if not acquired:
                return self._result('BUSY', compliant=False)
            now = _clock_value(self.clock)
            with self._db() as (db, binding, policy):
                s = dict(db.execute('SELECT * FROM ops_state').fetchone())
                if now < s['last_seen']:
                    db.execute("INSERT INTO ops_runs VALUES (?,?,?,'BLOCKED','CLOCK_ROLLBACK','{}')", (secrets.token_hex(16),now,now))
                    return self._result('CLOCK_ROLLBACK', s, policy, now)
                try:
                    self._verify(binding, s)
                except JournalUnavailable:
                    db.execute("INSERT INTO ops_runs VALUES (?,?,?,'BLOCKED','CONTROL_UNAVAILABLE','{}')", (secrets.token_hex(16),now,now))
                    return self._result('UNKNOWN_RECOVERY', s, policy, now)
                # A previous process died with no confirmed operational result.
                db.execute("UPDATE ops_runs SET status='INTERRUPTED',finished=?,reason='PROCESS_EXIT' WHERE status='RUNNING'", (now,))
                db.execute("DELETE FROM ops_runs WHERE status<>'RUNNING' AND finished<=?", (now-policy.result_ttl,))
                db.execute('UPDATE ops_state SET last_seen=?', (now,))
                if s['attempts'] >= policy.max_attempts:
                    return self._result('EXHAUSTED', s, policy, now)
                if now < s['next_due']:
                    return self._result('WAITING', s, policy, now)
                run_id = secrets.token_hex(16)
                db.execute("INSERT INTO ops_runs VALUES (?,?,NULL,'RUNNING',?,'{}')", (run_id, now,'OVERDUE' if self._result('DUE',s,policy,now)['overdue'] else 'NONE'))
                db.execute('UPDATE ops_state SET attempts=attempts+1,next_due=?', (now+policy.retry_seconds*2**s['attempts'],))
            # RUNNING + consumed attempt are durable BEFORE any cleanup side effects.
            reason = 'NONE'
            counts = {}
            try:
                store = preflight(self.root/'app'/'blago.sqlite3')
                self.backup(store)  # failure blocks cleanup, never claims a backup exists
                counts = _safe_counts(self.cleanup(store))
                if self.after_cleanup:
                    self.after_cleanup()
                with store._locked_transaction() as app:
                    retired = [r[0] for r in app.execute("SELECT backup_id FROM lifecycle_backups WHERE state='RETIRED'")]
                for bid in retired:
                    retire_backup(store, bid, unlink=True)
                # No SUCCESS without post-commit original journal/witness verification.
                _, rows, chain = _controls(self.cfg)
                with self._db() as (db, binding, policy):
                    state = db.execute('SELECT * FROM ops_state').fetchone()
                    self._verify(binding, state)
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise OpsUnavailable('Operational integrity failure')
                    finished = _clock_value(self.clock)
                    if finished < now:
                        raise OpsUnavailable('Clock rollback during operation')
                    db.execute("UPDATE ops_runs SET status='SUCCESS',finished=?,counts=? WHERE run_id=?", (finished, canonical(counts), run_id))
                    db.execute('UPDATE ops_state SET last_success=?,last_seen=?,attempts=0,next_due=?,anchor_seq=?,anchor_head=?', (finished, finished, finished+policy.interval, len(rows), chain[-1]))
                fresh = dict(s, last_success=finished)
                return self._result('SUCCESS', fresh, policy, finished, counts=counts, overdue_before=self._result('DUE', s, policy, now)['overdue'])
            except JournalUnavailable:
                reason = 'CONTROL_UNAVAILABLE'
            except RetentionBlocked:
                reason = 'RETENTION_REVIEW_REQUIRED'
            except (OSError, sqlite3.Error):
                reason = 'STORAGE_FAILURE'
            except Exception:
                # Never persist exception strings: they can contain paths/content.
                reason = 'OPERATION_UNCONFIRMED'
            try:
                with self._db() as (db, binding, policy):
                    db.execute("UPDATE ops_runs SET status='FAILED',finished=?,reason=? WHERE run_id=?", (_clock_value(self.clock), reason, run_id))
            except Exception as exc:
                raise OpsUnavailable('Result persistence unavailable; no success confirmed') from exc
            return self._result('FAILED', s, policy, now, reason=reason, effects_may_be_committed=True)

    def reschedule(self):
        """Explicit synthetic operator action; does NOT clear last-success overdue."""
        if not self.enabled:
            return self._result('DISABLED')
        from unittest.mock import patch
        with patch.dict(os.environ, self.cfg), self._lock() as acquired:
            if not acquired:
                return self._result('BUSY')
            with self._db() as (db, binding, policy):
                s = db.execute('SELECT * FROM ops_state').fetchone()
                self._verify(binding, s)
                now = _clock_value(self.clock)
                if now < s['last_seen']:
                    return self._result('CLOCK_ROLLBACK', s, policy, now)
                db.execute("INSERT INTO ops_runs VALUES (?,?,?,'RESCHEDULED','OPERATOR','{}')", (secrets.token_hex(16), now, now))
                db.execute('UPDATE ops_state SET attempts=0,next_due=?,last_seen=?', (now, now))
                return self._result('DUE', s, policy, now)
