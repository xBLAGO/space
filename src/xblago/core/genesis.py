"""Private declarations and author-approved public deliberation; no financial executor."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal
from xblago.core.security_journal import canonical, JournalUnavailable
from xblago.core.genesis_schema import enabled, validate
from xblago.core.memory import is_sensitive_memory
from xblago.core.memory_policy import memory_veto

ZERO = '0'*64


class EventType(str, Enum):
    ASSET = 'asset.declared'
    NEED = 'need.declared'
    PROPOSAL_DRAFT = 'proposal.drafted'
    ARGUMENT_DRAFT = 'argument.drafted'
    PROPOSAL_PUBLIC = 'proposal.published'
    ARGUMENT_PUBLIC = 'argument.published'
    PROPOSAL_WITHDRAWN = 'proposal.withdrawn'
    ARGUMENT_WITHDRAWN = 'argument.withdrawn'


@dataclass(frozen=True)
class Event:
    event_id: str
    stream_id: str
    sequence: int
    event_type: str
    schema_version: int
    payload: dict
    previous_hash: str
    recorded_at: int

    def digest(self):
        return hashlib.sha256(canonical(self.__dict__).encode()).hexdigest()


def error(status, message):
    from xblago.integrations.web import ApiError
    return ApiError(status, message)


def clean_text(text):
    if not isinstance(text,str) or not 2 <= len(text.strip()) <= 3000 or any(ord(c)<32 and c not in '\n\t' for c in text):
        raise error(400,'Text must contain 2–3000 printable characters')
    text=text.strip()
    if is_sensitive_memory('',text): raise error(400,'Sensitive information requires a separate protected procedure')
    return text


def guard(db,uid):
    validate(db,required=True)
    if not db.execute("SELECT 1 FROM users WHERE blago_user_id=? AND status='active'",(uid,)).fetchone():
        raise error(403,'Active account required')
    if db.execute('SELECT 1 FROM genesis_holds WHERE blago_user_id=?',(uid,)).fetchone():
        raise JournalUnavailable('Genesis snapshot is incomplete; restore reviewed current state')
    from xblago.core.privacy_control import PrivacyControl
    control=PrivacyControl.open(db)
    if control is None or control.journal.witness_directory is None:
        raise JournalUnavailable('Genesis requires original 05.4 journal and witness')
    found=set()
    for stream in db.execute('SELECT * FROM genesis_streams WHERE blago_user_id=?',(uid,)):
        previous=ZERO
        rows=db.execute('SELECT * FROM genesis_events WHERE stream_id=? ORDER BY sequence',(stream['stream_id'],)).fetchall()
        if not rows: raise JournalUnavailable('Genesis stream has no origin')
        for seq,row in enumerate(rows,1):
            event=Event(row['event_id'],row['stream_id'],row['sequence'],row['event_type'],row['schema_version'],json.loads(row['payload']),row['previous_hash'],row['recorded_at'])
            if seq!=event.sequence or event.previous_hash!=previous or event.digest()!=row['event_hash']:
                raise JournalUnavailable('Genesis event chain is corrupt')
            previous=row['event_hash']
            found.add(control.journal.tag('genesis-event',event.event_id,event.digest()))
        public_previous=ZERO
        expected_public=sum(r['event_type'] in ('proposal.published','argument.published') for r in rows)
        actual_public=db.execute('SELECT count(*) FROM genesis_publications WHERE stream_id=?',(stream['stream_id'],)).fetchone()[0]
        if actual_public!=expected_public: raise JournalUnavailable('Published version missing from projection')
        for rev,row in enumerate(db.execute('SELECT * FROM genesis_publications WHERE stream_id=? ORDER BY revision',(stream['stream_id'],)),1):
            payload=json.loads(row['payload'])
            body={'revision':rev,'previous_hash':public_previous,'publication':payload}
            digest=hashlib.sha256(canonical(body).encode()).hexdigest()
            event=db.execute('SELECT payload,event_type FROM genesis_events WHERE event_id=? AND stream_id=?',(row['event_id'],stream['stream_id'])).fetchone()
            if (row['revision']!=rev or row['previous_hash']!=public_previous or row['public_hash']!=digest
                    or event is None or event['event_type'] not in ('proposal.published','argument.published')
                    or json.loads(event['payload'])!={**body,'public_hash':digest}):
                raise JournalUnavailable('Public projection is corrupt')
            public_previous=digest
    for row in control.rows:
        intent=json.loads(row[2])
        if intent.get('kind')=='genesis_event' and intent['owner']==control.journal.tag('user',uid) and intent['target'] not in found:
            raise JournalUnavailable('Genesis event missing from retained snapshot')


def stream(db,uid,kind,identifier=None):
    identifier=identifier or secrets.token_hex(16)
    db.execute('INSERT INTO genesis_streams VALUES (?,?,?,?,?)',(identifier,uid,kind,'Участник '+secrets.token_hex(6),int(time.time())))
    return identifier


def emit(db,uid,identifier,event_type,payload):
    kind=db.execute('SELECT kind FROM genesis_streams WHERE stream_id=? AND blago_user_id=?',(identifier,uid)).fetchone()
    if kind is None or event_type.split('.')[0]!=kind[0]: raise error(403,'Event ownership or type mismatch')
    EventType(event_type)
    old=db.execute('SELECT sequence,event_hash FROM genesis_events WHERE stream_id=? ORDER BY sequence DESC LIMIT 1',(identifier,)).fetchone()
    event=Event(secrets.token_hex(16),identifier,old[0]+1 if old else 1,event_type,1,payload,old[1] if old else ZERO,int(time.time()))
    db.execute('INSERT INTO genesis_events VALUES (?,?,?,?,?,?,?,?,?,?)',(event.event_id,identifier,uid,event.sequence,event_type,1,canonical(payload),event.previous_hash,event.digest(),event.recorded_at))
    return event


def record_existing(db,uid,identifier,kind,payload):
    """Extension hook inside the existing asset/need transaction. No new executor."""
    if enabled():
        guard(db,uid)
        clean_text(payload['title']+' '+payload.get('description',''))
        stream(db,uid,kind,identifier)
        emit(db,uid,identifier,kind+'.declared',payload)


class Deliberation:
    def __init__(self,store): self.store=store

    def _enabled(self):
        if not enabled(): raise error(404,'Genesis is disabled')

    def converse(self,uid,text,*,request_id=None,channel='web',chat_id=None,preview_ref=None):
        """Return None for ordinary conversation. Never classify with an extra LLM call."""
        if not enabled(): return None
        if not isinstance(text,str) or len(text)>4000: raise error(400,'Invalid conversation input')
        trimmed=text.strip()
        declaration=re.fullmatch(r'У меня есть\s+(\d{1,9}(?:[.,]\d{1,2})?)\s*(?:рублей|рубля|рубль|руб\.?|₽)[.!]?',trimmed,re.I)
        draft=re.fullmatch(r'(?:Предлагаю|Помоги сформулировать (?:общественное )?предложение)(?:\s*:\s*|\s+)(.+)',trimmed,re.I|re.S)
        revision=re.fullmatch(r'Измени предложение\s+([0-9a-f]{32})\s*:\s*(.+)',trimmed,re.I|re.S)
        argument=re.fullmatch(r'Аргумент к предложению\s+([0-9a-f]{32})\s*:\s*(.+)',trimmed,re.I|re.S)
        need=re.fullmatch(r'Запиши потребность\s*:\s*(.+)',trimmed,re.I|re.S)
        withdraw=re.fullmatch(r'Сними с публикации\s+([0-9a-f]{32})[.!]?',trimmed,re.I)
        exact_approval=re.fullmatch(r'Публикуй\s*:\s*(.+)',trimmed,re.I|re.S)
        approve=bool(exact_approval) or trimmed.casefold().rstrip('.!') in ('да, публикуй этот текст','публикуй этот текст','публикую именно этот текст')
        cancel=trimmed.casefold().rstrip('.!') in ('не публикуй','отмени публикацию')
        if not any((declaration,draft,revision,argument,need,withdraw,approve,cancel)): return None
        if memory_veto(text): return None
        if request_id is None: request_id=secrets.token_hex(16)
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,100}',request_id): raise error(400,'Invalid request id')
        if preview_ref is not None and (not isinstance(preview_ref,str) or not re.fullmatch('[0-9a-f]{32}',preview_ref)):
            raise error(400,'Invalid preview reference')
        input_hash=hashlib.sha256(canonical({'text':text,'preview_ref':preview_ref}).encode()).hexdigest()
        with self.store._locked_transaction() as db:
            guard(db,uid)
            prior=db.execute('SELECT input_hash,reply FROM genesis_receipts WHERE blago_user_id=? AND request_key=?',(uid,request_id)).fetchone()
            if prior:
                if prior['input_hash']!=input_hash: raise error(409,'Idempotency key reused for different text')
                return prior['reply']
            if declaration:
                amount=int(Decimal(declaration[1].replace(',','.'))*100)
                if amount<=0: raise error(400,'Amount must be positive')
                title=f'{Decimal(amount)/100:g} RUB'
                existing=db.execute("SELECT id FROM blago_assets WHERE blago_user_id=? AND title=? AND asset_type='money'",(uid,title)).fetchone()
                if existing: reply='Это уже учтено как твой заявленный актив. Деньги никуда не переводятся.'
                else:
                    identifier=stream(db,uid,'asset')
                    now=datetime.now(timezone.utc).isoformat()
                    db.execute("INSERT INTO blago_assets VALUES (?,?,?,?,'money','self_declared','owner_only','not_under_management','self_declared_unverified','private',?,?)",(identifier,uid,title,'Заявлено участником в личном разговоре',now,now))
                    emit(db,uid,identifier,'asset.declared',{'amount_minor':amount,'currency':'RUB','verification':'self_declared_unverified','visibility':'private'})
                    reply='Учёл как твой заявленный актив: '+title+'. Это не подтверждение наличия денег и не разрешение ими распоряжаться.'
            elif need:
                content=clean_text(need[1]); identifier=stream(db,uid,'need')
                db.execute("INSERT INTO blago_needs VALUES (?,?,?,?,'private','open',?)",(identifier,uid,content[:160],content,datetime.now(timezone.utc).isoformat()))
                emit(db,uid,identifier,'need.declared',{'text':content,'visibility':'private'})
                reply='Записал потребность для тебя. Она не опубликована.'
            elif draft or revision or argument:
                kind='argument' if argument else 'proposal'
                content=clean_text((revision or argument)[2] if revision or argument else draft[1])
                target=argument[1] if argument else None
                if target: self._public(db,target)
                if revision:
                    identifier=revision[1]
                    row=db.execute("SELECT 1 FROM genesis_streams WHERE stream_id=? AND blago_user_id=? AND kind='proposal'",(identifier,uid)).fetchone()
                    if row is None: raise error(404,'Own proposal not found')
                else: identifier=stream(db,uid,kind)
                event=emit(db,uid,identifier,kind+'.drafted',{'text':content,'target':target})
                alias=db.execute('SELECT author_alias FROM genesis_streams WHERE stream_id=?',(identifier,)).fetchone()[0]
                reply=f'Черновик {"аргумента" if argument else "предложения"}:\n\n{content}\n\nАвторство: {alias} (псевдоним аккаунта, без подтверждения личности). После публикации текст доступен всем, внешние копии нельзя отозвать.\nОпубликовать именно этот текст? Можно ответить «Да, публикуй этот текст» в Web/Mini App либо «Публикуй: [полный текст]» в Telegram, или изменить его.'
                db.execute('INSERT OR REPLACE INTO genesis_previews VALUES (?,?,?,?,0,?)',(uid,identifier,event.event_id,reply,int(time.time())+600))
            elif approve:
                preview=db.execute('SELECT * FROM genesis_previews WHERE blago_user_id=?',(uid,)).fetchone()
                last=db.execute('SELECT id,text FROM dialogue_turns WHERE blago_user_id=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
                if preview is None or preview['expires_at']<int(time.time()) or last is None or last['id']!=preview['last_turn'] or last['text']!=preview['reply'] or (not exact_approval and preview_ref!=preview['draft_event']):
                    reply='Сначала нужно показать актуальный текст. Пришли предложение или его новую редакцию.'
                else:
                    identifier=preview['stream_id']
                    latest=db.execute('SELECT * FROM genesis_events WHERE stream_id=? ORDER BY sequence DESC LIMIT 1',(identifier,)).fetchone()
                    if latest['event_id']!=preview['draft_event']: raise error(409,'Draft version changed')
                    body=json.loads(latest['payload']); kind=latest['event_type'].split('.')[0]
                    content=clean_text(body['text'])
                    if exact_approval and exact_approval[1].strip()!=content:
                        raise error(409,'Approved text differs from the displayed draft')
                    if body['target']: self._public(db,body['target'])
                    previous=db.execute('SELECT revision,public_hash FROM genesis_publications WHERE stream_id=? ORDER BY revision DESC LIMIT 1',(identifier,)).fetchone()
                    alias=db.execute('SELECT author_alias FROM genesis_streams WHERE stream_id=?',(identifier,)).fetchone()[0]
                    publication={'id':identifier,'kind':kind,'text':content,'author':alias,'authorship':'authenticated_account_pseudonym','target':body['target'],'published_at':int(time.time())}
                    record={'revision':previous[0]+1 if previous else 1,'previous_hash':previous[1] if previous else ZERO,'publication':publication}
                    digest=hashlib.sha256(canonical(record).encode()).hexdigest()
                    event=emit(db,uid,identifier,kind+'.published',{**record,'public_hash':digest})
                    db.execute('INSERT INTO genesis_publications VALUES (?,?,?,?,?,?,?)',(identifier,uid,record['revision'],event.event_id,canonical(publication),record['previous_hash'],digest))
                    db.execute('DELETE FROM genesis_previews WHERE blago_user_id=?',(uid,))
                    reply='Опубликован утверждённый тобой текст: /proposals/'+(body['target'] or identifier)
            elif withdraw:
                identifier=withdraw[1]
                row=db.execute("SELECT kind FROM genesis_streams WHERE stream_id=? AND blago_user_id=? AND kind IN ('proposal','argument')",(identifier,uid)).fetchone()
                if row is None: raise error(404,'Own publication not found')
                emit(db,uid,identifier,row[0]+'.withdrawn',{})
                db.execute('DELETE FROM genesis_previews WHERE stream_id=?',(identifier,))
                reply='Снял с публикации. Уже сделанные другими людьми копии могут сохраниться.'
            else:
                db.execute('DELETE FROM genesis_previews WHERE blago_user_id=?',(uid,))
                reply='Черновики без твоего утверждения не публикую.'
            now=datetime.now(timezone.utc).isoformat()
            for role,body in (('user',text),('assistant',reply)):
                cursor=db.execute('INSERT INTO dialogue_turns (blago_user_id,channel,chat_id,role,text,created_at) VALUES (?,?,?,?,?,?)',(uid,channel,str(chat_id) if chat_id is not None else None,role,body,now))
            if draft or revision or argument: db.execute('UPDATE genesis_previews SET last_turn=? WHERE blago_user_id=?',(cursor.lastrowid,uid))
            db.execute('INSERT INTO genesis_receipts VALUES (?,?,?,?)',(uid,request_id,input_hash,reply))
            return reply

    def pending(self,uid):
        if not enabled(): return None
        with self.store._locked_transaction() as db:
            guard(db,uid)
            row=db.execute('SELECT * FROM genesis_previews WHERE blago_user_id=?',(uid,)).fetchone()
            last=db.execute('SELECT id,text FROM dialogue_turns WHERE blago_user_id=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
            if row and last and row['last_turn']==last['id'] and row['reply']==last['text'] and row['expires_at']>=int(time.time()):
                return row['draft_event']
            return None

    @staticmethod
    def _public(db,identifier):
        row=db.execute("SELECT * FROM genesis_streams WHERE stream_id=? AND kind IN ('proposal','argument')",(identifier,)).fetchone()
        if row is None: raise error(404,'Publication not found')
        # Ordinary removal is indistinguishable from an unknown public identifier.
        if not db.execute("SELECT 1 FROM users WHERE blago_user_id=? AND status='active'",(row['blago_user_id'],)).fetchone(): raise error(404,'Publication not found')
        guard(db,row['blago_user_id'])
        latest=db.execute("SELECT event_type FROM genesis_events WHERE stream_id=? AND event_type IN ('proposal.published','argument.published','proposal.withdrawn','argument.withdrawn') ORDER BY sequence DESC LIMIT 1",(identifier,)).fetchone()
        if latest is None or latest[0].endswith('.withdrawn'): raise error(404,'Publication not found')
        versions=[{'revision':r['revision'],'previous_hash':r['previous_hash'],'public_hash':r['public_hash'],'publication':json.loads(r['payload'])} for r in db.execute('SELECT * FROM genesis_publications WHERE stream_id=? ORDER BY revision',(identifier,))]
        if not versions: raise error(404,'Publication not found')
        return {'protocol':'BLAGO-deliberation-export-v1','id':identifier,'versions':versions,'current':versions[-1]['publication'],'hash_scope':'public approved versions only; not a blockchain timestamp or proof of personhood'}

    def public(self,identifier):
        self._enabled()
        if not re.fullmatch('[0-9a-f]{32}',identifier): raise error(404,'Publication not found')
        with self.store._locked_transaction() as db:
            validate(db,required=True)
            result=self._public(db,identifier)
            if result['current']['kind']!='proposal': raise error(404,'Proposal not found')
            arguments=[]
            for row in db.execute("SELECT DISTINCT s.stream_id FROM genesis_streams s JOIN genesis_publications p USING(stream_id) WHERE s.kind='argument' AND json_extract(p.payload,'$.target')=?",(identifier,)):
                try: arguments.append(self._public(db,row[0]))
                except Exception as exc:
                    from xblago.integrations.web import ApiError
                    if not isinstance(exc,ApiError) or exc.status!=404: raise
            result['arguments']=arguments
            return result

    def own(self,uid):
        self._enabled()
        with self.store._locked_transaction() as db:
            guard(db,uid)
            return {'streams':[dict(r) for r in db.execute('SELECT * FROM genesis_streams WHERE blago_user_id=?',(uid,))],
                    'events':[dict(r) for r in db.execute('SELECT * FROM genesis_events WHERE blago_user_id=? ORDER BY stream_id,sequence',(uid,))]}
