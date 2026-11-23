"""Three independent browser contexts; business actions ONLY through visible controls.

Outside the browser: initial synthetic seed, server start/restart, read-only
SQLite evidence and artifact writing. No API writes, cookie injection, hidden
audience expansion, route fulfillment or page.evaluate business operations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from tools.living_consent_lab import BANNER, PASSWORD, ROLES, MARKER


def digest(data): return hashlib.sha256(data).hexdigest()


def scrub(value):
    if isinstance(value, dict):
        return {k: ('REDACTED' if k in ('csrf', 'nonce', 'password') else scrub(v)) for k, v in value.items()}
    if isinstance(value, list): return [scrub(v) for v in value]
    return value


class Run:
    def __init__(self, args):
        self.args = args; self.out = args.output.resolve(); self.out.mkdir(parents=True, exist_ok=False)
        self.root = args.new_root.resolve(); self.events = []; self.steps = []; self.current = 'bootstrap'
        self.pending = {}; self.raw = {}; self.errors = []; self.process = None; self.generation = 0
        self.closing = False; self.seed_verified = False
        self.cleanup_aborts = []
        self.request_serial = 0; self.response_indexes = {}
        self.pages = {}; self.contexts = {}; self.commands = []
        self.source_sha = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        self.source_clean = not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
        self.source_patch = subprocess.check_output(['git','diff','--binary','6cce244f5b7cada33b00c08f7e6419acafc439a8'])
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); self.port = s.getsockname()[1]
        self.url = 'http://127.0.0.1:' + str(self.port)

    def command(self, tail): return [sys.executable, '-m', 'tools.living_consent_lab'] + tail

    def initialize(self):
        command = self.command(['init', '--new-root', str(self.root)])
        self.commands.append(command)
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with sqlite3.connect(self.root/'app/blago.sqlite3') as db:
            assert {r[0] for r in db.execute('SELECT kind FROM living_records')} == {'NOTE'}
            assert db.execute('SELECT count(*) FROM help_requests').fetchone()[0] == 3
        self.seed_verified = True

    def start(self):
        self.generation += 1
        command = self.command(['serve', '--root', str(self.root), '--enable-local-sandbox', '--port', str(self.port)])
        self.commands.append(command)
        self.log = (self.out/('server-'+str(self.generation)+'.log')).open('wb')
        self.process = subprocess.Popen(command, stdout=self.log, stderr=self.log)
        deadline = time.monotonic()+25
        while True:
            try:
                with urlopen(self.url, timeout=1) as response:
                    if response.status == 200: break
            except OSError:
                if self.process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('Local server startup failed')
                time.sleep(.1)

    def stop(self):
        if self.process:
            self.process.terminate(); self.process.wait(15); self.log.close(); self.process = None

    def browser(self, pw):
        env = {k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH')}
        self.chrome = pw.chromium.launch(executable_path=self.args.chrome, headless=True, env=env)
        self.browser_version = self.chrome.version
        self.pages = {}; self.contexts = {}
        for role in ROLES:
            context = self.chrome.new_context(viewport={'width':1100,'height':1000}, service_workers='block')
            self.contexts[role] = context
            def route(request, role=role):
                if request.request.url.startswith(self.url+'/'): request.continue_()
                else: self.errors.append('External URL blocked'); request.abort()
            context.route('**/*', route)
            page = context.new_page(); self.pages[role] = page
            page.on('pageerror', lambda e: self.errors.append(str(e)))
            page.on('request', lambda req, role=role: self.request_started(req, role))
            page.on('response', lambda res, role=role: self.response(res, role))

    def close_browser(self):
        self.closing = True
        try:
            # Ordinary navigation ends passive polling before closing contexts.
            for page in self.pages.values():
                if not page.is_closed(): page.goto('about:blank')
            self.chrome.close()
        finally: self.closing = False

    def request_started(self, request, role):
        self.request_serial += 1
        self.pending[request] = (self.current, role, self.request_serial)

    def response(self, response, role):
        try:
            request = response.request
            step, _, serial = self.pending.get(request, (self.current, role, -1))
            # HTML/JS/CSS are fixed reviewed assets, not data projections. Only
            # JSON APIs carry user state; reading obsolete navigation resources
            # after a new document loads is not a reliable Network capture.
            if not urlsplit(response.url).path.startswith('/api/'): return
            raw = response.body(); text = raw.decode('utf-8')
            if role != 'B' and MARKER in text: raise AssertionError('PRIVATE MARKER LEAK')
            value = json.loads(text)
            post = request.post_data_json if request.method == 'POST' else None
            nonce_hash = digest(post['data']['nonce'].encode()) if isinstance(post,dict) and post.get('data',{}).get('nonce') else None
            index = len(self.events)
            self.raw[index] = value
            self.response_indexes[request] = index
            self.events.append({'index':index,'step':step,'role':role,'method':request.method,
                'request_serial':serial,
                'path':urlsplit(response.url).path,'status':response.status,'request':scrub(post),
                'nonce_sha256':nonce_hash,'json':scrub(value),'body_sha256':digest(raw),
                'cache_control':response.headers.get('cache-control')})
        except Exception as exc:
            if self.closing and response.request.method == 'GET':
                self.cleanup_aborts.append({'role':role,'path':urlsplit(response.url).path,'stage':'browser context destroyed'})
                return
            self.errors.append(type(exc).__name__+': '+str(exc))

    def click(self, role, selector, expected_action):
        page = self.pages[role]
        with page.expect_response(lambda r: urlsplit(r.url).path == '/api/action' and
                r.request.method == 'POST' and r.request.post_data_json.get('action') == expected_action) as response:
            page.locator(selector).click()
        return response.value.json(), response.value.status

    def fresh(self, scenario):
        from playwright.sync_api import expect
        self.current = scenario+'-login'
        for role, page in self.pages.items():
            page.goto(self.url)
            page.locator('#username').fill(ROLES[role]); page.locator('#password').fill(PASSWORD)
            page.locator('#login').click()
            expect(page.locator('#enter')).to_be_visible()
            page.locator('#enter').click()
            expect(page.locator('h1')).to_have_text({'A':'Автор A','B':'Посредник B','C':'Получатель C'}[role])
            page.locator('#scenario').select_option(scenario)
        return self.capture('Три отдельных входа', 'A/B/C', 'Войти собственным синтетическим профилем')

    def capture(self, label, role, confirmed):
        from playwright.sync_api import expect
        snaps = {}
        for r, p in self.pages.items():
            selected = p.locator('#scenario').input_value()
            threshold = self.request_serial
            def fresh_response(response, path):
                return (response.url == self.url+path+'?scenario='+selected and
                        self.pending.get(response.request, ('', '', -1))[2] > threshold)
            if r != 'B':
                with p.expect_response(lambda response: fresh_response(response,'/api/view')) as view_response:
                    with p.expect_response(lambda response: fresh_response(response,'/api/state')) as response:
                        p.locator('#refresh').click()
                view_response.value.body()
            else:
                with p.expect_response(lambda response: fresh_response(response,'/api/state')) as response:
                    p.locator('#refresh').click()
            observed = response.value.json()['data']
            if r in ('A','B'):
                expect(p.locator('#request')).to_have_text(observed.get('approved_text') or observed['request']['text'])
                if r == 'A':
                    if observed['can_revoke']: expect(p.locator('#revoke')).to_be_enabled()
                    else: expect(p.locator('#revoke')).to_be_disabled()
            elif observed['inbox']:
                expect(p.locator('#inbox button')).to_have_count(len(observed['inbox']))
                expect(p.locator('#inbox button').first).to_be_visible()
            else: expect(p.locator('#inbox')).to_have_text('Входящих приглашений нет')
            if r != 'B':
                # Wait for the normal page refresh's view request, never fetch
                # the projection using an injected API call.
                expect(p.locator('header')).to_have_text(BANNER)
                p.wait_for_timeout(100)
            body = p.locator('body').inner_text()
            if r != 'B': assert MARKER not in body
            name = self.current+'-'+r
            p.screenshot(path=str(self.out/(name+'.png')), full_page=True)
            (self.out/(name+'.txt')).write_text(body)
            buttons = [{'text':b.inner_text(),'disabled':b.is_disabled()} for b in p.locator('button').all()]
            refs = {'/api/state':self.response_indexes[response.value.request]}
            if r != 'B': refs['/api/view'] = self.response_indexes[view_response.value.request]
            assert all(self.events[i]['request_serial'] > threshold for i in refs.values())
            snaps[r] = {'ui':name+'.txt','screenshot':name+'.png','body':body,'buttons':buttons,'responses':refs}
        row = {'step':self.current,'actor':role,'label':label,'confirmed':confirmed,'roles':snaps}
        self.steps.append(row)
        return row

    def step(self, scenario, name, role, selector, action, label):
        from playwright.sync_api import expect
        self.current = scenario+'-'+name
        value, status = self.click(role, selector, action)
        if status != 200: expect(self.pages[role].locator('#message')).to_have_text(value['error'])
        elif action == 'open': expect(self.pages['C'].locator('#task')).to_have_text(value['task'])
        elif action == 'contact': expect(self.pages['C'].locator('#offer-area')).to_be_visible()
        elif value.get('message'): expect(self.pages[role].locator('#message')).to_have_text(value['message'])
        self.capture(label, role, action)
        return value, status

    def invite(self, scenario, invalidation=False):
        from playwright.sync_api import expect
        self.step(scenario,'allow','A','#allow','allow','Разрешить конкретный текст и честный состав disclosure')
        self.step(scenario,'match','B','#match','match','Отдельно разрешить MATCH_PRIVATE')
        self.step(scenario,'search','B','#search','search','Человек сам запускает поиск')
        assert MARKER in self.pages['B'].locator('#matches').inner_text()
        self.step(scenario,'preview','B','#preview','preview','Проверить адресата, текст, referrer и запрет передачи заметки')
        if invalidation:
            self.current = scenario+'-recipient-change'
            self.pages['B'].locator('#recipient').select_option('A')
            expect(self.pages['B'].locator('#send')).to_be_disabled()
            self.pages['B'].locator('#recipient').select_option('C')
            self.capture('Смена адресата сбрасывает подтверждение','B','Выбран другой адресат; затем снова C')
            self.step(scenario,'preview-again','B','#preview','preview','Новое явное превью после смены адресата')
            self.current = scenario+'-text-change'
            self.pages['A'].locator('summary').click()
            self.pages['A'].locator('#edit-text').fill('SYNTHETIC_TASK_POSITIVE_UPDATED: нужна добровольная помощь с велосипедом')
            self.click('A','#edit','edit')
            self.capture('Автор изменяет текст, сохраняя аудиторию','A','Изменить и опубликовать с прежней аудиторией')
            expect(self.pages['B'].locator('#send')).to_be_disabled()
            self.step(scenario,'allow-updated','A','#allow','allow','Новое разрешение на исправленный текст')
            self.step(scenario,'match-updated','B','#match','match','Отдельное разрешение чтения для новой версии')
            self.step(scenario,'search-updated','B','#search','search','Повторный явный поиск')
            self.step(scenario,'preview-updated','B','#preview','preview','Подтвердить актуальный текст и адресата')
        value, status = self.step(scenario,'send','B','#send','send','Направить превью приглашения: BRIDGE + INVITE_ONCE + локальная очередь')
        assert status == 200 and value['queued']

    def open(self, scenario, suffix='open'):
        return self.step(scenario,suffix,'C','#inbox button','open','Открыть нейтральную карточку через LocalPullAdapter')

    def storage(self):
        with sqlite3.connect((self.root/'app/blago.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
            return {'holds':sorted(r[0] for r in db.execute('SELECT target_tag FROM living_contact_holds')),
                'identities':{r[0]:digest(r[1].encode()) for r in db.execute('SELECT username,blago_user_id FROM web_credentials')},
                'audiences':[(r[0],digest(r[1].encode())) for r in db.execute('SELECT request_id,member_id FROM help_audience ORDER BY request_id,member_id')],
                'offers':db.execute('SELECT count(*) FROM help_offers').fetchone()[0]}

    def execute(self, pw):
        self.initialize(); self.initial_storage = self.storage(); self.start(); self.browser(pw)
        self.fresh('positive'); self.invite('positive', invalidation=True)
        value, status = self.open('positive'); assert status==200 and value['task'].startswith('SYNTHETIC_TASK_POSITIVE_UPDATED') and value['receipt']
        self.step('positive','contact','C','#contact','contact','Согласие только на контакт')
        assert 'псевдонимный код' in self.pages['C'].locator('#offer-area').inner_text()
        value, status = self.step('positive','offer','C','#offer','offer','Добровольно предложить помощь с указанным составом раскрытия')
        assert status==200 and value['blocker']=='HELP_AUDIENCE_REQUIRED' and not value['offered']
        self.close_browser(); self.browser(pw); self.fresh('revoke'); self.invite('revoke')
        self.current = 'revoke-neutral-before-withdraw'
        self.capture('C явно обновляет входящий список и видит нейтральную карточку до отзыва','C','Обновить')
        self.step('revoke','withdraw','A','#revoke','revoke','Автор отзывает собственное разрешение до открытия')
        value, status = self.open('revoke'); assert status != 200 and value['error']=='Приглашение недоступно'
        self.close_browser(); self.browser(pw); self.fresh('decline'); self.invite('decline')
        self.current = 'decline-unopened'; before = self.capture('До открытия: автор не знает получателя и статус','A','Обновить')
        self.open('decline')
        self.current = 'decline-received'; received = self.capture('После открытия: авторский ответ тот же','A','Обновить')
        self.step('decline','declined','C','#decline','decline','Отказаться без публичного негативного статуса')
        self.current = 'decline-after-refusal'; declined = self.capture('После отказа: авторский ответ тот же','A','Обновить')
        self.author_comparison = []
        for path in ('/api/state','/api/view'):
            indexes = [s['roles']['A']['responses'][path] for s in (before,received,declined)]
            hashes = [self.events[i]['body_sha256'] for i in indexes]
            assert len(set(hashes))==1
            self.author_comparison.append({'path':path,'response_indexes':indexes,'body_sha256':hashes,'identical':True})
        assert before['roles']['A']['body']==received['roles']['A']['body']==declined['roles']['A']['body']
        assert before['roles']['A']['buttons']==received['roles']['A']['buttons']==declined['roles']['A']['buttons']
        self.before_restart = self.storage(); assert self.before_restart['holds']
        self.current = 'decline-restart-clear'; self.stop(); self.start()
        self.close_browser()  # Destroy all contexts: cookies, session/local storage and cache.
        self.browser(pw); self.fresh('decline')
        self.after_restart = self.storage(); assert self.before_restart==self.after_restart
        self.step('decline','new-match','B','#match','match','Новый вход и новое явное разрешение поиска')
        self.step('decline','new-search','B','#search','search','Новый поиск после очистки браузеров')
        self.step('decline','new-preview','B','#preview','preview','Новая попытка по тому же сочетанию посредник/получатель/тема')
        value, status = self.step('decline','new-send','B','#send','send','Новая отправка, не replay ссылки или запроса')
        assert status==409 and 'suppressed' in value['error'].lower()
        self.final_storage = self.storage()
        assert self.final_storage['audiences']==self.initial_storage['audiences'] and self.final_storage['offers']==0
        successful_opens = []
        def has_task(value):
            if isinstance(value, dict): return 'task' in value or any(has_task(v) for v in value.values())
            if isinstance(value, list): return any(has_task(v) for v in value)
            return False
        for event in self.events:
            if event['role']!='C': continue
            request = event.get('request') or {}; value = event['json']
            contains = 'SYNTHETIC_TASK_' in json.dumps(value)
            if contains or has_task(value):
                assert request.get('action')=='open' and event['status']==200 and value.get('receipt')
                successful_opens.append(event['step'])
            if event['step'].startswith('revoke'): assert not contains and 'task' not in json.dumps(value)
        sends = [e for e in self.events if e['role']=='B' and (e.get('request') or {}).get('action')=='send' and
                 (e.get('request') or {}).get('scenario')=='decline']
        assert len(sends)==2 and sends[0]['nonce_sha256']!=sends[1]['nonce_sha256']
        assert not self.errors, self.errors
        self.successful_opens = successful_opens

    def save(self, error=None):
        (self.out/'network.json').write_text(json.dumps(self.events,ensure_ascii=False,indent=2)+'\n')
        (self.out/'steps.json').write_text(json.dumps(self.steps,ensure_ascii=False,indent=2)+'\n')
        (self.out/'source.patch').write_bytes(self.source_patch)
        result = {'format':'living-consent-021-e2e-1','status':'FAIL' if error else 'PASS_WITH_CONTRACT_BLOCKER',
            'base_sha':'6cce244f5b7cada33b00c08f7e6419acafc439a8','source_sha':self.source_sha,
            'source_clean_at_start':self.source_clean,'base_diff_sha256':digest(self.source_patch),
            'source_sha_at_end':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'source_clean_at_end':not subprocess.check_output(['git','status','--porcelain'],text=True).strip(),
            'browser':getattr(self,'browser_version',None),'browser_contexts':3,'synthetic_roles':ROLES,
            'aborted_passive_GET_at_context_cleanup':self.cleanup_aborts,
            'synthetic_password':PASSWORD,'private_B_marker':MARKER,'server_commands':self.commands,
            'all_business_actions':'visible browser controls only; no API writes or injected cookies',
            'seed_contains_no_permissions_or_mandates':self.seed_verified,
            'author_comparison':getattr(self,'author_comparison',None),'successful_task_release_steps':getattr(self,'successful_opens',None),
            'storage_before_restart':getattr(self,'before_restart',None),'storage_after_restart':getattr(self,'after_restart',None),
            'initial_storage':getattr(self,'initial_storage',None),'final_storage':getattr(self,'final_storage',None),
            'help':'BLOCKED: HelpService._row requires existing help_audience membership',
            'external_delivery':False,'real_people':'NO-GO','production':'NO-GO','error':error}
        (self.out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--new-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chrome',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    args = p.parse_args(); run = Run(args)
    from playwright.sync_api import sync_playwright
    error = None
    try:
        with sync_playwright() as pw:
            try: run.execute(pw)
            finally:
                if hasattr(run,'chrome'): run.close_browser()
    except Exception as exc: error = type(exc).__name__+': '+str(exc)
    finally: run.stop(); run.save(error)
    print(json.dumps({'status':'FAIL' if error else 'PASS_WITH_CONTRACT_BLOCKER','output':str(run.out),'error':error},ensure_ascii=False))
    if error: raise SystemExit(1)


if __name__ == '__main__': main()
