"""Explicit OFFLINE Living Deeds extension; immutable predecessor DDL/checksums."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from xblago.core import uel_schema
from xblago.core.security_journal import JournalUnavailable, SecurityJournal

VERSION = 'living_deeds_001'
FLAG = 'XBLAGO_LIVING_DEEDS_ENABLED'
DDL = (
 'CREATE TABLE living_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
 'CREATE TABLE living_records (id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(blago_user_id),kind TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),state TEXT NOT NULL CHECK(json_valid(state)),head_tag TEXT NOT NULL)',
 'CREATE TABLE living_versions (record_id TEXT NOT NULL REFERENCES living_records(id) ON DELETE CASCADE,version INTEGER NOT NULL,event_id TEXT NOT NULL UNIQUE,body TEXT NOT NULL CHECK(json_valid(body)),PRIMARY KEY(record_id,version))',
 'CREATE TABLE living_commands (token_hash TEXT PRIMARY KEY,actor_id TEXT NOT NULL REFERENCES users(blago_user_id),operation_key TEXT NOT NULL,body_hash TEXT NOT NULL,body TEXT NOT NULL,expires_at INTEGER NOT NULL,seal TEXT NOT NULL,receipt TEXT,UNIQUE(actor_id,operation_key))',
 'CREATE TABLE living_slots (record_id TEXT PRIMARY KEY REFERENCES living_records(id) ON DELETE CASCADE,asset_id TEXT NOT NULL REFERENCES blago_assets(id) ON DELETE CASCADE,request_id TEXT NOT NULL REFERENCES help_requests(id) ON DELETE CASCADE,offer_id TEXT NOT NULL REFERENCES help_offers(id) ON DELETE CASCADE,starts_at INTEGER NOT NULL,ends_at INTEGER NOT NULL,CHECK(ends_at>starts_at))',
 'CREATE TABLE living_holds (target_tag TEXT PRIMARY KEY,reason TEXT NOT NULL)',
 'CREATE TABLE living_contact_holds (target_tag TEXT PRIMARY KEY)',
 'CREATE TABLE living_help_holds (target_tag TEXT PRIMARY KEY)',
 "CREATE TRIGGER living_immutable_version BEFORE UPDATE ON living_versions BEGIN SELECT RAISE(ABORT,'Living versions cannot be rewritten'); END",
 "CREATE TRIGGER living_context_insert AFTER INSERT ON living_records BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id=NEW.owner_id; END",
 "CREATE TRIGGER living_context_update AFTER UPDATE ON living_records BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id IN (OLD.owner_id,NEW.owner_id); END",
 "CREATE TRIGGER living_context_delete AFTER DELETE ON living_records BEGIN UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id=OLD.owner_id; END",
)
OBJECTS = {s.split()[2] for s in DDL}
TABLES = {s.split()[2] for s in DDL if s.startswith('CREATE TABLE')}
def uel_ddl():
    return tuple(s.replace("('ASSET','HELP')", "('ASSET','HELP','LIVING')") for s in uel_schema.DDL)
CHECKSUM = hashlib.sha256('\n'.join(DDL+uel_ddl()).encode()).hexdigest()
def enabled():return os.getenv(FLAG,'').lower() in ('true','1','yes')
def installed(db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='living_migrations'").fetchone())
def require_enrollment(rows=None):
    from xblago.core.uel_checkpoint import configuration
    try:config=configuration()
    except (OSError,ValueError,KeyError,TypeError) as exc:raise JournalUnavailable('Living checkpoint configuration unavailable') from exc
    if config is None:raise JournalUnavailable('Living requires a live pinned checkpoint configuration')
    if rows is not None and not any(json.loads(r[2])['kind']=='uel_checkpoint_policy' and json.loads(r[2])['target']==config['stream'] for r in rows):
        raise JournalUnavailable('Living requires independently retained checkpoint enrollment')
def validate(db,required=False):
    actual=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'living_%' AND sql IS NOT NULL"))
    if not actual:
        if required:raise JournalUnavailable('Explicit Living Deeds migration required')
        return False
    if actual!={s.split()[2]:s for s in DDL}:raise JournalUnavailable('Living schema drift')
    if [tuple(r) for r in db.execute('SELECT * FROM living_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('Living migration checksum mismatch')
    actual=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'uel_%' AND sql IS NOT NULL"))
    if actual!={s.split()[2]:s for s in uel_ddl()}:raise JournalUnavailable('Living UEL extension drift')
    if [tuple(r) for r in db.execute('SELECT * FROM uel_migrations')]!=[(uel_schema.VERSION,uel_schema.CHECKSUM)]:raise JournalUnavailable('Predecessor UEL ledger mismatch')
    return True

def migrate(path):
    uel_schema.require_synthetic()
    require_enrollment()
    from xblago.core.sqlite_safety import require_safe_sqlite
    from xblago.core.integration import preflight
    require_safe_sqlite();path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Existing isolated offline copy required')
    preflight(path)
    journal=SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    if journal.witness_directory is None:raise JournalUnavailable('Independent retained witness required')
    rows,_=journal.read();require_enrollment(rows)
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row
        db.create_function('blago_tag',-1,journal.tag,deterministic=True)
        db.create_function('blago_replaying',0,lambda:1)
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            if installed(db):validate(db);return False
            uel_schema.validate(db,mandatory=True)
            from xblago.core.privacy_control import PrivacyControl
            from xblago.core import uel_local
            control=PrivacyControl.open(db);control.reconcile();uel_local.check(db,control)
            old=[tuple(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]
            for sql in uel_schema.DDL:
                if sql.startswith('CREATE TRIGGER'):db.execute('DROP TRIGGER '+sql.split()[2])
            db.execute('ALTER TABLE uel_events RENAME TO living_migration_old')
            db.execute(uel_ddl()[3]);db.execute('INSERT INTO uel_events SELECT * FROM living_migration_old')
            db.execute('DROP TABLE living_migration_old')
            for sql in uel_ddl():
                if sql.startswith('CREATE TRIGGER'):db.execute(sql)
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO living_migrations VALUES (?,?)',(VERSION,CHECKSUM));validate(db)
            if old!=[tuple(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]:raise JournalUnavailable('UEL headers changed')
            uel_local.check(db,control);control.flush()
            if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise JournalUnavailable('Living migration integrity')
            db.commit();return True
        except BaseException:db.rollback();raise
