"""Consent/slot adapters over canonical Help/Asset and UEL, never a second Help lifecycle."""
from contextlib import contextmanager
import hashlib
import json
import re
import secrets
import time
from uuid import uuid4, uuid5, NAMESPACE_URL
from xblago.core import living_schema as schema
from xblago.core.security_journal import canonical, JournalUnavailable
from xblago.core.memory import is_sensitive_memory
from xblago.domain.help_service import HelpService
from xblago.domain.asset_registry import AssetRegistry
from xblago.integrations.web import ApiError

EVENTS = {'PrivateNoteRecorded','NeedExpressed','ResourceOffered','ReferralBridgeProposed',
          'ConsentRequested','ResourceReserved','FulfillmentProposed','FulfillmentAuthorized',
          'FulfillmentConfirmed','DeclinedOrWithdrawn','DisputeOpened','RecordCorrected',
          'ContactConsentGranted','ReferralScopeAuthorized','ResultConfirmationRecorded'}
FIELDS = {'note':{'text','expires_at'}, 'need_permission':{'need_id','topic','invitation_text','expires_at'},
          'propose_bridge':{'note_id','permission_id'}, 'request_consent':{'id','recipient'},
          'accept_invitation':{'id'}, 'decline_invitation':{'id'},
          'offer_resource':{'asset_id','audience','starts_at','ends_at','expires_at'},
          'attach_offer':{'resource_id','need_id','offer_id'},
          'reserve':{'resource_id','need_id','offer_id'},
          'withdraw':{'id'}, 'correct_note':{'id','text','causation_id'}, 'erase':{'id'}}

def fingerprint(db,rid,journal,root=None):
    root=dict(root or db.execute('SELECT * FROM living_records WHERE id=?',(rid,)).fetchone())
    root.pop('head_tag',None)
    return journal.tag('living-state-v1',canonical({'root':root,
        'versions':[dict(r) for r in db.execute('SELECT * FROM living_versions WHERE record_id=? ORDER BY version',(rid,))],
        'slots':[dict(r) for r in db.execute('SELECT * FROM living_slots WHERE record_id=?',(rid,))]}))

def retire(db,rid,c,reason='erased'):
    row=db.execute('SELECT * FROM living_records WHERE id=?',(rid,)).fetchone()
    if row is None:return
    if db.execute('SELECT 1 FROM living_slots WHERE record_id=?',(rid,)).fetchone():
        reason='restore'  # Occupancy cannot be inferred after erasure.
        need_tag=c.journal.tag('help-request',json.loads(row['state'])['need_id'])
        db.execute('INSERT OR IGNORE INTO living_help_holds VALUES (?)',(need_tag,))
        for kind,target in (('living_help_deny',need_tag),('living_recovery_hold',c.journal.tag('living-record',rid))):
            event={'kind':kind,'owner':'','target':target,'state':'DENY','recorded_at':0}
            db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(uuid4().hex,canonical(event)))
    dependencies=[r['id'] for r in db.execute('SELECT id,state FROM living_records')
                  if r['id']!=rid and any(json.loads(r['state']).get(k)==rid for k in ('note_id','permission_id','resource_id'))]
    tag=c.journal.tag('living-record',rid)
    db.execute('INSERT OR IGNORE INTO living_holds VALUES (?,?)',(tag,reason))
    db.execute('DELETE FROM living_records WHERE id=?',(rid,))
    db.execute('DELETE FROM living_commands WHERE receipt IS NULL AND (actor_id=? OR instr(body,?)>0)',(row['owner_id'],rid))
    e={'kind':'living_state','owner':c.journal.tag('user',row['owner_id']),'target':tag,'state':'ERASED','recorded_at':0}
    db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(uuid4().hex,canonical(e)))
    for child in dependencies:retire(db,child,c,reason)

def apply_checkpoint(db,event,journal):
    schema.validate(db,required=True)
    if event['kind'] not in ('living_state','living_contact_deny','living_help_deny','living_recovery_hold'):raise JournalUnavailable('Unknown living control event')
    if not re.fullmatch('[0-9a-f]{64}',event['target']):raise JournalUnavailable('Invalid living checkpoint')
    if event['kind'] in ('living_contact_deny','living_help_deny','living_recovery_hold'):
        if event['state']!='DENY':raise JournalUnavailable('Invalid contact denial')
        if event['kind']=='living_recovery_hold':db.execute("INSERT OR REPLACE INTO living_holds VALUES (?,'restore')",(event['target'],))
        else:db.execute('INSERT OR IGNORE INTO '+('living_contact_holds' if event['kind']=='living_contact_deny' else 'living_help_holds')+' VALUES (?)',(event['target'],))
        return
    if event['state']!='ERASED' and not re.fullmatch('[0-9a-f]{64}',event['state']):raise JournalUnavailable('Invalid living state')
    row=next((r for r in db.execute('SELECT * FROM living_records') if journal.tag('living-record',r['id'])==event['target']),None)
    held=db.execute('SELECT 1 FROM living_holds WHERE target_tag=?',(event['target'],)).fetchone()
    if row and not held and event['state']!='ERASED' and fingerprint(db,row['id'],journal)==event['state']:return
    db.execute('INSERT OR IGNORE INTO living_holds VALUES (?,?)',(event['target'],'erased' if event['state']=='ERASED' else 'restore'))
    if row:db.execute('DELETE FROM living_records WHERE id=?',(row['id'],))

def check_frontiers(db,c):
    if not schema.validate(db):return
    latest={};denials=set();help_denials=set();recovery=set()
    entries=[json.loads(r[2]) for r in c.rows]+[json.loads(r[0]) for r in db.execute('SELECT payload FROM privacy_outbox ORDER BY rowid')]
    for e in entries:
        if e['kind']=='living_state':latest[e['target']]=e['state']
        if e['kind']=='living_contact_deny':denials.add(e['target'])
        if e['kind']=='living_help_deny':help_denials.add(e['target'])
        if e['kind']=='living_recovery_hold':recovery.add(e['target'])
    actual={}
    for r in db.execute('SELECT * FROM living_records'):
        tag=c.journal.tag('living-record',r['id']);actual[tag]=r
        if db.execute('SELECT 1 FROM living_holds WHERE target_tag=?',(tag,)).fetchone():raise JournalUnavailable('Held living record returned')
        if latest.get(tag)!=r['head_tag'] or fingerprint(db,r['id'],c.journal)!=r['head_tag']:raise JournalUnavailable('Living source frontier mismatch')
    for tag,state in latest.items():
        if tag not in actual and not db.execute('SELECT 1 FROM living_holds WHERE target_tag=?',(tag,)).fetchone():raise JournalUnavailable('Missing living source')
        if tag not in actual and state!='ERASED':recovery.add(tag)
    if denials!={r[0] for r in db.execute('SELECT target_tag FROM living_contact_holds')}:raise JournalUnavailable('Contact denial rollback')
    if help_denials!={r[0] for r in db.execute('SELECT target_tag FROM living_help_holds')}:raise JournalUnavailable('Help denial rollback')
    for tag in recovery:
        if not db.execute("SELECT 1 FROM living_holds WHERE target_tag=? AND reason='restore'",(tag,)).fetchone():raise JournalUnavailable('Recovery hold rollback')

def sources(db,c):
    if not schema.validate(db):return {}
    check_frontiers(db,c)
    return {('LIVING',c.journal.tag('living-record',r['id'])):{'id':r['id'],'root':dict(r),
            'rows':[dict(v) for v in db.execute('SELECT * FROM living_versions WHERE record_id=? ORDER BY version',(r['id'],))],
            'state':r['head_tag']} for r in db.execute('SELECT * FROM living_records')}

def purge_member(db,uid,c):
    if not schema.validate(db):return
    assets=[r[0] for r in db.execute('SELECT id FROM blago_assets WHERE blago_user_id=?',(uid,))]
    for r in db.execute('SELECT * FROM living_records').fetchall():
        s=json.loads(r['state'])
        if r['owner_id']==uid or s.get('recipient_id')==uid or uid in s.get('audience',[]) or s.get('asset_id') in assets:retire(db,r['id'],c)
    db.execute('DELETE FROM living_commands WHERE actor_id=?',(uid,))

def purge_asset(db,aid,c):
    if not schema.validate(db):return
    for r in db.execute('SELECT * FROM living_records').fetchall():
        if json.loads(r['state']).get('asset_id')==aid:retire(db,r['id'],c)

def personal_export(db,uid):
    if not schema.validate(db):return []
    output=[]
    for r in db.execute('SELECT * FROM living_records WHERE owner_id=?',(uid,)):
        versions=[]
        for v in db.execute('SELECT body FROM living_versions WHERE record_id=? ORDER BY version',(r['id'],)):
            event=json.loads(v[0]);event['snapshot']['state']=canonical(view_state(db,json.loads(event['snapshot']['state'])))
            versions.append(event)
        output.append({'id':r['id'],'kind':r['kind'],'state':view_state(db,json.loads(r['state'])),
                       'versions':versions,'redacted_view':True})
    return output

def view_state(db,state):
    result=dict(state)
    if result.get('recipient_id'):
        result['recipient']=db.execute("SELECT blago_tag('help-member',?)",(result.pop('recipient_id'),)).fetchone()[0]
    if 'audience' in result:
        result['audience']=[db.execute("SELECT blago_tag('help-member',?)",(uid,)).fetchone()[0] for uid in result['audience']]
    return result

def emit(db,c,rid,event):
    if event not in EVENTS:raise ValueError('Unsupported living event')
    r=dict(db.execute('SELECT * FROM living_records WHERE id=?',(rid,)).fetchone())
    prior=db.execute('SELECT event_id FROM living_versions WHERE record_id=? ORDER BY version DESC LIMIT 1',(rid,)).fetchone()
    snapshot={k:v for k,v in r.items() if k!='head_tag'}
    body={'format':'living-deeds-event-1','type':event,'occurred_at':int(time.time()),
          'causation_id':prior[0] if prior else json.loads(r['state']).get('help_event_id'),'snapshot':snapshot}
    if json.loads(r['state']).get('help_event_id'):
        body['canonical_source_ref']={'kind':'HELP','event_id':json.loads(r['state'])['help_event_id']}
    eid=str(uuid5(NAMESPACE_URL,'living:'+rid+':'+str(r['version'])))
    db.execute('INSERT INTO living_versions VALUES (?,?,?,?)',(rid,r['version'],eid,canonical(body)))
    tag=fingerprint(db,rid,c.journal);db.execute('UPDATE living_records SET head_tag=? WHERE id=?',(tag,rid))
    e={'kind':'living_state','owner':c.journal.tag('user',r['owner_id']),'target':c.journal.tag('living-record',rid),'state':tag,'recorded_at':0}
    db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(eid,canonical(e)))

def update(db,c,rid,state,event):
    db.execute('UPDATE living_records SET state=?,version=version+1 WHERE id=?',(canonical(state),rid));emit(db,c,rid,event)

def guard_help(db,uid,rid,action,args,c):
    if not schema.validate(db):return
    if action in ('accept','confirm') and db.execute('SELECT 1 FROM living_help_holds WHERE target_tag=?',(c.journal.tag('help-request',rid),)).fetchone():raise ApiError(409,'Resource proof erased; Help authority held')
    if action=='accept':
        bindings=[r for r in db.execute("SELECT * FROM living_records WHERE kind='ATTACHMENT'")
                  if json.loads(r['state']).get('offer_id')==args.get('offer_id')]
        for binding in bindings:
            s=json.loads(binding['state'])
            slot=db.execute('SELECT * FROM living_slots WHERE request_id=? AND offer_id=?',(rid,args['offer_id'])).fetchone()
            if not slot:raise ApiError(409,'Use explicit Living resource reservation')
            validate_resource(db,s['resource_id'],uid,int(time.time()))
    if action=='confirm':
        for slot in db.execute('SELECT * FROM living_slots WHERE request_id=?',(rid,)):
            r=db.execute('SELECT state FROM living_records WHERE id=?',(slot['record_id'],)).fetchone()
            validate_resource(db,json.loads(r[0])['resource_id'],None,int(time.time()),allow_expired=True)

def observe_help(db,rid,action,c):
    if not schema.validate(db):return
    request=db.execute('SELECT * FROM help_requests WHERE id=?',(rid,)).fetchone()
    for slot in db.execute('SELECT * FROM living_slots WHERE request_id=?',(rid,)).fetchall():
        root=db.execute('SELECT * FROM living_records WHERE id=?',(slot['record_id'],)).fetchone();s=json.loads(root['state'])
        if s.get('help_version')==request['version']:continue
        s['help_state']=request['state'];s['help_version']=request['version']
        s['ever_completed']=s.get('ever_completed',False) or request['state']=='COMPLETED'
        s['help_event_id']=db.execute('SELECT event_id FROM help_events WHERE request_id=? AND version=?',(rid,request['version'])).fetchone()[0]
        if not root['head_tag']:
            db.execute('UPDATE living_records SET state=? WHERE id=?',(canonical(s),root['id']));emit(db,c,root['id'],'ResourceReserved');continue
        event='DisputeOpened' if request['state']=='DISPUTED' else 'FulfillmentConfirmed' if request['state']=='COMPLETED' else 'DeclinedOrWithdrawn' if request['state'] in ('WITHDRAWN','DRAFT') else 'ResultConfirmationRecorded'
        if request['state'] in ('WITHDRAWN','DRAFT') and not s['ever_completed']:db.execute('DELETE FROM living_slots WHERE record_id=?',(root['id'],))
        update(db,c,root['id'],s,event)

def validate_resource(db,rid,viewer,now,*,allow_expired=False):
    root=db.execute("SELECT * FROM living_records WHERE id=? AND kind='RESOURCE'",(rid,)).fetchone()
    if root is None:raise ApiError(404,'Resource unavailable')
    s=json.loads(root['state'])
    if not s['active'] or (not allow_expired and now>=min(s['expires_at'],s['ends_at'])) or (viewer is not None and viewer not in s['audience'] and viewer!=root['owner_id']):raise ApiError(404,'Resource unavailable')
    asset=db.execute('SELECT * FROM assetreg_records WHERE asset_id=?',(s['asset_id'],)).fetchone()
    if asset is None or asset['owner_id']!=root['owner_id'] or asset['status']!='ACTIVE' or asset['version']!=s['asset_version']:raise ApiError(404,'Resource unavailable')
    b=json.loads(db.execute('SELECT body FROM assetreg_versions WHERE asset_id=? AND version=?',(s['asset_id'],asset['version'])).fetchone()[0])
    if b['quantity']!='1' or b['available']!='1' or not b['share_consent'] or b['audience']!='PRIVATE':raise ApiError(409,'Canonical availability/consent changed')
    return root,s,asset,b

class LivingDeeds:
    def __init__(self,app):self.app,self.store=app,app.store;self.help=HelpService(app);self.assets=AssetRegistry(app)
    @contextmanager
    def tx(self,token,destructive=False):
        if not schema.enabled():raise ApiError(404,'Living Deeds disabled')
        from xblago.core.uel_schema import require_synthetic,enabled
        require_synthetic()
        schema.require_enrollment()
        if not enabled():raise JournalUnavailable('Living requires durable UEL')
        with self.help.tx(token,True) as (db,uid,c):
            schema.require_enrollment(c.rows)
            schema.validate(db,required=True);check_frontiers(db,c)
            if not destructive and db.execute("SELECT 1 FROM living_holds WHERE reason='restore'").fetchone():raise JournalUnavailable('Living UNKNOWN_RECOVERY: source payload lost; no restored grants')
            yield db,uid,c
    @staticmethod
    def text(text):
        if type(text) is not str or not 2<=len(text)<=1000 or is_sensitive_memory('living',text):raise ApiError(400,'Bounded nonsecret text required')
        return text
    @staticmethod
    def root(db,rid,owner=None,kind=None):
        if type(rid) is not str or len(rid)!=36:raise ApiError(400,'Bounded record reference required')
        r=db.execute('SELECT * FROM living_records WHERE id=?',(rid,)).fetchone()
        if r is None or (owner is not None and r['owner_id']!=owner) or (kind and r['kind']!=kind):raise ApiError(404,'Record unavailable')
        return r,json.loads(r['state'])
    def _validate(self,db,uid,c,action,d):
        now=int(time.time());refs={}
        def record(key,owner=None,kind=None):
            r,s=self.root(db,d[key],owner,kind);refs['living:'+r['id']]=r['version'];return r,s
        facets={'context','urgency','closure_criterion'}
        if type(action) is not str or action not in FIELDS or type(d) is not dict or (set(d)!=FIELDS[action] and not (action=='need_permission' and set(d)==FIELDS[action]|facets)):
            raise ApiError(400,'Exact reviewed action fields required')
        for key,value in d.items():
            if (key=='id' or key.endswith('_id')) and (type(value) is not str or len(value)!=36):raise ApiError(400,'Bounded source ID required')
        if action in ('note','need_permission','offer_resource'):
            if type(d['expires_at']) is not int or not now<d['expires_at']<=now+604800:raise ApiError(400,'Explicit expiry within seven days required')
        if action=='note':self.text(d['text'])
        elif action=='need_permission':
            r=self.help._row(db,uid,d['need_id'])
            if r['author_id']!=uid or r['state'] not in ('PUBLISHED','OFFERED'):raise ApiError(403,'Need author and published version required')
            if type(d['topic']) is not str or not re.fullmatch('[a-z][a-z0-9_-]{1,39}',d['topic']):raise ApiError(400,'Bounded topic required')
            self.help._text(d['invitation_text']);refs['help:'+r['id']]=r['version']
            if facets.issubset(d):
                self.text(d['context']);self.text(d['closure_criterion'])
                if d['urgency'] not in ('ROUTINE','TIME_SENSITIVE','UNKNOWN'):raise ApiError(400,'Reviewed urgency required')
        elif action=='propose_bridge':
            _,note=record('note_id',uid,'NOTE');_,p=record('permission_id',kind='NEED_PERMISSION')
            if note['expires_at']<=now:raise ApiError(409,'Private note expired')
            r=self.help._row(db,uid,p['need_id'])
            if p['expires_at']<=now or not p['active'] or r['version']!=p['need_version'] or r['state'] not in ('PUBLISHED','OFFERED'):raise ApiError(409,'Invitation permission expired')
            refs['help:'+r['id']]=r['version']
        elif action in ('request_consent','accept_invitation','decline_invitation'):
            r,s=record('id',uid if action=='request_consent' else None,'BRIDGE')
            _,p=self.root(db,s['permission_id'],kind='NEED_PERMISSION')
            if not p['active'] or now>=p['expires_at']:raise ApiError(409,'Invitation expired')
            need=self.help._row(db,r['owner_id'],p['need_id'])
            if need['version']!=p['need_version'] or need['state'] not in ('PUBLISHED','OFFERED'):raise ApiError(409,'Need permission is stale')
            if action=='request_consent':
                if s['status']!='PROPOSED':raise ApiError(409,'Bridge already addressed')
                recipient=self.help.audience(db,uid,c,[d['recipient']])[0]
                deny=c.journal.tag('living-no-contact',uid,recipient,p['topic'])
                if db.execute('SELECT 1 FROM living_contact_holds WHERE target_tag=?',(deny,)).fetchone():raise ApiError(409,'Contact suppressed for this topic')
            elif s['status']!='INVITED' or s['recipient_id']!=uid:raise ApiError(403,'Only invited participant can consent')
        elif action=='offer_resource':
            a=self.assets.owned(db,uid,d['asset_id']);b=self.assets.current(db,a)
            if a['status']!='ACTIVE' or b['quantity']!='1' or b['available']!='1' or not b['share_consent'] or b['audience']!='PRIVATE':raise ApiError(409,'Consented canonical singleton required')
            if any(json.loads(r[0]).get('asset_id')==d['asset_id'] and json.loads(r[0]).get('active') for r in db.execute("SELECT state FROM living_records WHERE kind='RESOURCE'")):raise ApiError(409,'Canonical asset already has a resource grant')
            if type(d['starts_at']) is not int or type(d['ends_at']) is not int or not now<=d['starts_at']<d['ends_at']<=d['expires_at']:raise ApiError(400,'Explicit bounded slot required')
            self.help.audience(db,uid,c,d['audience']);refs['asset:'+a['asset_id']]=a['version']
        elif action in ('attach_offer','reserve'):
            resource,s,asset,_=validate_resource(db,d['resource_id'],uid,now)
            r=self.help._row(db,uid,d['need_id'])
            o=db.execute('SELECT * FROM help_offers WHERE id=? AND request_id=?',(d['offer_id'],r['id'])).fetchone()
            if o is None or o['helper_id']!=resource['owner_id'] or o['status']!='OFFERED':raise ApiError(409,'Canonical helper offer required')
            if action=='attach_offer':
                if uid!=resource['owner_id']:raise ApiError(403,'Resource owner required')
                if any(json.loads(x[0])['offer_id']==o['id'] for x in db.execute("SELECT state FROM living_records WHERE kind='ATTACHMENT'")):raise ApiError(409,'Offer already bound')
            if action=='reserve':
                if r['author_id']!=uid:raise ApiError(403,'Need author required')
                attached=any(json.loads(x[0])=={'resource_id':resource['id'],'need_id':r['id'],'offer_id':o['id']} for x in db.execute("SELECT state FROM living_records WHERE kind='ATTACHMENT'"))
                if not attached:raise ApiError(409,'Helper has not authorized resource binding')
                if db.execute('SELECT 1 FROM living_slots WHERE asset_id=? AND starts_at<? AND ends_at>?',(s['asset_id'],s['ends_at'],s['starts_at'])).fetchone():raise ApiError(409,'Resource slot already reserved')
            refs.update({'living:'+resource['id']:resource['version'],'help:'+r['id']:r['version'],'asset:'+asset['asset_id']:asset['version']})
        elif action in ('withdraw','erase','correct_note'):
            r,s=record('id',uid)
            if action=='correct_note':
                if r['kind']!='NOTE':raise ApiError(409,'Only private notes support text correction')
                self.text(d['text'])
                prior=db.execute('SELECT event_id FROM living_versions WHERE record_id=? AND version=?',(r['id'],r['version'])).fetchone()[0]
                if d['causation_id']!=prior:raise ApiError(409,'Correction must reference current source event')
            if action=='withdraw' and r['kind'] not in ('RESOURCE','NEED_PERMISSION'):raise ApiError(409,'Use canonical Help withdrawals or invitation decline')
        return refs
    def prepare(self,token,action,data,key):
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):raise ApiError(400,'Idempotency key required')
        with self.tx(token,action=='erase') as (db,uid,c):
            h=c.journal.tag('living-command-body',uid,canonical([action,data]))
            old=db.execute('SELECT * FROM living_commands WHERE actor_id=? AND operation_key=?',(uid,key)).fetchone()
            if old:
                if old['body_hash']!=h:raise ApiError(409,'Idempotency key conflict')
                raise ApiError(409,'Command exists: retry the original commit receipt, not a new preparation')
            refs=self._validate(db,uid,c,action,data)
            context=list(db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?',(uid,)).fetchone())
            review={}
            if action in ('request_consent','accept_invitation','decline_invitation'):
                bridge,s=self.root(db,data['id'],kind='BRIDGE');_,permission=self.root(db,s['permission_id'],kind='NEED_PERMISSION')
                review={'task':permission['invitation_text'],'referrer':c.journal.tag('help-member',bridge['owner_id']),
                        'recipient':data.get('recipient') or c.journal.tag('help-member',s['recipient_id']),
                        'contact_only':True,'publishes_resource':False}
            elif action=='propose_bridge':
                _,note=self.root(db,data['note_id'],uid,'NOTE');_,permission=self.root(db,data['permission_id'],kind='NEED_PERMISSION')
                review={'owner_only_note':note['text'],'task':permission['invitation_text'],'sends_invitation':False}
            elif action=='offer_resource':
                a=self.assets.owned(db,uid,data['asset_id']);review={'canonical_claim':self.assets.current(db,a),'audience':data['audience'],
                            'starts_at':data['starts_at'],'ends_at':data['ends_at'],'expires_at':data['expires_at']}
            elif action in ('attach_offer','reserve'):
                resource,s,_,claim=validate_resource(db,data['resource_id'],uid,int(time.time()))
                review={'task':self.help._row(db,uid,data['need_id'])['text'],'helper':c.journal.tag('help-member',resource['owner_id']),
                        'category':claim['category'],'unit':claim['unit'],'quantity':'1','conditions':claim['conditions'],
                        'starts_at':s['starts_at'],'ends_at':s['ends_at'],'evidence':'SELF_DECLARED'}
            body=canonical({'action':action,'data':data,'review':review,'expected':refs,'context':context,'session':c.journal.tag('living-session',token)})
            raw=secrets.token_urlsafe(32);hashed=hashlib.sha256(raw.encode()).hexdigest();expires=int(time.time())+120
            seal=c.journal.tag('living-command',uid,key,h,body,str(expires),hashed)
            if db.execute('SELECT count(*) FROM living_commands').fetchone()[0]>=1024:raise ApiError(429,'Synthetic command limit')
            db.execute('INSERT INTO living_commands VALUES (?,?,?,?,?,?,?,NULL)',(hashed,uid,key,h,body,expires,seal))
            return {'challenge':raw,'preview':json.loads(body)|{'session':'REDACTED'},'confirmation':'ПОДТВЕРЖДАЮ'}
    def commit(self,token,challenge,confirmation):
        if type(challenge) is not str or len(challenge)>100 or confirmation!='ПОДТВЕРЖДАЮ':raise ApiError(400,'Explicit confirmation required')
        with self.tx(token,True) as (db,uid,c):
            q=db.execute('SELECT * FROM living_commands WHERE token_hash=? AND actor_id=?',(hashlib.sha256(challenge.encode()).hexdigest(),uid)).fetchone()
            if q is None:raise ApiError(409,'Challenge unavailable')
            if q['seal']!=c.journal.tag('living-command',uid,q['operation_key'],q['body_hash'],q['body'],str(q['expires_at']),q['token_hash']):raise JournalUnavailable('Living challenge substitution')
            body=json.loads(q['body']);action=body['action'];d=body['data']
            if q['receipt']:
                receipt=json.loads(q['receipt']);result=receipt['result']
                if receipt['seal']!=c.journal.tag('living-receipt',q['seal'],canonical(result)):raise JournalUnavailable('Receipt substitution')
                if db.execute('SELECT 1 FROM living_holds WHERE target_tag=?',(c.journal.tag('living-record',result['id']),)).fetchone():raise ApiError(409,'Record retired')
                return result
            if action!='erase' and db.execute("SELECT 1 FROM living_holds WHERE reason='restore'").fetchone():raise JournalUnavailable('Living UNKNOWN_RECOVERY')
            if not q['expires_at']-120<=int(time.time())<q['expires_at'] or body['session']!=c.journal.tag('living-session',token):raise ApiError(409,'Fresh bound session required')
            if body['context']!=list(db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?',(uid,)).fetchone()):raise ApiError(409,'Privacy context changed; prepare again')
            if self._validate(db,uid,c,action,d)!=body['expected']:raise ApiError(409,'Source version changed')
            rid=self._execute(db,uid,c,action,d,q['operation_key'],token)
            result={'id':rid,'committed':True}
            # Completed previews must not retain erased note/invitation content.
            safe_body=canonical({'action':action,'data':{},'expected':{},'context':body['context'],'session':body['session']})
            safe_seal=c.journal.tag('living-command',uid,q['operation_key'],q['body_hash'],safe_body,str(q['expires_at']),q['token_hash'])
            receipt={'result':result,'seal':c.journal.tag('living-receipt',safe_seal,canonical(result))}
            db.execute('INSERT OR REPLACE INTO living_commands VALUES (?,?,?,?,?,?,?,?)',
                       (q['token_hash'],uid,q['operation_key'],q['body_hash'],safe_body,q['expires_at'],safe_seal,canonical(receipt)))
            return result
    def _execute(self,db,uid,c,action,d,key,token):
        rid=str(uuid5(NAMESPACE_URL,'living:'+uid+':'+key));kind=None;event=None;s=dict(d)
        if db.execute('SELECT 1 FROM living_holds WHERE target_tag=?',(c.journal.tag('living-record',rid),)).fetchone():raise ApiError(409,'Retired operation')
        if action=='note':kind,event='NOTE','PrivateNoteRecorded'
        elif action=='need_permission':
            kind,event='NEED_PERMISSION','ReferralScopeAuthorized';s['active']=True
            s['need_version']=self.help._row(db,uid,d['need_id'])['version']
        elif action=='propose_bridge':kind,event='BRIDGE','ReferralBridgeProposed';s.update(status='PROPOSED',recipient_id=None)
        elif action in ('request_consent','accept_invitation','decline_invitation'):
            rid=d['id'];r,s=self.root(db,rid,kind='BRIDGE');_,p=self.root(db,s['permission_id'],kind='NEED_PERMISSION')
            if action=='request_consent':s.update(status='INVITED',recipient_id=self.help.audience(db,uid,c,[d['recipient']])[0]);event='ConsentRequested'
            elif action=='accept_invitation':s['status']='CONSENTED';event='ContactConsentGranted'
            else:
                s['status']='DECLINED';event='DeclinedOrWithdrawn'
                tag=c.journal.tag('living-no-contact',r['owner_id'],uid,p['topic'])
                db.execute('INSERT OR IGNORE INTO living_contact_holds VALUES (?)',(tag,))
                e={'kind':'living_contact_deny','owner':'','target':tag,'state':'DENY','recorded_at':0}
                db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(uuid4().hex,canonical(e)))
            update(db,c,rid,s,event);return rid
        elif action=='offer_resource':
            kind,event='RESOURCE','ResourceOffered';s['audience']=self.help.audience(db,uid,c,d['audience']);s['active']=True
            s['asset_version']=self.assets.owned(db,uid,d['asset_id'])['version']
        elif action=='attach_offer':kind,event='ATTACHMENT','FulfillmentProposed'
        elif action=='reserve':
            kind,event='RESERVATION','ResourceReserved';resource,rs,_,_=validate_resource(db,d['resource_id'],uid,int(time.time()))
            s.update(asset_id=rs['asset_id'],starts_at=rs['starts_at'],ends_at=rs['ends_at'],help_state='ACCEPTED')
            request=self.help._row(db,uid,d['need_id'])
            prepared=self.help.prepare(token,d['need_id'],request['version'],'accept',{'offer_id':d['offer_id']})
            def reserve_in_help(tx,actor,control):
                if tx is not db or actor!=uid:raise JournalUnavailable('Reservation transaction substitution')
                db.execute('INSERT INTO living_records VALUES (?,?,?,?,?,?)',(rid,uid,kind,1,canonical(s),''))
                db.execute('INSERT INTO living_slots VALUES (?,?,?,?,?,?)',(rid,rs['asset_id'],d['need_id'],d['offer_id'],rs['starts_at'],rs['ends_at']))
            self.help.commit(token,prepared['challenge'],'ПОДТВЕРЖДАЮ',_living_reservation=reserve_in_help)
            return rid
        elif action=='erase':retire(db,d['id'],c);return d['id']
        elif action in ('withdraw','correct_note'):
            rid=d['id'];r,s=self.root(db,rid,uid)
            if action=='withdraw':s['active']=False;event='DeclinedOrWithdrawn'
            else:s['text']=d['text'];event='RecordCorrected'
            update(db,c,rid,s,event);return rid
        if db.execute('SELECT count(*) FROM living_records').fetchone()[0]>=256:raise ApiError(429,'Synthetic record limit')
        db.execute('INSERT INTO living_records VALUES (?,?,?,?,?,?)',(rid,uid,kind,1,canonical(s),''));emit(db,c,rid,event);return rid
    def view(self,token):
        with self.tx(token) as (db,uid,c):
            items=[];now=int(time.time())
            for r in db.execute('SELECT * FROM living_records ORDER BY id'):
                s=json.loads(r['state']);owner=r['owner_id']==uid
                if r['kind']=='BRIDGE' and not owner and s.get('recipient_id')==uid:
                    try:
                        _,p=self.root(db,s['permission_id'],kind='NEED_PERMISSION')
                        need=self.help._row(db,r['owner_id'],p['need_id'])
                    except ApiError:continue
                    if p['expires_at']>now and p['active'] and need['version']==p['need_version'] and need['state'] in ('PUBLISHED','OFFERED'):
                        items.append({'id':r['id'],'kind':'INVITATION','status':s['status'],'topic':p['topic'],'task':p['invitation_text'],'referrer':c.journal.tag('help-member',r['owner_id'])})
                elif r['kind']=='NEED_PERMISSION' and not owner:
                    try:need=self.help._row(db,uid,s['need_id'])
                    except ApiError:continue
                    if s['active'] and s['expires_at']>now and need['version']==s['need_version'] and need['state'] in ('PUBLISHED','OFFERED'):
                        items.append({'id':r['id'],'kind':'REFERRAL_TASK','topic':s['topic'],'task':s['invitation_text'],'need_id':s['need_id']})
                elif r['kind']=='RESOURCE':
                    try:_,s,_,b=validate_resource(db,r['id'],uid,now)
                    except ApiError:continue
                    occupied=bool(db.execute('SELECT 1 FROM living_slots WHERE asset_id=? AND starts_at<? AND ends_at>?',(s['asset_id'],s['ends_at'],s['starts_at'])).fetchone())
                    items.append({'id':r['id'],'kind':'RESOURCE','version':r['version'],'asset_id':s['asset_id'],
                                  'provider':c.journal.tag('help-member',r['owner_id']),'category':b['category'],
                                  'unit':b['unit'],'quantity':'1','available':not occupied,'conditions':b['conditions'],
                                  'right_claim':b['right'],'starts_at':s['starts_at'],'ends_at':s['ends_at'],
                                  'evidence':'SELF_DECLARED','consent':'EXPLICIT_OWNER_CHALLENGE','expires_at':s['expires_at']})
                elif owner:
                    if s.get('expires_at',now+1)<=now:continue
                    items.append({'id':r['id'],'kind':r['kind'],'version':r['version'],'state':view_state(db,s)})
            feed=self.help.feed(token)
            feed['items'].sort(key=lambda need:need['id'])
            for need in feed['items']:
                need['publication_evidence']='EXPLICIT_AUTHOR_SESSION' if need['state']!='DRAFT' else 'PRIVATE_DRAFT'
                need['fulfillment_evidence']='MUTUAL_SESSION_CONFIRMATION' if need['state']=='COMPLETED' and need['owner_confirmed'] and need['helper_confirmed'] else 'NOT_MUTUALLY_CONFIRMED'
                typed=[];previous=None
                for event in db.execute('SELECT * FROM help_events WHERE request_id=? ORDER BY version',(need['id'],)):
                    name={'publish':'NeedExpressed','offer':'FulfillmentProposed','accept':'FulfillmentAuthorized',
                          'withdraw':'DeclinedOrWithdrawn','withdraw_offer':'DeclinedOrWithdrawn','decline':'DeclinedOrWithdrawn',
                          'dispute':'DisputeOpened','revise':'RecordCorrected'}.get(event['action'],'NeedDraftRecorded')
                    if event['action']=='confirm':name='FulfillmentConfirmed' if event['state']=='COMPLETED' else 'ResultConfirmationRecorded'
                    typed.append({'event_id':event['event_id'],'type':name,'version':event['version'],
                                  'canonical_source':'HELP','causation_id':previous})
                    previous=event['event_id']
                need['typed_events']=typed
            return {'format':'living-deeds-view-1','scope':'authorized current records, not public census','items':items,
                    'needs':feed['items'],'matches':'NO_AVAILABLE_MATCHES' if not any(i['kind']=='RESOURCE' and i['available'] for i in items) else 'AUTHORIZED_RESOURCES',
                    'summary':{'completed':sum(r['state']=='COMPLETED' for r in feed['items']),
                               'open_needs':sum(r['state'] in ('PUBLISHED','OFFERED') for r in feed['items']),
                               'disputed':sum(r['state']=='DISPUTED' for r in feed['items']),
                               'withdrawn':sum(r['state']=='WITHDRAWN' for r in feed['items']),
                               'available_resource_slots':sum(i['kind']=='RESOURCE' and i['available'] for i in items),
                               'resource_categories_visible':sorted({i['category'] for i in items if i['kind']=='RESOURCE'}),
                               'independently_verified_resources':None,'global_coverage':'UNKNOWN'},'federation':False}

    def expire(self,token):
        """Explicit synthetic retention worker; reads deny expiry even before cleanup."""
        with self.tx(token,True) as (db,uid,c):
            now=int(time.time())
            previews=db.execute('DELETE FROM living_commands WHERE actor_id=? AND receipt IS NULL AND expires_at<=?',(uid,now)).rowcount
            ids=[r['id'] for r in db.execute('SELECT id,state FROM living_records WHERE owner_id=?',(uid,)) if json.loads(r['state']).get('expires_at',now+1)<=now]
            for rid in ids:retire(db,rid,c)
            return {'expired_records':len(ids),'expired_previews':previews}

    @staticmethod
    def suggest(text):
        """Private uncommitted suggestion. Never search other people's journals."""
        if not schema.enabled() or type(text) is not str or not 2<=len(text)<=1000 or is_sensitive_memory('living',text):return None
        from xblago.domain.help_service import suggest_draft
        from xblago.domain.asset_registry import suggest_claim
        need=suggest_draft(text)
        asset=suggest_claim(text)
        return {'type':'HELP_DRAFT' if need else 'ASSET_DRAFT' if asset else 'PRIVATE_NOTE_DRAFT',
                'suggestion':need or asset or {'text':text,'audience':'OWNER_ONLY'},
                'committed':False,'requires_explicit_confirmation':True}
