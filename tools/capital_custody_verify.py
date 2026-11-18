"""Independent private custody verifier. No application/producer runtime imports.

Uses the preexisting independent capital proof verifier for immutable source
formats. Custody certificate, epoch, freshness and prefix checks are separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from tools.independent_capital_verifier import verify_bundle,Replay,InvalidProof

P='capital-custody-0.3-lab.1';Z='0'*64;MAX=8*1024*1024

def require(x,m):
    if not x:raise InvalidProof(m)
def enc(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def h(x):return hashlib.sha256(enc(x)).hexdigest()
def signature(value,key,domain):
    try:
        require(set(value)=={'body','signature'} and type(value['signature']) is str and len(value['signature'])==128,'signature')
        require(bytes.fromhex(value['signature']).hex()==value['signature'],'noncanonical signature')
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(value['signature']),(P+'/'+domain+'\0').encode()+enc(value['body']))
        return value['body']
    except Exception as exc:raise InvalidProof('custody signature '+domain) from exc

def quorum(body,votes,keys,n,kind):
    require(type(votes) is dict and len(votes)>=n and set(votes)<=set(keys),'quorum')
    for k,s in votes.items():require(signature(s,keys[k],kind)==body,'body substitution')

def config(c):
    require(set(c)=={'profile','fund','epoch','previous','nodes','origin_pins','rotation_delay'} and c['profile']==P and type(c['epoch']) is int and c['epoch']>=1 and c['rotation_delay']==60,'config')
    require(set(c['nodes'])=={'A','B','C'} and len(set(c['nodes'].values()))==3,'node independence keys')
    for k in [c['previous'],*c['nodes'].values()]:require(type(k) is str and len(k)==64 and bytes.fromhex(k).hex()==k,'key encoding')

def source(package,c):
    require(set(package)=={'proof','pins'} and package['proof']['history']['fund']==c['fund'],'source scope')
    for k in ('policy_hash','node_key','custodian_key','stream'):require(package['pins'][k]==c['origin_pins'][k],'origin pins changed')
    report=verify_bundle(package['proof'],package['pins'])
    events=package['proof']['history']['events'];engine=Replay(events[0]['policy'])
    for e in events[1:]:engine.apply(e['command'],e['signature'],e['recorded_at'])
    return engine,report

def human(body,approvals,package,c,at,kind):
    engine,_=source(package,c);policy=engine.s['policy']
    keys={k:policy['members'][k]['public_key'] for k in policy['governors'] if engine.active(k,at,'GOV')}
    quorum(body,approvals,keys,policy['governance_quorum'],kind)
    all_keys={x['public_key'] for x in package['proof']['history']['events'][0]['policy']['members'].values()}
    require(not set(c['nodes'].values())&(all_keys|{c['origin_pins']['node_key'],c['origin_pins']['custodian_key']}),'key separation')

def verify_archive(a,genesis_pin,floor):
    require(floor is not None,'UNKNOWN_RECOVERY: independent retained floor missing')
    require(len(enc(a))<=MAX and set(a)=={'genesis','config','entries','rotations'},'archive bound/shape')
    g=a['genesis'];require(set(g)=={'config','approvals','basis','at'} and h(g['config'])==genesis_pin,'genesis pin')
    c=g['config'];config(c);require(c['epoch']==1 and c['previous']==Z,'genesis epoch')
    human(c,g['approvals'],g['basis'],c,g['at'],'bootstrap')
    require(g['basis']['pins']==c['origin_pins'],'bootstrap pins')
    prior_package=g['basis'];prior=Z;rotations=list(a['rotations']);applied=[];prefixes=[Z];configs={1:h(c)}
    require(type(a['entries']) is list and len(a['entries'])<=64 and len(rotations)<=16,'lab bound')
    for index,cert in enumerate(a['entries'],1):
        require(set(cert)=={'body','votes','package'},'certificate fields');b=cert['body'];p=cert['package'];source(p,c)
        old=prior_package['proof']['history']['events'];new=p['proof']['history']['events']
        require(len(new)>=len(old) and new[:len(old)]==old and (index==1 or len(new)>len(old)),'history rollback/fork')
        oldleaves=prior_package['proof']['uel_leaves'];require(p['proof']['uel_leaves'][:len(oldleaves)]==oldleaves,'UEL truncation/fork')
        oldcp=prior_package['proof']['checkpoint']['body'];newcp=p['proof']['checkpoint']['body']
        require(all(newcp[k]>=oldcp[k] for k in ('generation','journal_seq','uel_seq')),'checkpoint rollback')
        require(b=={'profile':P,'fund':c['fund'],'epoch':c['epoch'],'config':h(c),'index':index,'parent':prior,'package':h(p),'history_count':len(new),'history_head':h(new[-1]),'uel_checkpoint':h(p['proof']['checkpoint'])},'checkpoint binding')
        require(all(type(b[k]) is int for k in ('epoch','index','history_count')),'counter types');quorum(b,cert['votes'],c['nodes'],3,'checkpoint');prior=h(cert);prefixes.append(prior);prior_package=p
        while rotations and rotations[0]['body']['index']==index:
            r=rotations.pop(0);require(set(r)=={'body','approvals','votes','activated_at'},'rotation cert')
            q=r['body'];n=q['new_config'];at=r['activated_at'];config(n)
            require(set(q)=={'profile','kind','parent','index','old_config','new_config','not_before','expires_at'},'rotation fields')
            require(type(q['index']) is int and type(at) is int and q['profile']==P and q['kind']=='custody-rotation' and q['parent']==prior and q['old_config']==h(c),'rotation scope')
            require(n['previous']==h(c) and n['epoch']==c['epoch']+1 and all(n[k]==c[k] for k in ('profile','fund','origin_pins','rotation_delay')),'rotation changes authority')
            require(type(q['not_before']) is int and type(q['expires_at']) is int and q['expires_at']-q['not_before']==540 and q['not_before']<=at<q['expires_at'],'rotation TTL/delay')
            human(q,r['approvals'],prior_package,c,at,'rotation-approval')
            # Separately check new committee's keys against every human/origin key.
            source_policy=prior_package['proof']['history']['events'][0]['policy'];allkeys={x['public_key'] for x in source_policy['members'].values()}
            require(not set(n['nodes'].values()) & (allkeys|{c['origin_pins']['node_key'],c['origin_pins']['custodian_key']}),'new key separation')
            require(type(r['votes']) is dict and 2<=len(r['votes'])<=3 and set(r['votes'])<=set(c['nodes']),'recovery node quorum')
            for name,v in r['votes'].items():
                vb=signature(v,c['nodes'][name],'rotation-vote')
                require(set(vb)=={'rotation','prepared_at','node'} and vb['node']==name and vb['rotation']==h(q) and type(vb['prepared_at']) is int and q['not_before']-60<=vb['prepared_at']<=at-60,'prepared delay')
            require(at==max(q['not_before'],max(v['body']['prepared_at'] for v in r['votes'].values())+60),'canonical activation time')
            applied.append(r);c=n;configs[c['epoch']]=h(c)
    require(not rotations and c==a['config'],'missing/contradictory rotations')
    require(set(floor)=={'index','tip','epoch','config'} and type(floor['index']) is int and 0<=floor['index']<len(prefixes) and prefixes[floor['index']]==floor['tip'] and type(floor['epoch']) is int and 1<=floor['epoch']<=c['epoch'] and configs[floor['epoch']]==floor['config'],'retained floor rollback/conflict')
    _,report=source(prior_package,c)
    return {'status':'PASS','tip':prior,'index':len(a['entries']),'epoch':c['epoch'],'balance':report['balance'],'spending_authority_restored':False,'freshness':'NOT_CHECKED','source_truth_proven':False}

class Probe:
    """Receiver-owned one-use challenge, plus an externally retained floor.

    A new process MUST obtain a fresh out-of-band floor; no implicit reset.
    """
    def __init__(self,genesis_pin,floor,now,nonce):
        require(floor is not None and type(nonce) is str and len(nonce)==64 and bytes.fromhex(nonce).hex()==nonce,'floor/nonce')
        self.pin=genesis_pin;self.floor=floor;self.now=now;self.nonce=nonce;self.used=False
    def verify(self,archive,replies,now):
        require(not self.used and self.now<=now<=self.now+30,'status challenge expired/replayed')
        self.used=True
        r=verify_archive(archive,self.pin,self.floor);c=archive['config']
        require(type(replies) is dict and 2<=len(replies)<=3 and set(replies)<=set(c['nodes']),'fresh status quorum')
        for k,s in replies.items():
            b=signature(s,c['nodes'][k],'status')
            require(set(b)=={'profile','node','nonce','config','epoch','tip','index','pending','issued_at','expires_at'},'status fields')
            require(b['profile']==P and b['node']==k and b['nonce']==self.nonce and b['config']==h(c) and b['epoch']==r['epoch'] and b['tip']==r['tip'] and b['index']==r['index'] and b['pending'] is False,'status binding/pending/stale')
            require(type(b['issued_at']) is int and type(b['expires_at']) is int and self.now<=b['issued_at']<=now<=b['expires_at']==b['issued_at']+30,'clock/TTL')
        r['freshness']='TWO_PINNED_NODES_NONCE_BOUND';return r

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--genesis-pin',required=True);p.add_argument('--floor',type=Path,required=True);a=p.parse_args()
    from tools.independent_capital_verifier import strict_json
    print(json.dumps(verify_archive(strict_json(a.archive),a.genesis_pin,strict_json(a.floor)),sort_keys=True))
if __name__=='__main__':main()
