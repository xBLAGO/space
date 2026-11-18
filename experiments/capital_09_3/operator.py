"""Operator-local synthetic packet CLI, not a public endpoint or spending service."""
import argparse
import json
import os
from pathlib import Path
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
from experiments.capital_09_3 import runtime as r
from tools.capital_custody_verify import verify_archive
from tools.independent_capital_verifier import strict_json


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['keygen','import','prepare','commit','status','export','prepare-rotation','activate-rotation'])
    p.add_argument('--root',type=Path);p.add_argument('--key',type=Path,required=True);p.add_argument('--control',type=Path);p.add_argument('--genesis-pin');p.add_argument('--alias',choices=list('ABC'));p.add_argument('--input',type=Path);p.add_argument('--floor',type=Path);a=p.parse_args()
    r.require_lab()
    if a.action=='keygen':
        key=Ed25519PrivateKey.generate();fd=os.open(a.key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()));f.flush();os.fsync(f.fileno())
        print(json.dumps({'public_key':r.public(key),'synthetic_only':True}));return
    if not all((a.root,a.control,a.genesis_pin)):p.error('--root --control --genesis-pin required')
    value=strict_json(a.input) if a.input else None
    if a.action=='import':
        if not a.alias or not a.floor:p.error('--alias and independently retained --floor required')
        verify_archive(value,a.genesis_pin,strict_json(a.floor))
        r.Node.create(a.root,a.key,a.control,a.alias,value,genesis_pin=a.genesis_pin);result={'imported':True,'spending_enabled':False}
    else:
        n=r.Node(a.root,a.key,a.control,a.genesis_pin)
        if a.action=='prepare':result=n.prepare(value)
        elif a.action=='commit':result={'committed':n.commit(value)}
        elif a.action=='status':result=n.status(value['nonce'])
        elif a.action=='export':result=n.export()
        elif a.action=='prepare-rotation':result=n.prepare_rotation(value['body'],value['approvals'],int(time.time()))
        else:result={'rotation':n.activate_rotation(value,int(time.time()))}
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
