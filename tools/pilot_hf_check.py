"""Opt-in live HF inference on synthetic data and one temporary Foundation DB.

Never calls a production Space chat/Telegram API and never serializes credentials.
"""
import argparse
import hashlib
import hmac
import json
import os
import statistics
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from pilot_fixture import fixture
from xblago.core.config import LlmConfig
from xblago.core.llm import OpenAICompatibleProvider,LlmProviderError
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from xblago.core.sqlite_safety import runtime_evidence
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.web import WebApp
from xblago.integrations.privacy import PrivacyService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=20)
    parser.add_argument('--model',default='Qwen/Qwen3-14B:cheapest')
    args=parser.parse_args()
    from huggingface_hub import get_token
    token=get_token()
    if not token:
        raise SystemExit('HF credential unavailable; no secrets were printed or changed')
    config=LlmConfig('hf',args.model,token,'https://router.huggingface.co/v1',args.timeout,384,'off')
    provider=OpenAICompatibleProvider(config)
    original=provider.generate
    state={'scenario':'setup','inject':False}
    calls,turns,checks=[],[],[]
    def generate(context):
        start=time.perf_counter()
        stage='extraction' if context.user_text.startswith('Extract only durable personal memory') else 'answer'
        entry={'scenario':state['scenario'],'stage':stage,'http_attempted':not state['inject'],'success':False}
        try:
            if state['inject']:
                state['inject']=False
                entry['injected']=True
                raise LlmProviderError('Synthetic provider outage')
            result=original(context)
            entry['success']=True
            return result
        except Exception as exc:
            entry.update(error_type=type(exc).__name__,status=getattr(exc,'status',None))
            raise
        finally:
            entry['latency_ms']=round((time.perf_counter()-start)*1000,2)
            calls.append(entry)
    provider.generate=generate
    def check(name,passed):
        checks.append({'name':name,'passed':bool(passed)})
    with fixture() as f,patch('xblago.integrations.web.build_chat_provider',return_value=provider),patch('xblago.integrations.telegram.build_chat_provider',return_value=provider):
        app,store=f['app'],f['store']
        web_token,_=app.register('synthetic_person','synthetic-long-password','synthetic-pilot-invite')
        uid=app.session(web_token)[0]
        identity=TelegramIdentity(1000000001,1000000001,'private')
        source=store.resolve_telegram_user(identity)
        app.redeem_telegram_link(source,app.new_link_code(uid,web_token))
        web_token,_=app.login('synthetic_person','synthetic-long-password')
        fields={'user':json.dumps({'id':1000000001}),'auth_date':str(int(time.time()))}
        secret=hmac.new(b'WebAppData',b'123:synthetic-pilot-bot',hashlib.sha256).digest()
        fields['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
        binding='synthetic-mini-app-browser-binding'
        challenge=app.identity.telegram_challenge(binding)
        mini_token,_=app.telegram_login(urlencode(fields),challenge,binding)
        check('Web_Telegram_MiniApp_one_ID',app.session(mini_token)[0]==uid==store.resolve_telegram_user(identity))
        bot=TelegramBot(None,f['root'],store=store)
        update_id=0
        def say(scenario,text,channel='web'):
            nonlocal update_id
            state['scenario']=scenario
            started=time.perf_counter()
            if channel=='telegram':
                update_id+=1
                replies=bot.replies_for_update({'update_id':update_id,'message':{'from':{'id':identity.telegram_user_id},'chat':{'id':identity.chat_id,'type':'private'},'text':text}})
                response='\n'.join(str(row['text']) for row in replies)
            else:
                response=app.chat(uid,text)
            entry={'scenario':scenario,'channel':channel,'input':text,'response':response,'latency_ms':round((time.perf_counter()-started)*1000,2)}
            turns.append(entry)
            print(json.dumps({'scenario':scenario,'channel':channel,'latency_ms':entry['latency_ms'],'provider_calls_so_far':len(calls)},ensure_ascii=False),flush=True)
        say('01_знакомство','Привет!','telegram')
        say('01_знакомство','Меня зовут Тестовая Ольга.')
        say('02_уточнение_потребности','Хочу починить велосипед. С чего начать?')
        say('02_уточнение_потребности','Поскрипывают тормоза, особенно после дождя.','telegram')
        say('03_предпочтения','Я предпочитаю короткие ответы.')
        say('03_предпочтения','Теперь мне удобнее подробные объяснения с примерами.')
        check('preference_changed',any('подробные' in str(v) for v in store.all_memory(uid).values()))
        say('04_исправление','Я живу в Москве.')
        say('04_исправление','Я больше не живу в Москве, теперь живу в Казани.','telegram')
        check('city_corrected',any('Казан' in str(v) for k,v in store.all_memory(uid).items() if k=='city'))
        before=store.all_memory(uid)
        say('05_не_запоминать','Не запоминай это, но расскажи, как починить велосипед.')
        check('optout_memory_unchanged',before==store.all_memory(uid))
        check('optout_no_extractor',not any(c['scenario']=='05_не_запоминать' and c['stage']=='extraction' for c in calls))
        say('06_забыть','Забудь мой город.','telegram')
        check('city_forgotten','city' not in store.all_memory(uid))
        say('07_чужая_потребность','Моему знакомому нужен велосипед. Это не моя потребность.')
        with store._connect() as db:
            check('no_implicit_need_created',db.execute('SELECT count(*) FROM blago_needs').fetchone()[0]==0)
        store=SQLiteBlagoStore(f['path']);app=WebApp(store);bot=TelegramBot(None,f['root'],store=store)
        check('sessions_survive_restart',app.session(web_token)[0]==app.session(mini_token)[0]==uid)
        say('08_повторное_обращение','Помнишь, какие ответы мне удобны?')
        before=store.all_memory(uid)
        state['inject']=True
        say('09_сбой_провайдера','Помоги продумать следующий шаг ремонта.')
        check('provider_failure_memory_unchanged',store.all_memory(uid)==before)
        say('10_восстановление','Хорошо, продолжим: как проверить тормоза велосипеда?','telegram')
        check('recovery_reached_real_provider',any(c['scenario']=='10_восстановление' and c['http_attempted'] for c in calls))
        with store._connect() as db:
            check('integrity',tuple(db.execute('PRAGMA integrity_check').fetchone())==('ok',))
            check('foreign_keys',not db.execute('PRAGMA foreign_key_check').fetchall())
        check('export_same_principal',PrivacyService(app).export(web_token)['blago_user_id']==uid)
    real=[c for c in calls if c['http_attempted']]
    result={'synthetic_only':True,'model':args.model,'sqlite':runtime_evidence(),'checks':checks,'turns':turns,'calls':calls,
            'summary':{'turns':len(turns),'provider_invocations':len(calls),'http_attempts':len(real),
                'real_successes':sum(c['success'] for c in real),'real_failures':sum(not c['success'] for c in real),
                'extractor_calls':sum(c['stage']=='extraction' for c in calls),
                'median_turn_ms':statistics.median(t['latency_ms'] for t in turns),
                'failed_checks':[c['name'] for c in checks if not c['passed']]},
            'scope':'Local temporary SQLite; direct HF inference only; production Space chat/DB untouched'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['summary'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
