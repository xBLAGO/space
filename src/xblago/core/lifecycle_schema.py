"""Explicit additive synthetic retention policy, never a startup migration."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from xblago.core.security_journal import JournalUnavailable,SecurityJournal,canonical

VERSION='foundation_privacy_lifecycle_001'
POLICY={'id':'synthetic-lifecycle-1','history_days':30,'candidate_days':7,'superseded_days':30,'legacy_record_days':90,
        'help_idle_days':30,'help_terminal_days':90,'help_max_days':180,'receipt_seconds':86400,
        'revoked_credential_days':90,'generation_seconds':604800,'pending_generation_seconds':300,'backup_seconds':604800,
        'confirmed_memory':'until user correction/deletion or account deletion',
        'control_tombstones':'retain until all relevant backups/copies are certified retired; no automatic pruning',
        'real_user_approval':'NOT APPROVED'}
ROWS={'blago_needs':'id','blago_resources':'id','blago_assets':'id','blago_resource_offers':'id',
      'passkey_credentials':'credential_id','user_devices':'device_id','semantic_memory':'id','activity_events':'event_id','security_signals':'signal_id','context_generations':'generation_id'}
DDL=[
 'CREATE TABLE lifecycle_schema_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
 'CREATE TABLE lifecycle_policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1),policy_json TEXT NOT NULL)',
 'CREATE TABLE lifecycle_holds (target_tag TEXT PRIMARY KEY,kind TEXT NOT NULL)',
 'CREATE TABLE lifecycle_history_pairs (user_turn INTEGER PRIMARY KEY REFERENCES dialogue_turns(id) ON DELETE CASCADE,assistant_turn INTEGER NOT NULL UNIQUE REFERENCES dialogue_turns(id) ON DELETE CASCADE)',
 'CREATE TABLE lifecycle_generation_times (generation_id TEXT PRIMARY KEY REFERENCES context_generations(generation_id) ON DELETE CASCADE,created_at INTEGER NOT NULL CHECK(created_at>=0))',
 "CREATE TABLE lifecycle_help_evidence (target_tag TEXT PRIMARY KEY,outcome TEXT NOT NULL CHECK(outcome IN ('mutual_confirmation_recorded','disputed','withdrawn','incomplete','privacy_removed')),event_count INTEGER NOT NULL CHECK(event_count>=0),protocol TEXT NOT NULL CHECK(protocol='help01'))",
 "CREATE TABLE lifecycle_backups (backup_id TEXT PRIMARY KEY,filename TEXT NOT NULL,content_hash TEXT NOT NULL,expires_at INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('ACTIVE','RETIRED')))",
 "CREATE TRIGGER lifecycle_generation_time AFTER INSERT ON context_generations BEGIN INSERT INTO lifecycle_generation_times VALUES (NEW.generation_id,CAST(strftime('%s','now') AS INTEGER)); END",
]
for table,key in ROWS.items():
    target=f"blago_tag('lifecycle-row','{table}',OLD.{key})"
    DDL.append(f"""CREATE TRIGGER lifecycle_delete_{table} BEFORE DELETE ON {table} WHEN blago_replaying()=0 BEGIN
    INSERT OR IGNORE INTO lifecycle_holds VALUES ({target},'{table}');
    INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object('kind','lifecycle_delete','owner','',
      'target',{target},'state','{table}','recorded_at',0)); END""")
    for op in ('INSERT','UPDATE'):
        DDL.append(f"""CREATE TRIGGER lifecycle_guard_{table}_{op.lower()} BEFORE {op} ON {table}
        WHEN blago_replaying()=0 AND EXISTS(SELECT 1 FROM lifecycle_holds WHERE target_tag=blago_tag('lifecycle-row','{table}',NEW.{key}))
        BEGIN SELECT RAISE(ABORT,'Retired data; stale write rejected'); END""")
DDL.extend([
 """CREATE TRIGGER lifecycle_activity_consent BEFORE INSERT ON activity_events WHEN blago_replaying()=0 AND
 COALESCE((SELECT decision FROM consent_records WHERE blago_user_id=NEW.blago_user_id AND purpose='optional_activity' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'
 BEGIN SELECT RAISE(ABORT,'Optional activity consent required'); END""",
 """CREATE TRIGGER lifecycle_label_consent BEFORE UPDATE OF label,blago_user_id ON user_devices WHEN blago_replaying()=0 AND NEW.label IS NOT NULL AND
 COALESCE((SELECT decision FROM consent_records WHERE blago_user_id=NEW.blago_user_id AND purpose='device_label' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'
 BEGIN SELECT RAISE(ABORT,'Device label consent required'); END""",
 """CREATE TRIGGER lifecycle_label_insert BEFORE INSERT ON user_devices WHEN blago_replaying()=0 AND NEW.label IS NOT NULL AND
 COALESCE((SELECT decision FROM consent_records WHERE blago_user_id=NEW.blago_user_id AND purpose='device_label' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'
 BEGIN SELECT RAISE(ABORT,'Device label consent required'); END""",
])
DDL.append("""CREATE TRIGGER lifecycle_activity_update BEFORE UPDATE ON activity_events WHEN blago_replaying()=0 AND
 COALESCE((SELECT decision FROM consent_records WHERE blago_user_id=NEW.blago_user_id AND purpose='optional_activity' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'
 BEGIN SELECT RAISE(ABORT,'Optional activity consent required'); END""")
DDL.append("""CREATE TRIGGER lifecycle_update_retire BEFORE DELETE ON events WHEN blago_replaying()=0 BEGIN
 INSERT OR IGNORE INTO lifecycle_holds VALUES (blago_tag('lifecycle-update',OLD.channel,OLD.external_update_id),'consumed-update');
 INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object('kind','lifecycle_update','owner','',
 'target',blago_tag('lifecycle-update',OLD.channel,OLD.external_update_id),'state','consumed','recorded_at',0)); END""")
for op in ('INSERT','UPDATE'):
    DDL.append(f"""CREATE TRIGGER lifecycle_update_guard_{op.lower()} BEFORE {op} ON events WHEN blago_replaying()=0 AND
 EXISTS(SELECT 1 FROM lifecycle_holds WHERE target_tag=blago_tag('lifecycle-update',NEW.channel,NEW.external_update_id) AND kind='consumed-update')
 BEGIN SELECT RAISE(ABORT,'Retired update; replay rejected'); END""")
DDL=tuple(DDL);OBJECTS={s.split()[2] for s in DDL};TABLES={s.split()[2] for s in DDL if s.startswith('CREATE TABLE')}
CHECKSUM=hashlib.sha256(('\n'.join(DDL)+canonical(POLICY)).encode()).hexdigest()
def required():return os.getenv('XBLAGO_LIFECYCLE_REQUIRED','').lower() in ('true','1','yes')
def present(db):return db.execute("SELECT 1 FROM sqlite_master WHERE name='lifecycle_schema_migrations'").fetchone() is not None

def validate(db,mandatory=False):
    objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'lifecycle_%' AND sql IS NOT NULL"))
    if not objects:
        if mandatory:raise JournalUnavailable('Explicit lifecycle migration required')
        return False
    if set(objects)!=OBJECTS or any(objects.get(s.split()[2])!=s for s in DDL):raise JournalUnavailable('Lifecycle schema drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM lifecycle_schema_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('Lifecycle migration mismatch')
    policy=db.execute('SELECT policy_json FROM lifecycle_policy WHERE singleton=1').fetchone()
    if not policy or policy[0]!=canonical(POLICY):raise JournalUnavailable('Lifecycle policy drift')
    return True

def migrate_lifecycle(path):
    from xblago.core.integration import enabled,schema_shape
    from xblago.core import context_schema,help_schema
    from xblago.core.context_schema import validate as context_validate,OBJECTS as CONTEXT_OBJECTS
    from xblago.core.sqlite_safety import require_safe_sqlite
    from xblago.core.help_schema import present as has_help
    if not enabled():raise ValueError('Synthetic mode required')
    path=Path(path);require_safe_sqlite()
    if not path.is_file():raise ValueError('Existing offline copy required')
    journal=SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    if journal.witness_directory is None:raise JournalUnavailable('Retained witness required')
    journal.read();db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True);db.row_factory=sqlite3.Row
    db.create_function('blago_tag',-1,journal.tag,deterministic=True);db.create_function('blago_replaying',0,lambda:1)
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        had_context=context_validate(db)
        expected=json.loads(Path(__file__).with_name('integration_help_schema.json' if has_help(db) else 'integration_schema.json').read_text())
        if {k:v for k,v in schema_shape(db).items() if k not in OBJECTS|CONTEXT_OBJECTS}!=expected:raise ValueError('Lifecycle requires frozen baseline')
        # Certified old baseline may predate Help/context while the retained
        # journal already contains their denials. Install canonical definitions
        # together offline, then replay; never initialize a new lineage.
        if not has_help(db):
            for sql in help_schema.DDL:db.execute(sql)
            db.execute('INSERT INTO help_schema_migrations VALUES (?,?)',(help_schema.VERSION,help_schema.CHECKSUM))
        if not had_context:
            for sql in context_schema.DDL:db.execute(sql)
            db.execute('INSERT INTO context_schema_migrations VALUES (?,?)',(context_schema.VERSION,context_schema.CHECKSUM))
            db.execute('INSERT INTO context_versions SELECT blago_user_id,0,lower(hex(randomblob(16))) FROM users')
        changed=not validate(db)
        if changed:
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO lifecycle_schema_migrations VALUES (?,?)',(VERSION,CHECKSUM))
            db.execute('INSERT INTO lifecycle_policy VALUES (1,?)',(canonical(POLICY),))
            db.execute("INSERT INTO lifecycle_generation_times SELECT generation_id,CAST(strftime('%s','now') AS INTEGER) FROM context_generations")
        from xblago.core.privacy_control import PrivacyControl
        c=PrivacyControl.open(db);c.reconcile()
        if not had_context:db.execute('UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16)))')
        c.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Lifecycle FK failure')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Lifecycle integrity failure')
        db.commit();return changed
    except BaseException:db.rollback();raise
    finally:db.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database',type=Path)
    print('applied' if migrate_lifecycle(p.parse_args().database) else 'unchanged')

def check_denials(db,control):
    if not present(db):return
    for row in control.rows:
        event=json.loads(row[2]);kind=event['kind'];target=event['target']
        if kind=='lifecycle_delete':
            table=event['state']
            if table not in ROWS or not db.execute('SELECT 1 FROM lifecycle_holds WHERE target_tag=? AND kind=?',(target,table)).fetchone():raise JournalUnavailable('Retained lifecycle denial missing')
            if db.execute(f"SELECT 1 FROM {table} WHERE blago_tag('lifecycle-row','{table}',{ROWS[table]})=?",(target,)).fetchone():raise JournalUnavailable('Retired data reappeared')
        elif kind=='lifecycle_help':
            expected=json.loads(event['state']);actual=db.execute('SELECT outcome,event_count,protocol FROM lifecycle_help_evidence WHERE target_tag=?',(target,)).fetchone()
            if not actual or dict(actual)!=expected or not db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(target,)).fetchone():raise JournalUnavailable('Minimal Help evidence mismatch')
        elif kind=='lifecycle_backup':
            if not db.execute("SELECT 1 FROM lifecycle_holds WHERE target_tag=? AND kind='backup'",(target,)).fetchone():raise JournalUnavailable('Backup retirement missing')
        elif kind=='lifecycle_update':
            if not db.execute("SELECT 1 FROM lifecycle_holds WHERE target_tag=? AND kind='consumed-update'",(target,)).fetchone():raise JournalUnavailable('Consumed update retirement missing')
            if db.execute("SELECT 1 FROM events WHERE blago_tag('lifecycle-update',channel,external_update_id)=?",(target,)).fetchone():raise JournalUnavailable('Retired update reappeared')
