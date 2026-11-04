"""Opt-in before/after HF check: synthetic conversations, disposable pilot SQLite.

Select the audited implementation through PYTHONPATH. No production Space calls,
real Telegram updates, provider credentials, headers or private conversations in output.
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from huggingface_hub import get_token
from pilot_fixture import fixture
from xblago.core.config import LlmConfig
from xblago.core.llm import ChatContext, OpenAICompatibleProvider
from xblago.core.sqlite_safety import runtime_evidence
from xblago.core.storage import SQLiteBlagoStore
from xblago.agents.personal_assistant import PersonalAssistantAgent
from xblago.integrations.web import WebApp

TURNS = (
    ('знакомство','Привет, я хочу просто поговорить, без планов и заявок.'),
    ('фактический_вопрос','Почему после дождя иногда видны две радуги? Объясни в двух предложениях.'),
    ('благодарность','Спасибо.'),
    ('размышление','Иногда думаю, что быть полезным — не единственный смысл жизни.'),
    ('эмоция','Мне грустно. Не предлагай решений, я просто хочу выговориться.'),
    ('чужая_потребность','У соседа сломался велосипед. Я просто рассказываю об этом.'),
    ('предложение_ресурса','Могу помочь ему с ремонтом, но пока ничего не публикуй.'),
    ('смена_намерения','Я передумал, не хочу предлагать помощь. Давай просто разберём, почему перескакивает цепь.'),
    ('после_restart','Вернёмся к цепи: что проверить первым?'),
    ('отказ_от_памяти','Не запоминай это, но расскажи, как безопасно проверить тормоза.'),
    ('предпочтение','Я предпочитаю короткие ответы'),
    ('применение_предпочтения','Чем отличается гипотеза от теории?'),
)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--budget',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=20)
    args=parser.parse_args()
    token=get_token()
    if not token:
        raise SystemExit('HF credentials unavailable; no secret was printed or changed')
    config=LlmConfig('hf','Qwen/Qwen3-14B:cheapest',token,'https://router.huggingface.co/v1',args.timeout,args.budget,'off')
    provider=OpenAICompatibleProvider(config)
    real_open=urllib.request.urlopen
    calls=[];turns=[];state={'scenario':'setup'}

    def measured(request,timeout,**options):
        payload=json.loads(request.data)
        extraction=payload['messages'][-1]['content'].startswith('Extract only durable personal memory')
        entry={'scenario':state['scenario'],'purpose':'extraction' if extraction else 'conversation',
               'max_tokens':payload['max_tokens'],'success':False}
        start=time.perf_counter()
        calls.append(entry)
        try:
            original=real_open(request,timeout=timeout,**options)
        except Exception as exc:
            entry.update(status=getattr(exc,'code',None),error_type=type(exc).__name__,
                transport_reason_type=type(getattr(exc,'reason',None)).__name__,latency_ms=round((time.perf_counter()-start)*1000,2))
            raise

        class Tracked:
            def __enter__(self):
                original.__enter__()
                return self

            def read(self,*a,**kw):
                raw=original.read(*a,**kw)
                body=json.loads(raw)
                choice=(body.get('choices') or [{}])[0]
                usage=body.get('usage') or {}
                visible=(choice.get('message') or {}).get('content')
                entry.update(success=True,finish_reason=choice.get('finish_reason'),
                    content_type=type(visible).__name__,has_visible_text=isinstance(visible,str) and bool(visible.strip()),
                    echoed_service_status=visible in (
                        'Сейчас не получается связаться. Твоё сообщение осталось в диалоге.',
                        'Сейчас не получается связаться с языковой моделью, но я здесь. Попробуй ещё раз позже.'),
                    prompt_tokens=usage.get('prompt_tokens'),completion_tokens=usage.get('completion_tokens'),
                    total_tokens=usage.get('total_tokens'),latency_ms=round((time.perf_counter()-start)*1000,2))
                return raw

            def __exit__(self,*a):
                return original.__exit__(*a)
        return Tracked()

    with fixture() as f,patch('urllib.request.urlopen',side_effect=measured),patch('xblago.integrations.web.build_chat_provider',return_value=provider):
        app=f['app'];store=f['store']
        auth,_=app.register('synthetic_dialogue','synthetic-long-password','synthetic-pilot-invite')
        uid=app.session(auth)[0]
        for scenario,text in TURNS:
            state['scenario']=scenario
            if scenario=='после_restart':
                app=WebApp(SQLiteBlagoStore(f['path']))
            before=len(calls);start=time.perf_counter()
            answer=app.chat(uid,text)
            turns.append({'scenario':scenario,'user':text,'answer':answer,'http_calls':len(calls)-before,
                          'latency_ms':round((time.perf_counter()-start)*1000,2)})
        # An explicit short budget creates a real provider truncation test. It is
        # isolated from the user's normal conversation and writes no memory/history.
        state['scenario']='реальный_малый_бюджет'
        provider.config=replace(config,max_tokens=96)
        try:
            answer=provider.generate(ChatContext(PersonalAssistantAgent.profile,
                'Объясни устройство велосипедной трансмиссии: цепь, кассета, переключатель и уход. Нужен связный ответ, без вопросов.',{}))
            pressure={'returned':True,'answer':answer}
        except Exception as exc:
            pressure={'returned':False,'error_type':type(exc).__name__}
        with store._connect() as db:
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
            fk=[tuple(row) for row in db.execute('PRAGMA foreign_key_check')]
            economic={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
                ('blago_needs','blago_resources','blago_payment_requests','blago_contribution_events')}
        checks={'integrity':integrity=='ok','foreign_keys':not fk,'no_economic_actions':not any(economic.values()),
                'one_pair_per_turn':len(app.history(uid))==2*len(TURNS),
                'no_unexpected_memory':set(store.all_memory(uid))<= {'preference:response_style'}}
    summary={'turns':len(turns),'http_calls':len(calls),'http_failures':sum(not c['success'] for c in calls),
             'extraction_calls':sum(c['purpose']=='extraction' for c in calls),
             'length_finishes':sum(c.get('finish_reason')=='length' for c in calls),
             'prompt_tokens':sum(c.get('prompt_tokens') or 0 for c in calls),
             'completion_tokens':sum(c.get('completion_tokens') or 0 for c in calls),
             'median_turn_ms':statistics.median(t['latency_ms'] for t in turns),
             'max_turn_ms':max(t['latency_ms'] for t in turns)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'label':args.label,'synthetic_only':True,'model':config.model,'normal_budget':args.budget,
        'sqlite':runtime_evidence(),'turns':turns,'pressure':pressure,'calls':calls,'summary':summary,'checks':checks,
        'quality':'Requires manual reading; automatic invariants do not prove naturalness or pilot readiness.'},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'label':args.label,'summary':summary,'checks':checks},ensure_ascii=False))


if __name__=='__main__':
    main()
