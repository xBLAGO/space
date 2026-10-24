"""Real Chrome UI/WebAuthn with a CDP virtual authenticator; synthetic localhost only."""
import argparse
import hashlib
import json
import os
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from pilot_fixture import fixture
from xblago.core.sqlite_safety import runtime_evidence
from xblago.integrations.web import BlagoWebHandler,ApiError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome',default=os.getenv('BLAGO_CHROME_EXECUTABLE') or shutil.which('google-chrome') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright,expect
    args.output.mkdir(parents=True,exist_ok=True)
    with fixture() as f:
        app=f['app']
        setup_token,_=app.register('browser_user','synthetic-browser-password','synthetic-pilot-invite')
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
        server.blago_web_app=app
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=f'http://localhost:{server.server_port}'
        failures=[];checks={}
        try:
            with patch.dict(os.environ,{'XBLAGO_PUBLIC_ORIGIN':url}),sync_playwright() as p:
                chrome_env={key:value for key,value in os.environ.items() if key not in ('LD_PRELOAD','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH')}
                browser=p.chromium.launch(executable_path=args.chrome,headless=True,env=chrome_env)
                try:
                    context=browser.new_context(viewport={'width':1360,'height':1000})
                    page=context.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
                    cdp=context.new_cdp_session(page)
                    cdp.send('WebAuthn.enable')
                    cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal',
                        'hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})
                    page.goto(url+'/app')
                    page.locator('#username').fill('browser_user')
                    page.locator('#password').fill('synthetic-browser-password')
                    page.locator('#auth-submit').click()
                    expect(page.locator('#shell')).to_be_visible()
                    def profile():
                        # Foundation 06 keeps tools in a deliberately closed drawer.
                        page.locator('#tools-toggle').click()
                        page.locator('[data-tab="profile"]').first.click()
                    profile()
                    page.get_by_text('Доступ и устройства',exact=True).click()
                    before=next(c['value'] for c in context.cookies() if c['name']=='blago_session')
                    with page.expect_response(lambda r:r.url.endswith('/auth/passkeys/register/verify')) as registered:
                        page.locator('#add-passkey').click()
                    checks['real_browser_registration']=registered.value.status==200
                    expect(page.locator('#notice')).to_have_text('Ключ доступа добавлен')
                    after=next(c['value'] for c in context.cookies() if c['name']=='blago_session')
                    checks['registration_rotates_cookie']=before!=after
                    try:
                        app.session(before);checks['old_cookie_denied']=False
                    except ApiError:
                        checks['old_cookie_denied']=True
                    profile()
                    page.locator('#logout').click()
                    expect(page.locator('#auth')).to_be_visible()
                    with page.expect_response(lambda r:r.url.endswith('/auth/passkeys/authenticate/verify')) as authenticated:
                        page.locator('#passkey-login').click()
                    checks['real_browser_discoverable_login']=authenticated.value.status==200
                    expect(page.locator('#shell')).to_be_visible()
                    profile()
                    details=page.locator('details').filter(has=page.locator('#refresh-access'))
                    if details.get_attribute('open') is None:
                        details.locator(':scope > summary').click()
                    page.locator('#refresh-access').click()
                    expect(page.locator('#access-sessions')).not_to_be_empty()
                    with page.expect_response(lambda r:r.url.endswith('/auth/sessions/revoke')) as revoked:
                        page.locator('#revoke-others').click()
                    checks['session_revoke_ui']=revoked.value.status==200
                    try:
                        app.session(setup_token);checks['other_session_denied']=False
                    except ApiError:
                        checks['other_session_denied']=True
                    page.get_by_text('Мои данные и приватность',exact=True).click()
                    with page.expect_download() as download:
                        page.locator('#export-personal').click()
                    destination=args.output/'synthetic-export.json'
                    download.value.save_as(destination)
                    exported=json.loads(destination.read_text())
                    checks['export_ui']=exported['format']=='blago-personal-export-1'
                    # Keep only a content hash in committed evidence, not the downloaded fixture.
                    export_hash=hashlib.sha256(destination.read_bytes()).hexdigest();destination.unlink()
                    page.screenshot(path=str(args.output/'chrome-desktop.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(args.output/'chrome-mobile-viewport.png'),full_page=True)
                    checks['no_page_errors']=not failures
                    result={'browser':browser.version,'engine':'installed Chrome, headless','origin':'localhost temporary',
                        'authenticator':'CDP virtual CTAP2, resident key + UV; no real device or biometric tested',
                        'sqlite':runtime_evidence(),'checks':checks,'page_errors':failures,'export_sha256':export_hash,
                        'unverified':['physical passkeys','Touch ID','Safari','Firefox','iOS Telegram WebView','Android Telegram WebView']}
                    (args.output/'browser-results.json').write_text(json.dumps(result,indent=2)+'\n')
                    print(json.dumps({'browser':result['browser'],'checks':checks,'page_errors':failures}))
                    if not all(checks.values()):
                        raise SystemExit(1)
                finally:
                    browser.close()
        finally:
            server.shutdown();server.server_close();worker.join(5)


if __name__=='__main__':
    main()
