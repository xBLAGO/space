"""Nonfinancial help. Sessions identify actors; an LLM never authorizes actions."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import time
from uuid import uuid4,uuid5,NAMESPACE_URL
from xblago.core import help_schema
from xblago.core.memory import is_sensitive_memory
from xblago.core.security_journal import canonical,JournalUnavailable
from xblago.integrations.web import ApiError

TERMINAL={'WITHDRAWN'}

def fingerprint(db,rid,journal):
    row=db.execute('SELECT * FROM help_requests WHERE id=?',(rid,)).fetchone()
    if not row:return None
    request=dict(row);request.pop('head_tag')
    values={'request':request}
    for table,order in [('help_audience','member_id'),('help_offers','id'),('help_events','version')]:
        values[table]=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE request_id=? ORDER BY {order}',(rid,))]
    return journal.tag('help-state',canonical(values))

def scrub(db,rid):
    db.execute("UPDATE help_requests SET text='',state='WITHDRAWN',selected_offer=NULL,owner_confirmed=0,helper_confirmed=0 WHERE id=?",(rid,))
    db.execute('DELETE FROM help_audience WHERE request_id=?',(rid,))
    db.execute("UPDATE help_offers SET note='',status='WITHDRAWN' WHERE request_id=?",(rid,))
    db.execute('DELETE FROM help_challenges WHERE request_id=?',(rid,))

def apply_checkpoint(db,event,journal):
    help_schema.validate(db,required=True)
    held=db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(event['target'],)).fetchone()
    if held:return
    row=next((r for r in db.execute('SELECT id,head_tag FROM help_requests') if journal.tag('help-request',r['id'])==event['target']),None)
    if row and row['head_tag']==event['state'] and fingerprint(db,row['id'],journal)==event['state']:return
    db.execute('INSERT INTO help_holds VALUES (?,?)',(event['target'],'backup-or-uncertain-commit'))
    if row:scrub(db,row['id'])

def purge_user(db,uid,journal):
    if not help_schema.present(db):return
    ids=[r[0] for r in db.execute('SELECT id FROM help_requests WHERE author_id=? UNION SELECT request_id FROM help_offers WHERE helper_id=? UNION SELECT request_id FROM help_audience WHERE member_id=?',(uid,uid,uid))]
    for rid in ids:
        scrub(db,rid);db.execute('INSERT OR IGNORE INTO help_holds VALUES (?,?)',(journal.tag('help-request',rid),'account-deleted'))
    db.execute('UPDATE help_requests SET author_id=NULL WHERE author_id=?',(uid,))
    db.execute('UPDATE help_offers SET helper_id=NULL WHERE helper_id=?',(uid,))
    db.execute('DELETE FROM help_audience WHERE member_id=?',(uid,))
    db.execute('DELETE FROM help_operations WHERE actor_id=?',(uid,));db.execute('DELETE FROM help_challenges WHERE actor_id=?',(uid,))

def suggest_draft(text):
    # A tentative UI suggestion, not durable extraction or action authorization.
    if type(text) is str and re.match(r'^\s*(?:мне (?:нужна|нужен|нужно|нужны) помощь|я ищу (?:помощь|помощника|мастера|учителя))\b',text,re.I) and not is_sensitive_memory('help',text):
        return {'text':text[:2000],'message':'Можно подготовить черновик потребности. Публикация потребует отдельного подтверждения.'}
    return None

class HelpService:
    def __init__(self,app):self.app,self.store=app,app.store
    @contextmanager
    def tx(self,token,fresh=False):
        if not help_schema.enabled():raise ApiError(404,'Help недоступен')
        from xblago.core.integration import enabled as integration_enabled
        from xblago.core.sqlite_safety import require_safe_sqlite,UnsafeSQLite
        if not integration_enabled() or any(os.getenv(k,'').lower() not in ('true','1','yes') for k in ('XBLAGO_PILOT_MODE','XBLAGO_REQUIRE_SECURE_IDENTITY','XBLAGO_REQUIRE_PRIVACY_JOURNAL')):raise ApiError(503,'Help требует защищённый synthetic runtime')
        try:require_safe_sqlite()
        except UnsafeSQLite as exc:raise JournalUnavailable('Help requires verified SQLite') from exc
        self.app.identity.require_enabled()
        with self.store._locked_transaction() as db:
            help_schema.validate(db,required=True)
            from xblago.core.privacy_control import PrivacyControl
            control=PrivacyControl.open(db)
            if control is None or control.journal.witness_directory is None:raise JournalUnavailable('Help requires journal and witness')
            uid=self.app.identity.session_row(db,token,fresh=True)['blago_user_id']
            # Detect silent app edits/partial loss even without a pending replay.
            for row in db.execute('SELECT id,head_tag FROM help_requests').fetchall():
                target=control.journal.tag('help-request',row['id'])
                if not db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(target,)).fetchone() and fingerprint(db,row['id'],control.journal)!=row['head_tag']:
                    raise JournalUnavailable('Help state integrity mismatch')
            latest={}
            for event_row in control.rows:
                event=json.loads(event_row[2])
                if event['kind']=='help_state':latest[event['target']]=event['state']
            current={control.journal.tag('help-request',row['id']):row for row in db.execute('SELECT id,head_tag FROM help_requests')}
            for target,expected in latest.items():
                if db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(target,)).fetchone():continue
                row=current.get(target)
                if row is None or row['head_tag']!=expected:raise JournalUnavailable('Help retained frontier mismatch')
            yield db,uid,control
    def code(self,token):
        with self.tx(token) as (db,uid,c):return {'code':c.journal.tag('help-member',uid)}
    @staticmethod
    def _text(text):
        if type(text) is not str or not 2<=len(text.strip())<=2000 or is_sensitive_memory('help',text):raise ApiError(400,'Нужен несекретный текст длиной 2–2000 символов')
        if re.search(r'рубл|₽|деньг|оплат|кошел[её]к|wallet|seed.?phrase',text,re.I):raise ApiError(400,'Help 01 предназначен только для нефинансовой помощи')
        return text.strip()
    def audience(self,db,uid,c,codes):
        if type(codes) is not list or not 1<=len(codes)<=8 or any(type(x) is not str or not re.fullmatch('[0-9a-f]{64}',x) for x in codes):raise ApiError(400,'Укажи коды 1–8 участников')
        members=[]
        for code in sorted(set(codes)):
            row=db.execute("SELECT blago_user_id FROM users WHERE status='active' AND blago_tag('help-member',blago_user_id)=?",(code,)).fetchone()
            if row is None or row[0]==uid:raise ApiError(400,'Код аудитории недоступен')
            members.append(row[0])
        return sorted(members)
    def _row(self,db,uid,rid):
        r=db.execute('SELECT * FROM help_requests WHERE id=?',(rid,)).fetchone()
        if r is None:raise ApiError(404,'Обращение недоступно')
        owner=r['author_id']==uid
        member=db.execute('SELECT 1 FROM help_audience WHERE request_id=? AND member_id=?',(rid,uid)).fetchone()
        previous_helper=db.execute('SELECT 1 FROM help_offers WHERE request_id=? AND helper_id=?',(rid,uid)).fetchone()
        if not (owner or (member and r['state']!='DRAFT') or (previous_helper and r['state']=='WITHDRAWN')):raise ApiError(404,'Обращение недоступно')
        return r
    def _view(self,db,uid,r):
        owner=r['author_id']==uid;rid=r['id']
        offers=[]
        for o in db.execute('SELECT * FROM help_offers WHERE request_id=? ORDER BY id',(rid,)):
            if owner or o['helper_id']==uid:offers.append({'id':o['id'],'status':o['status'],'note':o['note'],'mine':o['helper_id']==uid,'participant':db.execute("SELECT blago_tag('help-member',?)",(o['helper_id'],)).fetchone()[0] if owner and o['helper_id'] else ''})
        return {'id':rid,'text':r['text'],'state':r['state'],'version':r['version'],'owner':owner,'offers':offers,'owner_confirmed':bool(r['owner_confirmed']),'helper_confirmed':bool(r['helper_confirmed']),
                'audience':[x[0] for x in db.execute('SELECT blago_tag(\'help-member\',member_id) FROM help_audience WHERE request_id=? ORDER BY member_id',(rid,))] if owner else [],
                'events':[{'version':x['version'],'action':x['action'],'role':x['actor_role'],'state':x['state']} for x in db.execute('SELECT * FROM help_events WHERE request_id=? ORDER BY version',(rid,))]}
    def get(self,token,rid):
        with self.tx(token) as (db,uid,c):return self._view(db,uid,self._row(db,uid,rid))
    def feed(self,token):
        with self.tx(token) as (db,uid,c):
            rows=db.execute("SELECT DISTINCT r.* FROM help_requests r LEFT JOIN help_audience a ON a.request_id=r.id LEFT JOIN help_offers o ON o.request_id=r.id WHERE r.author_id=? OR (a.member_id=? AND r.state<>'DRAFT') OR (o.helper_id=? AND r.state='WITHDRAWN') ORDER BY r.created_at DESC LIMIT 100",(uid,uid,uid)).fetchall()
            return {'items':[self._view(db,uid,r) for r in rows],'completed_owned':db.execute("SELECT count(*) FROM help_requests WHERE author_id=? AND state='COMPLETED'",(uid,)).fetchone()[0]}
    def emit(self,db,c,rid,action,role):
        r=db.execute('SELECT * FROM help_requests WHERE id=?',(rid,)).fetchone()
        eid=str(uuid4());db.execute('INSERT INTO help_events VALUES (?,?,?,?,?,?,?)',(eid,rid,r['version'],action,role,r['state'],int(time.time())))
        tag=fingerprint(db,rid,c.journal);db.execute('UPDATE help_requests SET head_tag=? WHERE id=?',(tag,rid))
        payload={'kind':'help_state','owner':c.journal.tag('user',r['author_id'] or ''),'target':c.journal.tag('help-request',rid),'state':tag,'recorded_at':int(time.time())}
        db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(eid,canonical(payload)))
    def draft(self,token,text,audience,key):
        text=self._text(text)
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):raise ApiError(400,'Нужен ключ повторной доставки')
        with self.tx(token,True) as (db,uid,c):
            members=self.audience(db,uid,c,audience);h=hashlib.sha256(canonical([text,members]).encode()).hexdigest()
            old=db.execute('SELECT * FROM help_operations WHERE actor_id=? AND operation_key=?',(uid,key)).fetchone()
            rid=str(uuid5(NAMESPACE_URL,'help01:'+uid+':'+key))
            if db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(c.journal.tag('help-request',rid),)).fetchone():raise ApiError(409,'Состояние закрыто после восстановления')
            if old:
                if old['body_hash']!=h:raise ApiError(409,'Ключ уже использован для другого текста')
                return {'id':old['request_id']}
            if db.execute('SELECT count(*) FROM help_requests').fetchone()[0]>=256 or db.execute('SELECT count(*) FROM help_requests WHERE author_id=?',(uid,)).fetchone()[0]>=32:raise ApiError(429,'Лимит синтетической лаборатории')
            db.execute("INSERT INTO help_requests(id,author_id,text,state,version,created_at) VALUES (?,?,?,'DRAFT',1,?)",(rid,uid,text,int(time.time())))
            db.executemany('INSERT INTO help_audience VALUES (?,?)',[(rid,m) for m in members])
            db.execute('INSERT INTO help_operations VALUES (?,?,?,?)',(uid,key,h,rid));self.emit(db,c,rid,'draft','owner')
            return {'id':rid}
    def _check(self,db,uid,r,action,args):
        owner=r['author_id']==uid;state=r['state'];rid=r['id']
        own=db.execute('SELECT * FROM help_offers WHERE request_id=? AND helper_id=?',(rid,uid)).fetchone()
        if action=='publish':ok=owner and state=='DRAFT'
        elif action=='revise':ok=owner and state in ('DRAFT','PUBLISHED','OFFERED')
        elif action=='offer':ok=not owner and state in ('PUBLISHED','OFFERED') and (not own or own['status'] in ('WITHDRAWN','REJECTED'))
        elif action in ('accept','decline'):
            o=db.execute('SELECT * FROM help_offers WHERE id=? AND request_id=?',(args.get('offer_id'),rid)).fetchone()
            ok=owner and state=='OFFERED' and o is not None and o['status']=='OFFERED'
        elif action=='withdraw_offer':ok=not owner and own is not None and own['status'] in ('OFFERED','ACCEPTED') and state not in ('WITHDRAWN','COMPLETED','DISPUTED')
        elif action=='withdraw':ok=owner and state!='WITHDRAWN'
        elif action in ('confirm','dispute'):
            party=owner or (own is not None and own['id']==r['selected_offer'])
            ok=party and state in (('ACCEPTED','AWAITING_CONFIRMATION') if action=='confirm' else ('ACCEPTED','AWAITING_CONFIRMATION','COMPLETED'))
            if action=='confirm' and r['owner_confirmed' if owner else 'helper_confirmed']:ok=False
        else:ok=False
        if not ok:raise ApiError(409,'Действие не разрешено в текущем состоянии')
    def prepare(self,token,rid,version,action,args):
        if type(rid) is not str or len(rid)!=36 or type(action) is not str or action not in {'publish','revise','offer','accept','decline','withdraw_offer','withdraw','confirm','dispute'} or type(args) is not dict:raise ApiError(400,'Invalid action')
        expected={'revise':{'text','audience'},'offer':{'note'},'accept':{'offer_id'},'decline':{'offer_id'}}.get(action,set())
        if set(args)!=expected:raise ApiError(400,'Неверные поля подтверждения')
        if 'offer_id' in args and (type(args['offer_id']) is not str or len(args['offer_id'])!=36):raise ApiError(400,'Неверное предложение')
        with self.tx(token,True) as (db,uid,c):
            r=self._row(db,uid,rid)
            if type(version) is not int or version!=r['version']:raise ApiError(409,'Версия изменилась')
            if db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(c.journal.tag('help-request',rid),)).fetchone():raise ApiError(409,'Обращение закрыто после восстановления')
            if action=='revise':args={'text':self._text(args['text']),'members':self.audience(db,uid,c,args['audience'])}
            if action=='offer':args={'note':self._text(args['note'])}
            self._check(db,uid,r,action,args)
            db.execute('DELETE FROM help_challenges WHERE expires_at<?',(int(time.time()),))
            if db.execute('SELECT count(*) FROM help_challenges WHERE actor_id=?',(uid,)).fetchone()[0]>=32:raise ApiError(429,'Слишком много неподтверждённых действий')
            raw=secrets.token_urlsafe(32);hashed=hashlib.sha256(raw.encode()).hexdigest()
            db.execute('INSERT INTO help_challenges VALUES (?,?,?,?,?,?,?,NULL)',(hashed,uid,rid,version,action,canonical(args),int(time.time())+120))
            preview=self._view(db,uid,r)
            if action=='revise':preview=dict(preview,text=args['text'],audience=[c.journal.tag('help-member',x) for x in args['members']])
            selection=next((o for o in preview['offers'] if o['id']==args.get('offer_id')),None)
            return {'challenge':raw,'action':action,'request':preview,'selection':selection,'args':{k:v for k,v in args.items() if k!='members'},'confirmation':'ПОДТВЕРЖДАЮ'}
    def commit(self,token,challenge,confirmation):
        if type(challenge) is not str or len(challenge)>100 or confirmation!='ПОДТВЕРЖДАЮ':raise ApiError(400,'Нужно явное подтверждение')
        with self.tx(token,True) as (db,uid,c):
            row=db.execute('SELECT * FROM help_challenges WHERE token_hash=? AND actor_id=?',(hashlib.sha256(challenge.encode()).hexdigest(),uid)).fetchone()
            if row is None:raise ApiError(409,'Подтверждение недоступно')
            r=self._row(db,uid,row['request_id']);rid=r['id']
            if db.execute('SELECT 1 FROM help_holds WHERE target_tag=?',(c.journal.tag('help-request',rid),)).fetchone():raise ApiError(409,'Состояние закрыто')
            if row['receipt_json']:return json.loads(row['receipt_json']) # no repeated event/effect
            if row['expires_at']<=int(time.time()) or row['version']!=r['version']:raise ApiError(409,'Подтверждение устарело')
            action=row['action'];args=json.loads(row['args_json']);self._check(db,uid,r,action,args)
            owner=r['author_id']==uid
            if action=='publish':db.execute("UPDATE help_requests SET state='PUBLISHED' WHERE id=?",(rid,))
            elif action=='revise':
                db.execute("UPDATE help_requests SET text=?,state='DRAFT' WHERE id=?",(args['text'],rid));db.execute('DELETE FROM help_audience WHERE request_id=?',(rid,));db.executemany('INSERT INTO help_audience VALUES (?,?)',[(rid,m) for m in args['members']]);db.execute('DELETE FROM help_offers WHERE request_id=?',(rid,))
            elif action=='offer':
                db.execute("INSERT INTO help_offers VALUES (?,?,?,?,'OFFERED') ON CONFLICT(request_id,helper_id) DO UPDATE SET note=excluded.note,status='OFFERED'",(str(uuid4()),rid,uid,args['note']));db.execute("UPDATE help_requests SET state='OFFERED' WHERE id=?",(rid,))
            elif action in ('accept','decline'):
                oid=args['offer_id']
                db.execute('UPDATE help_offers SET status=? WHERE id=?',('ACCEPTED' if action=='accept' else 'REJECTED',oid))
                if action=='accept':
                    db.execute("UPDATE help_offers SET status='REJECTED' WHERE request_id=? AND id<>? AND status='OFFERED'",(rid,oid));db.execute("UPDATE help_requests SET state='ACCEPTED',selected_offer=? WHERE id=?",(oid,rid))
                elif not db.execute("SELECT 1 FROM help_offers WHERE request_id=? AND status='OFFERED'",(rid,)).fetchone():db.execute("UPDATE help_requests SET state='PUBLISHED' WHERE id=?",(rid,))
            elif action=='withdraw_offer':
                o=db.execute('SELECT * FROM help_offers WHERE request_id=? AND helper_id=?',(rid,uid)).fetchone()
                if o['status']=='ACCEPTED':scrub(db,rid)
                else:
                    db.execute("UPDATE help_offers SET status='WITHDRAWN',note='' WHERE id=?",(o['id'],))
                    if not db.execute("SELECT 1 FROM help_offers WHERE request_id=? AND status='OFFERED'",(rid,)).fetchone():db.execute("UPDATE help_requests SET state='PUBLISHED' WHERE id=?",(rid,))
            elif action=='withdraw':scrub(db,rid)
            elif action=='confirm':
                column='owner_confirmed' if owner else 'helper_confirmed';db.execute(f'UPDATE help_requests SET {column}=1 WHERE id=?',(rid,));db.execute("UPDATE help_requests SET state=CASE WHEN owner_confirmed=1 AND helper_confirmed=1 THEN 'COMPLETED' ELSE 'AWAITING_CONFIRMATION' END WHERE id=?",(rid,))
            elif action=='dispute':db.execute("UPDATE help_requests SET state='DISPUTED',owner_confirmed=0,helper_confirmed=0 WHERE id=?",(rid,))
            db.execute('UPDATE help_requests SET version=version+1 WHERE id=?',(rid,));self.emit(db,c,rid,action,'owner' if owner else 'helper')
            receipt={'id':rid,'version':r['version']+1,'state':db.execute('SELECT state FROM help_requests WHERE id=?',(rid,)).fetchone()[0]}
            # Clear stale plaintext previews; keep only the last content-free receipt.
            db.execute('DELETE FROM help_challenges WHERE request_id=?',(rid,))
            db.execute('INSERT OR REPLACE INTO help_challenges VALUES (?,?,?,?,?,?,?,?)',(row['token_hash'],uid,rid,r['version'],action,'{}',row['expires_at'],canonical(receipt)))
            return receipt


def personal_export(db,uid):
    """No third-party text/IDs, tokens, audience codes or helper notes in export."""
    if not help_schema.present(db):return []
    rows=db.execute("SELECT DISTINCT r.* FROM help_requests r LEFT JOIN help_offers o ON o.request_id=r.id WHERE r.author_id=? OR (o.helper_id=? AND (r.state='WITHDRAWN' OR (r.state<>'DRAFT' AND EXISTS(SELECT 1 FROM help_audience a WHERE a.request_id=r.id AND a.member_id=?))))",(uid,uid,uid)).fetchall()
    return [{'id':r['id'],'state':r['state'],'version':r['version'],'text':r['text'] if r['author_id']==uid else '', 'role':'owner' if r['author_id']==uid else 'helper'} for r in rows]
