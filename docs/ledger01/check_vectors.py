"""Offline UEL design evidence. WIRE_OK is deliberately NOT authorization or ACK."""
import argparse
import hashlib
import json
from pathlib import Path
from fixture_codec import inspect,object_ref,parse,merkle,public_leaf,Invalid as WireInvalid
from fixture_projection import project,Invalid as ProjectionInvalid

ROOT=Path(__file__).resolve().parent


def run():
    manifest=json.loads((ROOT/'vectors-manifest.json').read_text())
    for name,sha in manifest['files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha:raise AssertionError('Fixture hash drift: '+name)
    records=[]
    for case in json.loads((ROOT/'wire.json').read_text()):
        actual=inspect(bytes.fromhex(case['wire_hex']),case['context'])
        records.append({'id':case['id'],'expected':case['expected'],'actual':actual,'pass':actual==case['expected']})
    golden=json.loads((ROOT/'golden.json').read_text())
    assert object_ref(parse(bytes.fromhex(golden['wire_hex'])))==golden['object_ref']
    for case in json.loads((ROOT/'projection.json').read_text()):
        try:actual=project(case['input'])
        except ProjectionInvalid as exc:actual={'error':str(exc)}
        expected={'error':case['expected_error']} if 'expected_error' in case else case['expected']
        records.append({'id':case['id'],'expected':expected,'actual':actual,'pass':actual==expected})
    from xblago.domain.collective_calculator import ValueEngine,OfflineRateProvider,Quote,AccountingError
    for case in json.loads((ROOT/'valuation.json').read_text()):
        quotes=[Quote(case['currency'],rate,case['quote_at'],source,'fixture-'+source) for source,rate in zip(('a','b'),case['rates'])]
        engine=ValueEngine(OfflineRateProvider(quotes),{'a':'fixture-a','b':'fixture-b'})
        try:actual=engine.evaluate(case['price'],case['currency'],effective_at=case['effective_at'],now=case['effective_at']).btc
        except AccountingError:actual='ERROR'
        records.append({'id':case['id'],'expected':case['expected'],'actual':actual,'pass':actual==case['expected']})
    for case in json.loads((ROOT/'merkle.json').read_text()):
        actual=merkle([bytes.fromhex(x) for x in case['leaves_hex']]).hex()
        records.append({'id':case['id'],'expected':case['root_hex'],'actual':actual,'pass':actual==case['root_hex']})
    # Non-equivalence of private/public leaf domains; not an on-chain proof.
    from fixture_codec import canonical
    release={'format':'blago.public-aggregate.v1','release_id':'synthetic','metric':'synthetic','value':'1','privacy_policy_ref':'synthetic'}
    pub=public_leaf(bytes(32),release)
    private=b'BLAGO/UEL/0.1-lab.1/PRIVATE-LEAF\0'+bytes(32)+canonical(release)
    records.append({'id':'M05-private-leaf-not-public','expected':True,'actual':merkle([pub])!=merkle([private]),'pass':merkle([pub])!=merkle([private])})
    try:public_leaf(bytes(32),dict(release,personal_id='forbidden'));rejected=False
    except WireInvalid:rejected=True
    records.append({'id':'M06-personal-field-forbidden','expected':True,'actual':rejected,'pass':rejected})
    return {'format':'blago-uel-design-evidence-1','profile':'uel-0.1-lab.1','status':'PASS' if all(x['pass'] for x in records) else 'FAIL',
            'executed':len(records),'passed':sum(x['pass'] for x in records),'records':records,
            'scope':'single same-author offline byte/signature inspector and eligible-snapshot arithmetic; not full semantic/authority verifier',
            'authority_protocol':'NOT IMPLEMENTED','durable_ledger':'NOT IMPLEMENTED','independent_operators':'NOT EXECUTED','blockchain':'NOT EXECUTED','production':'NO-GO'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);args=p.parse_args();result=run()
    if args.output:args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'executed':result['executed'],'passed':result['passed']}))
    if result['status']!='PASS':
        for case in result['records']:
            if not case['pass']:print(case['id'],case['actual'])
        raise SystemExit(1)
