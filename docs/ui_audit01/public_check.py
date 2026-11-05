import os
import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(os.getenv('BLAGO_AUDIT_OUTPUT','/tmp/blago-ui-audit01-repeat'));root.mkdir(parents=True,exist_ok=True);out=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.getenv('BLAGO_AUDIT_CHROME','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),headless=True)
 c=b.new_context(viewport={'width':1360,'height':1000})
 c.route('**/*',lambda r:r.abort() if any(x in r.request.url for x in ['mc.yandex.ru','googletagmanager.com','google-analytics.com']) else r.continue_())
 for i,path in enumerate(['','en/en_description/','en/en_investors/','en/en_log/']):
  page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  try:
   response=page.goto('https://xblago.space/'+path,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(1500)
   page.screenshot(path=str(root/f'public-{i}-desktop.png'),full_page=True)
   desktop={'width':page.evaluate('innerWidth'),'scroll_width':page.evaluate('document.documentElement.scrollWidth')}
   page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/f'public-{i}-mobile.png'),full_page=True)
   text=page.locator('body').inner_text()
   out.append({'url':page.url,'status':response.status,'title':page.title(),'links':page.locator('a').evaluate_all('(a)=>a.map(x=>({text:x.innerText.trim(),href:x.href}))'),'desktop':desktop,'mobile_scroll_width':page.evaluate('document.documentElement.scrollWidth'),'forms':page.locator('form').count(),'errors':errors,'claims':{'btc_peg':'1 xBLAGO = 1 BTC' in text,'nft':'NFT' in text,'investment':'invest' in text.lower()}})
  except Exception as e:out.append({'path':path,'error':str(e)})
  page.close()
 result={'engine':b.version,'tracking_requests':'blocked for read-only auditor privacy; not consent verification','checks':out};b.close()
(root/'public-browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
