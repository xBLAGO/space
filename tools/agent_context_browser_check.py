"""Desktop and synthetic Telegram SDK mobile Chrome; localhost-only traffic."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

from tools.integration_fixture import create
from tools.agent_context_check import InspectingProvider
from tools.unified_mvp_check import init_data, bot_update
from xblago.core.agent_context_schema import migrate, FLAG
from xblago.core.agent_context import explain
from xblago.core.integration import preflight
from xblago.integrations.web import WebApp, BlagoWebHandler
from xblago.integrations.telegram import TelegramBot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chrome', default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright, expect
    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors = {}, []
    with tempfile.TemporaryDirectory(prefix='blago-context-browser-') as directory:
        with patch.dict(os.environ, {FLAG: 'false'}):
            cfg = create(Path(directory)/'new', help_enabled=True, context_fencing=True, lifecycle=True)
        cfg.update(XBLAGO_WEB_INVITE_CODE='synthetic-context-browser', TELEGRAM_BOT_TOKEN='123:synthetic-help011-only', XBLAGO_COOKIE_SECURE='false')
        provider = InspectingProvider()
        with patch.dict(os.environ, cfg), patch('xblago.integrations.web.build_chat_provider', return_value=provider):
            path = Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
            migrate(path)
            os.environ[FLAG] = 'true'
            app = WebApp(preflight(path))
            server = ThreadingHTTPServer(('127.0.0.1', 0), BlagoWebHandler)
            server.blago_web_app = app
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = 'http://localhost:' + str(server.server_port)
            try:
                with sync_playwright() as pw:
                    env = {k: v for k, v in os.environ.items() if k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH')}
                    browser = pw.chromium.launch(executable_path=args.chrome, headless=True, env=env)
                    def page(mobile=False):
                        context = browser.new_context(**({'viewport': {'width': 390, 'height': 844}, 'is_mobile': True, 'has_touch': True} if mobile else {}))
                        def route(r):
                            if urlsplit(r.request.url).hostname in ('localhost', '127.0.0.1'):
                                r.continue_()
                            elif r.request.url == 'https://telegram.org/js/telegram-web-app.js':
                                body = 'window.Telegram={WebApp:{initData:' + json.dumps(init_data()) + ',ready(){},expand(){}}};' if mobile else '/* no SDK in desktop Web */'
                                r.fulfill(status=200, content_type='application/javascript', body=body)
                            else:
                                r.abort()
                        context.route('**/*', route)
                        p = context.new_page()
                        p.on('pageerror', lambda e: errors.append(str(e)))
                        return p
                    desktop = page()
                    desktop.goto(url+'/app')
                    desktop.locator('#auth-toggle').click()
                    desktop.locator('#username').fill('synthetic_context_browser')
                    desktop.locator('#password').fill('synthetic-password-only')
                    desktop.locator('#invite').fill('synthetic-context-browser')
                    desktop.locator('#auth-submit').click()
                    expect(desktop.locator('#shell')).to_be_visible()
                    def say(p, text):
                        p.locator('#chat-input').fill(text)
                        with p.expect_response(lambda r: r.url.endswith('/api/v1/chat/messages') and r.request.method == 'POST') as response:
                            p.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                        expect(p.locator('#chat-input')).to_have_value('')
                        assert response.value.status == 200
                    say(desktop, 'Меня зовут Анна')
                    say(desktop, 'Я живу в Москве')
                    me = desktop.evaluate("fetch('/api/v1/me').then(r=>r.json())")
                    code = desktop.evaluate("async csrf => (await fetch('/api/v1/auth/link-code',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:'{}'})).json()", me['csrf'])['code']
                    TelegramBot(None, Path(directory)/'unused', app.store).replies_for_update(bot_update(98001, '/link '+code))
                    mini = page(True)
                    mini.goto(url+'/app')
                    expect(mini.locator('#shell')).to_be_visible()
                    checks['one_linked_identity'] = mini.evaluate("fetch('/api/v1/me').then(r=>r.json())")['profile']['blago_user_id'] == me['profile']['blago_user_id']
                    say(mini, 'Где я живу?')
                    context = provider.contexts[-1]
                    checks['web_source_retrieved_in_mini'] = explain(context.participant, context.memory, context.user_text)['city']['source']['quote'] == 'Москве'
                    say(mini, 'Я больше не живу в Москве, теперь живу в Казани')
                    server.blago_web_app = WebApp(preflight(path))
                    mini.reload()
                    expect(mini.locator('#shell')).to_be_visible()
                    say(mini, 'Где я живу?')
                    context = provider.contexts[-1]
                    checks['restart_current_fact'] = context.memory['city'] == 'Казани'
                    checks['no_old_history_in_context'] = not any('Москве' in t.text for t in context.history)
                    me2 = mini.evaluate("fetch('/api/v1/me').then(r=>r.json())")
                    status = mini.evaluate("async csrf => (await fetch('/api/v1/memory',{method:'DELETE',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({key:'city'})})).status", me2['csrf'])
                    say(mini, 'Где я живу?')
                    checks['delete_applied_to_context'] = status == 200 and 'city' not in provider.contexts[-1].memory
                    checks['no_horizontal_overflow'] = mini.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    checks['no_page_errors'] = not errors
                    mini.screenshot(path=str(args.output/'synthetic-mini-app.png'), full_page=True)
                    result = {'format': 'blago-context-browser-1', 'checks': checks, 'browser': browser.version,
                              'provider': 'scripted inspection, not live model quality', 'external_network': False,
                              'physical_android_telegram': 'NOT EXECUTED', 'physical_ios_telegram': 'NOT EXECUTED', 'real_pilot': 'NO-GO'}
                    (args.output/'results.json').write_text(json.dumps(result, indent=2)+'\n')
                    print(json.dumps(result))
                    browser.close()
                    if not all(checks.values()):
                        raise SystemExit(1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)


if __name__ == '__main__':
    main()
