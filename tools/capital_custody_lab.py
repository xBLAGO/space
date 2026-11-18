"""Reproducible private synthetic three-node custody and origin-loss rehearsal."""
import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
from experiments.capital_09_3 import runtime as r
from tools.foundation09_lab import lab
from tools.governance_lab import setup,command,proof_export
from tools.capital_lab import sign,receipt
from xblago.domain import capital_governance as gov
from tools.capital_custody_verify import verify_archive,Probe


def source_fixture(participants=100):
    """Real existing CapitalLedger + UEL exports; no fabricated financial lifecycle."""
    with lab(participants) as state:
        service,keys,oracle,policy=setup(state);counter=0
        def act(actor,kind,data,evidence=None):
            nonlocal counter
            counter+=1;c=command(service,state['tokens'][actor],policy,actor,kind,data,'custody-source-'+str(counter),evidence)
            service.execute(state['tokens'][actor],c,sign(keys[actor],c,gov.DOMAIN))
        def export():
            proof,pins=proof_export(service,state['tokens'][1],policy['id'])
            return {'proof':proof,'pins':pins}
        act(0,'CapitalReceived',{'lot':'first','source':'synthetic-first','amount_minor':600000000})
        act(1,'CapitalVerified',{'lot':'first'},receipt(oracle,policy,'RECEIVED','first',600000000,'evidence-first'))
        first=export()
        act(0,'CapitalReceived',{'lot':'second','source':'synthetic-second','amount_minor':400000000})
        second=export()
        act(1,'CapitalVerified',{'lot':'second'},receipt(oracle,policy,'RECEIVED','second',400000000,'evidence-second'))
        third=export()
        return {'packages':[first,second,third],'humans':{f'actor{i:03d}':keys[i] for i in (1,3,4,5,6)},'participants':participants,'at':int(time.time())}


def approvals(body,humans,kind):return {k:r.signed(body,key,kind) for k,key in list(humans.items())[:4]}

def cluster(root,fixture):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);keys={k:Ed25519PrivateKey.generate() for k in 'ABC'}
    p=fixture['packages'][0]
    cfg={'profile':r.PROFILE,'fund':p['proof']['history']['fund'],'epoch':1,'previous':r.ZERO,'nodes':{k:r.public(v) for k,v in keys.items()},'origin_pins':p['pins'],'rotation_delay':60}
    a=r.bootstrap(cfg,p,approvals(cfg,fixture['humans'],'bootstrap'),fixture['at']);pin=r.digest(cfg);nodes={}
    for name,key in keys.items():
        keypath=root/(name+'.key');keypath.write_bytes(key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()));keypath.chmod(0o600)
        nodes[name]=r.Node.create(root/name,keypath,root/('retained-'+name)/'head',name,a,genesis_pin=pin)
    return nodes,keys,pin


def recover(nodes,keys,pin,fixture,now):
    """A absent. Only custody changes; original fund authority remains unchanged."""
    a=nodes['B'].export();replacement=Ed25519PrivateKey.generate();newkeys=copy.deepcopy(a['config']['nodes']);newkeys['A']=r.public(replacement)
    b=r.rotation_body(a,newkeys,now);endorse=approvals(b,fixture['humans'],'rotation-approval')
    votes={k:nodes[k].prepare_rotation(b,endorse,now) for k in ('B','C')}
    cert={'body':b,'approvals':endorse,'votes':votes,'activated_at':now+60}
    for k in ('B','C'):nodes[k].activate_rotation(cert,now+60)
    root=nodes['B'].root.parent;keypath=root/'A2.key';keypath.write_bytes(replacement.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()));keypath.chmod(0o600)
    replica=r.Node.create(root/'A2',keypath,root/'retained-A2'/'head','A',nodes['B'].export(),genesis_pin=pin)
    return replica,cert


def run(archive_output=None,pin_output=None,floor_output=None):
    fixture=source_fixture(100)
    with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED':'true','XBLAGO_INTEGRATION_MODE':'synthetic'}):
        nodes,keys,pin=cluster(directory,fixture);t=r.Transport(nodes)
        for package in fixture['packages']:r.replicate(t,package)
        a=nodes['B'].export();floor={'index':len(a['entries']),'tip':r.tip(a),'epoch':a['config']['epoch'],'config':r.digest(a['config'])}
        before=verify_archive(a,pin,floor)
        t.offline.add('A');nodes['A'].key_file.unlink();keys.pop('A')
        nonce=secrets.token_hex(32);now=fixture['at'];probe=Probe(pin,floor,now,nonce)
        restored=probe.verify(nodes['B'].export(),{k:nodes[k].status(nonce,now) for k in ('B','C')},now)
        replacement,cert=recover(nodes,keys,pin,fixture,now)
        archive=replacement.export();recovered=verify_archive(archive,pin,floor)
        for path,value in ((archive_output,archive),(pin_output,{'genesis_pin':pin}),(floor_output,floor)):
            if path is not None:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as f:f.write(r.wire(value)+b'\n')
        assert recovered['balance']==before['balance'] and restored['balance']['verified_minor']==1000000000
        return {'format':r.PROFILE,'status':'PASS','participants':100,'nodes':3,'independent_operators':'NOT EXECUTED','operator_hosts':'SAME_HOST_LOCAL_LAB','capital_minor':1000000000,'replicated_checkpoints':3,'after_origin_key_loss':restored,'after_custody_rotation':recovered,'rotation_hash':r.digest(cert),'fund_spending_restored':False,'fund_owner_anchor_rotation':'NOT IMPLEMENTED / FAIL-CLOSED','real_users':'NO-GO','real_funds':'NO-GO','default_enabled':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--archive-output',type=Path);p.add_argument('--pin-output',type=Path);p.add_argument('--floor-output',type=Path);args=p.parse_args()
    need_env={'XBLAGO_CAPITAL_CUSTODY_LAB_ENABLED':'true','XBLAGO_INTEGRATION_MODE':'synthetic'}
    # Explicit command is a disposable harness, never a runtime startup path.
    with patch.dict(os.environ,need_env):result=run(args.archive_output,args.pin_output,args.floor_output)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print('Synthetic custody, 100 participants, origin loss: PASS (read-only fund recovery)')
if __name__=='__main__':main()
