"""Public build identity only; no database, environment dump, sessions or keys."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
from tools.freeze_integration import ROOT,PINS,manifest
from xblago.core.integration import migration_manifest
from xblago.core.sqlite_safety import runtime_evidence

def git(*args):return subprocess.check_output(['git',*args],text=True,cwd=ROOT).strip()

def evidence():
    schema=(ROOT/'src/xblago/core/integration_schema.json').read_text()
    if json.loads((ROOT/'integration/candidate.json').read_text())!=manifest(schema):raise RuntimeError('Unfrozen source changes')
    if subprocess.run(['git','diff','--quiet','HEAD','--','src','tools','tests','.github','Dockerfile','pyproject.toml','requirements-integration.lock','integration'],cwd=ROOT).returncode:raise RuntimeError('Uncommitted candidate code')
    checkout=git('rev-parse','HEAD');expected=os.getenv('BLAGO_EXPECTED_SOURCE_SHA',checkout)
    tree=git('rev-parse','HEAD^{tree}')
    if git('rev-parse',expected+'^{tree}')!=tree:raise RuntimeError('CI checkout tree differs from reviewed source')
    for name,sha in PINS.items():
        if name!='audit18' and subprocess.run(['git','merge-base','--is-ancestor',sha,'HEAD'],cwd=ROOT).returncode:
            raise RuntimeError('Source ancestry mismatch: '+name)
    lock=(ROOT/'requirements-integration.lock').read_text()
    installed={}
    for line in lock.splitlines():
        if line and not line.startswith('#'):
            name,pinned=line.split('==');installed[name]=version(name)
            if installed[name]!=pinned:raise RuntimeError('Installed dependency differs from lock: '+name)
    return {'format':'blago-integration-evidence-01','candidate_sha':expected,'ci_checkout_sha':checkout,'tree':tree,'source_pins':PINS,'python':platform.python_version(),'sqlite':runtime_evidence(),'migration_manifest':migration_manifest(),'manifest_sha256':hashlib.sha256((ROOT/'integration/candidate.json').read_bytes()).hexdigest(),'dependencies':installed,'mode':'synthetic only','production_access':False,'deployment_executed':False,'independent_storage_qualified':False,'real_pilot':'NO-GO'}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.write_text(json.dumps(evidence(),sort_keys=True,indent=2)+'\n');print('Public candidate identity evidence written')
