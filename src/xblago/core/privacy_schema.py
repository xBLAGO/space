"""Explicit offline 05.3 migration. No production or startup migration."""
import hashlib
import sqlite3
from pathlib import Path

from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.identity_schema import _check_database
from xblago.core.security_journal import SecurityJournal


VERSION = 'foundation05_privacy_003'
DDL = [
    '''CREATE TABLE privacy_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), journal_id TEXT NOT NULL,
        applied_seq INTEGER NOT NULL CHECK(applied_seq>=0), applied_head TEXT NOT NULL
    )''',
    '''CREATE TABLE privacy_outbox (
        event_id TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload))
    )''',
    '''CREATE TABLE privacy_aliases (
        source_uid TEXT PRIMARY KEY NOT NULL REFERENCES users(blago_user_id),
        target_uid TEXT NOT NULL REFERENCES users(blago_user_id), CHECK(source_uid<>target_uid)
    )''',
    '''CREATE TABLE privacy_delete_challenges (
        challenge_hash TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        session_hash TEXT NOT NULL, expires_at INTEGER NOT NULL
    )''',
    '''CREATE TABLE privacy_schema_migrations (
        version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL
    )''',
]


def trigger(name, table, operation, kind, owner, target, state="''", condition='1'):
    DDL.append(f'''CREATE TRIGGER {name} {operation} ON {table}
        WHEN blago_replaying()=0 AND ({condition}) BEGIN
        INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),
            json_object('kind','{kind}','owner',{owner},'target',{target},'state',{state},
                        'recorded_at',CAST(strftime('%s','now') AS INTEGER))); END''')


user = "blago_tag('user',OLD.blago_user_id)"
trigger('privacy_user_status','users','AFTER UPDATE OF status','user_status',user,user,'NEW.status',
        "OLD.status<>NEW.status AND NEW.status IN ('merged','blocked','deleted')")
trigger('privacy_session_delete','web_sessions','BEFORE DELETE','session',user,"blago_tag('session',OLD.session_hash)")
password = "blago_tag('password',OLD.blago_user_id,OLD.password_hash,OLD.salt)"
trigger('privacy_password_delete','web_credentials','BEFORE DELETE','password',user,password)
trigger('privacy_password_change','web_credentials','BEFORE UPDATE OF password_hash,salt','password',user,password,
        condition='OLD.password_hash<>NEW.password_hash OR OLD.salt<>NEW.salt')
key = "blago_tag('passkey',OLD.credential_id)"
trigger('privacy_passkey_delete','passkey_credentials','BEFORE DELETE','passkey',user,key)
trigger('privacy_passkey_revoke','passkey_credentials','AFTER UPDATE OF revoked_at','passkey',user,key,
        condition='OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL')
channel = "blago_tag('channel',OLD.channel,OLD.channel_user_id)"
trigger('privacy_channel_delete','channel_links','BEFORE DELETE','channel_revoke',user,channel)
trigger('privacy_channel_move','channel_links','BEFORE UPDATE OF blago_user_id','channel_revoke',user,channel,
        condition='OLD.blago_user_id<>NEW.blago_user_id')
trigger('privacy_channel_insert','channel_links','AFTER INSERT','channel_grant',
        "blago_tag('user',NEW.blago_user_id)","blago_tag('channel',NEW.channel,NEW.channel_user_id)")
trigger('privacy_channel_moved','channel_links','AFTER UPDATE OF blago_user_id','channel_grant',
        "blago_tag('user',NEW.blago_user_id)","blago_tag('channel',NEW.channel,NEW.channel_user_id)",
        condition='OLD.blago_user_id<>NEW.blago_user_id')
trigger('privacy_memory_delete','memory_facts','BEFORE DELETE','memory_legacy',user,
        "blago_tag('memory_legacy',OLD.blago_user_id,OLD.key,OLD.value_json,OLD.updated_at)")
trigger('privacy_semantic_delete','semantic_memory','BEFORE DELETE','memory_semantic',user,"blago_tag('memory_semantic',OLD.id)")
trigger('privacy_history_delete','dialogue_turns','BEFORE DELETE','history',user,
        "blago_tag('history',OLD.id,OLD.blago_user_id,OLD.created_at,OLD.role,OLD.text)")
trigger('privacy_alias_insert','privacy_aliases','AFTER INSERT','alias',
        "blago_tag('user',NEW.source_uid)","blago_tag('user',NEW.target_uid)")
trigger('privacy_alias_move','privacy_aliases','AFTER UPDATE OF target_uid','alias',
        "blago_tag('user',NEW.source_uid)","blago_tag('user',NEW.target_uid)",condition='OLD.target_uid<>NEW.target_uid')
trigger('privacy_consent_withdrawal','consent_records','AFTER INSERT','consent_withdrawal',
        "blago_tag('user',NEW.blago_user_id)","blago_tag('consent',NEW.consent_id)",'NEW.purpose',condition="NEW.decision='withdrawn'")
trigger('privacy_consent_change','consent_records','AFTER UPDATE OF decision','consent_withdrawal',
        "blago_tag('user',NEW.blago_user_id)","blago_tag('consent',NEW.consent_id)",'NEW.purpose',condition="OLD.decision<>NEW.decision AND NEW.decision='withdrawn'")
trigger('privacy_consent_delete','consent_records','BEFORE DELETE','consent_withdrawal',user,
        "blago_tag('consent',OLD.consent_id)",'OLD.purpose')
# A request already in flight before deletion must not repopulate personal data.
# These guards change ownership validity, never economic sums/status transitions.
from xblago.core.privacy_control import OWNED, ECONOMIC
guards = {table:('target_user_id',) if table=='account_link_codes' else ('blago_user_id',) for table in OWNED}
guards.update(ECONOMIC)
for table,columns in guards.items():
    condition = ' OR '.join(f"EXISTS(SELECT 1 FROM users WHERE blago_user_id=NEW.{column} AND status='deleted')" for column in columns)
    for operation in ('INSERT','UPDATE'):
        DDL.append(f'''CREATE TRIGGER privacy_guard_{table}_{operation.lower()} BEFORE {operation} ON {table}
            WHEN blago_replaying()=0 AND ({condition}) BEGIN
            SELECT RAISE(ABORT,'Account deleted; stale write rejected'); END''')
DDL.extend((
    '''CREATE TRIGGER privacy_deleted_status BEFORE UPDATE OF status ON users
        WHEN blago_replaying()=0 AND OLD.status='deleted' AND NEW.status<>'deleted' BEGIN
        SELECT RAISE(ABORT,'Account deleted; stale write rejected'); END''',
    '''CREATE TRIGGER privacy_keep_tombstone BEFORE DELETE ON users
        WHEN blago_replaying()=0 AND OLD.status='deleted' BEGIN
        SELECT RAISE(ABORT,'Deletion tombstone cannot be removed while backups may exist'); END''',
))
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def migrate_privacy(path: Path, journal_directory: Path) -> bool:
    migrate_identity_runtime(path)
    journal = SecurityJournal(journal_directory)
    rows, chain = journal.read()
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=30)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        _check_database(db)
        existing = dict(db.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
        with sqlite3.connect(':memory:') as ref:
            # Triggers require parent tables; schema copies contain DDL, never data.
            for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                if not name.startswith('privacy_'):
                    ref.execute(sql)
            for statement in DDL:
                ref.execute(statement)
            expected = {n:s for n,s in ref.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL') if n.startswith('privacy_')}
        if set(expected) & set(existing):
            if any(existing.get(name) != sql for name,sql in expected.items()):
                raise ValueError('Privacy schema collision/drift')
            if db.execute('SELECT version,checksum FROM privacy_schema_migrations').fetchall() != [(VERSION,CHECKSUM)]:
                raise ValueError('Privacy migration checksum mismatch')
            state = db.execute('SELECT journal_id,applied_seq,applied_head FROM privacy_state WHERE singleton=1').fetchone()
            if not state or state[0] != journal.journal_id or type(state[1]) is not int or state[1]<0 or state[1] >= len(chain) or chain[state[1]] != state[2]:
                raise ValueError('Privacy journal binding/rollback mismatch')
            db.commit()
            return False
        for statement in DDL:
            db.execute(statement)
        # Start at zero so restore of pre-05.3 snapshots replays every critical intent.
        db.execute('INSERT INTO privacy_state VALUES (1,?,?,?)',(journal.journal_id,0,'0'*64))
        db.execute('INSERT INTO privacy_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
        _check_database(db)
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
