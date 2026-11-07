"""Durable optimistic generation fencing. No write transaction during generation."""
from copy import deepcopy
from dataclasses import replace
import secrets
from xblago.core.memory import MemoryStore, MemoryConflictError, is_sensitive_memory
from xblago.core.messages import AgentResponse
from xblago.core.llm import ChatProvider, OpenAICompatibleProvider
from xblago.core.semantic_memory import SemanticMemoryExtractor
from xblago.core import context_schema

SAFE_CONFLICT='Контекст изменился. Ответ не сохранён. Повтори сообщение, если хочешь.'

class ContextConflict(RuntimeError):
    """No generated text, user text, identities or provider details in the error."""
    def __init__(self):super().__init__(SAFE_CONFLICT)


class BufferedMemory(MemoryStore):
    def __init__(self,facts,history):
        self.facts=deepcopy(facts);self.records=list(deepcopy(history));self.operations=[]
    def all(self):return deepcopy(self.facts)
    def semantic_records(self):return tuple(r for r in self.records if r.status in ('confirmed','candidate'))
    def semantic_history(self):return tuple(self.records)
    def set(self,key,value):
        if is_sensitive_memory(key,value):raise ValueError('Sensitive memory is not supported')
        self.operations.append(('set',(key,deepcopy(value))))
        self.records=[replace(r,status='superseded') if r.key==key and r.status in ('confirmed','candidate') else r for r in self.records]
        self.facts[key]=deepcopy(value)
    def delete(self,key):
        existed=key in self.facts or any(r.key==key for r in self.records)
        self.operations.append(('delete',(key,)));self.facts.pop(key,None);self.records=[r for r in self.records if r.key!=key]
        return existed
    def clear(self):
        self.operations.append(('clear',()));self.facts.clear();self.records.clear()
    def save_semantic(self,record):
        from xblago.core.storage import _validate_semantic_input
        _validate_semantic_input(record.key,record.value,record.category,record.status,record.confidence)
        self.operations.append(('save_semantic',(deepcopy(record),)))
        self.records=[replace(r,status='superseded') if r.key==record.key and r.status in ('confirmed','candidate') else r for r in self.records]+[record]
        self.facts.pop(record.key,None)
        if record.status=='confirmed':self.facts[record.key]=deepcopy(record.value)
    def replace_semantic(self,record,keys,expected):
        from xblago.core.storage import _validate_semantic_input
        _validate_semantic_input(record.key,record.value,record.category,record.status,record.confidence)
        if self.all()!=expected:raise MemoryConflictError('Memory changed')
        self.operations.append(('replace_semantic',(deepcopy(record),tuple(keys),deepcopy(expected))))
        keys=set(keys)|{record.key}
        for key in keys:self.facts.pop(key,None)
        self.records=[replace(r,key=record.key,status='superseded') if r.key in keys else r for r in self.records]+[record]
        if record.status=='confirmed':self.facts[record.key]=deepcopy(record.value)
    def apply(self,memory):
        for method,args in self.operations:getattr(memory,method)(*args)


class Generation:
    def __init__(self,store,uid,channel='web',chat_id=None,update_id=None,authorize=None):
        self.authorize=authorize
        self.store,self.uid,self.channel,self.chat_id,self.update_id=store,uid,channel,chat_id,update_id
        self.id=secrets.token_hex(24);self.exchange=None;self.clear=False
        with store._locked_transaction() as db:
            context_schema.validate(db,mandatory=True)
            if authorize is not None:authorize(db)
            active=db.execute("SELECT 1 FROM users WHERE blago_user_id=? AND status='active'",(uid,)).fetchone()
            row=db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?',(uid,)).fetchone()
            if not active or not row:raise ContextConflict()
            self.version=tuple(row)
            db.execute("INSERT INTO context_generations(generation_id,blago_user_id,revision,epoch,state,channel,external_update_id) VALUES (?,?,?,?,'PREPARED',?,?)",(self.id,uid,*self.version,channel,update_id))
            self.memory=BufferedMemory(store.all_memory(uid),store.all_semantic_memory(uid,include_inactive=True))
            self.history=store.recent_history(uid,24)
    def current(self,db):
        row=db.execute("SELECT v.revision,v.epoch,g.state,u.status FROM context_versions v JOIN users u USING(blago_user_id) JOIN context_generations g USING(blago_user_id) WHERE g.generation_id=? AND g.blago_user_id=?",(self.id,self.uid)).fetchone()
        return bool(row and tuple(row[:2])==self.version and row[2]=='PREPARED' and row[3]=='active')
    def check(self):
        with self.store._locked_transaction() as db:
            if not self.current(db):raise ContextConflict()
            if self.authorize is not None:self.authorize(db)
    def reject(self):
        with self.store._locked_transaction() as db:
            db.execute("UPDATE context_generations SET state='REJECTED' WHERE generation_id=? AND state='PREPARED'",(self.id,))
    def record(self,exchange):self.exchange=exchange
    def clear_history(self):self.clear=True
    def agent(self,provider):
        from xblago.agents.personal_assistant import PersonalAssistantAgent
        generation=self
        class BoundProvider(ChatProvider):
            def generate(self,context):
                generation.check()
                return provider.generate(replace(context,context_version=f'{generation.version[0]}:{generation.version[1]}'))
        bound=BoundProvider()
        return PersonalAssistantAgent(self.memory,bound,initial_history=self.history,
            exchange_recorder=self.record,history_clearer=self.clear_history,
            memory_extractor=SemanticMemoryExtractor(bound) if isinstance(provider,OpenAICompatibleProvider) else None)
    def commit(self,replies=None):
        conflict=False
        with self.store._locked_transaction() as db:
            if not self.current(db):
                db.execute("UPDATE context_generations SET state='REJECTED' WHERE generation_id=? AND state='PREPARED'",(self.id,))
                conflict=True
            else:
                if self.authorize is not None:self.authorize(db)
                # CAS and all effects share this BEGIN IMMEDIATE. Mutators use the
                # same connection; journal flush and SQL commit precede return.
                self.memory.apply(self.store.memory_for_user(self.uid))
                if self.clear:self.store.clear_history(self.uid)
                if self.exchange:self.store.append_exchange(self.uid,self.exchange,self.channel,self.chat_id)
                if replies is not None and self.update_id is not None:self.store.mark_update_processed(self.channel,self.update_id,replies)
                revision,epoch=db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?',(self.uid,)).fetchone()
                db.execute("UPDATE context_generations SET state='COMMITTED',committed_revision=?,committed_epoch=? WHERE generation_id=?",(revision,epoch,self.id))
        if conflict:raise ContextConflict()


def enabled(store):
    with store._connect() as db:return context_schema.validate(db,mandatory=context_schema.required())


def respond(store,uid,text,provider, *, channel='web',chat_id=None,update_id=None,reply_builder=None,authorize=None):
    from xblago.core.messages import UserMessage
    generation=Generation(store,uid,channel,chat_id,update_id,authorize)
    try:
        response=generation.agent(provider).respond(UserMessage(text))
        replies=reply_builder(response.text) if reply_builder else None
        generation.commit(replies)
        return response if reply_builder is None else replies
    except BaseException:
        generation.reject()
        raise
