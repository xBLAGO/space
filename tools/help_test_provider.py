"""Opt-in synthetic LLM check using ONLY an explicitly approved test credential.
Never reads HF_TOKEN, cached Hub tokens, .env files or production configuration.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch
import urllib.request
from tools.integration_fixture import create
from xblago.core.config import LlmConfig
from xblago.core.integration import preflight
from xblago.core.llm import OpenAICompatibleProvider
from xblago.integrations.web import WebApp

TURNS=('Привет. Я хочу просто поговорить.','Мне нужна помощь с ремонтом велосипеда.',
       'Пока ничего не публикуй, давай разберёмся сами.','Я предпочитаю короткие ответы.',
       'Нет, теперь объясняй подробнее.','Не запоминай это, но как настроить велосипед?',
       'У соседа сломалась цепь. Я просто рассказываю.','Я могу помочь ему, но пока не обещаю.',
       'Я передумал. Не нужно оформлять помощь.','Продолжим разговор: что проверить в тормозах?')


def run():
    token=os.environ.get('BLAGO_TEST_HF_TOKEN');model=os.environ.get('BLAGO_TEST_HF_MODEL')
    approved=os.environ.get('BLAGO_TEST_HF_APPROVED')=='synthetic-only'
    if not (token and model and approved):
        return {'format':'blago-help-test-provider-1','status':'NOT EXECUTED','reason':'Explicit test token, model and synthetic-only approval not supplied',
                'production_credentials_read':False,'requests':0,'real_pilot':'NO-GO'}
    # Test-only dependency injection; Integration launcher still rejects live provider.
    config=LlmConfig('hf',model,token,'https://router.huggingface.co/v1',20,768,'off')
    provider=OpenAICompatibleProvider(config);real_open=urllib.request.urlopen;calls=[];turns=[]
    def measured(request,**kwargs):
        if len(calls)>=24:raise RuntimeError('Synthetic call budget reached')
        start=time.perf_counter();row={};calls.append(row)
        try:
            response=real_open(request,**kwargs);row['status']=getattr(response,'status',200);return response
        except Exception as exc:
            row['status']=getattr(exc,'code',None);row['error_type']=type(exc).__name__;raise
        finally:row['headers_latency_ms']=round((time.perf_counter()-start)*1000,2)
    with tempfile.TemporaryDirectory(prefix='blago-help-test-provider-') as root:
        cfg=create(Path(root)/'new',help_enabled=True);cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-provider'
        with patch.dict(os.environ,cfg),patch('xblago.integrations.web.build_chat_provider',return_value=provider),patch('urllib.request.urlopen',side_effect=measured):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
            token=app.register('synthetic_provider','synthetic-password-only','synthetic-provider')[0];uid=app.session(token)[0]
            for text in TURNS:
                start=time.perf_counter();before=len(calls);reply=app.chat(uid,text)
                turns.append({'calls':len(calls)-before,'latency_ms':round((time.perf_counter()-start)*1000,2),'answer':reply})
            with app.store._connect() as db:
                no_actions=all(db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0 for table in ('help_requests','blago_payment_requests','blago_contribution_events'))
                integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    return {'format':'blago-help-test-provider-1','status':'EXECUTED; manual quality review required','synthetic_only':True,
            'model':model,'requests':len(calls),'calls':calls,'turns':turns,'no_automatic_actions':no_actions,'integrity_check':integrity,
            'median_turn_ms':statistics.median(t['latency_ms'] for t in turns),'production_credentials_read':False,'real_pilot':'NO-GO'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('calls','turns')},ensure_ascii=False))
