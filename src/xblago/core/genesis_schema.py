"""Explicit additive Foundation 08 migration. No startup or production migration."""
import hashlib
import os
import sqlite3
from pathlib import Path
from xblago.core.security_journal import JournalUnavailable

VERSION = 'foundation08_genesis_001'
TABLES = ('genesis_streams','genesis_events','genesis_previews','genesis_publications','genesis_receipts','genesis_holds')
DDL = [
'''CREATE TABLE genesis_migrations (version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL)''',
'''CREATE TABLE genesis_streams (stream_id TEXT PRIMARY KEY NOT NULL,
 blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
 kind TEXT NOT NULL CHECK(kind IN ('asset','need','proposal','argument')),
 author_alias TEXT NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(stream_id,blago_user_id))''',
'''CREATE TABLE genesis_events (event_id TEXT PRIMARY KEY NOT NULL,
 stream_id TEXT NOT NULL, blago_user_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>0),
 event_type TEXT NOT NULL CHECK(event_type IN ('asset.declared','need.declared','proposal.drafted','argument.drafted','proposal.published','argument.published','proposal.withdrawn','argument.withdrawn')),
 schema_version INTEGER NOT NULL CHECK(schema_version=1), payload TEXT NOT NULL CHECK(json_valid(payload)),
 previous_hash TEXT NOT NULL CHECK(length(previous_hash)=64), event_hash TEXT NOT NULL UNIQUE CHECK(length(event_hash)=64),
 recorded_at INTEGER NOT NULL, FOREIGN KEY(stream_id,blago_user_id) REFERENCES genesis_streams(stream_id,blago_user_id) ON DELETE CASCADE,
 UNIQUE(stream_id,sequence))''',
'''CREATE TABLE genesis_previews (blago_user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(blago_user_id),
 stream_id TEXT NOT NULL, draft_event TEXT NOT NULL REFERENCES genesis_events(event_id) ON DELETE CASCADE,
 reply TEXT NOT NULL, last_turn INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 FOREIGN KEY(stream_id,blago_user_id) REFERENCES genesis_streams(stream_id,blago_user_id) ON DELETE CASCADE)''',
'''CREATE TABLE genesis_publications (stream_id TEXT NOT NULL, blago_user_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0), event_id TEXT NOT NULL UNIQUE REFERENCES genesis_events(event_id) ON DELETE CASCADE,
 payload TEXT NOT NULL CHECK(json_valid(payload)), previous_hash TEXT NOT NULL CHECK(length(previous_hash)=64),
 public_hash TEXT NOT NULL CHECK(length(public_hash)=64), PRIMARY KEY(stream_id,revision),
 FOREIGN KEY(stream_id,blago_user_id) REFERENCES genesis_streams(stream_id,blago_user_id) ON DELETE CASCADE)''',
'''CREATE TABLE genesis_receipts (blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
 request_key TEXT NOT NULL, input_hash TEXT NOT NULL, reply TEXT NOT NULL,
 PRIMARY KEY(blago_user_id,request_key))''',
'''CREATE TABLE genesis_holds (blago_user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(blago_user_id),
 reason TEXT NOT NULL CHECK(reason='missing_event'))''',
'''CREATE TRIGGER genesis_event_immutable BEFORE UPDATE ON genesis_events
 BEGIN SELECT RAISE(ABORT,'Genesis events are immutable'); END''',
'''CREATE TRIGGER genesis_stream_immutable BEFORE UPDATE ON genesis_streams
 BEGIN SELECT RAISE(ABORT,'Genesis streams are immutable'); END''',
'''CREATE TRIGGER genesis_public_immutable BEFORE UPDATE ON genesis_publications
 BEGIN SELECT RAISE(ABORT,'Published versions are immutable'); END''',
'''CREATE TRIGGER genesis_event_kind BEFORE INSERT ON genesis_events
 WHEN NOT EXISTS(SELECT 1 FROM genesis_streams WHERE stream_id=NEW.stream_id AND blago_user_id=NEW.blago_user_id
 AND kind=substr(NEW.event_type,1,instr(NEW.event_type,'.')-1))
 BEGIN SELECT RAISE(ABORT,'Genesis event kind mismatch'); END''',
'''CREATE TRIGGER genesis_event_intent AFTER INSERT ON genesis_events WHEN blago_replaying()=0
 BEGIN INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(32))),json_object('kind','genesis_event',
 'owner',blago_tag('user',NEW.blago_user_id),'target',blago_tag('genesis-event',NEW.event_id,NEW.event_hash),'state','','recorded_at',NEW.recorded_at)); END''',
]
for table in TABLES:
    for action in ('INSERT','UPDATE'):
        DDL.append(f'''CREATE TRIGGER genesis_active_{table}_{action.lower()} BEFORE {action} ON {table}
 WHEN NOT EXISTS(SELECT 1 FROM users WHERE blago_user_id=NEW.blago_user_id AND status='active')
 BEGIN SELECT RAISE(ABORT,'Genesis owner is inactive'); END''')
NAMES = tuple(sql.split()[2] for sql in DDL)
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()


def enabled():
    return os.getenv('XBLAGO_GENESIS_ENABLED','').lower() in ('1','true','yes')


def validate(db, required=False):
    actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'genesis_%' AND sql IS NOT NULL"))
    if not actual:
        if required: raise JournalUnavailable('Explicit Genesis migration required')
        return False
    if any(actual.get(name)!=sql for name,sql in zip(NAMES,DDL)):
        raise JournalUnavailable('Genesis schema drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM genesis_migrations')] != [(VERSION,CHECKSUM)]:
        raise JournalUnavailable('Genesis migration mismatch')
    return True


def migrate_genesis(path: Path):
    from xblago.core.sqlite_safety import require_safe_sqlite
    from xblago.core.storage import SQLiteBlagoStore
    from xblago.core.privacy_control import PrivacyControl
    from xblago.core.pilot_schema import validate as pilot_validate
    require_safe_sqlite()
    if enabled() or not path.is_file(): raise ValueError('Disable Genesis and select a reviewed offline COPY')
    # Never initialise missing foundational schemas implicitly.
    with sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_safety_state'").fetchone():
            raise ValueError('Foundation 05.4 is required')
    with SQLiteBlagoStore(path)._locked_transaction() as db:
        control = PrivacyControl.open(db)
        if control is None or control.journal.witness_directory is None: raise ValueError('Original journal and witness required')
        pilot_validate(db,required=True)
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Inconsistent migration input')
        if validate(db): return False
        for sql in DDL: db.execute(sql)
        db.execute('INSERT INTO genesis_migrations VALUES (?,?)',(VERSION,CHECKSUM))
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Genesis FK failure')
    return True


def replay(db, event, uid):
    if not validate(db): raise JournalUnavailable('Old backup lacks Genesis schema; no automatic baseline promotion')
    if uid and db.execute("SELECT 1 FROM users WHERE blago_user_id=? AND status='active'",(uid,)).fetchone():
        if not db.execute("SELECT 1 FROM genesis_events WHERE blago_user_id=? AND blago_tag('genesis-event',event_id,event_hash)=?",(uid,event['target'])).fetchone():
            db.execute("INSERT OR IGNORE INTO genesis_holds VALUES (?,'missing_event')",(uid,))


def purge(db, uid):
    if validate(db):
        db.execute('DELETE FROM genesis_receipts WHERE blago_user_id=?',(uid,))
        db.execute('DELETE FROM genesis_holds WHERE blago_user_id=?',(uid,))
        db.execute('DELETE FROM genesis_streams WHERE blago_user_id=?',(uid,))


def clear_transient(db, uid):
    if validate(db):
        for row in db.execute('WITH RECURSIVE family(uid) AS (SELECT ? UNION SELECT a.source_uid FROM privacy_aliases a JOIN family f ON a.target_uid=f.uid) SELECT uid FROM family',(uid,)).fetchall():
            db.execute('DELETE FROM genesis_previews WHERE blago_user_id=?',(row[0],))
            db.execute('DELETE FROM genesis_receipts WHERE blago_user_id=?',(row[0],))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('database',type=Path)
    print('changed' if migrate_genesis(p.parse_args().database) else 'unchanged')
