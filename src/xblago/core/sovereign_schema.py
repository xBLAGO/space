"""Explicit additive 07.1 migration; no startup migration or account promotion."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
from pathlib import Path

from xblago.core.security_journal import JournalUnavailable

VERSION = 'foundation07_identity_core_001'


def enabled():
    return os.getenv('XBLAGO_IDENTITY_CORE_ENABLED','').lower() in ('1','true','yes')


DDL = [
    '''CREATE TABLE sovereign_schema_migrations (version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL)''',
    '''CREATE TABLE sovereign_identities (
        identity_id TEXT PRIMARY KEY NOT NULL CHECK(length(identity_id)=64 AND identity_id NOT GLOB '*[^0-9a-f]*'),
        blago_user_id TEXT UNIQUE NOT NULL REFERENCES users(blago_user_id),
        status TEXT NOT NULL CHECK(status IN ('pending','active','suspended','revoked')),
        version INTEGER NOT NULL CHECK(version>=1), created_at INTEGER NOT NULL CHECK(created_at>=0),
        UNIQUE(identity_id,blago_user_id)
    )''',
    '''CREATE TABLE identity_controllers (
        controller_id TEXT PRIMARY KEY NOT NULL,
        identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        algorithm TEXT NOT NULL CHECK(algorithm='Ed25519'),
        public_key BLOB NOT NULL CHECK(typeof(public_key)='blob' AND length(public_key)=32),
        role TEXT NOT NULL CHECK(role IN ('owner','proof')),
        status TEXT NOT NULL CHECK(status IN ('active','revoked')),
        created_at INTEGER NOT NULL, revoked_at INTEGER,
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE,
        UNIQUE(identity_id,public_key), UNIQUE(identity_id,controller_id),
        CHECK((status='active' AND revoked_at IS NULL) OR (status='revoked' AND revoked_at>=created_at))
    )''',
    '''CREATE TABLE identity_wallet_links (
        link_id TEXT PRIMARY KEY NOT NULL, identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        chain TEXT NOT NULL CHECK(length(chain) BETWEEN 1 AND 40),
        network TEXT NOT NULL CHECK(network IN ('offline','localnet','devnet','testnet')),
        address TEXT NOT NULL CHECK(length(address) BETWEEN 1 AND 160),
        role TEXT NOT NULL CHECK(role IN ('receive','spend','recovery','prove')),
        status TEXT NOT NULL CHECK(status IN ('unverified','revoked')), created_at INTEGER NOT NULL, revoked_at INTEGER,
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE,
        UNIQUE(identity_id,chain,network,address,role)
    )''',
    '''CREATE TABLE identity_credentials (
        reference_id TEXT PRIMARY KEY NOT NULL, identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind='passkey_reference'),
        passkey_credential_id BLOB NOT NULL REFERENCES passkey_credentials(credential_id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE,
        UNIQUE(identity_id,passkey_credential_id)
    )''',
    '''CREATE TABLE identity_events (
        event_id TEXT PRIMARY KEY NOT NULL, identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('created','bootstrap','add','rotate','revoke','prove','withdraw')),
        version INTEGER NOT NULL CHECK(version>=1), recorded_at INTEGER NOT NULL,
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE
    )''',
    '''CREATE TABLE identity_consents (
        consent_id TEXT PRIMARY KEY NOT NULL, identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK(purpose='isolated_identity_test'), policy_version TEXT NOT NULL CHECK(policy_version='07.1-test-v1'),
        decision TEXT NOT NULL CHECK(decision IN ('granted','withdrawn')), recorded_at INTEGER NOT NULL,
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE
    )''',
    '''CREATE TABLE sovereign_challenges (
        challenge_hash TEXT PRIMARY KEY NOT NULL, identity_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
        session_hash TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('bootstrap','add','rotate','revoke','prove')),
        payload TEXT NOT NULL CHECK(json_valid(payload)), message BLOB NOT NULL,
        expected_version INTEGER NOT NULL CHECK(expected_version>=1),
        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+120),
        consumed_at INTEGER CHECK(consumed_at IS NULL OR consumed_at BETWEEN created_at AND expires_at),
        FOREIGN KEY(identity_id,blago_user_id) REFERENCES sovereign_identities(identity_id,blago_user_id) ON DELETE CASCADE
    )''',
    '''CREATE TABLE sovereign_recovery_holds (
        blago_user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(blago_user_id),
        reason TEXT NOT NULL CHECK(reason IN ('missing_identity','missing_controller')), recorded_at INTEGER NOT NULL
    )''',
    '''CREATE TRIGGER sovereign_credential_owner BEFORE INSERT ON identity_credentials
        WHEN NOT EXISTS(SELECT 1 FROM passkey_credentials WHERE credential_id=NEW.passkey_credential_id
            AND blago_user_id=NEW.blago_user_id AND revoked_at IS NULL)
        BEGIN SELECT RAISE(ABORT,'Passkey reference ownership mismatch'); END''',
    '''CREATE TRIGGER sovereign_credential_immutable BEFORE UPDATE ON identity_credentials
        BEGIN SELECT RAISE(ABORT,'Passkey reference is immutable'); END''',
    '''CREATE TRIGGER sovereign_identity_immutable BEFORE UPDATE OF identity_id,blago_user_id,created_at ON sovereign_identities
        BEGIN SELECT RAISE(ABORT,'Identity ownership is immutable'); END''',
    '''CREATE TRIGGER sovereign_controller_immutable BEFORE UPDATE OF controller_id,identity_id,blago_user_id,algorithm,public_key,role,created_at ON identity_controllers
        BEGIN SELECT RAISE(ABORT,'Controller binding is immutable; use rotation'); END''',
    '''CREATE TRIGGER sovereign_controller_no_reactivate BEFORE UPDATE OF status ON identity_controllers
        WHEN OLD.status='revoked' AND NEW.status<>'revoked'
        BEGIN SELECT RAISE(ABORT,'Revoked controller cannot be reactivated'); END''',
    '''CREATE TRIGGER sovereign_identity_no_reactivate BEFORE UPDATE OF status ON sovereign_identities
        WHEN OLD.status IN ('suspended','revoked') AND NEW.status<>OLD.status AND NEW.status<>'revoked'
        BEGIN SELECT RAISE(ABORT,'Identity recovery requires a separate protocol'); END''',
    '''CREATE TRIGGER sovereign_version_monotonic BEFORE UPDATE OF version ON sovereign_identities
        WHEN NEW.version<=OLD.version BEGIN SELECT RAISE(ABORT,'Identity version must increase'); END''',
]


def intent(name,table,operation,kind,owner,target,state="''",condition='1'):
    return f'''CREATE TRIGGER {name} {operation} ON {table}
        WHEN blago_replaying()=0 AND ({condition}) BEGIN
        INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object(
            'kind','{kind}','owner',blago_tag('user',{owner}),'target',{target},'state',{state},
            'recorded_at',CAST(strftime('%s','now') AS INTEGER))); END'''


IDENTITY_TAG="blago_tag('sovereign_identity',NEW.identity_id)"
CONTROLLER_TAG="blago_tag('sovereign_controller',NEW.controller_id,NEW.public_key,NEW.role)"
DDL += [
    intent('sovereign_identity_grant','sovereign_identities','AFTER INSERT','sovereign_identity',
           'NEW.blago_user_id',IDENTITY_TAG),
    intent('sovereign_controller_grant','identity_controllers','AFTER INSERT','sovereign_grant',
           'NEW.blago_user_id',IDENTITY_TAG,CONTROLLER_TAG),
    intent('sovereign_controller_revoke','identity_controllers','AFTER UPDATE OF status','sovereign_revoke',
           'NEW.blago_user_id',CONTROLLER_TAG,condition="OLD.status='active' AND NEW.status='revoked'"),
    intent('sovereign_controller_delete','identity_controllers','BEFORE DELETE','sovereign_revoke',
           'OLD.blago_user_id',"blago_tag('sovereign_controller',OLD.controller_id,OLD.public_key,OLD.role)"),
    intent('sovereign_revision','sovereign_identities','AFTER UPDATE OF version','sovereign_revision',
           'NEW.blago_user_id',IDENTITY_TAG,'CAST(NEW.version AS TEXT)'),
    intent('sovereign_challenge_used','sovereign_challenges','AFTER UPDATE OF consumed_at','sovereign_challenge',
           'NEW.blago_user_id',"blago_tag('sovereign_challenge',NEW.challenge_hash)",condition='OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL'),
    intent('sovereign_withdraw','identity_consents','AFTER INSERT','sovereign_withdraw',
           'NEW.blago_user_id',IDENTITY_TAG,condition="NEW.decision='withdrawn'"),
]
TABLES=('sovereign_identities','identity_controllers','identity_wallet_links','identity_credentials',
        'identity_events','identity_consents','sovereign_challenges','sovereign_recovery_holds')
for table in TABLES:
    for operation in ('INSERT','UPDATE'):
        DDL.append(f'''CREATE TRIGGER sovereign_guard_{table}_{operation.lower()} BEFORE {operation} ON {table}
            WHEN blago_replaying()=0 AND NOT EXISTS(SELECT 1 FROM users WHERE blago_user_id=NEW.blago_user_id AND status='active')
            BEGIN SELECT RAISE(ABORT,'Inactive sovereign owner'); END''')
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
NAMES=tuple(sql.split()[2] for sql in DDL)


def validate(db,required=False):
    actual=dict(db.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
    present=set(NAMES)&actual.keys()
    if not present:
        if required:
            raise JournalUnavailable('Explicit Foundation 07.1 migration required')
        return False
    if any(actual.get(name)!=sql for name,sql in zip(NAMES,DDL)):
        raise JournalUnavailable('Sovereign schema collision or drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM sovereign_schema_migrations')]!=[(VERSION,CHECKSUM)]:
        raise JournalUnavailable('Sovereign migration checksum mismatch')
    return True


def migrate_sovereign(path: Path, *, rollback_empty=False):
    from xblago.core.storage import SQLiteBlagoStore
    from xblago.core.privacy_control import PrivacyControl
    from xblago.core.pilot_schema import validate as validate_pilot
    from xblago.core.sqlite_safety import require_safe_sqlite
    require_safe_sqlite()
    if enabled() or not path.is_file():
        raise ValueError('Disable Identity Core and select an existing reviewed offline COPY')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True) as check:
        required={'users','identity_runtime_migrations','privacy_state','pilot_safety_state'}
        tables={row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required<=tables:
            raise ValueError('Initialize and review Foundation 05.4 first; no automatic foundational migration')
    store=SQLiteBlagoStore(path)
    with store._locked_transaction() as db:
        if [row[0] for row in db.execute('PRAGMA integrity_check')]!=['ok'] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Identity migration requires a consistent database')
        control=PrivacyControl.open(db)
        if control is None or control.journal.witness_directory is None:
            raise ValueError('Foundation 05.4 original journal and independent witness required')
        validate_pilot(db,required=True)
        exists=validate(db)
        if rollback_empty:
            if not exists:
                return False
            if any(db.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() for t in TABLES) or any(
                    '"kind":"sovereign_' in row[2] for row in control.rows):
                raise ValueError('Used identity schema cannot be downgraded while recovery evidence/backups exist')
            for sql,name in reversed(tuple(zip(DDL,NAMES))):
                db.execute(f'DROP {"TRIGGER" if sql.startswith("CREATE TRIGGER") else "TABLE"} {name}')
            return True
        if exists:
            return False
        for sql in DDL:
            db.execute(sql)
        db.execute('INSERT INTO sovereign_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Sovereign migration foreign key check failed')
    return True


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('--rollback-empty',action='store_true')
    args=parser.parse_args()
    print('changed' if migrate_sovereign(args.database,rollback_empty=args.rollback_empty) else 'unchanged')
