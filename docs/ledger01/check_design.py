"""Consistency and source-boundary checks, not UEL authority/runtime validation."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from fixture_codec import FIELDS,PROFILE,DOMAIN,canonical,hashref

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
BASE='c3b4c5b4fd37a5e16e09b19cdbcf494183963bf5'


def main():
    baseline=json.loads((HERE/'baseline.json').read_text())
    assert baseline['design_base_sha']==BASE and baseline['runtime_changes'] is False
    subprocess.run(['git','merge-base','--is-ancestor',BASE,'HEAD'],cwd=ROOT,check=True)
    subprocess.run(['git','diff','--exit-code',BASE,'--','src','tools','tests','integration','Dockerfile','pyproject.toml',
                    'requirements-integration.lock','.github/workflows/tests.yml','.github/workflows/huggingface-space.yml'],cwd=ROOT,check=True)
    schema=json.loads((HERE/'event-envelope.schema.json').read_text())
    assert set(schema['properties']['body']['properties'])==FIELDS
    assert schema['properties']['body']['properties']['profile']['const']==PROFILE
    assert schema['properties']['body']['additionalProperties'] is False
    golden=json.loads((HERE/'golden.json').read_text());policy=json.loads((HERE/'policy.json').read_text())
    assert bytes.fromhex(golden['signing_bytes_hex'])==DOMAIN+canonical(golden['body'])
    assert golden['body']['policy_ref']==hashref(b'BLAGO/UEL/0.1-lab.1/POLICY\0'+canonical(policy))
    cases=json.loads((HERE/'design-scenarios.json').read_text())['cases']
    assert len(cases)==22 and all(c['actual']=='NOT EXECUTED' for c in cases)
    assert 'not_authorized' in (HERE/'check_vectors.py').read_text().lower() or 'not authorization' in (HERE/'check_vectors.py').read_text().lower()
    for path in [ROOT/'docs/BLAGO_UNIVERSAL_EVENT_LEDGER_0_1.md',*HERE.glob('*.md')]:
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if '://' not in link and not link.startswith('#'):
                assert (path.parent/link.split('#')[0]).exists(),(str(path),link)
    for path in HERE.glob('*.json'):json.loads(path.read_text())
    print('Draft schema/links/fixture consistency and unchanged runtime: PASS')


if __name__=='__main__':main()
