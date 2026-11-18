"""Explicit offline synthetic capital migration. Legacy UEL checksums stay immutable."""
import hashlib
import os
import sqlite3
from pathlib import Path
from xblago.core import uel_schema
from xblago.core.security_journal import JournalUnavailable, SecurityJournal

VERSION = 'capital_local_001'
DDL = (
    'CREATE TABLE capital_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
    'CREATE TABLE capital_funds (id TEXT PRIMARY KEY,version INTEGER NOT NULL CHECK(version>0),state TEXT NOT NULL,head_tag TEXT NOT NULL)',
    'CREATE TABLE capital_members (fund_id TEXT NOT NULL REFERENCES capital_funds(id) ON DELETE CASCADE,key_id TEXT NOT NULL,blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),public_key TEXT NOT NULL,roles TEXT NOT NULL,PRIMARY KEY(fund_id,key_id),UNIQUE(fund_id,blago_user_id))',
    'CREATE TABLE capital_versions (fund_id TEXT NOT NULL REFERENCES capital_funds(id) ON DELETE CASCADE,version INTEGER NOT NULL,event_id TEXT NOT NULL UNIQUE,body TEXT NOT NULL,PRIMARY KEY(fund_id,version))',
    'CREATE TABLE capital_holds (target_tag TEXT PRIMARY KEY,reason TEXT NOT NULL)',
)
CHECKSUM = hashlib.sha256('\n'.join(DDL + tuple(s.replace("('ASSET','HELP')", "('ASSET','HELP','CAPITAL')") for s in uel_schema.DDL)).encode()).hexdigest()
OBJECTS = {s.split()[2] for s in DDL}
TABLES = set(OBJECTS)


def enabled():
    return os.getenv('XBLAGO_CAPITAL_LAB_ENABLED', '').lower() in ('true','1','yes')


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='capital_migrations'").fetchone())


def uel_ddl():
    return tuple(s.replace("('ASSET','HELP')", "('ASSET','HELP','CAPITAL')") for s in uel_schema.DDL)


def validate(db):
    actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE (name LIKE 'uel_%' OR name LIKE 'capital_%') AND sql IS NOT NULL"))
    expected = {s.split()[2]: s for s in uel_ddl() + DDL}
    if actual != expected:
        raise JournalUnavailable('Capital/UEL extension schema drift; UNKNOWN_RECOVERY')
    if [tuple(r) for r in db.execute('SELECT * FROM uel_migrations')] != [(uel_schema.VERSION,uel_schema.CHECKSUM)]:
        raise JournalUnavailable('Legacy UEL checksum mismatch')
    if [tuple(r) for r in db.execute('SELECT * FROM capital_migrations')] != [(VERSION,CHECKSUM)]:
        raise JournalUnavailable('Capital checksum mismatch')
    return True


def migrate(path):
    """All writers stopped; original controls required. No startup migration/import."""
    uel_schema.require_synthetic()
    from xblago.core.sqlite_safety import require_safe_sqlite
    require_safe_sqlite()
    path = Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Existing synthetic copy required')
    from xblago.core.integration import preflight
    preflight(path)  # Certify all existing source schemas and controls before DDL.
    journal = SecurityJournal(Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
    if journal.witness_directory is None:raise JournalUnavailable('Witness required')
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            if installed(db):validate(db);return False
            uel_schema.validate(db,mandatory=True)
            from xblago.core.privacy_control import PrivacyControl
            from xblago.core import uel_local
            control=PrivacyControl.open(db);control.reconcile();uel_local.check(db,control)
            old=[dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]
            for sql in uel_schema.DDL:
                if sql.startswith('CREATE TRIGGER'):db.execute('DROP TRIGGER '+sql.split()[2])
            db.execute('ALTER TABLE uel_events RENAME TO capital_migration_old')
            db.execute(uel_ddl()[3])
            db.execute('INSERT INTO uel_events SELECT * FROM capital_migration_old')
            db.execute('DROP TABLE capital_migration_old')
            for sql in uel_ddl():
                if sql.startswith('CREATE TRIGGER'):db.execute(sql)
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO capital_migrations VALUES (?,?)',(VERSION,CHECKSUM))
            validate(db)
            if old != [dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]:raise JournalUnavailable('UEL history changed')
            uel_local.check(db,control)
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():raise JournalUnavailable('Migration integrity')
            control.flush();db.commit();return True
        except BaseException:db.rollback();raise
