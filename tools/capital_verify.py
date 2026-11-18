"""Offline private-history verifier. No server, SQLite, journal key or session needed.

Same-author reference implementation, NOT an independent security audit. Caller
must pin the policy hash and latest event hash/count outside the rollback domain.
"""
import argparse
import json
from pathlib import Path
from xblago.domain.capital import empty, transition, balance, digest, verify, event_amount, CapitalError


def check(bundle, policy_hash, count, head):
    if set(bundle)!={'format','fund','events'} or bundle['format']!='capital-private-audit-1':raise CapitalError('Audit schema')
    events=bundle['events']
    if len(events)!=count or not events or digest(events[-1])!=head:raise CapitalError('Pinned frontier mismatch')
    first=events[0];policy=first['policy']
    if digest(policy)!=policy_hash or policy['id']!=bundle['fund'] or first['type']!='PolicyEnrolled':raise CapitalError('Pinned policy mismatch')
    if set(first['signatures'])!=set(policy['members']):raise CapitalError('Missing genesis consent')
    for key,sig in first['signatures'].items():verify(policy['members'][key]['public_key'],sig,policy,'BLAGO-CAPITAL-POLICY-1\n')
    state=empty(policy);prior='0'*64;ids=set();operations=set()
    at=0
    for index,event in enumerate(events,1):
        if type(event['recorded_at']) is not int or event['recorded_at']<at:raise CapitalError('Clock order')
        at=event['recorded_at']
        if event['version']!=index or event['fund']!=bundle['fund'] or event['previous']!=prior or event['event_id'] in ids:raise CapitalError('Order/identity mismatch')
        ids.add(event['event_id'])
        if index>1:
            cmd=event['command']
            if cmd['version']!=index-1 or cmd['fund']!=bundle['fund'] or cmd['operation_id'] in operations or cmd['type']!=event['type']:raise CapitalError('Source version/replay mismatch')
            operations.add(cmd['operation_id']);state=transition(state,cmd,event['signature'],event['recorded_at'])
        if event['currency']!='RUB' or type(event['amount_minor']) is not int or event['amount_minor']!=event_amount(state,event):raise CapitalError('Event amount/currency binding')
        if digest(state)!=event['state_hash']:raise CapitalError('Projection mismatch')
        prior=digest(event)
    result,_=balance(state)
    return {'status':'VERIFIED_SYNTHETIC_HISTORY','events':count,'head':prior,'balance':result,'independent_audit':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle',type=Path);p.add_argument('--policy-hash',required=True);p.add_argument('--events',type=int,required=True);p.add_argument('--head',required=True);a=p.parse_args()
    print(json.dumps(check(json.loads(a.bundle.read_text()),a.policy_hash,a.events,a.head),sort_keys=True))

if __name__=='__main__':main()
