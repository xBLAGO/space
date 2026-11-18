"""Versioned synthetic collective policy. Fixed electorate; no positive admin recovery."""
import copy
import json
import re
import time
from uuid import uuid4
from xblago.core.security_journal import canonical,JournalUnavailable
from xblago.core import governance_schema
from xblago.domain import capital as cap

FORMAT='capital-governance-policy-2'
DOMAIN='BLAGO-CAPITAL-GOVERNANCE-0.2\n'
PRIVACY_DOMAIN='BLAGO-CAPITAL-PRIVACY-0.2\n'
POLICY_DOMAIN='BLAGO-CAPITAL-GOVERNANCE-POLICY-0.2\n'
ACTIONS={'DelegationGranted','DelegationRevoked','MemberExited','PolicyProposed','PolicyVoted','PolicyVoteRevoked','PolicyActivated','AuthorityRevoked'}


def is_policy(p):return type(p) is dict and p.get('format')==FORMAT


def validate_policy(p):
    fields={'format','id','version','currency','members','evidence_key','privacy_key','governors','governance_quorum','spend_quorum','activation_delay','delegation_seconds'}
    if type(p) is not dict or set(p)!=fields or p['format']!=FORMAT or type(p['version']) is not int or p['version']<1:raise cap.CapitalError('Governance policy schema')
    basic={k:p[k] for k in ('id','currency','members','evidence_key')}
    basic.update(format='capital-lab-policy-1',version=1)
    for key,row in basic['members'].items():
        if type(row) is not dict or type(row.get('roles')) is not list:raise cap.CapitalError('Member schema')
    # Validate the legacy public-key/role shape without broadening its immutable checksum.
    basic=copy.deepcopy(basic)
    for row in basic['members'].values():row['roles']=[r for r in row['roles'] if r!='GOV']
    cap._legacy_validate_policy(basic)
    for value in [m['public_key'] for m in p['members'].values()]+[p['evidence_key'],p['privacy_key']]:
        if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value):raise cap.CapitalError('Canonical public-key encoding')
    if any(len(r['roles'])!=len(set(r['roles'])) for r in p['members'].values()):raise cap.CapitalError('Duplicate role')
    if any(set(r['roles'])-set(cap.ROLES.values())-{'VIEW','GOV'} for r in p['members'].values()):raise cap.CapitalError('Unknown governance role')
    if any('EXECUTE' in r['roles'] and set(r['roles'])&{'APPROVE','PROPOSE'} for r in p['members'].values()):raise cap.CapitalError('Executor role separation required')
    gs=p['governors']
    if type(gs) is not list or gs!=sorted(set(gs)) or not 3<=len(gs)<=100 or set(gs)!={k for k,r in p['members'].items() if 'GOV' in r['roles']}:raise cap.CapitalError('Fixed distinct electorate required')
    if type(p['governance_quorum']) is not int or not (2*len(gs)//3+1)<=p['governance_quorum']<=len(gs):raise cap.CapitalError('Supermajority required')
    voters=sum('APPROVE' in r['roles'] for r in p['members'].values())
    if type(p['spend_quorum']) is not int or not 2<=p['spend_quorum']<=voters:raise cap.CapitalError('Independent spend quorum required')
    if type(p['activation_delay']) is not int or not 60<=p['activation_delay']<=86400:raise cap.CapitalError('Activation delay required')
    if type(p['delegation_seconds']) is not int or not 60<=p['delegation_seconds']<=3600:raise cap.CapitalError('Bounded delegation TTL')
    if type(p['privacy_key']) is not str or len(bytes.fromhex(p['privacy_key']))!=32 or p['privacy_key'] in [r['public_key'] for r in p['members'].values()]+[p['evidence_key']]:raise cap.CapitalError('Separate denial-only node key required')


def canonical_signature(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{128}',value):raise cap.CapitalError('Canonical signature encoding')


def initial(p):
    validate_policy(p)
    s=cap._legacy_empty(p,validate=False)
    s.update(delegations={},policy_proposals={},exited=[],genesis_policy_hash=cap.digest(p))
    return s


def live(s,key,now):
    p=s['policy'];m=p['members'].get(key)
    return bool(m and key not in s['exited'] and key not in s['revoked_keys'] and now<m['expires_at'])


def direct(s,key,role,now):return live(s,key,now) and role in s['policy']['members'][key]['roles']


def vote_live(s,principal,vote,p,now):
    signer=vote['signer']
    if not direct(s,principal,'APPROVE',now) or not live(s,signer,now) or principal==p['proposer'] or signer==p['proposer']:return False
    if 'EXECUTE' in s['policy']['members'][signer]['roles']:return False
    did=vote['delegation']
    if did is None:return signer==principal
    d=s['delegations'].get(did)
    return bool(d and d['active'] and d['grantor']==principal and d['recipient']==signer and d['proposal']==p['proposal'] and now<d['expires_at'] and p['amount_minor']<=d['max_amount_minor'])


def valid_votes(s,a,now):
    p=s['proposals'][a['proposal']]
    if p['governance_policy']!=cap.digest(s['policy']) or not live(s,p['proposer'],now) or now>=p['expires_at']:return {}
    return {k:v for k,v in a['votes'].items() if vote_live(s,k,v,p,now)}


def invalidate(s,now):
    for aid,a in s['approvals'].items():
        if a['status']=='SPENT':continue
        votes=valid_votes(s,a,now)
        if a['status']=='CONSUMED' and len(votes)<s['policy']['spend_quorum']:
            a['status']='REVOKED'
            for r in s['reservations'].values():
                if r['approval']==aid and r['status']=='RESERVED':r['status']='RELEASED'
    for r in s['reservations'].values():
        if r['status']=='RESERVED' and not live(s,r['executor'],now):
            r['status']='RELEASED';s['approvals'][r['approval']]['status']='REVOKED'
    for pending in s['policy_proposals'].values():
        if pending['status']=='PENDING' and len([k for k in pending['votes'] if direct(s,k,'GOV',now)])<s['policy']['governance_quorum']:pending['ready_at']=None


def exited(previous,key,now):
    s=copy.deepcopy(previous)
    if key not in s['policy']['members'] or key in s['exited']:raise cap.CapitalError('Already absent member')
    s['exited'].append(key)
    invalidate(s,now)
    return s


def change_policy(old,new):
    validate_policy(new)
    if new['id']!=old['id'] or new['version']!=old['version']+1 or new['currency']!=old['currency'] or new['governors']!=old['governors'] or set(new['members'])!=set(old['members']):raise cap.CapitalError('Electorate cannot be replaced')
    for field in ('evidence_key','privacy_key'):
        if new[field]!=old[field]:raise cap.CapitalError('Trust anchor rotation requires separate protocol')
    for k,m in old['members'].items():
        if new['members'][k]['public_key']!=m['public_key']:raise cap.CapitalError('Key replacement is not policy editing')
    if new['governance_quorum']<old['governance_quorum'] or new['spend_quorum']<old['spend_quorum'] or new['activation_delay']<old['activation_delay'] or new['delegation_seconds']>old['delegation_seconds']:raise cap.CapitalError('Security floor cannot be weakened')


def transition(previous,c,signature,now):
    s=copy.deepcopy(previous);p=s['policy'];actor=c.get('key_id');kind=c.get('type');d=c.get('data')
    fields={'fund','version','policy_hash','operation_id','key_id','type','expires_at','data','evidence'}
    if type(c) is not dict or set(c)!=fields or len(canonical(c))>100000 or c['fund']!=p['id'] or type(c['version']) is not int or c['version']<1 or c['policy_hash']!=cap.digest(p):raise cap.CapitalError('Governance command binding')
    cap.ident(c['operation_id']);cap.ident(actor)
    canonical_signature(signature)
    if c['evidence'] is not None:
        if type(c['evidence']) is not dict or 'signature' not in c['evidence']:raise cap.CapitalError('Evidence schema')
        canonical_signature(c['evidence']['signature'])
    if type(c['expires_at']) is not int or not now<c['expires_at']<=now+86400 or type(d) is not dict:raise cap.CapitalError('Expired/malformed command')
    if kind=='PrivacyMemberExited':
        if set(d)!={'principal'} or d['principal']!=actor or c['evidence'] is not None:raise cap.CapitalError('Denial-only scope')
        cap.verify(p['privacy_key'],signature,c,PRIVACY_DOMAIN)
        return exited(s,actor,now)
    if not live(s,actor,now):raise cap.CapitalError('Inactive authority')
    cap.verify(p['members'][actor]['public_key'],signature,c,DOMAIN)
    if kind in ('CapitalReceived','CapitalVerified','ProposalCreated','RefundConfirmed'):
        updated=cap._legacy_transition(s,c,signature,now,domain=DOMAIN)
        if kind=='ProposalCreated':updated['proposals'][d['proposal']]['governance_policy']=cap.digest(p)
        return updated
    if c['evidence'] is not None and kind!='CapitalSpent':raise cap.CapitalError('Unexpected governance evidence')
    required={'ApprovalGranted':({'proposal','approval'},{'proposal','approval','delegation'}), 'ApprovalRevoked':({'approval','principal'},),
              'BudgetReserved':({'approval','reservation','allocations'},),'CapitalSpent':({'reservation'},), 'ReservationReleased':({'reservation'},),
              'DelegationGranted':({'delegation','recipient','proposal','max_amount_minor','expires_at'},),'DelegationRevoked':({'delegation'},),
              'MemberExited':(set(),),'PolicyProposed':({'proposal','policy'},),'PolicyVoted':({'proposal'},),'PolicyVoteRevoked':({'proposal'},),'PolicyActivated':({'proposal'},),
              'AuthorityRevoked':({'key_id','endorsements'},)}
    if kind not in required or set(d) not in required[kind]:raise cap.CapitalError('Unsupported governance action/data')
    if kind=='MemberExited':return exited(s,actor,now)
    if kind=='DelegationGranted':
        if not direct(s,actor,'APPROVE',now):raise cap.CapitalError('Only direct approver can delegate')
        did=cap.ident(d['delegation']);target=d['recipient'];proposal=s['proposals'][d['proposal']]
        if did in s['delegations'] or not live(s,target,now) or target==actor or target==proposal['proposer'] or 'EXECUTE' in p['members'][target]['roles']:raise cap.CapitalError('Delegation conflict')
        if type(d['expires_at']) is not int or not now<d['expires_at']<=min(now+p['delegation_seconds'],p['members'][actor]['expires_at'],p['members'][target]['expires_at'],proposal['expires_at']):raise cap.CapitalError('Delegation expiry')
        if cap.amount(d['max_amount_minor'])<proposal['amount_minor'] or proposal['governance_policy']!=cap.digest(p):raise cap.CapitalError('Delegation scope')
        if len(s['delegations'])>=128:raise cap.CapitalError('Delegation lab limit')
        s['delegations'][did]=dict(d,grantor=actor,active=True)
    elif kind=='DelegationRevoked':
        grant=s['delegations'][d['delegation']]
        if grant['grantor']!=actor or not grant['active']:raise cap.CapitalError('Delegation revoke authority')
        grant['active']=False;invalidate(s,now)
    elif kind=='ApprovalGranted':
        pid=d['proposal'];proposal=s['proposals'][pid];aid=cap.ident(d['approval']);did=d.get('delegation');principal=actor
        if did is not None:principal=s['delegations'][did]['grantor']
        vote={'signer':actor,'delegation':did}
        if not vote_live(s,principal,vote,proposal,now) or proposal['governance_policy']!=cap.digest(p) or now>=proposal['expires_at']:raise cap.CapitalError('Approval scope/authority')
        a=s['approvals'].get(aid)
        if a is None:
            if any(x['proposal']==pid for x in s['approvals'].values()):raise cap.CapitalError('Proposal already has an approval round')
            a={'proposal':pid,'votes':{},'status':'COLLECTING','action_hash':cap.digest(proposal),'policy_hash':cap.digest(p)};s['approvals'][aid]=a
        if a['proposal']!=pid or a['status'] not in ('COLLECTING','GRANTED') or principal in a['votes'] or any(v['signer']==actor for v in a['votes'].values()):raise cap.CapitalError('Duplicate principal/signer or spent round')
        a['votes'][principal]=vote
        a['status']='GRANTED' if len(valid_votes(s,a,now))>=p['spend_quorum'] else 'COLLECTING'
    elif kind=='ApprovalRevoked':
        a=s['approvals'][d['approval']];principal=d['principal'];vote=a['votes'].get(principal)
        if not vote or actor not in (principal,vote['signer']):raise cap.CapitalError('Only represented voter can revoke vote')
        del a['votes'][principal];invalidate(s,now)
        if a['status']=='GRANTED' and len(valid_votes(s,a,now))<p['spend_quorum']:a['status']='COLLECTING'
    elif kind=='BudgetReserved':
        if not direct(s,actor,'EXECUTE',now):raise cap.CapitalError('Executor required')
        a=s['approvals'][d['approval']];proposal=s['proposals'][a['proposal']];votes=valid_votes(s,a,now);rid=cap.ident(d['reservation'])
        if a['status']!='GRANTED' or len(votes)<p['spend_quorum'] or actor==proposal['proposer'] or actor in votes or actor in [v['signer'] for v in votes.values()] or rid in s['reservations']:raise cap.CapitalError('Quorum/current authority required')
        allocations=d['allocations']
        if type(allocations) is not dict or not 1<=len(allocations)<=128:raise cap.CapitalError('Allocations required')
        total=sum(cap.amount(v) for v in allocations.values());_,used=cap.balance(s)
        if total>proposal['amount_minor']:raise cap.CapitalError('Approval amount exceeded')
        for lid,v in allocations.items():
            lot=s['lots'][lid]
            if not lot['verified'] or used[lid]+v>lot['amount']:raise cap.CapitalError('Insufficient verified source')
        s['reservations'][rid]={'approval':d['approval'],'amount':total,'allocations':dict(allocations),'status':'RESERVED','executor':actor,'refunded':0,'refund_allocations':{}};a['status']='CONSUMED'
    elif kind=='CapitalSpent':
        r=s['reservations'][d['reservation']];a=s['approvals'][r['approval']]
        if not direct(s,actor,'EXECUTE',now) or actor!=r['executor'] or r['status']!='RESERVED' or a['status']!='CONSUMED' or len(valid_votes(s,a,now))<p['spend_quorum']:raise cap.CapitalError('Current quorum/reserve required')
        cap.signed_evidence(s,c,'SPENT',d['reservation'],r['amount']);r['status']='SPENT';a['status']='SPENT'
    elif kind=='ReservationReleased':
        r=s['reservations'][d['reservation']]
        if actor!=r['executor'] or not direct(s,actor,'EXECUTE',now) or r['status']!='RESERVED':raise cap.CapitalError('Current executor required')
        r['status']='RELEASED';s['approvals'][r['approval']]['status']='REVOKED'
    elif kind=='PolicyProposed':
        if not direct(s,actor,'GOV',now):raise cap.CapitalError('Direct governor required')
        change_policy(p,d['policy']);pid=cap.ident(d['proposal'])
        if pid in s['policy_proposals'] or len(s['policy_proposals'])>=64:raise cap.CapitalError('Policy proposal collision/limit')
        s['policy_proposals'][pid]={'policy':copy.deepcopy(d['policy']),'base':cap.digest(p),'votes':[],'ready_at':None,'status':'PENDING'}
    elif kind in ('PolicyVoted','PolicyVoteRevoked','PolicyActivated'):
        if not direct(s,actor,'GOV',now):raise cap.CapitalError('Direct governor required')
        pending=s['policy_proposals'][d['proposal']]
        if pending['status']!='PENDING' or pending['base']!=cap.digest(p):raise cap.CapitalError('Stale policy proposal')
        votes=pending['votes']
        if kind=='PolicyVoted':
            if actor in votes:raise cap.CapitalError('One governor one vote')
            votes.append(actor)
            if len([k for k in votes if direct(s,k,'GOV',now)])>=p['governance_quorum']:
                if pending['ready_at'] is None:pending['ready_at']=now+p['activation_delay']
            else:pending['ready_at']=None
        elif kind=='PolicyVoteRevoked':
            if actor not in votes:raise cap.CapitalError('No vote to revoke')
            votes.remove(actor)
            if len([k for k in votes if direct(s,k,'GOV',now)])<p['governance_quorum']:pending['ready_at']=None
        else:
            if len([k for k in votes if direct(s,k,'GOV',now)])<p['governance_quorum'] or pending['ready_at'] is None or now<pending['ready_at']:raise cap.CapitalError('Quorum and delay required')
            change_policy(p,pending['policy']);pending['status']='ACTIVATED';s['policy']=copy.deepcopy(pending['policy'])
            for a in s['approvals'].values():
                if a['status']!='SPENT':a['status']='REVOKED'
            for r in s['reservations'].values():
                if r['status']=='RESERVED':r['status']='RELEASED'
            for grant in s['delegations'].values():grant['active']=False
    elif kind=='AuthorityRevoked':
        target=d['key_id'];endors=d['endorsements']
        if target not in p['members'] or target==actor or not direct(s,actor,'GOV',now) or type(endors) is not dict:raise cap.CapitalError('Revocation governance required')
        body={k:v for k,v in c.items() if k!='data'};body['data']={'key_id':target}
        if len(endors)<p['governance_quorum']:raise cap.CapitalError('Revocation supermajority required')
        for voter,sig in endors.items():
            if voter==target or not direct(s,voter,'GOV',now):raise cap.CapitalError('Invalid revocation endorser')
            canonical_signature(sig)
            cap.verify(p['members'][voter]['public_key'],sig,body,'BLAGO-CAPITAL-REVOKE-0.2\n')
        if target in s['revoked_keys']:raise cap.CapitalError('Already revoked')
        s['revoked_keys'].append(target);invalidate(s,now)
    cap.balance(s);return s


def guard(p,db):
    if is_policy(p):
        governance_schema.validate(db,required=True)
        from xblago.core import uel_checkpoint as cp
        config=cp.configuration()
        if config is None or cp.public(cp.load_key(config['source_key_file']))!=p['privacy_key']:raise JournalUnavailable('Pinned denial signer unavailable/mismatched')
        if db.execute('SELECT 1 FROM govcap_holds WHERE fund_id=?',(p['id'],)).fetchone():raise JournalUnavailable('Governance fund frozen; UNKNOWN_RECOVERY')
        if not governance_schema.enabled():raise cap.ApiError(404,'Governance lab disabled')


def hold(db,fid,journal):
    governance_schema.validate(db,required=True)
    db.execute('INSERT OR IGNORE INTO govcap_holds VALUES (?,?)',(fid,'unverified-recovery'))
    db.execute('INSERT OR IGNORE INTO capital_holds VALUES (?,?)',(journal.tag('capital-record',fid),'governance-frozen-not-erased'))


def purge(db,uid,journal,fid):
    """Privacy may remove authority only; node signature cannot create a vote/grant."""
    root=db.execute('SELECT * FROM capital_funds WHERE id=?',(fid,)).fetchone();s=json.loads(root['state'])
    keys=[r[0] for r in db.execute('SELECT key_id FROM capital_members WHERE fund_id=? AND blago_user_id=?',(fid,uid))]
    if not keys:return
    governance_schema.validate(db,required=True)
    if db.execute('SELECT blago_replaying()').fetchone()[0] or db.execute('SELECT 1 FROM govcap_holds WHERE fund_id=?',(fid,)).fetchone():
        # The original negative user-status journal remains authoritative. Never reconstruct grants.
        db.execute('DELETE FROM capital_members WHERE fund_id=? AND blago_user_id=?',(fid,uid));hold(db,fid,journal);return
    from xblago.core import uel_checkpoint as cp
    cfg=cp.configuration()
    if cfg is None:raise JournalUnavailable('Privacy custody unavailable')
    key=cp.load_key(cfg['source_key_file'])
    if cp.public(key)!=s['policy']['privacy_key']:raise JournalUnavailable('Privacy denial signer mismatch')
    now=int(time.time())
    for principal in keys:
        c={'fund':fid,'version':root['version'],'policy_hash':cap.digest(s['policy']),'operation_id':'privacy-'+uuid4().hex,'key_id':principal,'type':'PrivacyMemberExited','expires_at':now+60,'data':{'principal':principal},'evidence':None}
        sig=key.sign((PRIVACY_DOMAIN+canonical(c)).encode()).hex();updated=transition(s,c,sig,now);v=root['version']+1
        db.execute('DELETE FROM capital_members WHERE fund_id=? AND key_id=?',(fid,principal))
        db.execute('UPDATE capital_funds SET state=?,version=? WHERE id=?',(canonical(updated),v,fid))
        cap.CapitalLedger._emit(None,db,type('Control',(),{'journal':journal})(),fid,v,{'type':c['type'],'command':c,'signature':sig,'recorded_at':now,'state_hash':cap.digest(updated)})
