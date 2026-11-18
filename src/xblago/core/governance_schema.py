"""Additive governance marker and frozen recovery holds; no automatic policy import."""
import hashlib
import os
import sqlite3
from pathlib import Path
from xblago.core.security_journal import JournalUnavailable

VERSION='capital_governance_002'
DDL=(
    'CREATE TABLE govcap_migrations (version TEXT PRIMARY KEY,checksum TEXT NOT NULL)',
    'CREATE TABLE govcap_holds (fund_id TEXT PRIMARY KEY REFERENCES capital_funds(id) ON DELETE CASCADE,reason TEXT NOT NULL)',
)
CHECKSUM=hashlib.sha256('\n'.join(DDL).encode()).hexdigest()
OBJECTS={s.split()[2] for s in DDL}


def enabled():return os.getenv('XBLAGO_CAPITAL_GOVERNANCE_ENABLED','').lower() in ('true','1','yes')


def installed(db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='govcap_migrations'").fetchone())


def validate(db,required=False):
    actual=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'govcap_%' AND sql IS NOT NULL"))
    if not actual:
        if required:raise JournalUnavailable('Explicit governance migration required')
        return False
    if actual!={s.split()[2]:s for s in DDL} or [tuple(r) for r in db.execute('SELECT * FROM govcap_migrations')]!=[(VERSION,CHECKSUM)]:raise JournalUnavailable('Governance schema drift')
    return True


def migrate(path):
    from xblago.core import capital_schema,uel_schema
    from xblago.core.integration import preflight
    from xblago.core.privacy_control import PrivacyControl
    uel_schema.require_synthetic();path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Existing isolated copy required')
    preflight(path)
    with sqlite3.connect(path,timeout=30) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            capital_schema.validate(db)
            if validate(db):return False
            control=PrivacyControl.open(db);control.reconcile()
            for sql in DDL:db.execute(sql)
            db.execute('INSERT INTO govcap_migrations VALUES (?,?)',(VERSION,CHECKSUM))
            validate(db,required=True)
            if db.execute('PRAGMA foreign_key_check').fetchall():raise JournalUnavailable('Governance FK failure')
            control.flush();db.commit();return True
        except BaseException:db.rollback();raise
