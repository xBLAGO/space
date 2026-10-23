"""Explicit offline Foundation 05.2 migration; application startup never calls it."""
import argparse
import hashlib
import sqlite3
import time
from pathlib import Path

from xblago.core.identity_schema import migrate_identity_store, _check_database

VERSION = 'foundation05_runtime_002'
COLUMNS = {
    'public_id': 'TEXT',
    'created_at': 'INTEGER NOT NULL DEFAULT 0',
    'last_seen_at': 'INTEGER NOT NULL DEFAULT 0',
    'authenticated_at': 'INTEGER NOT NULL DEFAULT 0',
    'auth_method': "TEXT NOT NULL DEFAULT 'legacy'",
}
DDL = (
    'CREATE UNIQUE INDEX idx_session_public_id ON web_sessions(public_id)',
    '''CREATE TABLE telegram_exchanges (
        challenge_hash TEXT PRIMARY KEY NOT NULL,
        binding_hash TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL CHECK(expires_at > created_at AND expires_at <= created_at + 300),
        consumed_at INTEGER CHECK(consumed_at IS NULL OR consumed_at BETWEEN created_at AND expires_at)
    )''',
    '''CREATE TABLE telegram_proofs (
        proof_hash TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        expires_at INTEGER NOT NULL
    )''',
    '''CREATE TABLE webauthn_pending (
        challenge_hash TEXT PRIMARY KEY NOT NULL REFERENCES auth_challenges(challenge_hash) ON DELETE CASCADE,
        user_handle BLOB NOT NULL CHECK(length(user_handle) BETWEEN 1 AND 64)
    )''',
    '''CREATE TABLE identity_recovery_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        telegram_resume_at INTEGER NOT NULL CHECK(telegram_resume_at>=0)
    )''',
    '''CREATE TABLE identity_runtime_migrations (
        version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL
    )''',
)
CHECKSUM = hashlib.sha256(repr((COLUMNS, DDL)).encode()).hexdigest()


def migrate_identity_runtime(path: Path) -> bool:
    # Validate 05.1, but do not partially apply it when 05.2 is requested.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True) as check:
        if not check.execute("SELECT 1 FROM sqlite_master WHERE name='identity_schema_migrations'").fetchone():
            raise ValueError('Apply Foundation 05.1 first')
    migrate_identity_store(path)
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        _check_database(db)
        columns = {r[1]: r for r in db.execute('PRAGMA table_info(web_sessions)')}
        if not {'session_hash', 'blago_user_id', 'csrf', 'expires_at'} <= columns.keys():
            raise ValueError('Initialize Foundation WebApp first')
        expected_names = {'idx_session_public_id', 'telegram_exchanges', 'telegram_proofs', 'webauthn_pending', 'identity_recovery_state', 'identity_runtime_migrations'}
        existing = dict(db.execute('SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL'))
        applied = 'identity_runtime_migrations' in existing
        if applied:
            if db.execute('SELECT version, checksum FROM identity_runtime_migrations').fetchall() != [(VERSION, CHECKSUM)]:
                raise ValueError('Runtime migration checksum mismatch')
            if not expected_names <= existing.keys() or not COLUMNS.keys() <= columns.keys():
                raise ValueError('Runtime schema drift')
            for statement in DDL:
                name = statement.split()[3] if statement.startswith('CREATE UNIQUE') else statement.split()[2]
                if existing[name] != statement:
                    raise ValueError('Runtime SQL drift')
            for name, definition in COLUMNS.items():
                row = columns[name]
                expected_type = definition.split()[0]
                default = definition.split(' DEFAULT ',1)[1] if ' DEFAULT ' in definition else None
                if row[2] != expected_type or bool(row[3]) != ('NOT NULL' in definition) or row[4] != default:
                    raise ValueError('Runtime column drift')
            recovery = db.execute('SELECT singleton,telegram_resume_at FROM identity_recovery_state').fetchall()
            if len(recovery) != 1 or recovery[0][0] != 1 or not isinstance(recovery[0][1], int):
                raise ValueError('Runtime recovery state missing')
            db.commit()
            return False
        if COLUMNS.keys() & columns.keys() or expected_names & existing.keys():
            raise ValueError('Runtime schema collision')
        for name, definition in COLUMNS.items():
            db.execute(f'ALTER TABLE web_sessions ADD COLUMN {name} {definition}')
        # Preserve old absolute deadline; do not grant fresh-auth privileges.
        db.execute('''UPDATE web_sessions SET public_id=lower(hex(randomblob(16))),
            created_at=max(0, CAST(strftime('%s', expires_at) AS INTEGER)-604800),
            last_seen_at=CAST(strftime('%s', 'now') AS INTEGER)''')
        for statement in DDL:
            db.execute(statement)
        db.execute('INSERT INTO identity_recovery_state VALUES (1,0)')
        db.execute('INSERT INTO identity_runtime_migrations VALUES (?,?)', (VERSION, CHECKSUM))
        _check_database(db)
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def invalidate_restored_authentication(path: Path) -> None:
    """Offline restore step: old bearer sessions/challenges must not resurrect.

    Telegram proofs issued after the backup may be absent, so quarantine exchanges
    for their full maximum validity. Credential revocations require operator review.
    """
    migrate_identity_runtime(path)
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True, timeout=30)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        for table in ('web_sessions','auth_challenges','telegram_exchanges'):
            db.execute(f'DELETE FROM {table}')
        db.execute('UPDATE identity_recovery_state SET telegram_resume_at=? WHERE singleton=1', (int(time.time())+3661,))
        _check_database(db)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--invalidate-restored-authentication', action='store_true', help='Offline restored copy only; revoke sessions/challenges and quarantine Telegram exchanges')
    args = parser.parse_args()
    if args.invalidate_restored_authentication:
        invalidate_restored_authentication(args.database)
        print('restored authentication invalidated; review credential revocations before reopening')
    else:
        print('applied' if migrate_identity_runtime(args.database) else 'already applied')


if __name__ == '__main__':
    main()
