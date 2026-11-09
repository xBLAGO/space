"""09.1 additive OFFLINE synthetic migration. Canonical objects remain blago_assets."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from xblago.core.security_journal import JournalUnavailable, SecurityJournal

VERSION = 'foundation09_asset_registry_001'
DDL = [
 'CREATE TABLE assetreg_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
 "CREATE TABLE assetreg_records (asset_id TEXT PRIMARY KEY REFERENCES blago_assets(id) ON DELETE CASCADE,owner_id TEXT NOT NULL REFERENCES users(blago_user_id),version INTEGER NOT NULL CHECK(version>0),status TEXT NOT NULL CHECK(status IN ('DRAFT','ACTIVE','REVOKED')),head_tag TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)",
 "CREATE TABLE assetreg_versions (asset_id TEXT NOT NULL REFERENCES assetreg_records(asset_id) ON DELETE CASCADE,version INTEGER NOT NULL CHECK(version>0),event_id TEXT NOT NULL UNIQUE,body TEXT NOT NULL CHECK(json_valid(body)),action TEXT NOT NULL CHECK(action IN ('DRAFT','DECLARE','REVISE','REVOKE')),recorded_at INTEGER NOT NULL,PRIMARY KEY(asset_id,version))",
 'CREATE TABLE assetreg_operations (owner_id TEXT NOT NULL REFERENCES users(blago_user_id),operation_key TEXT NOT NULL,body_hash TEXT NOT NULL,asset_id TEXT NOT NULL UNIQUE REFERENCES assetreg_records(asset_id) ON DELETE CASCADE,PRIMARY KEY(owner_id,operation_key))',
 "CREATE TABLE assetreg_challenges (token_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(blago_user_id),asset_id TEXT NOT NULL REFERENCES assetreg_records(asset_id) ON DELETE CASCADE,version INTEGER NOT NULL,session_hash TEXT NOT NULL,body TEXT NOT NULL CHECK(json_valid(body)),expires_at INTEGER NOT NULL,seal TEXT NOT NULL,receipt TEXT)",
 'CREATE TABLE assetreg_holds (target_tag TEXT PRIMARY KEY,reason TEXT NOT NULL)',
 """CREATE TRIGGER assetreg_erase BEFORE DELETE ON blago_assets
 WHEN blago_replaying()=0 AND EXISTS(SELECT 1 FROM assetreg_records WHERE asset_id=OLD.id) BEGIN
 INSERT OR IGNORE INTO assetreg_holds VALUES (blago_tag('asset-record',OLD.id),'erased');
 INSERT INTO privacy_outbox VALUES (lower(hex(randomblob(16))),json_object('kind','asset_state',
 'owner',blago_tag('user',OLD.blago_user_id),'target',blago_tag('asset-record',OLD.id),'state','ERASED','recorded_at',0)); END""",
 """CREATE TRIGGER assetreg_no_resurrection BEFORE INSERT ON blago_assets WHEN blago_replaying()=0 AND
 EXISTS(SELECT 1 FROM assetreg_holds WHERE target_tag=blago_tag('asset-record',NEW.id))
 BEGIN SELECT RAISE(ABORT,'Retired asset'); END""",
]
for op in ('INSERT', 'UPDATE', 'DELETE'):
    refs = 'OLD.owner_id,NEW.owner_id' if op == 'UPDATE' else ('OLD' if op == 'DELETE' else 'NEW')+'.owner_id'
    DDL.append(f"CREATE TRIGGER assetreg_context_{op.lower()} AFTER {op} ON assetreg_records BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id IN ({refs}); END")
DDL = tuple(DDL)
CHECKSUM = hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
OBJECTS = {s.split()[2] for s in DDL}
TABLES = {s.split()[2] for s in DDL if s.startswith('CREATE TABLE')}


def enabled():
    return os.getenv('XBLAGO_ASSET_REGISTRY_ENABLED','').lower() in ('1','true','yes')


def validate(db, *, mandatory=False):
    actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'assetreg_%' AND sql IS NOT NULL"))
    if not actual:
        if mandatory:
            raise JournalUnavailable('Explicit asset registry migration required')
        return False
    if set(actual) != OBJECTS or any(actual.get(s.split()[2]) != s for s in DDL):
        raise JournalUnavailable('Asset registry SQL drift')
    if [tuple(r) for r in db.execute('SELECT version,checksum FROM assetreg_migrations')] != [(VERSION,CHECKSUM)]:
        raise JournalUnavailable('Asset registry ledger mismatch')
    return True


def migrate_registry(path):
    """Validate installed predecessors; never rerun their incompatible ALTER loops."""
    from xblago.core import integration, context_schema, lifecycle_schema, help_schema
    from xblago.core.sqlite_safety import require_safe_sqlite
    if not integration.enabled() or any(os.getenv(k,'').lower() not in ('1','true','yes') for k in
            ('XBLAGO_PILOT_MODE','XBLAGO_REQUIRE_SECURE_IDENTITY','XBLAGO_REQUIRE_PRIVACY_JOURNAL')):
        raise ValueError('Guarded synthetic mode required')
    require_safe_sqlite(); path=Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Existing offline copy required')
    journal=SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    if journal.witness_directory is None:
        raise JournalUnavailable('Retained witness required')
    journal.read()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=30);db.row_factory=sqlite3.Row
    db.create_function('blago_tag',-1,journal.tag,deterministic=True);db.create_function('blago_replaying',0,lambda:1)
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        help_schema.validate(db,required=True);context_schema.validate(db,mandatory=True);lifecycle_schema.validate(db,mandatory=True)
        expected=json.loads(Path(__file__).with_name('integration_help_schema.json').read_text())
        actual={k:v for k,v in integration.schema_shape(db).items() if k not in OBJECTS|context_schema.OBJECTS|lifecycle_schema.OBJECTS}
        if actual!=expected:
            raise ValueError('Registry migration requires exact installed Integration/Help baseline')
        # Predecessor SQL/ledgers remain immutable and checked, not trusted by name.
        for module,table in zip(integration.MODULES,('identity_schema_migrations','identity_runtime_migrations','privacy_schema_migrations','pilot_schema_migrations')):
            if [tuple(r) for r in db.execute(f'SELECT version,checksum FROM {table}')] != [(module.VERSION,module.CHECKSUM)]:
                raise ValueError('Predecessor ledger mismatch')
            objects=dict(db.execute('SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL'))
            if any(objects.get(s.split()[3] if s.startswith('CREATE UNIQUE') else s.split()[2])!=s for s in module.DDL):
                raise ValueError('Predecessor SQL mismatch')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Invalid baseline')
        changed=not validate(db)
        if changed:
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO assetreg_migrations VALUES (?,?)',(VERSION,CHECKSUM))
        from xblago.core.privacy_control import PrivacyControl
        control=PrivacyControl.open(db);control.reconcile()
        from xblago.domain.asset_registry import check_frontiers
        check_frontiers(db,control);control.flush()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Registry FK failure')
        db.commit();return changed
    except BaseException:
        db.rollback();raise
    finally:
        db.close()


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('database',type=Path)
    print('applied' if migrate_registry(p.parse_args().database) else 'unchanged')
