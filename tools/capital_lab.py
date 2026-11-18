"""Synthetic signed capital on the existing 100-participant, Help/Asset/UEL lab."""
import argparse
import json
import os
from pathlib import Path
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from xblago.core.security_journal import canonical
from xblago.core import capital_schema
from xblago.domain.capital import CapitalLedger, digest, DOMAIN
from tools.foundation09_lab import lab
from tools.capital_verify import check


def sign(key,body,domain=DOMAIN):return key.sign((domain+canonical(body)).encode()).hex()


def setup(state):
    capital_schema.migrate(Path(state['cfg']['XBLAGO_DATA_DIR'])/'blago.sqlite3')
    state['cfg']['XBLAGO_CAPITAL_LAB_ENABLED']='true';os.environ.update(state['cfg'])
    service=CapitalLedger(state['app']);keys=[Ed25519PrivateKey.generate() for _ in state['tokens']];oracle=Ed25519PrivateKey.generate()
    members={f'actor{i}':{'public_key':k.public_key().public_bytes_raw().hex(),'roles':['VIEW']+(['DECLARE','PROPOSE'] if i==0 else ['VERIFY','APPROVE','REVOKE'] if i==1 else ['EXECUTE'] if i==2 else []),'expires_at':int(time.time())+7200} for i,k in enumerate(keys)}
    policy={'format':'capital-lab-policy-1','id':'synthetic-collective','version':1,'currency':'RUB','members':members,'evidence_key':oracle.public_key().public_bytes_raw().hex()}
    service.enroll(state['tokens'][0],policy,{f'actor{i}':t for i,t in enumerate(state['tokens'])},{f'actor{i}':sign(k,policy,'BLAGO-CAPITAL-POLICY-1\n') for i,k in enumerate(keys)})
    return service,keys,oracle,policy


def command(service,token,policy,actor,kind,data,operation,evidence=None):
    return {'fund':policy['id'],'version':service.inspect(token,policy['id'])['version'],'policy_hash':digest(policy),
            'operation_id':operation,'key_id':f'actor{actor}','type':kind,'expires_at':int(time.time())+3600,'data':data,'evidence':evidence}


def receipt(oracle,policy,kind,subject,value,key):
    b={'format':'capital-fixture-evidence-1','fund':policy['id'],'kind':kind,'subject':subject,'currency':'RUB','amount_minor':value,'receipt_id':key}
    return {'body':b,'signature':sign(oracle,b,'BLAGO-CAPITAL-EVIDENCE-1\n')}


def run():
    with lab(100) as s:
        service,keys,oracle,policy=setup(s);tokens=s['tokens'];counter=0
        def act(actor,kind,data,evidence=None):
            nonlocal counter
            counter+=1;cmd=command(service,tokens[actor],policy,actor,kind,data,f'op{counter}',evidence)
            return service.execute(tokens[actor],cmd,sign(keys[actor],cmd))
        for lid,value in [('lot-a',600000000),('lot-b',400000000)]:
            act(0,'CapitalReceived',{'lot':lid,'amount_minor':value,'source':'fixture-'+lid})
            act(1,'CapitalVerified',{'lot':lid},receipt(oracle,policy,'RECEIVED',lid,value,'receipt-'+lid))
        act(0,'ProposalCreated',{'proposal':'infrastructure','amount_minor':703000000,'purpose':'shared-capacity','destination':'synthetic-provider','expires_at':int(time.time())+3500})
        act(1,'ApprovalGranted',{'proposal':'infrastructure','approval':'approval-main'})
        act(2,'BudgetReserved',{'approval':'approval-main','reservation':'main','allocations':{'lot-a':600000000,'lot-b':103000000}})
        act(2,'CapitalSpent',{'reservation':'main'},receipt(oracle,policy,'SPENT','main',703000000,'fixture-spend'))
        act(0,'ProposalCreated',{'proposal':'future','amount_minor':50000000,'purpose':'future-capacity','destination':'synthetic-provider','expires_at':int(time.time())+3500})
        act(1,'ApprovalGranted',{'proposal':'future','approval':'approval-future'})
        act(2,'BudgetReserved',{'approval':'approval-future','reservation':'future','allocations':{'lot-b':50000000}})
        bundle=service.audit(tokens[0],policy['id']);verified=check(bundle,digest(policy),len(bundle['events']),digest(bundle['events'][-1]))
        current=service.inspect(tokens[0],policy['id'])
        assert current['available_minor']==247000000 and current['reserved_minor']==50000000 and current['spent_minor']==703000000
        return {'format':'foundation09-1-durable-lab-1','status':'PASS','participants':100,'balance':current,'offline_verification':verified,'feature_default':'OFF','real_users':'NO-GO','independent_operator':'NOT EXECUTED'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();result=run();a.output.write_text(json.dumps(result,indent=2)+'\n');print('Durable synthetic capital / 100 participants: PASS')

if __name__=='__main__':main()
