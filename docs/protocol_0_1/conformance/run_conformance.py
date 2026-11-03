"""Offline fixtures only. Runs two isolated validator processes, no application imports."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent


def main():
    data=(HERE/'vectors.jsonl').read_bytes()
    manifest=json.loads((HERE/'manifest.json').read_text())
    assert hashlib.sha256(data).hexdigest()==manifest['vectors_sha256'],'Fixture manifest drift'
    cases=[json.loads(line) for line in data.splitlines()]
    assert len(cases)==manifest['count']
    policy=json.loads((HERE/'lab-policy.json').read_text())
    encoded=json.dumps(policy,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
    policy_ref='sha256:'+hashlib.sha256(b'BOP/0.1-lab.1/POLICY\0'+encoded).hexdigest()
    assert manifest['policy_ref']==policy_ref and all(c['context']['policy_ref']==policy_ref for c in cases)
    all_results={};failures=[]
    for name in ('validator_a.py','validator_b.py'):
        result=subprocess.run([sys.executable,str(HERE/name)],input=data,capture_output=True,timeout=60)
        if result.returncode: raise RuntimeError(name+': '+result.stderr.decode())
        answers=[json.loads(line) for line in result.stdout.splitlines()]
        assert len(answers)==len(cases),(name,'wrong number of verdicts')
        for case,answer in zip(cases,answers):
            if case['expected']!=answer: failures.append({'validator':name,'case':case['id'],'expected':case['expected'],'actual':answer})
        all_results[name]=answers
    parity=all_results['validator_a.py']==all_results['validator_b.py']
    report={'profile':manifest['profile'],'vectors_sha256':manifest['vectors_sha256'],'cases':len(cases),
        'accept_vectors':sum(c['expected']['decision']=='ACCEPT' for c in cases),
        'reject_vectors':sum(c['expected']['decision']=='REJECT' for c in cases),
        'unknown_vectors':sum(c['expected']['decision']=='UNKNOWN' for c in cases),
        'validators':['manual encoder + cryptography/OpenSSL','stdlib encoder + PyNaCl/libsodium'],
        'same_author':True,'independent_operator_experiment_executed':False,
        'production_imports':False,'network_calls':0,'database_opened':False,
        'parity':parity,'failures':failures}
    (HERE/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if failures or not parity: raise SystemExit(1)


if __name__=='__main__': main()
