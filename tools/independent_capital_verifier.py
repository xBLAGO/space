"""Second implementation. Stdlib + cryptography only; no BLAGO runtime imports.

Private lab audit, not public source-truth proof or independent-operator audit.
Trust pins MUST arrive out of band. Node payload binding is explicitly trusted.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROFILE='uel-checkpoint-0.3-lab.1'
ZERO='0'*64


class InvalidProof(ValueError):pass


def require(condition,message):
    if not condition:raise InvalidProof(message)


def wire(value):return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'))
def hash_json(value):return hashlib.sha256(wire(value).encode()).hexdigest()
def hash_bytes(value):return hashlib.sha256(value).hexdigest()
def integer(value):return type(value) is int

def identifier(value):
    require(type(value) is str and re.fullmatch('[a-zA-Z0-9_-]{1,80}',value),'identifier')
    return value

def money(value):
    require(integer(value) and 0<value<=10**12,'minor amount')
    return value


def sig(key,signature,value,domain):
    try:
        require(type(key) is str and re.fullmatch('[0-9a-f]{64}',key) and type(signature) is str and re.fullmatch('[0-9a-f]{128}',signature),'signature encoding')
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(signature),(domain+wire(value)).encode())
    except Exception as exc:raise InvalidProof('signature verification') from exc


def policy_check(policy):
    require(set(policy)=={'format','id','version','currency','members','evidence_key','privacy_key','governors','governance_quorum','spend_quorum','activation_delay','delegation_seconds'},'policy fields')
    require(policy['format']=='capital-governance-policy-2' and integer(policy['version']) and policy['version']>=1 and policy['currency']=='RUB','policy format')
    identifier(policy['id']);members=policy['members'];require(type(members) is dict and 3<=len(members)<=100,'members')
    public=[];approve=0;gs=[]
    for kid,m in members.items():
        identifier(kid);require(set(m)=={'roles','public_key','expires_at'} and integer(m['expires_at']),'member fields')
        roles=m['roles'];require(type(roles) is list and len(roles)==len(set(roles)) and set(roles)<={'VIEW','DECLARE','VERIFY','PROPOSE','APPROVE','EXECUTE','REVOKE','GOV'},'roles')
        require(not ('EXECUTE' in roles and set(roles)&{'PROPOSE','APPROVE'}),'separation')
        require(type(m['public_key']) is str and re.fullmatch('[0-9a-f]{64}',m['public_key']),'member key')
        public.append(m['public_key']);approve+=int('APPROVE' in roles)
        if 'GOV' in roles:gs.append(kid)
    require(len(set(public))==len(public),'unique keys')
    require(policy['governors']==sorted(gs) and len(gs)>=3,'electorate')
    require(integer(policy['governance_quorum']) and 2*len(gs)//3+1<=policy['governance_quorum']<=len(gs),'governance quorum')
    require(integer(policy['spend_quorum']) and 2<=policy['spend_quorum']<=approve,'spend quorum')
    require(integer(policy['activation_delay']) and 60<=policy['activation_delay']<=86400,'delay')
    require(integer(policy['delegation_seconds']) and 60<=policy['delegation_seconds']<=3600,'delegation ceiling')
    for name in ('privacy_key','evidence_key'):require(type(policy[name]) is str and re.fullmatch('[0-9a-f]{64}',policy[name]),'trust anchor')
    require(len(set(public+[policy['privacy_key'],policy['evidence_key']]))==len(public)+2,'independent anchor keys')


class Replay:
    def __init__(self,policy):
        policy_check(policy)
        self.s={'policy':copy.deepcopy(policy),'lots':{},'proposals':{},'approvals':{},'reservations':{},'evidence_used':[], 'revoked_keys':[],
                'delegations':{},'policy_proposals':{},'exited':[],'genesis_policy_hash':hash_json(policy)}

    def active(self,k,at,role=None):
        s=self.s;p=s['policy'];m=p['members'].get(k)
        return bool(m and k not in s['revoked_keys']+s['exited'] and at<m['expires_at'] and (role is None or role in m['roles']))

    def voters(self,a,at):
        s=self.s;p=s['proposals'][a['proposal']];policy=s['policy'];valid={}
        if p['governance_policy']!=hash_json(policy) or not self.active(p['proposer'],at) or at>=p['expires_at']:return valid
        for root,v in a['votes'].items():
            k=v['signer']
            if not self.active(root,at,'APPROVE') or not self.active(k,at) or p['proposer'] in (root,k) or 'EXECUTE' in policy['members'][k]['roles']:continue
            if v['delegation'] is None:
                if root==k:valid[root]=v
            else:
                d=s['delegations'].get(v['delegation'])
                if d and d['active'] and (d['grantor'],d['recipient'],d['proposal'])==(root,k,p['proposal']) and at<d['expires_at'] and p['amount_minor']<=d['max_amount_minor']:valid[root]=v
        return valid

    def revoke_effects(self,at):
        s=self.s
        for aid,a in s['approvals'].items():
            if a['status']=='CONSUMED' and len(self.voters(a,at))<s['policy']['spend_quorum']:
                a['status']='REVOKED'
                for r in s['reservations'].values():
                    if r['approval']==aid and r['status']=='RESERVED':r['status']='RELEASED'
        for r in s['reservations'].values():
            if r['status']=='RESERVED' and not self.active(r['executor'],at):r['status']='RELEASED';s['approvals'][r['approval']]['status']='REVOKED'
        for q in s['policy_proposals'].values():
            if q['status']=='PENDING' and sum(self.active(k,at,'GOV') for k in q['votes'])<s['policy']['governance_quorum']:q['ready_at']=None

    def replacement(self,new):
        old=self.s['policy'];policy_check(new)
        require(new['id']==old['id'] and new['version']==old['version']+1 and new['currency']==old['currency'] and new['governors']==old['governors'] and set(new['members'])==set(old['members']),'replacement scope')
        require(all(new[k]==old[k] for k in ('privacy_key','evidence_key')) and all(new['members'][k]['public_key']==m['public_key'] for k,m in old['members'].items()),'anchor replacement')
        require(new['governance_quorum']>=old['governance_quorum'] and new['spend_quorum']>=old['spend_quorum'] and new['activation_delay']>=old['activation_delay'] and new['delegation_seconds']<=old['delegation_seconds'],'downgrade')

    def evidence(self,c,kind,subject,value):
        s=self.s;e=c['evidence'];require(type(e) is dict and set(e)=={'body','signature'},'evidence schema');body=e['body']
        require(body=={'format':'capital-fixture-evidence-1','fund':c['fund'],'kind':kind,'subject':subject,'currency':'RUB','amount_minor':value,'receipt_id':identifier(body.get('receipt_id'))},'evidence binding')
        sig(s['policy']['evidence_key'],e['signature'],body,'BLAGO-CAPITAL-EVIDENCE-1\n')
        require(body['receipt_id'] not in s['evidence_used'],'receipt replay');s['evidence_used'].append(body['receipt_id'])

    def balances(self):
        s=self.s;verified=sum(v['amount'] for v in s['lots'].values() if v['verified']);declared=sum(v['amount'] for v in s['lots'].values())
        held=out=refund=0;usage={k:0 for k in s['lots']}
        for r in s['reservations'].values():
            if r['status']=='RESERVED':held+=r['amount']
            if r['status']=='SPENT':out+=r['amount'];refund+=r['refunded']
            for lid,v in r['allocations'].items():
                if r['status']=='RESERVED':usage[lid]+=v
                elif r['status']=='SPENT':usage[lid]+=v-r['refund_allocations'].get(lid,0)
        for k,v in usage.items():require(0<=v<=s['lots'][k]['amount'] and (not v or s['lots'][k]['verified']),'lot invariant')
        available=verified-held-out+refund;require(min(available,held,out,refund)>=0 and refund<=out,'cash invariant')
        return {'currency':'RUB','declared_minor':declared,'unverified_minor':declared-verified,'verified_minor':verified,'available_minor':available,'reserved_minor':held,'spent_minor':out,'refunded_minor':refund,'net_spent_minor':out-refund,'real_funds_verified':False},usage

    def apply(self,c,signature,at):
        s=self.s;p=s['policy'];k=c['key_id'];t=c['type'];d=c['data']
        require(set(c)=={'fund','version','policy_hash','operation_id','key_id','type','expires_at','data','evidence'} and len(wire(c))<=100000,'command fields')
        identifier(k);identifier(c['operation_id'])
        require(c['fund']==p['id'] and c['policy_hash']==hash_json(p) and integer(c['version']) and c['version']>0 and integer(c['expires_at']) and at<c['expires_at']<=at+86400 and type(d) is dict,'command scope')
        shapes={'CapitalReceived':[{'lot','amount_minor','source'}],'CapitalVerified':[{'lot'}],'ProposalCreated':[{'proposal','amount_minor','purpose','destination','expires_at'}],
                'ApprovalGranted':[{'proposal','approval'},{'proposal','approval','delegation'}],'ApprovalRevoked':[{'approval','principal'}],
                'BudgetReserved':[{'approval','reservation','allocations'}],'ReservationReleased':[{'reservation'}],'CapitalSpent':[{'reservation'}],'RefundConfirmed':[{'reservation','amount_minor'}],
                'DelegationGranted':[{'delegation','recipient','proposal','max_amount_minor','expires_at'}],'DelegationRevoked':[{'delegation'}],
                'MemberExited':[set()],'PrivacyMemberExited':[{'principal'}],'PolicyProposed':[{'proposal','policy'}],'PolicyVoted':[{'proposal'}],'PolicyVoteRevoked':[{'proposal'}],'PolicyActivated':[{'proposal'}],'AuthorityRevoked':[{'key_id','endorsements'}]}
        require(t in shapes and set(d) in shapes[t],'action shape')
        if t=='PrivacyMemberExited':require(d['principal']==k,'privacy target');sig(p['privacy_key'],signature,c,'BLAGO-CAPITAL-PRIVACY-0.2\n')
        else:require(self.active(k,at),'inactive key');sig(p['members'][k]['public_key'],signature,c,'BLAGO-CAPITAL-GOVERNANCE-0.2\n')
        require(t in ('CapitalVerified','CapitalSpent','RefundConfirmed') or c['evidence'] is None,'unexpected evidence')
        if t in ('MemberExited','PrivacyMemberExited'):
            require(k in p['members'] and k not in s['exited'],'exit repeated');s['exited'].append(k);self.revoke_effects(at)
        elif t=='CapitalReceived':
            require(self.active(k,at,'DECLARE'),'declare role');lid=identifier(d['lot']);identifier(d['source']);money(d['amount_minor'])
            require(lid not in s['lots'] and all(v['source']!=d['source'] for v in s['lots'].values()),'source duplicate');s['lots'][lid]={'amount':d['amount_minor'],'source':d['source'],'declarer':k,'verified':False}
        elif t=='CapitalVerified':
            l=s['lots'][d['lot']];require(self.active(k,at,'VERIFY') and not l['verified'] and l['declarer']!=k,'verify role/independence');self.evidence(c,'RECEIVED',d['lot'],l['amount']);l['verified']=True
        elif t=='ProposalCreated':
            require(self.active(k,at,'PROPOSE'),'propose role');pid=identifier(d['proposal']);money(d['amount_minor']);identifier(d['purpose']);identifier(d['destination'])
            require(pid not in s['proposals'] and integer(d['expires_at']) and at<d['expires_at']<=at+86400,'proposal duplicate/expiry')
            s['proposals'][pid]=dict(d,proposer=k,version=c['version'],governance_policy=hash_json(p))
        elif t=='RefundConfirmed':
            r=s['reservations'][d['reservation']];v=money(d['amount_minor'])
            require(self.active(k,at,'VERIFY') and r['status']=='SPENT' and k!=r['executor'] and r['refunded']+v<=r['amount'],'refund bounds');self.evidence(c,'REFUND',d['reservation'],v)
            left=v
            for lid,q in sorted(r['allocations'].items()):
                part=min(left,q-r['refund_allocations'].get(lid,0));left-=part;r['refund_allocations'][lid]=r['refund_allocations'].get(lid,0)+part
            r['refunded']+=v
        elif t=='DelegationGranted':
            require(self.active(k,at,'APPROVE'),'direct delegation');did=identifier(d['delegation']);who=d['recipient'];q=s['proposals'][d['proposal']]
            require(did not in s['delegations'] and self.active(who,at) and who not in (k,q['proposer']) and 'EXECUTE' not in p['members'][who]['roles'],'delegation recipient')
            require(integer(d['expires_at']) and at<d['expires_at']<=min(at+p['delegation_seconds'],p['members'][k]['expires_at'],p['members'][who]['expires_at'],q['expires_at']),'delegation expiry')
            require(money(d['max_amount_minor'])>=q['amount_minor'] and q['governance_policy']==hash_json(p) and len(s['delegations'])<128,'delegation amount/scope');s['delegations'][did]=dict(d,grantor=k,active=True)
        elif t=='DelegationRevoked':
            grant=s['delegations'][d['delegation']];require(grant['grantor']==k and grant['active'],'revoke grant');grant['active']=False;self.revoke_effects(at)
        elif t=='ApprovalGranted':
            q=s['proposals'][d['proposal']];aid=identifier(d['approval']);did=d.get('delegation');root=k if did is None else s['delegations'][did]['grantor'];v={'signer':k,'delegation':did}
            probe={'proposal':d['proposal'],'votes':{root:v}}
            require(root in self.voters(probe,at),'approval scope');a=s['approvals'].get(aid)
            if a is None:
                require(all(x['proposal']!=d['proposal'] for x in s['approvals'].values()),'existing approval round')
                a={'proposal':d['proposal'],'votes':{},'status':'COLLECTING','action_hash':hash_json(q),'policy_hash':hash_json(p)};s['approvals'][aid]=a
            require(a['proposal']==d['proposal'] and a['status'] in ('COLLECTING','GRANTED') and root not in a['votes'] and all(x['signer']!=k for x in a['votes'].values()),'duplicate authority')
            a['votes'][root]=v;a['status']='GRANTED' if len(self.voters(a,at))>=p['spend_quorum'] else 'COLLECTING'
        elif t=='ApprovalRevoked':
            a=s['approvals'][d['approval']];v=a['votes'].get(d['principal']);require(v and k in (d['principal'],v['signer']),'vote revoke')
            del a['votes'][d['principal']];self.revoke_effects(at)
            if a['status']=='GRANTED' and len(self.voters(a,at))<p['spend_quorum']:a['status']='COLLECTING'
        elif t=='BudgetReserved':
            a=s['approvals'][d['approval']];q=s['proposals'][a['proposal']];votes=self.voters(a,at);rid=identifier(d['reservation']);alloc=d['allocations']
            require(self.active(k,at,'EXECUTE') and a['status']=='GRANTED' and len(votes)>=p['spend_quorum'] and k!=q['proposer'] and k not in votes and k not in [v['signer'] for v in votes.values()] and rid not in s['reservations'],'reserve authority')
            require(type(alloc) is dict and 1<=len(alloc)<=128,'allocation schema');total=sum(money(v) for v in alloc.values());_,used=self.balances();require(total<=q['amount_minor'],'approved amount')
            for lid,v in alloc.items():require(s['lots'][lid]['verified'] and used[lid]+v<=s['lots'][lid]['amount'],'source funds')
            s['reservations'][rid]={'approval':d['approval'],'amount':total,'allocations':dict(alloc),'status':'RESERVED','executor':k,'refunded':0,'refund_allocations':{}};a['status']='CONSUMED'
        elif t=='CapitalSpent':
            r=s['reservations'][d['reservation']];a=s['approvals'][r['approval']]
            require(self.active(k,at,'EXECUTE') and k==r['executor'] and r['status']=='RESERVED' and a['status']=='CONSUMED' and len(self.voters(a,at))>=p['spend_quorum'],'spend current quorum')
            self.evidence(c,'SPENT',d['reservation'],r['amount']);r['status']='SPENT';a['status']='SPENT'
        elif t=='ReservationReleased':
            r=s['reservations'][d['reservation']];require(self.active(k,at,'EXECUTE') and k==r['executor'] and r['status']=='RESERVED','release authority');r['status']='RELEASED';s['approvals'][r['approval']]['status']='REVOKED'
        elif t=='PolicyProposed':
            require(self.active(k,at,'GOV'),'governor');self.replacement(d['policy']);pid=identifier(d['proposal']);require(pid not in s['policy_proposals'] and len(s['policy_proposals'])<64,'policy proposal')
            s['policy_proposals'][pid]={'policy':copy.deepcopy(d['policy']),'base':hash_json(p),'votes':[],'ready_at':None,'status':'PENDING'}
        elif t in ('PolicyVoted','PolicyVoteRevoked','PolicyActivated'):
            require(self.active(k,at,'GOV'),'direct governance');q=s['policy_proposals'][d['proposal']];require(q['status']=='PENDING' and q['base']==hash_json(p),'current policy proposal')
            if t=='PolicyVoted':
                require(k not in q['votes'],'duplicate governor');q['votes'].append(k);q['ready_at']=at+p['activation_delay'] if sum(self.active(x,at,'GOV') for x in q['votes'])>=p['governance_quorum'] else None
            elif t=='PolicyVoteRevoked':require(k in q['votes'],'missing vote');q['votes'].remove(k);q['ready_at']=None
            else:
                require(sum(self.active(x,at,'GOV') for x in q['votes'])>=p['governance_quorum'] and q['ready_at'] is not None and at>=q['ready_at'],'policy quorum/delay');self.replacement(q['policy']);q['status']='ACTIVATED';s['policy']=copy.deepcopy(q['policy'])
                for a in s['approvals'].values():
                    if a['status']!='SPENT':a['status']='REVOKED'
                for r in s['reservations'].values():
                    if r['status']=='RESERVED':r['status']='RELEASED'
                for grant in s['delegations'].values():grant['active']=False
        elif t=='AuthorityRevoked':
            target=d['key_id'];endorse=d['endorsements'];require(target in p['members'] and target!=k and self.active(k,at,'GOV') and type(endorse) is dict and len(endorse)>=p['governance_quorum'],'revocation quorum')
            body={x:y for x,y in c.items() if x!='data'};body['data']={'key_id':target}
            for root,signature in endorse.items():
                require(root!=target and self.active(root,at,'GOV'),'revocation endorser');sig(p['members'][root]['public_key'],signature,body,'BLAGO-CAPITAL-REVOKE-0.2\n')
            require(target not in s['revoked_keys'],'already revoked');s['revoked_keys'].append(target);self.revoke_effects(at)
        require(len(s['lots'])<=128 and len(s['proposals'])<=256,'lab limits');self.balances()

    def amount(self,event):
        t=event['type']
        if t=='PolicyEnrolled':return 0
        d=event['command']['data'];s=self.s
        if t in ('CapitalReceived','ProposalCreated','RefundConfirmed'):return d['amount_minor']
        if t=='CapitalVerified':return s['lots'][d['lot']]['amount']
        if t in ('ApprovalGranted','ApprovalRevoked'):return s['proposals'][s['approvals'][d['approval']]['proposal']]['amount_minor']
        if t in ('BudgetReserved','ReservationReleased','CapitalSpent'):return s['reservations'][d['reservation']]['amount']
        return 0


def verify_bundle(bundle,pins):
    """Return verified accounting; unknown/missing external pins always reject."""
    try:return _verify(bundle,pins)
    except InvalidProof:raise
    except Exception as exc:raise InvalidProof('Malformed or unsupported proof') from exc


def _verify(b,pins):
    require(set(b)=={'format','history','uel_headers','uel_leaves','checkpoint','binding','binding_signature'} and b['format']=='capital-governance-proof-2','proof schema')
    require(set(pins)=={'policy_hash','checkpoint_hash','custodian_key','stream','node_key'},'external pins')
    history=b['history'];events=history['events'];require(history['format']=='capital-private-audit-1' and type(events) is list and 1<=len(events)<=1024,'history shape')
    first=events[0];p=first['policy'];policy_check(p)
    require(hash_json(p)==pins['policy_hash'] and p['privacy_key']==pins['node_key'] and p['id']==history['fund'] and first['type']=='PolicyEnrolled','genesis pin')
    require(set(first['signatures'])==set(p['members']),'genesis signatures')
    require(integer(first['recorded_at']) and all(first['recorded_at']<m['expires_at'] for m in p['members'].values()),'initial key expiry')
    for key,signature in first['signatures'].items():sig(p['members'][key]['public_key'],signature,p,'BLAGO-CAPITAL-GOVERNANCE-POLICY-0.2\n')
    engine=Replay(p);prior=ZERO;last_time=0;seen=set();ops=set()
    for n,e in enumerate(events,1):
        require(e['fund']==history['fund'] and e['version']==n and integer(e['version']) and e['event_id'] not in seen and e['previous']==prior and integer(e['recorded_at']) and e['recorded_at']>=last_time,'event order')
        seen.add(e['event_id']);last_time=e['recorded_at']
        if n>1:
            c=e['command'];require(c['version']==n-1 and c['type']==e['type'] and c['operation_id'] not in ops,'command order/replay');ops.add(c['operation_id']);engine.apply(c,e['signature'],last_time)
        require(e['currency']=='RUB' and integer(e['amount_minor']) and e['amount_minor']==engine.amount(e) and hash_json(engine.s)==e['state_hash'],'deterministic state/amount')
        prior=hash_json(e)
    checkpoint=b['checkpoint'];require(set(checkpoint)=={'body','signature'} and hash_json(checkpoint)==pins['checkpoint_hash'],'external checkpoint frontier')
    cb=checkpoint['body'];require(set(cb)=={'profile','stream','generation','previous','journal_seq','journal_root','uel_seq','uel_root'} and cb['profile']==PROFILE and cb['stream']==pins['stream'],'checkpoint scope')
    sig(pins['custodian_key'],checkpoint['signature'],cb,PROFILE+'/checkpoint\0')
    for k in ('generation','journal_seq','uel_seq'):require(integer(cb[k]) and 0<=cb[k]<=65536,'checkpoint counters')
    require(cb['uel_seq']<=cb['journal_seq'],'checkpoint order')
    leaves=b['uel_leaves'];require(type(leaves) is list and len(leaves)==cb['uel_seq'],'UEL completeness')
    root=ZERO
    for leaf in leaves:
        require(type(leaf) is str and re.fullmatch('[0-9a-f]{64}',leaf),'UEL leaf')
        root=hash_bytes((PROFILE+'/chain\0').encode()+bytes.fromhex(root)+bytes.fromhex(leaf))
    require(root==cb['uel_root'],'UEL checkpoint root')
    headers=b['uel_headers'];require(type(headers) is list and len(headers)==len(events),'one source per event')
    causal=ZERO;last_seq=0;object_ref=None;refs=[];source_refs=set()
    for n,h in enumerate(headers,1):
        require(set(h)=={'sequence','source_kind','source_ref','object_ref','source_version','phase','coverage','previous','causal_ref','payload_ref','source_state','record_ref'},'UEL header fields')
        require(integer(h['sequence']) and last_seq<h['sequence']<=len(leaves) and h['source_version']==n and integer(h['source_version']) and h['source_kind']=='CAPITAL' and h['phase']=='OBSERVED' and h['coverage']=='FROM_ORIGIN' and h['causal_ref']==causal,'UEL source sequence')
        for key in ('source_ref','object_ref','previous','causal_ref','payload_ref','source_state','record_ref'):require(type(h[key]) is str and re.fullmatch('[0-9a-f]{64}',h[key]),'UEL hash encoding')
        if object_ref is None:object_ref=h['object_ref']
        require(h['object_ref']==object_ref and h['source_ref'] not in source_refs and h['record_ref'] not in refs,'UEL unique source')
        if h['sequence']==last_seq+1:require(h['previous']==causal,'adjacent UEL order')
        require(hash_bytes((PROFILE+'/private-uel\0'+wire(h)).encode())==leaves[h['sequence']-1],'UEL inclusion')
        source_refs.add(h['source_ref']);refs.append(h['record_ref']);causal=h['record_ref'];last_seq=h['sequence']
    binding=b['binding'];require(binding=={'format':'capital-uel-binding-2','fund':history['fund'],'history_head':prior,'history_count':len(events),'record_refs':refs,'checkpoint_hash':pins['checkpoint_hash']},'node binding')
    sig(pins['node_key'],b['binding_signature'],binding,'BLAGO-CAPITAL-EXPORT-0.2\n')
    balances,_=engine.balances()
    return {'status':'PASS','events':len(events),'policy_version':engine.s['policy']['version'],'balance':balances,'uel_inclusion':'PINNED_SIGNED_CHECKPOINT','payload_binding':'TRUSTED_NODE_ATTESTATION','implementation':'INDEPENDENT_NO_RUNTIME_IMPORTS','independent_operator':'NOT EXECUTED','source_truth_proven':False}


def strict_json(path):
    require(Path(path).stat().st_size<=20*1024*1024,'file bound')
    def pairs(rows):
        d={}
        for k,v in rows:
            require(k not in d,'duplicate JSON key');d[k]=v
        return d
    return json.loads(Path(path).read_text(),object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(InvalidProof('nonfinite JSON')))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle',type=Path);p.add_argument('--pins',type=Path,required=True);a=p.parse_args()
    print(json.dumps(verify_bundle(strict_json(a.bundle),strict_json(a.pins)),sort_keys=True))

if __name__=='__main__':main()
