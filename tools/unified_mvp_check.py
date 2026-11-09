"""One real HTTP server / one synthetic SQLite across Web, Bot and Mini App.

No production endpoints, live Telegram, external LLM or financial operations.
Report capability gaps separately from invariant/test results.
"""
import argparse
import hashlib
import hmac
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import statistics
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from unittest.mock import patch

from tools.help_readiness_lab import initialize
from xblago.core.integration import preflight
from xblago.core.llm import LocalPlanningProvider, LlmProviderError
from xblago.core.privacy_maintenance import restore
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.web import WebApp, BlagoWebHandler


class Client:
    def __init__(self, url):
        self.url = url
        self.csrf = ''
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, body=None, *, csrf=True):
        headers = {'Content-Type':'application/json'}
        if csrf:
            headers['X-CSRF-Token'] = self.csrf
        req = urllib.request.Request(self.url+'/api/v1/'+path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            response = self.opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            value = json.load(response)
            if 'csrf' in value:
                self.csrf = value['csrf']
            return response.status, value

    def ok(self, method, path, body=None, expected=200):
        status, value = self.request(method, path, body)
        if status != expected:
            raise AssertionError('Synthetic HTTP step failed: '+method+' '+path.split('/')[0]+' '+str(status))
        return value


class Provider(LocalPlanningProvider):
    def __init__(self):
        self.calls = 0
        self.mode = None

    def generate(self, context):
        self.calls += 1
        if self.mode == '503':
            raise LlmProviderError('Synthetic unavailable', status=503, retryable=True)
        if self.mode == 'unconfirmed':
            return 'Готово, я опубликовал заявку и принял помощь.'
        return super().generate(context)


def committed_without_response(cfg, token, challenge):
    os.environ.update(cfg)
    from xblago.domain.help_service import HelpService
    HelpService(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))).commit(token,challenge,'ПОДТВЕРЖДАЮ')
    os._exit(86)


def init_data():
    fields = {'user':json.dumps({'id':7000000201,'first_name':'Synthetic'}),
              'auth_date':str(int(time.time())), 'query_id':'synthetic-'+secrets.token_hex(8)}
    secret = hmac.new(b'WebAppData', b'123:synthetic-help011-only', hashlib.sha256).digest()
    check = '\n'.join(f'{key}={value}' for key,value in sorted(fields.items()))
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def bot_update(number, text):
    return {'update_id':number,'message':{'message_id':number,'from':{'id':7000000201},
            'chat':{'id':7000000201,'type':'private'},'text':text}}


def run():
    checks = {}
    latencies = []
    transcripts = []
    provider = Provider()
    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError('Unified invariant failed: '+name)
    with tempfile.TemporaryDirectory(prefix='blago-unified-mvp-') as directory:
        root = Path(directory)/'new'
        cfg = initialize(root)
        with patch.dict(os.environ,cfg), patch('xblago.integrations.web.build_chat_provider',return_value=provider), patch('xblago.integrations.telegram.build_chat_provider',return_value=provider):
            path = root/'app/blago.sqlite3'
            app = WebApp(preflight(path))
            server = ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
            server.blago_web_app = app
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                clients = [Client('http://127.0.0.1:'+str(server.server_port)) for _ in range(5)]
                a,b,c,_,_ = clients
                auth = lambda name,invite: {'username':name,'password':'synthetic-password-only','invite':invite}
                check('invalid_invitation_denied',a.request('POST','auth/register',auth('synthetic_unified_a','wrong'))[0]==403)
                for client,letter in zip(clients,'abcde'):
                    client.ok('POST','auth/register',auth('synthetic_unified_'+letter,'synthetic-help011-only'))
                uid = a.ok('GET','me')['profile']['blago_user_id']
                check('invited_password_identity',bool(uid))
                check('csrf_enforced',a.request('POST','help/drafts',{'text':'Синтетическая помощь','audience':[],'key':'bad'},csrf=False)[0]==403)
                def say(client,text):
                    start=time.perf_counter();before=provider.calls
                    result=client.ok('POST','chat/messages',{'text':text})
                    latencies.append(round((time.perf_counter()-start)*1000,3))
                    transcripts.append({'user':text,'reply':result['reply'],'provider_calls':provider.calls-before})
                    return result
                say(a,'Я живу в Москве')
                say(a,'Я предпочитаю чай')
                say(a,'Я умею писать на Python')
                say(a,'Я интересуюсь темой книги')
                offline_facts=a.ok('GET','memory')['facts']
                code=a.ok('POST','auth/link-code',{})['code']
                bot=TelegramBot(None,root/'unused',app.store)
                bot.replies_for_update(bot_update(91000,'/link '+code))
                check('link_rotates_old_web_session',a.request('GET','me')[0]==401)
                a.ok('POST','auth/login',auth('synthetic_unified_a',''))
                check('canonical_id_survives_link',a.ok('GET','me')['profile']['blago_user_id']==uid)
                mini=Client(a.url)
                exchange=mini.ok('POST','auth/telegram/challenge',{})
                payload={'challenge':exchange['challenge'],'init_data':init_data()}
                mini.ok('POST','auth/telegram',payload)
                check('web_mini_app_one_identity',mini.ok('GET','me')['profile']['blago_user_id']==uid)
                check('mini_exchange_replay_denied',mini.request('POST','auth/telegram',payload)[0]==409)
                say(mini,'Я больше не живу в Москве, теперь живу в Казани')
                message=bot_update(91001,'Раньше я любил чай, теперь предпочитаю кофе')
                first=bot.replies_for_update(message);before=provider.calls
                check('bot_duplicate_delivery_no_generation',bot.replies_for_update(message)==first and provider.calls==before)
                facts=a.ok('GET','memory')['facts']
                check('memory_shared_and_corrected',facts.get('city')=='Казани' and facts.get('preference:drink')=='кофе')
                check('other_user_memory_isolated',not b.ok('GET','memory')['facts'])
                history=a.ok('GET','chat/messages')['messages']
                check('history_shared_across_channels',history==mini.ok('GET','chat/messages')['messages'] and len(history)>=8)
                server.blago_web_app=WebApp(preflight(path));app=server.blago_web_app
                check('restart_preserves_history',a.ok('GET','chat/messages')['messages']==history)
                say(a,'Не запоминай это, но расскажи, как починить велосипед')
                check('do_not_remember_does_not_end_dialogue',a.ok('GET','memory')['facts']==facts)
                text='Мне нужна помощь с ремонтом синтетического велосипеда'
                result=say(a,text)
                check('natural_need_suggestion_without_publication',bool(result.get('help_suggestion')) and not b.ok('GET','help/requests')['items'])
                say(a,'Да, публикуй')
                check('plain_chat_is_not_action_authority',not a.ok('GET','help/requests')['items'])
                bc=b.ok('GET','help/code')['code']
                before_actions_history=a.ok('GET','chat/messages')['messages']
                rid=a.ok('POST','help/drafts',{'text':text,'audience':[bc],'key':'synthetic-unified-request'},expected=201)['id']
                check('private_draft_hidden',not b.ok('GET','help/requests')['items'])
                get=lambda client:client.ok('GET','help/requests/'+rid)
                def action(client,name,args=None):
                    preview=client.ok('POST','help/prepare',{'rid':rid,'version':get(client)['version'],'action':name,'args':args or {}})
                    return client.ok('POST','help/commit',{'challenge':preview['challenge'],'confirmation':'ПОДТВЕРЖДАЮ'})
                preview=a.ok('POST','help/prepare',{'rid':rid,'version':get(a)['version'],'action':'publish','args':{}})
                check('exact_text_version_audience_preview',preview['request']['text']==text and preview['request']['version']==1 and preview['request']['audience']==[bc])
                check('preview_not_publication',not b.ok('GET','help/requests')['items'])
                check('other_actor_cannot_confirm',b.request('POST','help/commit',{'challenge':preview['challenge'],'confirmation':'ПОДТВЕРЖДАЮ'})[0]>=400)
                commit={'challenge':preview['challenge'],'confirmation':'ПОДТВЕРЖДАЮ'}
                published=a.ok('POST','help/commit',commit)
                check('explicit_publication',published['state']=='PUBLISHED')
                event_count=len(get(a)['events']);again=a.ok('POST','help/commit',commit)
                check('duplicate_confirmation_no_event',again==published and len(get(a)['events'])==event_count)
                check('outsider_audience_denied',c.request('GET','help/requests/'+rid)[0]==404)
                action(b,'offer',{'note':'Добровольно помогу проверить цепь'})
                offered=get(a);action(a,'accept',{'offer_id':offered['offers'][0]['id']})
                check('explicit_offer_and_accept',get(a)['state']=='ACCEPTED')
                action(a,'confirm');check('single_confirmation_not_complete',get(a)['state']=='AWAITING_CONFIRMATION')
                pending=b.ok('POST','help/prepare',{'rid':rid,'version':get(b)['version'],'action':'confirm','args':{}})
                previous_events=len(get(a)['events'])
                secret=next(cookie.value for cookie in b.jar if cookie.name=='blago_session')
                child=multiprocessing.get_context('spawn').Process(target=committed_without_response,args=(cfg,secret,pending['challenge']))
                child.start();child.join(30)
                if child.is_alive():
                    child.kill();child.join();raise AssertionError('Synthetic commit worker timed out')
                check('crash_after_help_commit',child.exitcode==86)
                b.ok('POST','help/commit',{'challenge':pending['challenge'],'confirmation':'ПОДТВЕРЖДАЮ'})
                check('replay_after_crash_no_duplicate',len(get(a)['events'])==previous_events+1)
                check('mutual_completion',get(a)['state']=='COMPLETED')
                check('personal_completed_statistic',a.ok('GET','help/requests')['completed_owned']==1)
                check('help_event_history_separate_from_chat',a.ok('GET','chat/messages')['messages']==before_actions_history)
                community=a.ok('GET','analytics/community')['analytics']
                check('community_has_no_private_help_text',text not in json.dumps(community,ensure_ascii=False))
                action(b,'dispute');check('dispute_after_completion',get(a)['state']=='DISPUTED' and a.ok('GET','help/requests')['completed_owned']==0)
                provider.mode='unconfirmed'
                false_claim=say(a,'Опубликуй ещё одно обращение')
                check('llm_claim_not_committed',len(a.ok('GET','help/requests')['items'])==1 and 'я опубликовал' not in false_claim['reply'].lower())
                provider.mode='503';say(a,'Почему цепь может проскальзывать?')
                provider.mode=None;say(a,'Продолжим: что проверить в тормозах?')
                check('provider_failure_no_help_mutation',get(a)['state']=='DISPUTED')
                # Restore an older app copy after an explicit withdrawal. No serving
                # both copies: the recovered copy is checked offline only.
                old=app.store.backup_database(root/'offline-recovery')
                action(a,'withdraw');a.ok('DELETE','memory',{'key':'city'})
                recovered=restore(old,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
                with recovered._locked_transaction() as db:
                    row=db.execute('SELECT state,text FROM help_requests WHERE id=?',(rid,)).fetchone()
                    check('restore_does_not_republish',row['state']=='WITHDRAWN' and row['text']=='')
                    check('restore_invalidates_sessions',db.execute('SELECT count(*) FROM web_sessions').fetchone()[0]==0)
                    check('no_financial_events',all(db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0 for table in ('blago_payment_requests','blago_payment_request_offers','blago_fulfillment_events','blago_contribution_events')))
                    check('integrity',db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall())
                check('restore_does_not_restore_memory','city' not in recovered.all_memory(uid))
                return {'format':'blago-integration02-unified-journey-1','scope':'one synthetic SQLite, real localhost HTTP, simulated Bot delivery and signed synthetic Mini App exchange',
                        'checks':checks,'passed':sum(checks.values()),'total':len(checks),'turns':transcripts,
                        'local_provider_calls':provider.calls,'extra_external_llm_calls':0,'median_chat_http_ms':statistics.median(latencies),
                        'agent_quality':{'provider':'deterministic offline; not real HF quality','generic_acknowledgements':sum(t['reply']=='Я прочитал твоё сообщение.' for t in transcripts),
                            'skill_auto_saved_offline':'skill:python' in offline_facts,'interest_auto_saved_offline':any(k.startswith('interest:') for k in offline_facts),'human_perceived_naturalness':'NOT QUALIFIED','model_extraction_contract':'separately mocked in tests.test_unified_mvp; not live inference'},
                        'gaps':{'invitation':'shared deployment code, not individual invitation lifecycle','identity':'authenticated account, not unique physical human','conversational_publication':'PARTIAL; explicit Help panel/API required','verified_help_status_in_agent':'MISSING; no scoped Help read projection into agent context','collective_fulfillment_statistics':'MISSING; existing completed counter is personal','real_llm_quality':'NOT EXECUTED by this check','physical_telegram':'NOT EXECUTED'},
                        'production_access':False,'real_pilot':'NO-GO'}
            finally:
                server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='turns'},ensure_ascii=False))
