"""Mobile Chrome emulation + synthetic Telegram HMAC exchange. NOT a real WebView."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp, BlagoWebHandler


def init_data(valid=True):
    fields={'user':json.dumps({'id':7000000001,'first_name':'Synthetic Mini App'}),'auth_date':str(int(time.time()))}
    secret=hmac.new(b'WebAppData',b'123:synthetic-help011-only',hashlib.sha256).digest()
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest() if valid else '0'*64
    return urlencode(fields)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--chrome',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');args=p.parse_args()
    from playwright.sync_api import sync_playwright, expect
    args.output.mkdir(parents=True,exist_ok=True);checks={};errors=[]
    with tempfile.TemporaryDirectory(prefix='blago-help-mobile-') as root:
        cfg=create(Path(root)/'new',help_enabled=True)
        cfg.update(TELEGRAM_BOT_TOKEN='123:synthetic-help011-only',XBLAGO_WEB_INVITE_CODE='synthetic-mobile',XBLAGO_COOKIE_SECURE='false')
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));help_service=HelpService(app)
            helper=app.register('synthetic_mobile_helper','synthetic-password-only','synthetic-mobile')[0]
            code=help_service.code(helper)['code']
            server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=app
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://localhost:{server.server_port}'
            try:
                with patch.dict(os.environ,{'XBLAGO_PUBLIC_ORIGIN':url,'XBLAGO_WEBAUTHN_RP_ID':'localhost'}),sync_playwright() as pw:
                    chrome_env={k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH')}
                    browser=pw.chromium.launch(executable_path=args.chrome,headless=True,env=chrome_env)
                    contexts=[]
                    def new_page(raw):
                        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True);contexts.append(context)
                        def route(r):
                            if urlsplit(r.request.url).hostname in ('localhost','127.0.0.1'):r.continue_()
                            elif r.request.url=='https://telegram.org/js/telegram-web-app.js':
                                r.fulfill(status=200,content_type='application/javascript',body='window.Telegram={WebApp:{initData:'+json.dumps(raw)+',ready(){},expand(){}}};')
                            else:r.abort()
                        context.route('**/*',route);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));return context,page
                    context,page=new_page(init_data())
                    auth_requests=[]
                    page.on('request',lambda r:auth_requests.append(r.post_data_json) if r.url==url+'/api/v1/auth/telegram' and r.method=='POST' else None)
                    page.goto(url+'/app');expect(page.locator('#shell')).to_be_visible();checks['synthetic_telegram_hmac_exchange']=True
                    me=page.evaluate("fetch('/api/v1/me').then(r=>r.json())");uid=me['profile']['blago_user_id'];csrf=me['csrf']
                    page.reload();expect(page.locator('#shell')).to_be_visible()
                    again=page.evaluate("fetch('/api/v1/me').then(r=>r.json())")
                    checks['reopen_preserves_identity']=again['profile']['blago_user_id']==uid
                    payload=auth_requests[0]
                    status=page.evaluate("async payload => (await fetch('/api/v1/auth/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).status",payload)
                    checks['consumed_exchange_replay_denied']=status>=400
                    page.locator('#chat-input').fill('Мне нужна помощь с синтетическим велосипедом');page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                    expect(page.get_by_role('button',name='Подготовить черновик',exact=True)).to_be_visible()
                    checks['conversation_does_not_create_request']=help_service.feed(helper)['items']==[]
                    page.get_by_role('button',name='Подготовить черновик',exact=True).click();page.locator('#help-audience').fill(code);page.locator('#help-draft').click()
                    expect(page.locator('#help-items')).to_contain_text('DRAFT');checks['mobile_private_draft']=help_service.feed(helper)['items']==[]
                    page.get_by_role('button',name='Проверить публикацию',exact=True).click();expect(page.locator('#help-approval')).to_be_visible()
                    checks['preview_is_not_publication']=help_service.feed(helper)['items']==[]
                    page.locator('#help-confirm').click();expect(page.locator('#help-items')).to_contain_text('PUBLISHED')
                    checks['explicit_publication_to_audience']=len(help_service.feed(helper)['items'])==1
                    checks['mobile_no_horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(args.output/'synthetic-mini-app-mobile.png'),full_page=True)
                    context.set_offline(True)
                    checks['offline_no_private_api_cache']=page.evaluate("fetch('/api/v1/help/requests').then(()=>false).catch(()=>true)")
                    context.set_offline(False)
                    _,bad=new_page(init_data(False));bad.goto(url+'/app');expect(bad.locator('#auth')).to_be_visible();checks['invalid_initdata_no_session']=not bad.locator('#shell').is_visible()
                    checks['no_page_errors']=not errors
                    result={'format':'blago-help-mobile-1','scope':'Chrome touch/mobile emulation with synthetic SDK and HMAC; not Telegram','browser':browser.version,'checks':checks,'page_errors':errors,
                            'physical_ios_telegram':'NOT EXECUTED','physical_android_telegram':'NOT EXECUTED','physical_passkeys':'NOT EXECUTED','production_access':False,'real_pilot':'NO-GO'}
                    (args.output/'mobile-results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));browser.close()
                    if not all(checks.values()):raise SystemExit(1)
            finally:server.shutdown();server.server_close();thread.join(5)

if __name__=='__main__':main()
