"""Synthetic durable capital domain. No payment transport or real-human authority."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
import re
import time
from uuid import uuid4
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from xblago.core import capital_schema as schema
from xblago.core.security_journal import canonical, JournalUnavailable
from xblago.domain.local_ledger import LocalLedger
from xblago.integrations.web import ApiError

DOMAIN = 'BLAGO-CAPITAL-SYNTHETIC-0.1\n'
ROLES = {'CapitalReceived':'DECLARE','CapitalVerified':'VERIFY','ProposalCreated':'PROPOSE',
         'ApprovalGranted':'APPROVE','ApprovalRevoked':'APPROVE','BudgetReserved':'EXECUTE',
         'ReservationReleased':'EXECUTE','CapitalSpent':'EXECUTE','RefundConfirmed':'VERIFY','KeyRevoked':'REVOKE'}


class CapitalError(ValueError):pass


def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def ident(value):
    if type(value) is not str or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',value):raise CapitalError('Opaque bounded identifier required')
    return value


def amount(value):
    if type(value) is not int or not 0 < value <= 10**12:raise CapitalError('Positive bounded integer minor units required')
    return value


def verify(key, signature, body, domain=DOMAIN):
    try:
        if type(signature) is not str or len(signature)!=128:raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(bytes.fromhex(signature),(domain+canonical(body)).encode())
    except (ValueError,TypeError,InvalidSignature) as exc:raise CapitalError('Invalid synthetic signature') from exc


def _legacy_validate_policy(policy):
    if type(policy) is not dict or set(policy)!={'format','id','version','currency','members','evidence_key'}:raise CapitalError('Policy schema')
    if policy['format']!='capital-lab-policy-1' or policy['currency']!='RUB' or type(policy['version']) is not int or policy['version']!=1:raise CapitalError('Unsupported policy')
    ident(policy['id'])
    members=policy['members']
    if type(members) is not dict or not 3<=len(members)<=100:raise CapitalError('Bounded pinned membership required')
    keys=set()
    for key_id,row in members.items():
        ident(key_id)
        if type(row) is not dict or set(row)!={'public_key','roles','expires_at'}:raise CapitalError('Member schema')
        if type(row['roles']) is not list or len(set(row['roles']))!=len(row['roles']) or not set(row['roles'])<=set(ROLES.values())|{'VIEW'}:raise CapitalError('Roles')
        if type(row['expires_at']) is not int:raise CapitalError('Key expiry')
        key=bytes.fromhex(row['public_key'])
        if len(key)!=32 or key in keys:raise CapitalError('Distinct pinned keys required')
        keys.add(key)
    evidence=bytes.fromhex(policy['evidence_key'])
    if len(evidence)!=32 or evidence in keys:raise CapitalError('Independent fixture evidence key required')


def _legacy_empty(policy,validate=True):
    if validate:_legacy_validate_policy(policy)
    return {'policy':copy.deepcopy(policy),'lots':{},'proposals':{},'approvals':{},'reservations':{},'evidence_used':[], 'revoked_keys':[]}


def validate_policy(policy):
    from xblago.domain import capital_governance as gov
    return gov.validate_policy(policy) if gov.is_policy(policy) else _legacy_validate_policy(policy)


def empty(policy):
    from xblago.domain import capital_governance as gov
    return gov.initial(policy) if gov.is_policy(policy) else _legacy_empty(policy)


def policy_domain(policy):
    from xblago.domain import capital_governance as gov
    return gov.POLICY_DOMAIN if gov.is_policy(policy) else 'BLAGO-CAPITAL-POLICY-1\n'


def command_domain(state):
    from xblago.domain import capital_governance as gov
    return gov.DOMAIN if gov.is_policy(state['policy']) else DOMAIN


def transition(previous,command,signature,recorded_at):
    from xblago.domain import capital_governance as gov
    return gov.transition(previous,command,signature,recorded_at) if gov.is_policy(previous['policy']) else _legacy_transition(previous,command,signature,recorded_at)


def balance(state):
    received=sum(l['amount'] for l in state['lots'].values())
    verified=sum(l['amount'] for l in state['lots'].values() if l['verified'])
    reserved=spent=refunded=0
    allocations={k:0 for k in state['lots']}
    for r in state['reservations'].values():
        if r['status']=='RESERVED':reserved+=r['amount']
        if r['status']=='SPENT':spent+=r['amount'];refunded+=r['refunded']
        for lid,v in r['allocations'].items():
            if r['status']=='RESERVED':allocations[lid]+=v
            if r['status']=='SPENT':allocations[lid]+=v-r['refund_allocations'].get(lid,0)
    for lid,used in allocations.items():
        l=state['lots'][lid]
        if used<0 or used>l['amount'] or (used and not l['verified']):raise CapitalError('Lot conservation violated')
    available=verified-reserved-spent+refunded
    if min(available,reserved,spent,refunded)<0 or refunded>spent:raise CapitalError('Capital conservation violated')
    return {'currency':'RUB','declared_minor':received,'unverified_minor':received-verified,'verified_minor':verified,
            'available_minor':available,'reserved_minor':reserved,'spent_minor':spent,'refunded_minor':refunded,
            'net_spent_minor':spent-refunded,'real_funds_verified':False},allocations


def signed_evidence(state, command, kind, subject, value):
    evidence=command['evidence']
    if type(evidence) is not dict or set(evidence)!={'body','signature'}:raise CapitalError('Signed fixture evidence required')
    body=evidence['body']
    expected={'format':'capital-fixture-evidence-1','fund':command['fund'],'kind':kind,'subject':subject,
              'currency':'RUB','amount_minor':value,'receipt_id':ident(body.get('receipt_id'))}
    if body!=expected:raise CapitalError('Evidence substitution')
    verify(state['policy']['evidence_key'],evidence['signature'],body,'BLAGO-CAPITAL-EVIDENCE-1\n')
    receipt=body['receipt_id']
    if receipt in state['evidence_used']:raise CapitalError('Evidence already consumed')
    state['evidence_used'].append(receipt)


def _legacy_transition(previous, command, signature, recorded_at,domain=DOMAIN):
    """Pure reference transition, also used by a separate offline verifier process."""
    s=copy.deepcopy(previous);policy=s['policy']
    fields={'fund','version','policy_hash','operation_id','key_id','type','expires_at','data','evidence'}
    if type(command) is not dict or set(command)!=fields or len(canonical(command))>20000:raise CapitalError('Command schema')
    ident(command['fund']);ident(command['operation_id']);ident(command['key_id'])
    if type(command['version']) is not int or command['version']<1:raise CapitalError('Version')
    if command['policy_hash']!=digest(policy):raise CapitalError('Policy substitution')
    member=policy['members'].get(command['key_id']);kind=command['type'];actor=command['key_id']
    if not member or actor in s['revoked_keys'] or ROLES.get(kind) not in member['roles']:raise CapitalError('Authority denied')
    if type(command['expires_at']) is not int or not recorded_at < command['expires_at'] <= recorded_at+86400 or recorded_at>=member['expires_at']:raise CapitalError('Authority expired')
    verify(member['public_key'],signature,command,domain)
    d=command['data']
    if type(d) is not dict:raise CapitalError('Data object required')
    required={
        'CapitalReceived':{'lot','amount_minor','source'},'CapitalVerified':{'lot'},
        'ProposalCreated':{'proposal','amount_minor','purpose','destination','expires_at'},
        'ApprovalGranted':{'proposal','approval'},'ApprovalRevoked':{'approval'},
        'BudgetReserved':{'approval','reservation','allocations'},'ReservationReleased':{'reservation'},
        'CapitalSpent':{'reservation'},'RefundConfirmed':{'reservation','amount_minor'},'KeyRevoked':{'key_id'}}
    if set(d)!=required[kind]:raise CapitalError('Action data schema')
    if kind not in ('CapitalVerified','CapitalSpent','RefundConfirmed') and command['evidence'] is not None:raise CapitalError('Unexpected evidence')
    if kind=='CapitalReceived':
        lid=ident(d['lot']);amount(d['amount_minor']);ident(d['source'])
        if lid in s['lots'] or any(l['source']==d['source'] for l in s['lots'].values()):raise CapitalError('Duplicate capital source')
        s['lots'][lid]={'amount':d['amount_minor'],'source':d['source'],'declarer':actor,'verified':False}
    elif kind=='CapitalVerified':
        l=s['lots'][d['lot']]
        if l['verified'] or l['declarer']==actor:raise CapitalError('Independent verification required')
        signed_evidence(s,command,'RECEIVED',d['lot'],l['amount']);l['verified']=True
    elif kind=='ProposalCreated':
        pid=ident(d['proposal']);amount(d['amount_minor']);ident(d['purpose']);ident(d['destination'])
        if pid in s['proposals'] or type(d['expires_at']) is not int or not recorded_at<d['expires_at']<=recorded_at+86400:raise CapitalError('Proposal conflict/expiry')
        s['proposals'][pid]=dict(d,proposer=actor,version=command['version'])
    elif kind=='ApprovalGranted':
        p=s['proposals'][d['proposal']];aid=ident(d['approval'])
        if p['proposer']==actor or p['proposer'] in s['revoked_keys'] or recorded_at>=policy['members'][p['proposer']]['expires_at'] or aid in s['approvals'] or recorded_at>=p['expires_at']:raise CapitalError('Self/duplicate/expired approval')
        if any(a['proposal']==d['proposal'] and a['status']!='REVOKED' for a in s['approvals'].values()):raise CapitalError('Proposal already approved')
        s['approvals'][aid]={'proposal':d['proposal'],'approver':actor,'status':'GRANTED','action_hash':digest(p),'policy_hash':digest(policy)}
    elif kind=='ApprovalRevoked':
        a=s['approvals'][d['approval']]
        if a['approver']!=actor or a['status']=='SPENT':raise CapitalError('Cannot revoke another approval or undo spend')
        a['status']='REVOKED'
        for r in s['reservations'].values():
            if r['approval']==d['approval'] and r['status']=='RESERVED':r['status']='RELEASED'
    elif kind=='BudgetReserved':
        a=s['approvals'][d['approval']];p=s['proposals'][a['proposal']];rid=ident(d['reservation'])
        if a['status']!='GRANTED' or a['approver'] in s['revoked_keys'] or p['proposer'] in s['revoked_keys'] or recorded_at>=policy['members'][a['approver']]['expires_at'] or a['approver']==actor or p['proposer']==actor or recorded_at>=p['expires_at'] or rid in s['reservations']:raise CapitalError('Reservation authority/expiry/reuse')
        allocations=d['allocations']
        if type(allocations) is not dict or not 1<=len(allocations)<=128:raise CapitalError('Explicit lot allocation required')
        total=sum(amount(v) for v in allocations.values())
        if total>p['amount_minor']:raise CapitalError('Approval amount exceeded')
        _,used=balance(s)
        for lid,v in allocations.items():
            l=s['lots'][lid]
            if not l['verified'] or used[lid]+v>l['amount']:raise CapitalError('Insufficient verified lot')
        s['reservations'][rid]={'approval':d['approval'],'amount':total,'allocations':dict(allocations),'status':'RESERVED','executor':actor,'refunded':0,'refund_allocations':{}}
        a['status']='CONSUMED'
    elif kind=='ReservationReleased':
        r=s['reservations'][d['reservation']]
        if r['status']!='RESERVED' or r['executor']!=actor:raise CapitalError('Only current executor reservation can release')
        r['status']='RELEASED'  # Consumed approval cannot be reused.
    elif kind=='CapitalSpent':
        r=s['reservations'][d['reservation']];a=s['approvals'][r['approval']];p=s['proposals'][a['proposal']]
        if r['status']!='RESERVED' or a['status']!='CONSUMED' or r['executor']!=actor or a['approver'] in s['revoked_keys'] or p['proposer'] in s['revoked_keys'] or recorded_at>=policy['members'][a['approver']]['expires_at'] or recorded_at>=policy['members'][p['proposer']]['expires_at'] or recorded_at>=p['expires_at']:raise CapitalError('Spend authority/expiry/replay')
        signed_evidence(s,command,'SPENT',d['reservation'],r['amount']);r['status']='SPENT';a['status']='SPENT'
    elif kind=='RefundConfirmed':
        r=s['reservations'][d['reservation']];value=amount(d['amount_minor'])
        if r['status']!='SPENT' or actor==r['executor'] or value+r['refunded']>r['amount']:raise CapitalError('Refund exceeds spent or self-confirms')
        signed_evidence(s,command,'REFUND',d['reservation'],value)
        remaining=value
        for lid,v in sorted(r['allocations'].items()):
            part=min(remaining,v-r['refund_allocations'].get(lid,0));remaining-=part
            r['refund_allocations'][lid]=r['refund_allocations'].get(lid,0)+part
        r['refunded']+=value
    elif kind=='KeyRevoked':
        key=ident(d['key_id'])
        if key not in policy['members'] or key==actor or key in s['revoked_keys']:raise CapitalError('Invalid key revocation')
        s['revoked_keys'].append(key)
        for aid,a in s['approvals'].items():
            if a['approver']==key and a['status']!='SPENT':
                a['status']='REVOKED'
                for r in s['reservations'].values():
                    if r['approval']==aid and r['status']=='RESERVED':r['status']='RELEASED'
    if len(s['lots'])>128 or len(s['proposals'])>256:raise CapitalError('Lab domain limit')
    balance(s);return s


def event_amount(state,event):
    """Explicit event amount, derived from the exact signed action and its source."""
    if event['type']=='PolicyEnrolled':return 0
    cmd=event['command'];d=cmd['data'];kind=cmd['type']
    if kind in ('CapitalReceived','ProposalCreated','RefundConfirmed'):return d['amount_minor']
    if kind=='CapitalVerified':return state['lots'][d['lot']]['amount']
    if kind in ('ApprovalGranted','ApprovalRevoked'):
        return state['proposals'][state['approvals'][d['approval']]['proposal']]['amount_minor']
    if kind in ('BudgetReserved','ReservationReleased','CapitalSpent'):return state['reservations'][d['reservation']]['amount']
    return 0  # Key revocation is a nonmonetary authority event.


def fingerprint(db,fid,journal):
    root=db.execute('SELECT * FROM capital_funds WHERE id=?',(fid,)).fetchone()
    if root is None:return None
    return journal.tag('capital-state-v1',fid,root['version'],root['state'],canonical([dict(r) for r in db.execute('SELECT * FROM capital_members WHERE fund_id=? ORDER BY key_id',(fid,))]),canonical([dict(r) for r in db.execute('SELECT * FROM capital_versions WHERE fund_id=? ORDER BY version',(fid,))]))


def retire(db,fid,journal):
    tag=journal.tag('capital-record',fid)
    db.execute('INSERT OR IGNORE INTO capital_holds VALUES (?,?)',(tag,'privacy-or-unverified-restore'))
    db.execute('DELETE FROM capital_funds WHERE id=?',(fid,))


def purge_member(db,uid,journal):
    if not schema.installed(db):return
    for row in db.execute('SELECT fund_id FROM capital_members WHERE blago_user_id=?',(uid,)).fetchall():
        from xblago.domain import capital_governance as gov
        state=json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(row[0],)).fetchone()[0])
        if gov.is_policy(state['policy']):gov.purge(db,uid,journal,row[0])
        else:retire(db,row[0],journal)


def apply_checkpoint(db,event,journal):
    schema.validate(db)
    root=next((r for r in db.execute('SELECT id FROM capital_funds') if journal.tag('capital-record',r[0])==event['target']),None)
    held=db.execute('SELECT 1 FROM capital_holds WHERE target_tag=?',(event['target'],)).fetchone()
    if not held and root and fingerprint(db,root[0],journal)==event['state']:return
    db.execute('INSERT OR IGNORE INTO capital_holds VALUES (?,?)',(event['target'],'unverified-restore'))
    if root:
        from xblago.domain import capital_governance as gov
        state=json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(root[0],)).fetchone()[0])
        if gov.is_policy(state['policy']):gov.hold(db,root[0],journal)
        else:retire(db,root[0],journal)


def capital_sources(db,c):
    if not schema.installed(db):return {}
    schema.validate(db);latest={}
    for row in c.rows:
        e=json.loads(row[2])
        if e['kind']=='capital_state':latest[e['target']]=e['state']
    for row in db.execute('SELECT payload FROM privacy_outbox ORDER BY rowid'):
        e=json.loads(row[0])
        if e['kind']=='capital_state':latest[e['target']]=e['state']
    result={};seen=set()
    for root in db.execute('SELECT * FROM capital_funds'):
        fid=root['id'];tag=c.journal.tag('capital-record',fid);seen.add(tag)
        from xblago.core import governance_schema
        if governance_schema.installed(db) and db.execute('SELECT 1 FROM govcap_holds WHERE fund_id=?',(fid,)).fetchone():continue
        if db.execute('SELECT 1 FROM capital_holds WHERE target_tag=?',(tag,)).fetchone() or fingerprint(db,fid,c.journal)!=root['head_tag'] or latest.get(tag)!=root['head_tag']:raise JournalUnavailable('Capital frontier mismatch')
        result[('CAPITAL',tag)]={'id':fid,'root':dict(root),'state':root['head_tag'],'rows':[dict(r) for r in db.execute('SELECT * FROM capital_versions WHERE fund_id=? ORDER BY version',(fid,))]}
    for tag in latest:
        if tag not in seen and not db.execute('SELECT 1 FROM capital_holds WHERE target_tag=?',(tag,)).fetchone():raise JournalUnavailable('Missing capital frontier')
    return result


class CapitalLedger:
    def __init__(self,app):self.app=app;self.ledger=LocalLedger(app)

    @contextmanager
    def tx(self,token):
        if not schema.enabled():raise ApiError(404,'Synthetic capital disabled')
        from xblago.core.uel_checkpoint import configuration
        if configuration() is None:raise JournalUnavailable('Live pinned custody required')
        with self.ledger.tx(token) as (db,uid,c):
            schema.validate(db);capital_sources(db,c)
            yield db,uid,c

    def enroll(self,token,policy,members,signatures):
        """Explicit lab bootstrap: every pinned key signs policy; map existing sessions only."""
        validate_policy(policy);fid=policy['id'];state=empty(policy)
        if set(members)!=set(policy['members']) or set(signatures)!=set(members):raise CapitalError('All member proofs required')
        with self.tx(token) as (db,uid,c):
            from xblago.domain import capital_governance as gov
            gov.guard(policy,db)
            mapping={key:self.app.identity.session_row(db,t,fresh=True)['blago_user_id'] for key,t in members.items()}
            if uid not in mapping.values() or len(set(mapping.values()))!=len(mapping):raise CapitalError('Distinct authenticated participants required')
            if any(int(time.time())>=r['expires_at'] for r in policy['members'].values()):raise CapitalError('Expired bootstrap key')
            for key,sig in signatures.items():
                if gov.is_policy(policy):gov.canonical_signature(sig)
                verify(policy['members'][key]['public_key'],sig,policy,policy_domain(policy))
            if db.execute('SELECT 1 FROM capital_holds WHERE target_tag=?',(c.journal.tag('capital-record',fid),)).fetchone():raise CapitalError('Retired fund')
            old=db.execute('SELECT * FROM capital_funds WHERE id=?',(fid,)).fetchone()
            if old:
                actual={r['key_id']:r['blago_user_id'] for r in db.execute('SELECT key_id,blago_user_id FROM capital_members WHERE fund_id=?',(fid,))}
                if json.loads(old['state'])['policy']!=policy or mapping!=actual:raise CapitalError('Fund collision')
                return {'fund':fid,'version':old['version']}
            if db.execute('SELECT count(*) FROM capital_funds').fetchone()[0]>=16:raise CapitalError('Lab fund limit')
            db.execute('INSERT INTO capital_funds VALUES (?,1,?,?)',(fid,canonical(state),''))
            for key,user in mapping.items():
                row=policy['members'][key]
                db.execute('INSERT INTO capital_members VALUES (?,?,?,?,?)',(fid,key,user,row['public_key'],canonical(row['roles'])))
            event={'type':'PolicyEnrolled','policy':policy,'signatures':signatures,'recorded_at':int(time.time()),'state_hash':digest(state)}
            self._emit(db,c,fid,1,event)
            return {'fund':fid,'version':1}

    def _member(self,db,uid,fid):
        r=db.execute('SELECT * FROM capital_members WHERE fund_id=? AND blago_user_id=?',(fid,uid)).fetchone()
        if r is None:raise ApiError(404,'Fund inaccessible')
        return r

    def _emit(self,db,c,fid,version,event):
        prior=db.execute('SELECT body FROM capital_versions WHERE fund_id=? ORDER BY version DESC LIMIT 1',(fid,)).fetchone()
        eid=str(uuid4())
        state=json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(fid,)).fetchone()[0])
        event=dict(event,currency='RUB',amount_minor=event_amount(state,event),event_id=eid,version=version,fund=fid,previous=digest(json.loads(prior[0])) if prior else '0'*64)
        db.execute('INSERT INTO capital_versions VALUES (?,?,?,?)',(fid,version,eid,canonical(event)))
        tag=fingerprint(db,fid,c.journal);db.execute('UPDATE capital_funds SET head_tag=? WHERE id=?',(tag,fid))
        payload={'kind':'capital_state','owner':'','target':c.journal.tag('capital-record',fid),'state':tag,'recorded_at':0}
        db.execute('INSERT INTO privacy_outbox VALUES (?,?)',('capital:'+str(uuid4()),canonical(payload)))

    def execute(self,token,command,signature):
        if type(command) is not dict or 'fund' not in command:raise CapitalError('Command object')
        with self.tx(token) as (db,uid,c):
            fid=ident(command['fund']);member=self._member(db,uid,fid)
            if command.get('key_id')!=member['key_id']:raise CapitalError('Session/key substitution')
            root=db.execute('SELECT * FROM capital_funds WHERE id=?',(fid,)).fetchone();state=json.loads(root['state'])
            from xblago.domain import capital_governance as gov
            gov.guard(state['policy'],db)
            key=state['policy']['members'][member['key_id']]
            if member['key_id'] in state['revoked_keys'] or member['key_id'] in state.get('exited',[]) or int(time.time())>=key['expires_at']:raise CapitalError('Key expired/revoked')
            verify(key['public_key'],signature,command,command_domain(state))
            for row in db.execute('SELECT body,version FROM capital_versions WHERE fund_id=? ORDER BY version',(fid,)):
                event=json.loads(row['body'])
                if event.get('command',{}).get('operation_id')==command.get('operation_id'):
                    if event['command']!=command or event['signature']!=signature:raise CapitalError('Idempotency collision')
                    return {'fund':fid,'version':row['version'],'status':'COMMITTED'}
            if command.get('version')!=root['version']:raise CapitalError('CAS conflict')
            latest=db.execute('SELECT body FROM capital_versions WHERE fund_id=? ORDER BY version DESC LIMIT 1',(fid,)).fetchone()
            now=int(time.time())
            if now<json.loads(latest[0])['recorded_at'] or root['version']>=1024:raise CapitalError('Clock rollback or lab history limit')
            updated=transition(state,command,signature,now);version=root['version']+1
            changed=db.execute('UPDATE capital_funds SET version=?,state=? WHERE id=? AND version=?',(version,canonical(updated),fid,root['version'])).rowcount
            if changed!=1:raise CapitalError('CAS conflict')
            if command['type']=='MemberExited':db.execute('DELETE FROM capital_members WHERE fund_id=? AND key_id=?',(fid,member['key_id']))
            event={'type':command['type'],'command':command,'signature':signature,'recorded_at':now,'state_hash':digest(updated)}
            self._emit(db,c,fid,version,event)
            return {'fund':fid,'version':version,'status':'COMMITTED'}

    def inspect(self,token,fid):
        with self.tx(token) as (db,uid,c):
            member=self._member(db,uid,fid);root=db.execute('SELECT * FROM capital_funds WHERE id=?',(fid,)).fetchone();state=json.loads(root['state'])
            from xblago.domain import capital_governance as gov
            gov.guard(state['policy'],db)
            row=state['policy']['members'][member['key_id']]
            if 'VIEW' not in row['roles'] or member['key_id'] in state['revoked_keys'] or member['key_id'] in state.get('exited',[]) or int(time.time())>=row['expires_at']:raise CapitalError('View denied')
            result,_=balance(state)
            return dict(result,fund=fid,version=root['version'],scope='SYNTHETIC_PINNED_MEMBERS_ONLY')

    def audit(self,token,fid):
        self.inspect(token,fid)
        with self.tx(token) as (db,uid,c):
            member=self._member(db,uid,fid)
            root=db.execute('SELECT state FROM capital_funds WHERE id=?',(fid,)).fetchone();state=json.loads(root[0]);row=state['policy']['members'][member['key_id']]
            from xblago.domain import capital_governance as gov
            gov.guard(state['policy'],db)
            if 'VIEW' not in row['roles'] or member['key_id'] in state['revoked_keys'] or member['key_id'] in state.get('exited',[]) or int(time.time())>=row['expires_at']:raise CapitalError('View denied')
            return {'format':'capital-private-audit-1','fund':fid,'events':[json.loads(r[0]) for r in db.execute('SELECT body FROM capital_versions WHERE fund_id=? ORDER BY version',(fid,))]}

    def erase(self,token,fid,confirmation):
        if confirmation!='УДАЛИТЬ СИНТЕТИЧЕСКИЙ ФОНД':raise CapitalError('Explicit destructive confirmation required')
        with self.tx(token) as (db,uid,c):
            self._member(db,uid,fid)
            from xblago.domain import capital_governance as gov
            if gov.is_policy(json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(fid,)).fetchone()[0])['policy']):raise CapitalError('Governance members may exit, not erase others rights')
            retire(db,fid,c.journal)
            event={'kind':'capital_state','owner':'','target':c.journal.tag('capital-record',fid),'state':'ERASED','recorded_at':0}
            db.execute('INSERT INTO privacy_outbox VALUES (?,?)',('capital-erase:'+str(uuid4()),canonical(event)))
            return {'status':'ERASED_AND_HELD','balances':'UNKNOWN'}


def personal_export(db,uid):
    if not schema.installed(db):return []
    # Export only this participant's private binding, never other members or a shared history.
    result=[dict(r) for r in db.execute('SELECT fund_id,key_id,public_key,roles FROM capital_members WHERE blago_user_id=?',(uid,))]
    from xblago.domain import capital_governance as gov
    for item in result:
        state=json.loads(db.execute('SELECT state FROM capital_funds WHERE id=?',(item['fund_id'],)).fetchone()[0])
        if gov.is_policy(state['policy']):
            item['roles']=canonical(state['policy']['members'][item['key_id']]['roles'])
            item['policy_version']=state['policy']['version']
            item['authority_revoked']=item['key_id'] in state['revoked_keys'] or item['key_id'] in state['exited']
    return result
