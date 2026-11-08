"""Additive, explicit offline context fencing migration; no startup DDL."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from xblago.core.security_journal import JournalUnavailable, SecurityJournal

VERSION='foundation_privacy_context_001'
TABLES={'context_schema_migrations','context_versions','context_generations'}
DDL=[
    'CREATE TABLE context_schema_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
    'CREATE TABLE context_versions (blago_user_id TEXT PRIMARY KEY REFERENCES users(blago_user_id),revision INTEGER NOT NULL CHECK(revision>=0),epoch TEXT NOT NULL CHECK(length(epoch)=32))',
    "CREATE TABLE context_generations (generation_id TEXT PRIMARY KEY,blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),revision INTEGER NOT NULL,epoch TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('PREPARED','REJECTED','COMMITTED')),channel TEXT NOT NULL,external_update_id TEXT,committed_revision INTEGER,committed_epoch TEXT,UNIQUE(channel,external_update_id))",
    """CREATE TRIGGER context_frontier AFTER UPDATE ON context_versions WHEN blago_replaying()=0 BEGIN
    INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object('kind','context_version',
    'owner',blago_tag('user',NEW.blago_user_id),'target',blago_tag('context',NEW.blago_user_id),
    'state',CAST(NEW.revision AS TEXT)||':'||NEW.epoch,'recorded_at',CAST(strftime('%s','now') AS INTEGER))); END""",
    """CREATE TRIGGER context_new_user AFTER INSERT ON users BEGIN
    INSERT INTO context_versions VALUES (NEW.blago_user_id,0,lower(hex(randomblob(16))));
    UPDATE context_versions SET revision=revision+1 WHERE blago_user_id=NEW.blago_user_id; END""",
]
# Every durable input and relevant authority change invalidates an in-flight answer.
for table in ('memory_facts','semantic_memory','dialogue_turns','profiles','channel_links','web_credentials','passkey_credentials','consent_records'):
    for operation in ('INSERT','UPDATE','DELETE'):
        refs=['OLD.blago_user_id','NEW.blago_user_id'] if operation=='UPDATE' else [('OLD' if operation=='DELETE' else 'NEW')+'.blago_user_id']
        when=''
        if table=='channel_links' and operation=='UPDATE':
            when=' WHEN '+ ' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in ('channel','channel_user_id','channel_chat_id','chat_type','blago_user_id','username','first_name','last_name'))
        DDL.append(f"CREATE TRIGGER context_{table}_{operation.lower()} AFTER {operation} ON {table}{when} BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id IN ({','.join(refs)}); END")
for operation in ('INSERT','DELETE'):
    ref=('OLD' if operation=='DELETE' else 'NEW')+'.blago_user_id'
    DDL.append(f'CREATE TRIGGER context_sessions_{operation.lower()} AFTER {operation} ON web_sessions BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id={ref}; END')
DDL.extend([
    "CREATE TRIGGER context_user_status AFTER UPDATE OF status ON users WHEN OLD.status<>NEW.status BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id=NEW.blago_user_id; END",
    "CREATE TRIGGER context_session_authority AFTER UPDATE OF blago_user_id,session_hash,auth_method,authenticated_at,expires_at,csrf ON web_sessions WHEN OLD.blago_user_id<>NEW.blago_user_id OR OLD.session_hash<>NEW.session_hash OR OLD.auth_method<>NEW.auth_method OR OLD.authenticated_at<>NEW.authenticated_at OR OLD.expires_at IS NOT NEW.expires_at OR OLD.csrf IS NOT NEW.csrf BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id IN (OLD.blago_user_id,NEW.blago_user_id); END",
])
DDL=tuple(DDL)
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
OBJECTS={s.split()[2] for s in DDL}

def required():return os.getenv('XBLAGO_CONTEXT_FENCING_REQUIRED','').lower() in ('true','1','yes')
def present(db):return db.execute("SELECT 1 FROM sqlite_master WHERE name='context_schema_migrations'").fetchone() is not None

def validate(db, *, mandatory=False):
    objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'context_%' AND sql IS NOT NULL"))
    if not objects:
        if mandatory:raise JournalUnavailable('Explicit context fencing migration required')
        return False
    if set(objects)!=OBJECTS or any(objects.get(s.split()[2])!=s for s in DDL):raise JournalUnavailable('Context schema drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM context_schema_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('Context ledger mismatch')
    return True

def apply_checkpoint(db,event,journal):
    validate(db,mandatory=True)
    uid=db.execute("SELECT blago_user_id FROM users WHERE blago_tag('user',blago_user_id)=?",(event['owner'],)).fetchone()
    revision,epoch=event['state'].split(':')
    if not revision.isdecimal() or len(revision)>18 or len(epoch)!=32 or any(x not in '0123456789abcdef' for x in epoch):raise ValueError('Invalid context frontier')
    if uid:
        if event['target']!=journal.tag('context',uid[0]):raise ValueError('Context owner mismatch')
        db.execute('UPDATE context_versions SET revision=?,epoch=? WHERE blago_user_id=?',(int(revision),epoch,uid[0]))
        db.execute("UPDATE context_generations SET state='REJECTED' WHERE blago_user_id=? AND state='PREPARED'",(uid[0],))

def check_frontiers(db,control):
    if not present(db):return
    if db.execute("SELECT 1 FROM users u LEFT JOIN context_versions v USING(blago_user_id) WHERE v.blago_user_id IS NULL LIMIT 1").fetchone():raise JournalUnavailable("Context version missing")
    latest={}
    for row in control.rows:
        event=json.loads(row[2])
        if event['kind']=='context_version':latest[event['owner']]=event
    for r in db.execute('SELECT * FROM context_versions'):
        event=latest.get(control.journal.tag('user',r['blago_user_id']))
        if not event or event['state']!=str(r['revision'])+':'+r['epoch']:raise JournalUnavailable('Context retained frontier mismatch')

def migrate_context(path):
    from xblago.core.sqlite_safety import require_safe_sqlite
    from xblago.core.integration import schema_shape
    from xblago.core.help_schema import present as has_help
    require_safe_sqlite();path=Path(path)
    if not path.is_file():raise ValueError('Existing offline synthetic copy required')
    journal=SecurityJournal(Path(os.environ.get('XBLAGO_SECURITY_JOURNAL_DIR','')))
    if journal.witness_directory is None:raise JournalUnavailable('Retained witness required')
    journal.read()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True);db.row_factory=sqlite3.Row
    replay=[True];db.create_function('blago_tag',-1,journal.tag,deterministic=True);db.create_function('blago_replaying',0,lambda:int(replay[0]))
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        expected=json.loads(Path(__file__).with_name('integration_help_schema.json' if has_help(db) else 'integration_schema.json').read_text())
        from xblago.core import lifecycle_schema
        lifecycle_schema.validate(db)
        if {k:v for k,v in schema_shape(db).items() if k not in OBJECTS|lifecycle_schema.OBJECTS}!=expected:raise ValueError('Context migration requires frozen Integration/Help baseline')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Context baseline integrity')
        changed=not validate(db)
        if changed:
            for statement in DDL:db.execute(statement)
            db.execute('INSERT INTO context_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
            db.execute('INSERT INTO context_versions SELECT blago_user_id,0,lower(hex(randomblob(16))) FROM users')
        from xblago.core.privacy_control import PrivacyControl
        control=PrivacyControl.open(db);control.reconcile()
        if changed:
            # Emit the reconciled baseline, never relabel a stale snapshot as revision 0.
            db.execute('UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16)))')
            # open() owns the UDF replay flag; it is false after reconciliation.
        control.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Context foreign keys')
        db.commit();return changed
    except BaseException:db.rollback();raise
    finally:db.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database',type=Path)
    print('applied' if migrate_context(p.parse_args().database) else 'unchanged')
