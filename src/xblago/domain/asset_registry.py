"""Durable self-declared assets over canonical blago_assets; no transfers or Help writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time
from uuid import uuid4, uuid5, NAMESPACE_URL
from xblago.core import asset_registry_schema as schema
from xblago.core.memory import is_sensitive_memory
from xblago.core.security_journal import canonical, JournalUnavailable
from xblago.domain.collective_calculator import AccountingError, LabRegistry, number, UNITS
from xblago.domain.help_service import HelpService
from xblago.integrations.web import ApiError


def fingerprint(db, aid, journal):
    root=db.execute('SELECT * FROM assetreg_records WHERE asset_id=?',(aid,)).fetchone()
    asset=db.execute('SELECT * FROM blago_assets WHERE id=?',(aid,)).fetchone()
    if root is None or asset is None:return None
    r=dict(root);r.pop('head_tag')
    payload={'canonical':dict(asset),'record':r,
             'versions':[dict(v) for v in db.execute('SELECT * FROM assetreg_versions WHERE asset_id=? ORDER BY version',(aid,))],
             'operations':[dict(v) for v in db.execute('SELECT * FROM assetreg_operations WHERE asset_id=? ORDER BY operation_key',(aid,))]}
    if root['owner_id']!=asset['blago_user_id']:
        raise JournalUnavailable('Asset owner binding mismatch')
    return journal.tag('asset-state-v1',canonical(payload))


def apply_checkpoint(db,event,journal):
    schema.validate(db,mandatory=True)
    if not re.fullmatch('[0-9a-f]{64}',event['target']) or (event['state']!='ERASED' and not re.fullmatch('[0-9a-f]{64}',event['state'])):
        raise ValueError('Invalid asset checkpoint')
    row=next((r for r in db.execute('SELECT id FROM blago_assets') if journal.tag('asset-record',r['id'])==event['target']),None)
    held=db.execute('SELECT 1 FROM assetreg_holds WHERE target_tag=?',(event['target'],)).fetchone()
    if row and not held and event['state']!='ERASED' and fingerprint(db,row['id'],journal)==event['state']:
        return
    # Denial dominates missing data, rolled-back updates, grant and lost commit.
    db.execute('INSERT OR IGNORE INTO assetreg_holds VALUES (?,?)',(event['target'],'restore-or-erased'))
    if row:db.execute('DELETE FROM blago_assets WHERE id=?',(row['id'],))


def check_frontiers(db,control):
    if not schema.validate(db):return
    latest={}
    for row in control.rows:
        e=json.loads(row[2])
        if e['kind']=='asset_state':latest[e['target']]=e['state']
    # Include this transaction's protocol intents when checking before commit.
    for row in db.execute('SELECT payload FROM privacy_outbox ORDER BY rowid'):
        e=json.loads(row[0])
        if e['kind']=='asset_state':latest[e['target']]=e['state']
    found={}
    for r in db.execute('SELECT asset_id,head_tag FROM assetreg_records'):
        target=control.journal.tag('asset-record',r['asset_id']);found[target]=r
        if db.execute('SELECT 1 FROM assetreg_holds WHERE target_tag=?',(target,)).fetchone():
            raise JournalUnavailable('Held asset reappeared')
        if latest.get(target)!=r['head_tag'] or fingerprint(db,r['asset_id'],control.journal)!=r['head_tag']:
            raise JournalUnavailable('Asset integrity/frontier mismatch')
    for target,state in latest.items():
        held=db.execute('SELECT 1 FROM assetreg_holds WHERE target_tag=?',(target,)).fetchone()
        if target not in found and not held:
            raise JournalUnavailable('Missing asset retained frontier')
        if state=='ERASED' and (not held or target in found):
            raise JournalUnavailable('Asset erasure frontier mismatch')
    # A detached canonical row must not bypass a retained hold.
    for r in db.execute('SELECT id FROM blago_assets'):
        if db.execute('SELECT 1 FROM assetreg_holds WHERE target_tag=?',(control.journal.tag('asset-record',r['id']),)).fetchone():
            raise JournalUnavailable('Erased canonical asset reappeared')


def personal_export(db,uid):
    if not schema.validate(db):return []
    return [{'id':r['asset_id'],'version':r['version'],'status':r['status'],
             'history':[dict(v) for v in db.execute('SELECT version,event_id,body,action,recorded_at FROM assetreg_versions WHERE asset_id=? ORDER BY version',(r['asset_id'],))]}
            for r in db.execute('SELECT * FROM assetreg_records WHERE owner_id=? ORDER BY asset_id',(uid,))]


def suggest_claim(text):
    """Tentative schema only. No persistence, inferred title, quantity or permission."""
    if type(text) is not str or len(text)>2000 or is_sensitive_memory('asset',text):return None
    if not re.match(r'^\s*у меня есть\b',text,re.I):return None
    category=next((code for word,code in [('велосипед','bicycle'),('ноутбук','computer'),('компьютер','computer'),('инструмент','tool')] if re.search(r'\b'+word,text,re.I)),None)
    if not category:return None
    return {'category':category,'quantity':None,'available':None,'share_consent':False,'audience':'PRIVATE',
            'status':'TENTATIVE','message':'Можно подготовить личный черновик. Количество, право и доступность нужно уточнить; публикация требует отдельного подтверждения.'}


class AssetRegistry:
    def __init__(self,app):
        self.app,self.store=app,app.store;self.guards=HelpService(app)

    @contextmanager
    def tx(self,token):
        if not schema.enabled():raise ApiError(404,'Реестр выключен')
        with self.guards.tx(token) as (db,uid,c):
            schema.validate(db,mandatory=True);check_frontiers(db,c)
            yield db,uid,c
            check_frontiers(db,c)

    @staticmethod
    def body(value):
        if type(value) is not dict:raise ApiError(400,'Нужны поля заявления')
        v=dict(value);label=v.pop('label',None)
        if type(label) is not str or not 2<=len(label.strip())<=160 or is_sensitive_memory('asset',label):
            raise ApiError(400,'Нужно несекретное название')
        unknown=v.get('quantity') is None
        try:
            if unknown:
                if v.get('available') is not None:raise AccountingError('Unknown stock has unknown availability')
                checked=LabRegistry._body(dict(v,quantity='1',available='0'))
                checked.update(quantity=None,available=None)
            else:checked=LabRegistry._body(v)
        except (AccountingError,TypeError,KeyError) as exc:
            raise ApiError(400,'Неверные количество, единицы или согласие') from exc
        return dict(checked,label=label.strip())

    @staticmethod
    def stamp(now):return datetime.fromtimestamp(now,timezone.utc).isoformat()

    def owned(self,db,uid,aid):
        r=db.execute('SELECT * FROM assetreg_records WHERE asset_id=? AND owner_id=?',(aid,uid)).fetchone()
        if r is None:raise ApiError(404,'Заявление недоступно')
        return r

    def current(self,db,r):
        return json.loads(db.execute('SELECT body FROM assetreg_versions WHERE asset_id=? AND version=?',(r['asset_id'],r['version'])).fetchone()[0])

    def emit(self,db,c,aid,action,body):
        r=db.execute('SELECT * FROM assetreg_records WHERE asset_id=?',(aid,)).fetchone()
        eid=str(uuid4())
        db.execute('INSERT INTO assetreg_versions VALUES (?,?,?,?,?,?)',(aid,r['version'],eid,canonical(body),action,r['updated_at']))
        tag=fingerprint(db,aid,c.journal)
        db.execute('UPDATE assetreg_records SET head_tag=? WHERE asset_id=?',(tag,aid))
        payload={'kind':'asset_state','owner':c.journal.tag('user',r['owner_id']),'target':c.journal.tag('asset-record',aid),'state':tag,'recorded_at':0}
        db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(eid,canonical(payload)))

    def draft(self,token,body,key):
        body=self.body(body)
        if body['audience']!='PRIVATE' or body['share_consent'] or body['available'] not in ('0',None):
            raise ApiError(400,'Черновик не публикует и не предоставляет имущество')
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):raise ApiError(400,'Нужен ключ повторной доставки')
        with self.tx(token) as (db,uid,c):
            aid=str(uuid5(NAMESPACE_URL,'asset09.1:'+uid+':'+key));h=hashlib.sha256(canonical(body).encode()).hexdigest()
            if db.execute('SELECT 1 FROM assetreg_holds WHERE target_tag=?',(c.journal.tag('asset-record',aid),)).fetchone():
                raise ApiError(409,'Заявление закрыто после удаления или восстановления')
            old=db.execute('SELECT * FROM assetreg_operations WHERE owner_id=? AND operation_key=?',(uid,key)).fetchone()
            if old:
                if old['body_hash']!=h:raise ApiError(409,'Ключ использован для другого заявления')
                return {'id':old['asset_id']}
            if db.execute('SELECT count(*) FROM assetreg_records').fetchone()[0]>=256 or db.execute('SELECT count(*) FROM assetreg_records WHERE owner_id=?',(uid,)).fetchone()[0]>=32:
                raise ApiError(429,'Лимит синтетического стенда')
            now=int(time.time());stamp=self.stamp(now)
            db.execute("INSERT INTO blago_assets (id,blago_user_id,title,description,asset_type,ownership_claim,use_permission,management_status,verification_status,visibility,created_at,updated_at) VALUES (?,?,?,?,?,'self_declared','owner_only','not_under_management','self_declared_unverified','private',?,?)",(aid,uid,body['label'],'','other',stamp,stamp))
            db.execute("INSERT INTO assetreg_records VALUES (?,?,1,'DRAFT','',?,?)",(aid,uid,now,now))
            db.execute('INSERT INTO assetreg_operations VALUES (?,?,?,?)',(uid,key,h,aid))
            self.emit(db,c,aid,'DRAFT',body)
            return {'id':aid}

    def prepare(self,token,aid,version,body):
        body=self.body(body)
        with self.tx(token) as (db,uid,c):
            r=self.owned(db,uid,aid);now=int(time.time())
            if r['version']>=128:raise ApiError(429,'Лимит версий; отзыв и удаление остаются доступны')
            if type(version) is not int or version!=r['version'] or r['status']=='REVOKED' or now<r['updated_at']:
                raise ApiError(409,'Версия изменилась или заявление отозвано')
            db.execute('DELETE FROM assetreg_challenges WHERE expires_at<=?',(now,))
            if db.execute('SELECT count(*) FROM assetreg_challenges WHERE owner_id=?',(uid,)).fetchone()[0]>=32:raise ApiError(429,'Лимит подтверждений')
            raw=secrets.token_urlsafe(32);hashed=hashlib.sha256(raw.encode()).hexdigest();session=hashlib.sha256(token.encode()).hexdigest()
            contents=canonical(body);expires=now+120;seal=c.journal.tag('asset-confirm-v1',hashed,uid,aid,version,session,contents,expires)
            db.execute('INSERT INTO assetreg_challenges VALUES (?,?,?,?,?,?,?,?,NULL)',(hashed,uid,aid,version,session,contents,expires,seal))
            return {'challenge':raw,'id':aid,'version':version,'exact_body':body,'confirmation':'ПОДТВЕРЖДАЮ'}

    def commit(self,token,challenge,confirmation):
        if type(challenge) is not str or not 20<=len(challenge)<=100 or confirmation!='ПОДТВЕРЖДАЮ':raise ApiError(400,'Нужно явное подтверждение')
        with self.tx(token) as (db,uid,c):
            hashed=hashlib.sha256(challenge.encode()).hexdigest()
            q=db.execute('SELECT * FROM assetreg_challenges WHERE token_hash=? AND owner_id=?',(hashed,uid)).fetchone()
            if q is None or q['session_hash']!=hashlib.sha256(token.encode()).hexdigest():raise ApiError(409,'Подтверждение недоступно')
            r=self.owned(db,uid,q['asset_id'])
            if q['seal']!=c.journal.tag('asset-confirm-v1',hashed,uid,r['asset_id'],q['version'],q['session_hash'],q['body'],q['expires_at']):
                raise JournalUnavailable('Asset confirmation seal mismatch')
            if q['receipt']:
                receipt=json.loads(q['receipt'])
                committed=db.execute('SELECT body,action FROM assetreg_versions WHERE asset_id=? AND version=?',(r['asset_id'],q['version']+1)).fetchone()
                if receipt!={'id':r['asset_id'],'version':q['version']+1,'status':'ACTIVE'} or committed is None or committed['body']!=q['body'] or committed['action'] not in ('DECLARE','REVISE'):
                    raise JournalUnavailable('Asset receipt substitution')
                return receipt
            now=int(time.time())
            if not q['expires_at']-120<=now<q['expires_at'] or now<r['updated_at'] or q['version']!=r['version'] or r['status']=='REVOKED':
                raise ApiError(409,'Подтверждение устарело')
            body=self.body(json.loads(q['body']));aid=r['asset_id']
            db.execute("UPDATE assetreg_records SET version=version+1,status='ACTIVE',updated_at=? WHERE asset_id=?",(now,aid))
            db.execute('UPDATE blago_assets SET title=?,visibility=?,use_permission=?,ownership_claim=?,updated_at=? WHERE id=?',
                       (body['label'],'community' if body['audience']=='LAB_COMMUNITY' else 'private','permission_required' if body['share_consent'] else 'owner_only','self_declared' if body['right']=='OWNER' else 'custodian',self.stamp(now),aid))
            self.emit(db,c,aid,'DECLARE' if r['status']=='DRAFT' else 'REVISE',body)
            receipt={'id':aid,'version':r['version']+1,'status':'ACTIVE'}
            db.execute('UPDATE assetreg_challenges SET receipt=? WHERE token_hash=?',(canonical(receipt),hashed))
            return receipt

    def revoke(self,token,aid):
        with self.tx(token) as (db,uid,c):
            r=self.owned(db,uid,aid)
            if r['status']=='REVOKED':return {'id':aid,'status':'REVOKED','version':r['version']}
            now=int(time.time())
            if now<r['updated_at']:raise ApiError(409,'Часы требуют проверки')
            body=dict(self.current(db,r),share_consent=False,available='0' if self.current(db,r)['quantity'] is not None else None,audience='PRIVATE')
            db.execute("UPDATE assetreg_records SET version=version+1,status='REVOKED',updated_at=? WHERE asset_id=?",(now,aid))
            db.execute("UPDATE blago_assets SET visibility='private',use_permission='owner_only',updated_at=? WHERE id=?",(self.stamp(now),aid))
            db.execute('DELETE FROM assetreg_challenges WHERE asset_id=?',(aid,));self.emit(db,c,aid,'REVOKE',body)
            return {'id':aid,'status':'REVOKED','version':r['version']+1}

    def erase(self,token,aid):
        with self.tx(token) as (db,uid,c):
            self.owned(db,uid,aid);db.execute('DELETE FROM blago_assets WHERE id=?',(aid,))
            return {'erased':True}

    def feed(self,token):
        with self.tx(token) as (db,uid,c):
            items=[]
            for r in db.execute('SELECT * FROM assetreg_records ORDER BY asset_id'):
                body=self.current(db,r);owner=r['owner_id']==uid
                if not owner and (r['status']!='ACTIVE' or body['audience']!='LAB_COMMUNITY'):continue
                item={'id':r['asset_id'],'version':r['version'],'status':r['status'],'mine':owner,'body':body,'evidence':'SELF_DECLARED'}
                if owner:item['history']=[dict(v) for v in db.execute('SELECT version,event_id,body,action,recorded_at FROM assetreg_versions WHERE asset_id=? ORDER BY version',(r['asset_id'],))]
                items.append(item)
            return {'format':'blago.asset.claim-view.v1','scope':'authorized current synthetic community view','items':items,'federation':False}

    def balance(self,token):
        # Same transaction for authorization + projection; no cross-worker stale cache.
        with self.tx(token) as (db,uid,c):
            view=self.feed(token);stock={};unknown=0;objects=0
            from decimal import Decimal,localcontext
            with localcontext() as ctx:
                ctx.prec=90
                for r in view['items']:
                    if r['status']!='ACTIVE':continue
                    objects+=1;b=r['body'];key=b['category']+':'+b['unit']
                    q=stock.setdefault(key,{'known_stock':Decimal(0),'available_self_declared':Decimal(0),'unknown_stock_objects':0,'unknown_availability_objects':0})
                    if b['quantity'] is None:
                        q['unknown_stock_objects']+=1;unknown+=1
                        if b['share_consent']:q['unknown_availability_objects']+=1
                    else:
                        q['known_stock']+=number(b['quantity'])
                        if b['share_consent']:q['available_self_declared']+=number(b['available'])
                return {'scope':view['scope'],'registered_objects':objects,'unknown_stock_objects':unknown,
                        'categories':{k:dict({x:str(y) if isinstance(y,Decimal) else y for x,y in v.items()},total_stock=None if v['unknown_stock_objects'] else str(v['known_stock']),total_available=None if v['unknown_availability_objects'] else str(v['available_self_declared'])) for k,v in stock.items()},
                        'unmapped_owned_assets':db.execute('SELECT count(*) FROM blago_assets WHERE blago_user_id=? AND id NOT IN (SELECT asset_id FROM assetreg_records)',(uid,)).fetchone()[0],
                        'coverage':'PARTIAL: mapped statements only; unmapped legacy excluded',
                        'btc':None,'confirmed_available':None,'flow_btc':None,'wealth_score':None,'evidence':'SELF_DECLARED'}
