"""Documentation/corpus consistency checks only; no application/database imports."""
import ast
import hashlib
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
DOCS=HERE.parents[1]
SPEC=HERE.parent


def main():
    required=['BASELINE_AND_ENTITIES.md','ENVELOPE.md','MANDATE_AND_AUTHORITY.md','PORTABILITY.md','THREAT_MODEL.md','CONFORMANCE.md','EXPERIMENT_AB.md']
    pages=[DOCS/'BLAGO_FOUNDATION_08_1.md']+[SPEC/name for name in required]
    checked=0
    for page in pages:
        text=page.read_text()
        assert 'Draft' in text or 'PLAN, NOT EXECUTED' in text,page
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if target.startswith(('https://','http://','#')): continue
            assert (page.parent/target.split('#')[0]).is_file(),(page,target)
            checked+=1
    base=(DOCS/'BLAGO_PROTOCOL_0_1_REQUIREMENTS.md').read_text()
    requirements=set(re.findall(r'^\*\*(R-[A-Z]+-\d+)',base,re.M))
    trace=(SPEC/'CONFORMANCE.md').read_text()
    assert requirements <= set(re.findall(r'R-[A-Z]+-\d+',trace)),requirements-set(re.findall(r'R-[A-Z]+-\d+',trace))
    for name,forbidden in [('validator_a.py',{'validator_b','xblago','sqlite3','requests','urllib','subprocess'}),('validator_b.py',{'validator_a','xblago','sqlite3','requests','urllib','subprocess'})]:
        tree=ast.parse((HERE/name).read_text())
        imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imports.update(x.name.split('.')[0] for x in node.names)
            if isinstance(node,ast.ImportFrom): imports.add((node.module or '').split('.')[0])
        assert not imports & forbidden,(name,imports&forbidden)
    cases=[json.loads(line) for line in (HERE/'vectors.jsonl').read_text().splitlines()]
    manifest=json.loads((HERE/'manifest.json').read_text());result=json.loads((HERE/'results.json').read_text())
    assert len({c['id'] for c in cases})==len(cases)==manifest['count']==result['cases']==73
    assert all(c['id'] in trace for c in cases)
    assert result['parity'] and result['failures']==[]
    assert result['same_author'] is True and result['independent_operator_experiment_executed'] is False
    assert hashlib.sha256((HERE/'vectors.jsonl').read_bytes()).hexdigest()==manifest['vectors_sha256']==result['vectors_sha256']
    print(json.dumps({'spec_pages':len(pages),'local_links':checked,'requirement_ids_traced':len(requirements),'vectors':len(cases),'separate_validator_imports':True,'status':'PASS'}))


if __name__=='__main__': main()
