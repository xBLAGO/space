"""Explicit Help01 migration on an existing guarded OFFLINE synthetic copy."""
import hashlib
import os
import sqlite3
from pathlib import Path
from xblago.core.security_journal import SecurityJournal,JournalUnavailable
VERSION='foundation_help_001'
DDL=(
'''CREATE TABLE help_schema_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)''',
'''CREATE TABLE help_requests (id TEXT PRIMARY KEY,author_id TEXT REFERENCES users(blago_user_id),text TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('DRAFT','PUBLISHED','OFFERED','ACCEPTED','AWAITING_CONFIRMATION','COMPLETED','WITHDRAWN','DISPUTED')),version INTEGER NOT NULL CHECK(version>0),selected_offer TEXT,owner_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(owner_confirmed IN (0,1)),helper_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(helper_confirmed IN (0,1)),head_tag TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL)''',
'''CREATE TABLE help_audience (request_id TEXT NOT NULL REFERENCES help_requests(id),member_id TEXT NOT NULL REFERENCES users(blago_user_id),PRIMARY KEY(request_id,member_id))''',
'''CREATE TABLE help_offers (id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES help_requests(id),helper_id TEXT REFERENCES users(blago_user_id),note TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('OFFERED','ACCEPTED','REJECTED','WITHDRAWN')),UNIQUE(request_id,helper_id))''',
'''CREATE TABLE help_events (event_id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES help_requests(id),version INTEGER NOT NULL,action TEXT NOT NULL,actor_role TEXT NOT NULL CHECK(actor_role IN ('owner','helper')),state TEXT NOT NULL,occurred_at INTEGER NOT NULL,UNIQUE(request_id,version))''',
'''CREATE TABLE help_challenges (token_hash TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(blago_user_id),request_id TEXT NOT NULL REFERENCES help_requests(id),version INTEGER NOT NULL,action TEXT NOT NULL,args_json TEXT NOT NULL,expires_at INTEGER NOT NULL,receipt_json TEXT)''',
'''CREATE TABLE help_operations (actor_id TEXT NOT NULL REFERENCES users(blago_user_id),operation_key TEXT NOT NULL,body_hash TEXT NOT NULL,request_id TEXT NOT NULL REFERENCES help_requests(id),PRIMARY KEY(actor_id,operation_key))''',
'''CREATE TABLE help_holds (target_tag TEXT PRIMARY KEY,reason TEXT NOT NULL)''',
)
TABLES={s.split()[2] for s in DDL}
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
def enabled(): return os.getenv('XBLAGO_HELP_ENABLED','').lower() in ('true','1','yes')
def present(db):return db.execute("SELECT 1 FROM sqlite_master WHERE name='help_schema_migrations'").fetchone() is not None
def validate(db,required=False):
    objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'help_%' AND sql IS NOT NULL"))
    if not objects:
        if required:raise JournalUnavailable('Explicit Help migration required')
        return False
    if set(objects)!=TABLES or any(objects.get(s.split()[2])!=s for s in DDL):raise JournalUnavailable('Help schema drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM help_schema_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('Help migration ledger drift')
    return True

def migrate_help(path):
    from xblago.core.sqlite_safety import require_safe_sqlite
    from xblago.core.identity_schema import _check_database
    require_safe_sqlite();path=Path(path)
    if not path.is_file():raise ValueError('Existing offline copy required')
    journal=SecurityJournal(Path(os.environ.get('XBLAGO_SECURITY_JOURNAL_DIR','')))
    if journal.witness_directory is None:raise JournalUnavailable('Retained witness required')
    journal.read() # authenticate current controls before any DDL
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        from xblago.core.pilot_schema import validate as pilot_validate
        pilot_validate(db,required=True)
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Help baseline integrity check failed')
        from xblago.core.integration import schema_shape
        import json
        base={k:v for k,v in schema_shape(db).items() if k not in TABLES}
        expected=json.loads(Path(__file__).with_name('integration_schema.json').read_text())
        if base!=expected:raise ValueError('Help migration requires the frozen Integration 01 schema')
        changed=not validate(db)
        if changed:
            for statement in DDL:db.execute(statement)
            db.execute('INSERT INTO help_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
        from xblago.core.privacy_control import PrivacyControl
        control=PrivacyControl.open(db)
        if control is None:raise JournalUnavailable('Original privacy controls required')
        control.reconcile();control.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Help migration foreign keys')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Help integrity check failed')
        db.commit();return changed
    except BaseException:db.rollback();raise
    finally:db.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database',type=Path)
    print('applied' if migrate_help(p.parse_args().database) else 'unchanged')
