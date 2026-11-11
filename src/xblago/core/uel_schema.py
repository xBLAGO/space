"""UEL0.2 optional explicit local synthetic migration, no startup DDL/import."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from xblago.core.security_journal import SecurityJournal,JournalUnavailable

VERSION='uel_local_002'
DDL=(
 'CREATE TABLE uel_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
 'CREATE TABLE uel_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1),journal_id TEXT NOT NULL)',
 "CREATE TABLE uel_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1),sequence INTEGER NOT NULL CHECK(sequence>=0),head TEXT NOT NULL CHECK(length(head)=64))",
 "CREATE TABLE uel_events (sequence INTEGER PRIMARY KEY CHECK(sequence>0),source_kind TEXT NOT NULL CHECK(source_kind IN ('ASSET','HELP')),source_ref TEXT NOT NULL CHECK(length(source_ref)=64),object_ref TEXT NOT NULL CHECK(length(object_ref)=64),source_version INTEGER NOT NULL CHECK(source_version>0),phase TEXT NOT NULL CHECK(phase IN ('OBSERVED','RETIRED')),coverage TEXT NOT NULL CHECK(coverage IN ('FROM_ORIGIN','BASELINE_CURRENT')),previous TEXT NOT NULL CHECK(length(previous)=64),causal_ref TEXT NOT NULL CHECK(length(causal_ref)=64),payload_ref TEXT,source_state TEXT NOT NULL CHECK(length(source_state)=64),record_ref TEXT NOT NULL UNIQUE CHECK(length(record_ref)=64),UNIQUE(source_kind,source_ref),UNIQUE(source_kind,object_ref,source_version,phase))",
 "CREATE TRIGGER uel_append_only_insert BEFORE INSERT ON uel_events WHEN EXISTS(SELECT 1 FROM uel_events WHERE sequence=NEW.sequence OR record_ref=NEW.record_ref OR (source_kind=NEW.source_kind AND source_ref=NEW.source_ref) OR (source_kind=NEW.source_kind AND object_ref=NEW.object_ref AND source_version=NEW.source_version AND phase=NEW.phase)) BEGIN SELECT RAISE(ABORT,'UEL headers are append-only'); END",
 "CREATE TRIGGER uel_append_only_update BEFORE UPDATE ON uel_events BEGIN SELECT RAISE(ABORT,'UEL headers are append-only'); END",
 "CREATE TRIGGER uel_append_only_delete BEFORE DELETE ON uel_events BEGIN SELECT RAISE(ABORT,'UEL headers are append-only'); END",
)
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
OBJECTS={s.split()[2] for s in DDL}
TABLES={s.split()[2] for s in DDL if s.startswith('CREATE TABLE')}


def enabled():return os.getenv('XBLAGO_UEL_LOCAL_ENABLED','').lower() in ('true','1','yes')


def require_synthetic():
    required = ('XBLAGO_PILOT_MODE', 'XBLAGO_REQUIRE_SECURE_IDENTITY', 'XBLAGO_REQUIRE_PRIVACY_JOURNAL')
    if (os.getenv('XBLAGO_INTEGRATION_MODE') != 'synthetic'
            or os.getenv('XBLAGO_LLM_PROVIDER', 'local') != 'local'
            or any(os.getenv(key, '').lower() not in ('true', '1', 'yes') for key in required)):
        raise JournalUnavailable('UEL requires guarded synthetic mode and local provider')


def validate(db,mandatory=False):
    actual=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'uel_%' AND sql IS NOT NULL"))
    if not actual:
        if mandatory:raise JournalUnavailable('Explicit UEL migration required; UNKNOWN_RECOVERY')
        return False
    if set(actual)!=OBJECTS or any(actual.get(s.split()[2])!=s for s in DDL):raise JournalUnavailable('UEL schema drift; UNKNOWN_RECOVERY')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM uel_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('UEL ledger mismatch')
    return True


def migrate_local(path,*,allow_baseline=False):
    from xblago.core import integration,context_schema,lifecycle_schema,help_schema,asset_registry_schema
    from xblago.core.sqlite_safety import require_safe_sqlite
    require_synthetic()
    if type(allow_baseline) is not bool:raise ValueError('Explicit baseline switch required')
    path=Path(path);require_safe_sqlite()
    if path.is_symlink() or not path.is_file():raise ValueError('Existing offline certified copy required')
    journal=SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    if journal.witness_directory is None:raise JournalUnavailable('Original retained witness required')
    journal.read()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=30);db.row_factory=sqlite3.Row
    db.create_function('blago_tag',-1,journal.tag,deterministic=True);db.create_function('blago_replaying',0,lambda:1)
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        help_schema.validate(db,required=True);context_schema.validate(db,mandatory=True);lifecycle_schema.validate(db,mandatory=True);asset_registry_schema.validate(db,mandatory=True)
        expected=json.loads(Path(__file__).with_name('integration_help_schema.json').read_text())
        ignored=OBJECTS|context_schema.OBJECTS|lifecycle_schema.OBJECTS|asset_registry_schema.OBJECTS
        if {k:v for k,v in integration.schema_shape(db).items() if k not in ignored}!=expected:raise ValueError('UEL requires complete certified #30 baseline')
        objects=dict(db.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
        for module,table in zip(integration.MODULES,('identity_schema_migrations','identity_runtime_migrations','privacy_schema_migrations','pilot_schema_migrations')):
            if [tuple(r) for r in db.execute(f'SELECT version,checksum FROM {table}')]!=[(module.VERSION,module.CHECKSUM)]:raise ValueError('Predecessor ledger mismatch')
            if any(objects.get(s.split()[3] if s.startswith('CREATE UNIQUE') else s.split()[2])!=s for s in module.DDL):raise ValueError('Predecessor SQL mismatch')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Invalid baseline integrity')
        changed=not validate(db)
        if changed:
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO uel_migrations VALUES (?,?)',(VERSION,CHECKSUM))
            db.execute('INSERT INTO uel_binding VALUES (1,?)',(journal.journal_id,));db.execute('INSERT INTO uel_state VALUES (1,0,?)',('0'*64,))
        from xblago.core.privacy_control import PrivacyControl
        from xblago.core import uel_local
        control=PrivacyControl.open(db);control.reconcile();uel_local.check(db,control)
        # Sources are never reconstructed from UEL. Explicit baseline records only
        # current authenticated managed sources; older history remains UNKNOWN.
        uel_local.synchronize(db,control,baseline=changed and allow_baseline,at_start=True)
        uel_local.check(db,control);control.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('UEL foreign keys')
        db.commit();return changed
    except BaseException:db.rollback();raise
    finally:db.close()


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database',type=Path);p.add_argument('--record-baseline',action='store_true');a=p.parse_args()
    print('applied' if migrate_local(a.database,allow_baseline=a.record_baseline) else 'unchanged')
