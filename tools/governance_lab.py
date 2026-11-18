"""Synthetic 100-participant governance experiment; fresh keys, no public service."""
import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import time
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from xblago.core import capital_schema,governance_schema,uel_checkpoint as cp
from xblago.core.security_journal import canonical
from xblago.domain import capital as cap,capital_governance as gov
from tools.foundation09_lab import lab
from tools.capital_lab import sign,receipt


def setup(state):
    path=Path(state['cfg']['XBLAGO_DATA_DIR'])/'blago.sqlite3'
    capital_schema.migrate(path);governance_schema.migrate(path)
    state['cfg'].update(XBLAGO_CAPITAL_LAB_ENABLED='true',XBLAGO_CAPITAL_GOVERNANCE_ENABLED='true');os.environ.update(state['cfg'])
    keys=[Ed25519PrivateKey.generate() for _ in state['tokens']];oracle=Ed25519PrivateKey.generate()
    members={}
    for i,key in enumerate(keys):
        roles=['VIEW']
        if i==0:roles+=['DECLARE','PROPOSE']
        if i==2:roles+=['EXECUTE']
        if i in (1,3,4):roles+=['APPROVE','VERIFY']
        if i in (1,3,4,5,6):roles+=['GOV']
        members[f'actor{i:03d}']={'public_key':key.public_key().public_bytes_raw().hex(),'roles':roles,'expires_at':int(time.time())+7200}
    policy={'format':gov.FORMAT,'id':'synthetic-governed-capital','version':1,'currency':'RUB','members':members,'evidence_key':oracle.public_key().public_bytes_raw().hex(),
            'privacy_key':cp.public(cp.load_key(cp.configuration()['source_key_file'])),'governors':sorted(k for k,v in members.items() if 'GOV' in v['roles']),
            'governance_quorum':4,'spend_quorum':2,'activation_delay':60,'delegation_seconds':300}
    service=cap.CapitalLedger(state['app'])
    service.enroll(state['tokens'][0],policy,{f'actor{i:03d}':t for i,t in enumerate(state['tokens'])},
                   {f'actor{i:03d}':sign(k,policy,gov.POLICY_DOMAIN) for i,k in enumerate(keys)})
    return service,keys,oracle,policy


def command(service,token,policy,actor,kind,data,operation,evidence=None):
    return {'fund':policy['id'],'version':service.inspect(token,policy['id'])['version'],'policy_hash':cap.digest(policy),'operation_id':operation,'key_id':f'actor{actor:03d}',
            'type':kind,'expires_at':int(time.time())+3600,'data':data,'evidence':evidence}


def proof_export(service,token,fid):
    """Private auditor export. Unselected UEL leaves stay opaque; counts are visible."""
    bundle=service.audit(token,fid)
    with service.tx(token) as (db,uid,c):
        member=service._member(db,uid,fid);state=json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(fid,)).fetchone()[0]);p=state['policy']
        if not gov.direct(state,member['key_id'],'GOV',int(time.time())):raise cap.CapitalError('Direct governor audit authority required')
        cfg=cp.configuration();journal_leaves,uel_leaves=cp.proof(c.rows)
        request=cp.signed({'profile':cp.PROFILE,'stream':cfg['stream'],'nonce':secrets.token_hex(32),'journal':journal_leaves,'uel':uel_leaves},cp.load_key(cfg['source_key_file']),'request')
        checkpoint=cp.verify_reply(cp.exchange(cfg['endpoint'],request),request,cfg)
        headers=[dict(r) for r in db.execute("SELECT * FROM uel_events WHERE source_kind='CAPITAL' AND object_ref=? ORDER BY sequence",(c.journal.tag('capital-record',fid),))]
        binding={'format':'capital-uel-binding-2','fund':fid,'history_head':cap.digest(bundle['events'][-1]),'history_count':len(bundle['events']),
                 'record_refs':[r['record_ref'] for r in headers],'checkpoint_hash':cp.digest(cp.encode(checkpoint))}
        key=cp.load_key(cfg['source_key_file']);signature=key.sign(('BLAGO-CAPITAL-EXPORT-0.2\n'+canonical(binding)).encode()).hex()
        result={'format':'capital-governance-proof-2','history':bundle,'uel_headers':headers,'uel_leaves':uel_leaves,'checkpoint':checkpoint,'binding':binding,'binding_signature':signature}
        pins={'policy_hash':cap.digest(bundle['events'][0]['policy']),'checkpoint_hash':cp.digest(cp.encode(checkpoint)),'custodian_key':cfg['custodian_key'],'stream':cfg['stream'],'node_key':p['privacy_key']}
        return result,pins


def run(proof_output=None,pins_output=None):
    from tools.independent_capital_verifier import verify_bundle
    with lab(100) as state:
        service,keys,oracle,policy=setup(state);tokens=state['tokens'];counter=0
        def act(actor,kind,data,evidence=None):
            nonlocal counter
            counter+=1;c=command(service,tokens[actor],policy,actor,kind,data,'lab-'+str(counter),evidence)
            return service.execute(tokens[actor],c,sign(keys[actor],c,gov.DOMAIN))
        for lid,value in [('first',600000000),('second',400000000)]:
            act(0,'CapitalReceived',{'lot':lid,'amount_minor':value,'source':'fixture-'+lid})
            act(1,'CapitalVerified',{'lot':lid},receipt(oracle,policy,'RECEIVED',lid,value,'verify-'+lid))
        act(0,'ProposalCreated',{'proposal':'infrastructure','amount_minor':703000000,'purpose':'shared-capacity','destination':'fixture','expires_at':int(time.time())+3000})
        for actor in (1,3):act(actor,'ApprovalGranted',{'proposal':'infrastructure','approval':'approve-main'})
        act(2,'BudgetReserved',{'approval':'approve-main','reservation':'main','allocations':{'first':600000000,'second':103000000}})
        act(2,'CapitalSpent',{'reservation':'main'},receipt(oracle,policy,'SPENT','main',703000000,'fixture-spent'))
        new=copy.deepcopy(policy);new['version']=2;new['delegation_seconds']=240
        act(1,'PolicyProposed',{'proposal':'policy-next','policy':new})
        for actor in (1,3,4,5):act(actor,'PolicyVoted',{'proposal':'policy-next'})
        with patch('time.time',return_value=state['clock']+61):
            act(1,'PolicyActivated',{'proposal':'policy-next'});policy=new
            act(99,'MemberExited',{})
            observed=service.inspect(tokens[0],policy['id']);bundle,pins=proof_export(service,tokens[1],policy['id'])
            verified=verify_bundle(bundle,pins)
            proof_hash=pins_hash=None
            if proof_output is not None and pins_output is not None:
                import hashlib
                for path,value in ((proof_output,bundle),(pins_output,pins)):
                    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as output:output.write(canonical(value)+'\n')
                proof_hash=hashlib.sha256(Path(proof_output).read_bytes()).hexdigest();pins_hash=hashlib.sha256(Path(pins_output).read_bytes()).hexdigest()
        assert observed['available_minor']==297000000 and observed['spent_minor']==703000000 and verified['balance']['available_minor']==297000000
        return {'format':'capital-governance-lab-2','status':'PASS','participants':100,'policy_version':2,'exited_members':1,'balance':observed,
                'independent_implementation_verifier':verified,'proof_sha256':proof_hash,'producer_pins_sha256':pins_hash,'pins_source':'SYNTHETIC_PRODUCER_NOT_INDEPENDENT_PINNING','independent_operator':'NOT EXECUTED','independent_audit':'NOT EXECUTED','real_users':'NO-GO'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--proof-output',type=Path);p.add_argument('--pins-output',type=Path);a=p.parse_args()
    if (a.proof_output is None)!=(a.pins_output is None):p.error('Both proof and producer-pins output paths required')
    a.output.write_text(json.dumps(run(a.proof_output,a.pins_output),indent=2)+'\n');print('Governance / independent verifier / 100 synthetic participants: PASS')

if __name__=='__main__':main()
