"""Two isolated real Chrome contexts, synthetic Help participants, localhost only."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.integrations.web import WebApp,BlagoWebHandler

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--chrome',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');args=parser.parse_args()
    from playwright.sync_api import sync_playwright,expect
    with tempfile.TemporaryDirectory() as root:
        cfg=create(Path(root)/'new',help_enabled=True);cfg.update(XBLAGO_WEB_INVITE_CODE='synthetic-help-browser',XBLAGO_COOKIE_SECURE='false')
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
            for username in ('help_author','help_helper'):app.register(username,'synthetic-password-only','synthetic-help-browser')
            server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=app
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright() as p:
                    chrome_env={k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH')}
                    browser=p.chromium.launch(executable_path=args.chrome,headless=True,args=['--no-sandbox'],env=chrome_env)
                    contexts=[browser.new_context(),browser.new_context()]
                    from urllib.parse import urlsplit
                    for context in contexts:
                        def local_only(route):
                            if urlsplit(route.request.url).hostname in ('localhost','127.0.0.1'):route.continue_()
                            elif route.request.url=='https://telegram.org/js/telegram-web-app.js':route.fulfill(status=200,content_type='application/javascript',body='/* Web-only synthetic test: Telegram SDK intentionally unavailable */')
                            else:route.abort()
                        context.route('**/*',local_only)
                    pages=[c.new_page() for c in contexts];errors=[]
                    for page,user in zip(pages,('help_author','help_helper')):
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        page.goto('http://localhost:'+str(server.server_port)+'/app')
                        page.locator('#username').fill(user);page.locator('#password').fill('synthetic-password-only');page.locator('#auth-submit').click();expect(page.locator('#shell')).to_be_visible()
                        page.locator('#help-panel summary').first.click();page.locator('#help-refresh').click();expect(page.locator('#help-code')).not_to_be_empty()
                    a,b=pages;bc=b.locator('#help-code').inner_text()
                    # Natural conversation proposes only; no draft or publication yet.
                    a.locator('#chat-input').fill('Мне нужна помощь с ремонтом велосипеда <script>window.helpInjected=1</script>');a.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                    expect(a.get_by_role('button',name='Подготовить черновик',exact=True)).to_be_visible();a.get_by_role('button',name='Подготовить черновик',exact=True).click()
                    a.locator('#help-audience').fill(bc)
                    a.locator('#help-draft').click();expect(a.locator('#help-items')).to_contain_text('DRAFT')
                    b.locator('#help-refresh').click();expect(b.locator('#help-items')).to_be_empty()
                    def action(page,label):
                        page.get_by_role('button',name=label,exact=True).first.click()
                        expect(page.locator('#help-approval')).to_be_visible()
                        page.locator('#help-confirm').click();expect(page.locator('#help-approval')).to_be_hidden()
                    action(a,'Проверить публикацию');expect(a.locator('#help-items')).to_contain_text('PUBLISHED')
                    b.locator('#help-refresh').click();expect(b.locator('#help-items')).to_contain_text('велосипеда')
                    action(b,'Предложить помощь');a.locator('#help-refresh').click();action(a,'Принять это предложение')
                    action(a,'Подтвердить результат');expect(a.locator('#help-items')).to_contain_text('AWAITING_CONFIRMATION')
                    b.locator('#help-refresh').click();action(b,'Подтвердить результат');expect(b.locator('#help-items')).to_contain_text('COMPLETED')
                    a.locator('#help-refresh').click();expect(a.locator('#help-stats')).to_contain_text('1')
                    args.output.mkdir(parents=True,exist_ok=True);a.screenshot(path=str(args.output/'help-completed.png'),full_page=True)
                    action(a,'Отозвать обращение');b.locator('#help-refresh').click();expect(b.locator('#help-items')).not_to_contain_text('велосипеда');expect(b.locator('#help-items')).to_contain_text('WITHDRAWN')
                    assert not a.evaluate('window.helpInjected') and not b.evaluate('window.helpInjected')
                    assert not errors,errors
                    args.output.mkdir(parents=True,exist_ok=True)
                    result={'scope':'two synthetic participants, two isolated Chrome contexts','browser':browser.version,'checks':{'natural_suggestion_no_publish':True,'private_draft':True,'exact_preview_and_confirmation':True,'authorized_helper_feed':True,'voluntary_offer':True,'owner_accept':True,'mutual_completion':True,'completed_statistic':True,'withdrawal_scrubs_refreshed_view':True,'untrusted_text_is_inert':True,'no_page_errors':True},'page_errors':errors,'external_resources':'blocked; Telegram SDK stubbed for Web-only test','production_access':False,'real_pilot':'NO-GO'}
                    (args.output/'help-browser-results.json').write_text(json.dumps(result,indent=2)+'\n');a.screenshot(path=str(args.output/'help-completed-withdrawn.png'),full_page=True)
                    print(json.dumps(result));browser.close()
            finally:server.shutdown();server.server_close();thread.join(5)
if __name__=='__main__':main()
