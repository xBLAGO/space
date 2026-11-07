"""Isolated nonfinancial integration entrypoint. No startup migrations or deploy.

Legacy entrypoints remain compatibility interfaces; only this launcher is the
Integration 01 candidate. It deliberately cannot authorize real-user operation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import RLock, local
from xblago.core import identity_schema, identity_runtime_schema, privacy_schema, pilot_schema
from xblago.core.security_journal import JournalUnavailable
from xblago.core.sqlite_safety import require_safe_sqlite

MODULES = (identity_schema, identity_runtime_schema, privacy_schema, pilot_schema)
EXPERIMENTAL = ('XBLAGO_IDENTITY_CORE_ENABLED','XBLAGO_IDENTITY_RECOVERY_ENABLED','XBLAGO_GENESIS_ENABLED')

class IntegrationBlocked(RuntimeError): pass

def migration_manifest():
    return [{'module':m.__name__, 'version':m.VERSION,'checksum':m.CHECKSUM} for m in MODULES]

def enabled(): return os.getenv('XBLAGO_INTEGRATION_MODE') == 'synthetic'

def route_allowed(path):
    # No payment simulation/asset/financial publication in this isolated surface.
    return not path.startswith(('/api/v1/economy','/api/v1/identity-core','/api/v1/genesis'))

def schema_shape(db):
    """Structural legacy compatibility: ALTER ADD COLUMN order is not identity.
    Versioned 05.x SQL remains checked byte-for-byte separately below.
    """
    shape={}
    for name,kind,sql in db.execute("SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted='"'+name.replace('"','""')+'"'
        if kind=='table':
            columns=sorted([list(r[1:]) for r in db.execute(f'PRAGMA table_info({quoted})')])
            foreign=sorted([list(r[2:]) for r in db.execute(f'PRAGMA foreign_key_list({quoted})')],key=str)
            uniques=[]
            for row in db.execute(f'PRAGMA index_list({quoted})'):
                if row[2]:
                    index='"'+row[1].replace('"','""')+'"'
                    uniques.append([r[2] for r in db.execute(f'PRAGMA index_info({index})')])
            shape[name]={'type':kind,'columns':columns,'foreign_keys':foreign,'unique_indexes':sorted(uniques,key=str)}
        else:shape[name]={'type':kind,'sql':sql}
    return shape

def preflight(path: Path):
    if not enabled(): raise IntegrationBlocked('Only explicit synthetic integration mode is available')
    required=('XBLAGO_PILOT_MODE','XBLAGO_REQUIRE_SECURE_IDENTITY','XBLAGO_REQUIRE_PRIVACY_JOURNAL')
    if any(os.getenv(k,'').lower() not in ('1','true','yes') for k in required):
        raise IntegrationBlocked('All secure identity, privacy and SQLite controls are mandatory')
    if any(os.getenv(k,'').lower() in ('1','true','yes') for k in EXPERIMENTAL):
        raise IntegrationBlocked('Experimental components are excluded')
    if os.getenv('XBLAGO_LLM_PROVIDER','local') != 'local':
        raise IntegrationBlocked('Integration rehearsal requires local provider; live provider needs separate approval')
    journal=os.getenv('XBLAGO_SECURITY_JOURNAL_DIR','');witness=os.getenv('XBLAGO_JOURNAL_WITNESS_DIR','')
    if not journal or not witness: raise IntegrationBlocked('Original journal and retained witness are required')
    path=path.resolve()
    if not path.is_file(): raise IntegrationBlocked('Initialize a new synthetic store offline; startup never migrates')
    require_safe_sqlite()
    # Check version/SQL without repairing or creating any table.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA foreign_keys=ON'); identity_schema._check_database(db)
        objects=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"))
        if any(x.startswith(('sovereign_','genesis_')) or x in ('identity_controllers','identity_wallet_links','identity_credentials','identity_events','identity_consents') for x in objects):
            raise IntegrationBlocked('Experimental schema is not an Integration 01 baseline')
        from xblago.core.help_schema import present,validate
        has_help=present(db)
        validate(db)
        expected=json.loads(Path(__file__).with_name('integration_help_schema.json' if has_help else 'integration_schema.json').read_text())
        from xblago.core import context_schema
        context_schema.validate(db,mandatory=context_schema.required())
        actual_shape={k:v for k,v in schema_shape(db).items() if k not in context_schema.OBJECTS}
        if actual_shape!=expected: raise IntegrationBlocked('Application schema differs from frozen integration baseline')
        for module,table in zip(MODULES,('identity_schema_migrations','identity_runtime_migrations','privacy_schema_migrations','pilot_schema_migrations')):
            if table not in objects or db.execute(f'SELECT version,checksum FROM {table}').fetchall()!=[(module.VERSION,module.CHECKSUM)]:
                raise IntegrationBlocked('Migration manifest mismatch')
            for statement in module.DDL:
                name=statement.split()[3] if statement.startswith('CREATE UNIQUE') else statement.split()[2]
                if objects.get(name)!=statement: raise IntegrationBlocked('Migration SQL drift')
        columns={row[1]:row for row in db.execute('PRAGMA table_info(web_sessions)')}
        for name,definition in identity_runtime_schema.COLUMNS.items():
            row=columns.get(name)
            if row is None or row[2]!=definition.split()[0] or bool(row[3])!=('NOT NULL' in definition):
                raise IntegrationBlocked('Session schema drift')
    from xblago.core.storage import SQLiteBlagoStore
    # Guarded writer reconciles previously durable denials before serving traffic.
    # Avoid legacy constructors which perform executescript on startup.
    store=SQLiteBlagoStore.__new__(SQLiteBlagoStore)
    store.path=path;store.legacy_memory_dir=None;store._lock=RLock();store.conversation_lock=RLock();store._transaction_local=local()
    with store._locked_transaction() as db:
        from xblago.core.privacy_control import PrivacyControl
        control=PrivacyControl.open(db)
        if control is None or control.journal.witness_directory is None:
            raise JournalUnavailable('Original journal and witness are unavailable')
    return store

def main():
    if len(sys.argv)>1: raise SystemExit('Integration launcher accepts configuration only, no path overrides')
    directory=os.getenv('XBLAGO_DATA_DIR','')
    if not directory: raise SystemExit('An explicit synthetic XBLAGO_DATA_DIR is required')
    try: preflight(Path(directory)/'blago.sqlite3')
    except Exception as exc: raise SystemExit('Integration startup blocked: '+type(exc).__name__) from exc
    # Force a synthetic legacy-memory location; never inspect the operator HOME.
    sys.argv=[sys.argv[0],'--data-dir',directory,'--memory-dir',str(Path(directory)/'legacy-disabled')]
    from xblago.integrations.telegram import main as telegram_main
    telegram_main()

if __name__=='__main__': main()
