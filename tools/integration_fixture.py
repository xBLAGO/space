"""Explicitly initialize a NEW synthetic sandbox. Never accepts an existing root.
Not a production migration tool. Same-volume custody is only a local simulation.
"""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch
from xblago.core.integration import migration_manifest, preflight
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import WebApp
from xblago.core.identity_schema import migrate_identity_store
from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.privacy_schema import migrate_privacy
from xblago.core.pilot_schema import migrate_pilot
from xblago.core.journal_witness import initialize
from xblago.core.security_journal import SecurityJournal

def create(root, *, validate=True):
    root=Path(root);root.mkdir(mode=0o700,parents=False,exist_ok=False)
    data=root/'app';data.mkdir(mode=0o700)
    with patch.dict(os.environ,{'XBLAGO_PILOT_MODE':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false','XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'','XBLAGO_INTEGRATION_MODE':''}):
        path=data/'blago.sqlite3';WebApp(SQLiteBlagoStore(path))
        migrate_identity_store(path);migrate_identity_runtime(path)
        journal=root/'journal';witness=root/'witness'
        SecurityJournal.initialize(journal);migrate_privacy(path,journal)
        os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal)
        migrate_pilot(path);initialize(SecurityJournal(journal),witness)
    config={'XBLAGO_DATA_DIR':str(data),'XBLAGO_SECURITY_JOURNAL_DIR':str(journal),'XBLAGO_JOURNAL_WITNESS_DIR':str(witness),'XBLAGO_PILOT_MODE':'true','XBLAGO_REQUIRE_SECURE_IDENTITY':'true','XBLAGO_REQUIRE_PRIVACY_JOURNAL':'true','XBLAGO_INTEGRATION_MODE':'synthetic','XBLAGO_LLM_PROVIDER':'local'}
    if validate:
        with patch.dict(os.environ,config):preflight(path)
    (root/'public-config.json').write_text(json.dumps(config,indent=2)+'\n')
    return config

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--new-synthetic-root',type=Path,required=True)
    config=create(parser.parse_args().new_synthetic_root)
    print(json.dumps({'mode':'SYNTHETIC ONLY','config':config,'migrations':migration_manifest()},indent=2))
