"""Reproduce manifests using a new temporary synthetic DB only; never real data."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from xblago.core.integration import migration_manifest,schema_shape
from tools.integration_fixture import create

ROOT=Path(__file__).resolve().parents[1]
PINS={
 'integration01':'d7f619a1e6a7f15564facb5e8e84b184a3f24658',
 'main':'3c218dc5757879c26b3aec520af3636b2d702715',
 'audit18':'6f83b66db0ef8fc87a93dd0a7de70fdcecca4428',
 'foundation02':'4bc48f2a6b23f198ca88a022e4bad0f03f709e8a',
 'foundation03':'8e086ec24bc9f3fe0d11e6dd12473f867ede3b44',
 'foundation04':'25dcb0fae420b27916629b1686f07f6f96e5c446',
 'foundation04_2':'af94ac93dde649747b1a89104000f92f75ecb7fc',
 'foundation05_1':'8820d7852f0db6c4fc5101f9e281211ba7b6b074',
 'foundation05_2':'8b63b8795490a11ad2b5a709b9a258033b7d6381',
 'foundation05_3':'224ad9d2a1775a8372354af35d542f433cb8bdde',
 'foundation05_4':'8634e94a6dddd2b8cfff415b112f6b895eb0f4ae',
 'foundation06':'37be0437578125503f0a851c6ce9a86222937cdc',
 'foundation06_1':'d11a2a555efe57e6818450d534655020bf47b9ca'}

def dump(value):return json.dumps(value,sort_keys=True,indent=2)+'\n'
def manifest(schema,help_schema=None):
 paths=sorted((ROOT/'src').rglob('*.py'))+sorted((ROOT/'src/xblago/integrations/static').glob('*'))+sorted((ROOT/'tools').glob('*.py'))+sorted((ROOT/'tests').rglob('*.py'))+[ROOT/p for p in ('Dockerfile','pyproject.toml','requirements-integration.lock','.github/workflows/tests.yml','.github/workflows/huggingface-space.yml')]
 from xblago.core.help_schema import VERSION,CHECKSUM
 return {'help_schema_sha256':hashlib.sha256((help_schema or (ROOT/'src/xblago/core/integration_help_schema.json').read_text()).encode()).hexdigest(),'help_migration':{'module':'xblago.core.help_schema','version':VERSION,'checksum':CHECKSUM,'optional':True},'format':'blago-integration-01','mode':'synthetic-only','source_pins':PINS,'excluded':['Sovereign Identity','Genesis','federation','wallets','Solana','real payments'],'migration_order':migration_manifest(),'schema_sha256':hashlib.sha256(schema.encode()).hexdigest(),'dependencies':[line for line in (ROOT/'requirements-integration.lock').read_text().splitlines() if line and not line.startswith('#')],'source_files_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'candidate_sha_policy':'Git commit and image identity recorded externally in CI evidence; never self-referential JSON SHA'}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true');args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='blago-integration-manifest-') as root:
  config=create(Path(root)/'new',validate=False)
  with sqlite3.connect(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3') as db: schema=dump(schema_shape(db))
 with tempfile.TemporaryDirectory(prefix='blago-help-manifest-') as root:
  config=create(Path(root)/'new',validate=False,help_enabled=True)
  with sqlite3.connect(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3') as db: help_schema=dump(schema_shape(db))
 result=dump(manifest(schema,help_schema));targets={ROOT/'src/xblago/core/integration_schema.json':schema,ROOT/'integration/candidate.json':result,ROOT/'src/xblago/core/integration_help_schema.json':help_schema}
 for p,expected in targets.items():
  if args.write:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(expected)
  elif p.read_text()!=expected:raise SystemExit('Manifest drift: '+str(p.relative_to(ROOT)))
 print('Integration source/schema/migration/dependency manifest: PASS')
if __name__=='__main__':main()
