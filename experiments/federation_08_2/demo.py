"""Two actual local Node processes; fresh Human/Agent processes; synthetic only.
Creates a NEW disposable directory. Never use a production path.
"""
import argparse
import multiprocessing
from pathlib import Path
from runtime import *
from actors import Conversation
from transport import Transport, server

def node(path, retained, config, private, now, pipe):
    receiver=Receiver(path,Retained(retained),config,Ed25519PrivateKey.from_private_bytes(private),validator=config['validator'],clock=lambda:now)
    srv=server(receiver); pipe.send(srv.server_port); pipe.close(); srv.serve_forever()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--new-directory',required=True); args=parser.parse_args()
    root=Path(args.new_directory); root.mkdir(mode=0o700,exist_ok=False)
    ca=Conversation(); cb=Conversation(network=ca.common['network']); processes=[]; pipes=[]
    try:
        ca.recipient=cb.agent_id; cb.recipient=ca.agent_id
        keys=[Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()]
        # Receiver A trusts Human B; receiver B trusts Human A. No implicit reciprocity.
        configs=[dict(cb.config(keys[0]),validator='A'),dict(ca.config(keys[1]),validator='B')]
        endpoints=[]; ctx=multiprocessing.get_context('spawn')
        for label,cfg,key in zip(('A','B'),configs,keys):
            directory=root/label; directory.mkdir(mode=0o700)
            write(directory/'public-config.json',encode(cfg)); write(directory/'synthetic-node-key',key.private_bytes_raw())
            Receiver.bootstrap(directory/'app'); Retained.bootstrap(directory/'retained',digest(encode(cfg)))
            parent,child=ctx.Pipe(); pipes.append(parent)
            proc=ctx.Process(target=node,args=(directory/'app',directory/'retained',cfg,key.private_bytes_raw(),ca.now,child));proc.start(); child.close(); processes.append(proc)
            if not parent.poll(10): raise Failure('NODE_START')
            endpoints.append(Transport('http://127.0.0.1:'+str(parent.recv())))
        evidence=[]
        for sender,receiver,target,label in [(ca,endpoints[1],1,'A-to-B'),(cb,endpoints[0],0,'B-to-A')]:
            sender.issue(); env=sender.envelope(); n=receiver.post('/challenge',{'envelope_hex':wire(env)})['nonce']; packet=sender.packet(env,n)
            receipt=receiver.post('/deliver',packet)['receipt']; body=receipt['body']
            expected=dict(kind='delivery-receipt',profile=a.PROFILE,network=sender.common['network'],policy_ref=sender.common['policy_ref'],node=sender.node,issuer_key=configs[target]['node_key'],event_id=env['body']['event_id'],event_ref=a.ref(env),mandate_ref=a.ref(sender.grant),status_nonce=n,authority_epoch=1,sequence=len(sender.history),head=a.ref(sender.history[-1]),decision='committed')
            verify_receipt(wire(receipt),keys[target].public_key().public_bytes_raw(),expected,Retained(root/('A' if target==0 else 'B')/'retained'))
            if receiver.post('/deliver',packet)['receipt'] != receipt: raise Failure('DUPLICATE')
            event=sender.transition('mandate.revoke'); rn=receiver.post('/revoke-challenge',{'revoke_hex':wire(event)})['nonce']
            revoke=receiver.post('/revoke',{'history_hex':[wire(x) for x in sender.history],'request_nonce':rn})['receipt']
            revoke_expected=dict(kind='revocation-receipt',profile=a.PROFILE,network=sender.common['network'],policy_ref=sender.common['policy_ref'],node=sender.node,issuer_key=configs[target]['node_key'],identity=sender.identity,authority_epoch=1,revoke_ref=a.ref(event),sequence=len(sender.history),head=a.ref(event),request_nonce=rn,decision='revocation-recorded')
            verify_receipt(wire(revoke),keys[target].public_key().public_bytes_raw(),revoke_expected,Retained(root/('A' if target==0 else 'B')/'retained'))
            evidence.append({'direction':label,'event':packet,'delivery_receipt':receipt,'revocation_receipt':revoke,'revocation_history':[wire(x) for x in sender.history],'assertions':['fresh-owner-grant','pinned-node-receipt','stable-duplicate','durable-revoke']})
        write(root/'public-evidence.json',encode({'classification':'LOCAL INTEGRATION ONLY','S12':'NOT EXECUTED','receipt_schema_sha256':SCHEMA_HASH,'public_configs':configs,'directions':evidence}))
        print('PASS: two Node processes, both directions, fresh distinct Human/Agent/Node keys. S12 NOT EXECUTED.')
        print('Public evidence:',root/'public-evidence.json')
    finally:
        for proc in processes:
            proc.terminate(); proc.join(5)
        for pipe in pipes: pipe.close()
        ca.close(); cb.close()
if __name__=='__main__': main()
