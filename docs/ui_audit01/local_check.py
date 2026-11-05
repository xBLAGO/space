import sys,os,json,threading
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from pilot_fixture import fixture
from xblago.integrations.web import BlagoWebHandler
from playwright.sync_api import sync_playwright,expect
root=Path(os.getenv('BLAGO_AUDIT_OUTPUT','/tmp/blago-ui-audit01-repeat'));root.mkdir(parents=True,exist_ok=True);checks={};errors=[]
with fixture() as f:
 os.environ['XBLAGO_INTEGRATION_MODE']='synthetic'
 from xblago.core.integration import preflight
 preflight(f['path']);app=f['app'];app.register('ui_audit_synthetic','synthetic-audit-password','synthetic-pilot-invite')
 server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=app
 worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();url=f'http://localhost:{server.server_port}'
 try:
  with patch.dict(os.environ,{'XBLAGO_PUBLIC_ORIGIN':url}),sync_playwright() as p:
   b=p.chromium.launch(executable_path=os.getenv('BLAGO_AUDIT_CHROME','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),headless=True)
   context=b.new_context(viewport={'width':1360,'height':1000});context.route('https://telegram.org/**',lambda r:r.fulfill(status=200,body='/* no Telegram SDK in Web-only synthetic audit */',content_type='application/javascript'))
   page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url+'/app',wait_until='domcontentloaded');page.locator('#username').fill('ui_audit_synthetic');page.locator('#password').fill('synthetic-audit-password');page.locator('#auth-submit').click();expect(page.locator('#shell')).to_be_visible();checks['web_login']=True
   def tab(name):
    page.locator('#tools-toggle').click();page.locator('[data-tab="'+name+'"]').first.click();expect(page.locator('#'+name+'-screen')).to_be_visible();page.wait_for_timeout(300)
   for name in ['chat','needs','resources','analytics','profile']:
    if name!='chat':tab(name)
    page.screenshot(path=str(root/f'local-{name}.png'),full_page=True);checks[name+'_screen']=True
   tab('chat');checks['empty_history_no_greeting']=page.locator('#chat-stream .bubble').count()==0
   page.reload();expect(page.locator('#shell')).to_be_visible();checks['cookie_session_reload']=True
   page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/'local-mobile-chat.png'),full_page=True)
   tab('needs');page.locator('#needs-title').fill('Синтетический запрос на помощь');page.locator('#needs-form button[type=submit]').click();expect(page.locator('#needs-list')).to_contain_text('Синтетический запрос');checks['own_need_create']=True
   tab('resources');page.locator('#resources-title').fill('Синтетический навык ремонта');page.locator('#resources-form button[type=submit]').click();expect(page.locator('#resources-list')).to_contain_text('Синтетический навык');checks['own_resource_create']=True
   checks['mobile_no_horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth <= innerWidth');checks['no_page_errors']=not errors
   result={'scope':'localhost synthetic Web only; SDK stubbed, no Telegram authentication or physical devices','browser':b.version,'checks':checks,'page_errors':errors};b.close()
  (root/'local-browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
 finally:server.shutdown();server.server_close();worker.join(5)
