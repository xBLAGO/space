"""Explicit loopback-only consent lab. No production routes or external delivery.

Init alone may seed accounts, private memory and published source needs. All
subsequent consent/search/invitation/Help operations require browser commands.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from unittest.mock import patch

from tools.help_readiness_lab import initialize, configuration
from xblago.core import agent_context_schema, living_schema, uel_schema, uel_checkpoint as cp
from xblago.core.asset_registry_schema import migrate_registry
from xblago.core.integration import preflight
from xblago.core.security_journal import SecurityJournal, JournalUnavailable, canonical
from xblago.domain.living_deeds_agent import AgentLivingDeeds, LocalPullAdapter, FLAG, DELIVERY_FLAG
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp, ApiError

BANNER = 'LOCAL SANDBOX · SYNTHETIC DATA · NO EXTERNAL DELIVERY'
UI_FLAG = 'XBLAGO_LIVING_CONSENT_UI_ENABLED'
PASSWORD = 'synthetic-living021-only'
MARKER = 'LD021_PRIVATE_B_MARKER_Q7M4'
ROLES = {'A': 'ld021_a', 'B': 'ld021_b', 'C': 'ld021_c'}
TASKS = {name: 'SYNTHETIC_TASK_' + name.upper() + ': нужна помощь с велосипедом'
         for name in ('positive', 'decline', 'revoke')}
CONFIRM = 'ПОДТВЕРЖДАЮ'
STATIC = Path(__file__).with_name('living021_static')


@contextmanager
def custodian(root, cfg):
    config = json.loads(Path(cfg['XBLAGO_UEL_CHECKPOINT_CONFIG']).read_text())
    endpoint = Path(config['endpoint'])
    endpoint.unlink(missing_ok=True)  # Only the socket in this private synthetic root.
    child = subprocess.Popen([sys.executable, '-m', 'xblago.core.uel_checkpoint', 'serve',
        '--directory', str(root/'custody'), '--endpoint', str(endpoint)],
        env=os.environ | cfg, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not endpoint.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Synthetic checkpoint did not start')
            time.sleep(.05)
        yield
    finally:
        child.terminate()
        try: child.wait(8)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
        child.stderr.close()


def init(root):
    root = root.resolve()
    if len(str(root/'cp.sock').encode()) > 95:
        raise ValueError('Use a short new /tmp path for the local checkpoint socket')
    off = {name: 'false' for name in (living_schema.FLAG, agent_context_schema.FLAG,
        FLAG, DELIVERY_FLAG, UI_FLAG, 'XBLAGO_ASSET_REGISTRY_ENABLED', 'XBLAGO_UEL_LOCAL_ENABLED')}
    with patch.dict(os.environ, off): cfg = initialize(root)
    cfg.update({k:v for k,v in off.items() if k != UI_FLAG})
    os.environ.update(cfg)
    migrate_registry(root/'app/blago.sqlite3')
    cfg['XBLAGO_ASSET_REGISTRY_ENABLED'] = 'true'; os.environ.update(cfg)
    uel_schema.migrate_local(root/'app/blago.sqlite3')
    cfg['XBLAGO_UEL_LOCAL_ENABLED'] = 'true'
    source = cp.create_key(root/'source.key')
    stream = secrets.token_hex(32)
    pinned = cp.initialize(root/'custody', stream, source)
    cp_config = root/'checkpoint.json'
    cp_config.write_bytes(cp.encode({'stream': stream, 'custodian_key': pinned,
        'source_key_file': str(root/'source.key'), 'endpoint': str(root/'cp.sock')}))
    cp_config.chmod(0o600)
    cfg['XBLAGO_UEL_CHECKPOINT_CONFIG'] = str(cp_config)
    os.environ.update(cfg)
    with custodian(root, cfg):
        cp.enroll(SecurityJournal(root/'journal'))
        living_schema.migrate(root/'app/blago.sqlite3')
        cfg[living_schema.FLAG] = 'true'; os.environ.update(cfg)
        agent_context_schema.migrate(root/'app/blago.sqlite3')
        cfg.update({agent_context_schema.FLAG: 'true', FLAG: 'true', DELIVERY_FLAG: 'true'})
        os.environ.update(cfg)
        app = WebApp(preflight(root/'app/blago.sqlite3'))
        svc, help = AgentLivingDeeds(app), HelpService(app)
        tokens = {role: app.register(username, PASSWORD, cfg['XBLAGO_WEB_INVITE_CODE'])[0]
                  for role, username in ROLES.items()}
        def action(token, name, data):
            p = svc.prepare(token, name, data, 'seed-' + secrets.token_hex(16))
            return svc.commit(token, p['challenge'], CONFIRM)['id']
        uid = app.session(tokens['B'])[0]
        app.store.set_memory(uid, 'skill', 'Синтетический ремонт велосипедов')
        note = action(tokens['B'], 'note', {'text': MARKER + ': синтетическая знакомая чинит велосипеды',
                                           'expires_at': int(time.time()) + 7200})
        needs = {}
        for name, text in TASKS.items():
            rid = help.draft(tokens['A'], text, [help.code(tokens['B'])['code']],
                'seed-request-' + name + '-synthetic')['id']
            p = help.prepare(tokens['A'], rid, 1, 'publish', {})
            help.commit(tokens['A'], p['challenge'], CONFIRM)
            needs[name] = rid
        seed = {'format': 'living-consent-synthetic-1', 'roles': ROLES,
                'note_id': note, 'needs': needs, 'synthetic_only': True}
    marker = json.loads((root/'lab-only.json').read_text()); marker['config'] = cfg
    (root/'lab-only.json').write_text(json.dumps(marker, indent=2)+'\n')
    (root/'lab-only.json').chmod(0o600)
    (root/'ui-seed.json').write_text(json.dumps(seed, indent=2)+'\n')
    (root/'ui-seed.json').chmod(0o600)


class Lab:
    def __init__(self, root):
        self.root = Path(root).resolve()
        cfg = configuration(self.root)
        if os.getenv(UI_FLAG) != 'true': raise ValueError('Explicit local UI activation required')
        cfg.pop(UI_FLAG, None)
        os.environ.update(cfg)
        seed = self.root/'ui-seed.json'
        if seed.is_symlink() or seed.stat().st_mode & 0o077: raise ValueError('Private synthetic seed required')
        self.seed = json.loads(seed.read_text())
        if self.seed.get('format') != 'living-consent-synthetic-1' or self.seed.get('synthetic_only') is not True:
            raise ValueError('Synthetic seed required')
        self.app = WebApp(preflight(self.root/'app/blago.sqlite3'))
        self.s = AgentLivingDeeds(self.app); self.h = HelpService(self.app)
        self.plans = {}; self.lock = threading.Lock()

    def identity(self, token):
        with self.app.store._locked_transaction() as db:
            row = self.app.identity.session_row(db, token, fresh=True)
            uid, csrf = row['blago_user_id'], row['csrf']
            username = db.execute('SELECT username FROM web_credentials WHERE blago_user_id=?', (uid,)).fetchone()[0]
        role = next((r for r, n in ROLES.items() if n == username), None)
        if not role: raise ApiError(403, 'Only seeded synthetic roles')
        return role, uid, csrf

    def member(self, db, role, c):
        uid = db.execute('SELECT blago_user_id FROM web_credentials WHERE username=?', (ROLES[role],)).fetchone()[0]
        return c.journal.tag('help-member', uid)

    def permission(self, db, need):
        for r in db.execute("SELECT * FROM living_records WHERE kind='NEED_PERMISSION' ORDER BY rowid DESC"):
            state = json.loads(r['state'])
            if state['need_id'] == need and state['active']:
                return r, state
        raise ApiError(409, 'Автор ещё не разрешил приглашение')

    def read_mandate(self, db, uid, permission):
        for r in db.execute("SELECT * FROM living_records WHERE owner_id=? AND kind='AGENT_MANDATE' ORDER BY rowid DESC", (uid,)):
            state = json.loads(r['state'])
            if state['permission_id'] == permission and state['scope'] == 'MATCH_PRIVATE' and state['active']:
                return r['id']
        raise ApiError(409, 'Сначала явно разрешите MATCH_PRIVATE')

    def state(self, token, scenario):
        role, uid, csrf = self.identity(token); need = self.seed['needs'][scenario]
        # The author projection deliberately has no invitation/custody counters,
        # timestamps, global frontier, recipient identifiers or status oracle.
        with self.s.tx(token) as (db, _, c):
            if role == 'A':
                request = self.h._view(db, uid, self.h._row(db, uid, need))
                try: p, _ = self.permission(db, need); permission = p['id']
                except ApiError: permission = None
                data = {'request': request, 'permission': permission,
                        'status': 'Ожидаем добровольные предложения', 'can_revoke': permission is not None,
                        'can_allow': permission is None or json.loads(p['state'])['need_version'] != request['version']}
            elif role == 'B':
                request = self.h._view(db, uid, self.h._row(db, uid, need))
                try: p, ps = self.permission(db, need); permission = p['id']; task = ps['invitation_text']; expiry = ps['expires_at']
                except ApiError: permission = None; task = None; expiry = None
                if permission and ps['need_version'] != request['version']:
                    permission = None; task = None; expiry = None
                data = {'request': request, 'permission': permission, 'approved_text': task,
                        'permission_expires_at': expiry,
                        'recipients': [{'id': r, 'label': 'Синтетический участник '+r} for r in ('C', 'A')]}
            else:
                inbox = []
                for row in db.execute("SELECT * FROM living_records WHERE kind='INVITATION_DELIVERY' ORDER BY id"):
                    s = json.loads(row['state'])
                    br = db.execute('SELECT state FROM living_records WHERE id=?', (s['bridge_id'],)).fetchone()
                    if not br: continue
                    b = json.loads(br[0])
                    p = db.execute('SELECT state FROM living_records WHERE id=?', (b['permission_id'],)).fetchone()
                    if b.get('recipient_id') == uid and p and json.loads(p[0])['need_id'] == need:
                        inbox.append({'id': row['id'], 'label': 'Локальное приглашение'})
                data = {'inbox': inbox}
        return {'role': role, 'csrf': csrf, 'scenario': scenario, 'data': data}

    def act(self, token, scenario, action, d):
        role, uid, _ = self.identity(token); need = self.seed['needs'][scenario]
        allowed = {'A': {'allow', 'revoke', 'edit', 'accept', 'confirm'}, 'B': {'match', 'search', 'preview', 'send', 'invalidate'},
                   'C': {'open', 'contact', 'decline', 'offer', 'confirm'}}
        if action not in allowed[role]: raise ApiError(403, 'Действие недоступно этой роли')
        if action == 'invalidate':
            with self.lock:
                plan = self.plans.get(d.get('nonce'))
                if plan and plan['session'] == hashlib.sha256(token.encode()).hexdigest(): self.plans.pop(d['nonce'])
            return {'invalidated': True}
        if action in ('accept', 'confirm'):
            r = self.h.get(token, need)
            p = self.h.prepare(token, need, r['version'], action, {'offer_id': d['offer_id']} if action == 'accept' else {})
            return self.h.commit(token, p['challenge'], CONFIRM)
        if action == 'send': return self.send(token, need, d)
        if action == 'revoke':
            with self.s.tx(token) as (db, _, c):
                ids = [r['id'] for r in db.execute("SELECT * FROM living_records WHERE kind='NEED_PERMISSION'")
                       if json.loads(r['state']).get('need_id') == need and json.loads(r['state']).get('active')]
            for pid in ids: self.command(token, 'withdraw', {'id': pid})
            return {'revoked': True}
        if action in ('allow', 'edit', 'match', 'preview', 'search'):
            if action == 'allow':
                r = self.h.get(token, need)
                with self.s.tx(token) as (db, _, c):
                    try:
                        current, state = self.permission(db, need)
                        if state['need_version'] == r['version']: return {'id': current['id']}
                    except ApiError: pass
                return self.command(token, 'need_permission', {'need_id': need, 'topic': 'bike_'+scenario,
                    'invitation_text': r['text'], 'expires_at': int(time.time())+3600})
            if action == 'edit':
                r = self.h.get(token, need)
                p = self.h.prepare(token, need, r['version'], 'revise',
                    {'text': d['text'], 'audience': r['audience']})
                self.h.commit(token, p['challenge'], CONFIRM)
                # Publishing the changed exact text is part of this explicitly
                # labelled button; it never adds a new audience member.
                r = self.h.get(token, need); p = self.h.prepare(token, need, r['version'], 'publish', {})
                self.h.commit(token, p['challenge'], CONFIRM)
                return {'saved': True}
            with self.s.tx(token) as (db, _, c):
                p, ps = self.permission(db, need)
                if action == 'match':
                    return self.command(token, 'grant_agent', {'agent': 'personal-agent', 'scope': 'MATCH_PRIVATE',
                        'permission_id': p['id'], 'bridge_id': None, 'expires_at': ps['expires_at']})
                if action == 'search':
                    m = self.read_mandate(db, uid, p['id'])
                    return self.s.candidates(token, m, 'велосипед')
                if action == 'preview':
                    m = self.read_mandate(db, uid, p['id']); self.s.mandate(db, uid, m, 'MATCH_PRIVATE')
                    if d.get('recipient') not in ('C', 'A'): raise ApiError(400, 'Выберите адресата')
                    note, _ = self.s.root(db, self.seed['note_id'], uid, 'NOTE')
                    recipient = self.member(db, d['recipient'], c)
                    plan = {'permission': p['id'], 'permission_version': p['version'], 'note_version': note['version'],
                        'need_version': self.h.get(token, need)['version'], 'recipient': d['recipient'],
                        'task': ps['invitation_text'], 'session': hashlib.sha256(token.encode()).hexdigest(),
                        'expires_at': int(time.time())+120}
                    nonce = secrets.token_urlsafe(32)
                    with self.lock:
                        self.plans = {k: v for k, v in self.plans.items() if v['expires_at'] > int(time.time())}
                        if len(self.plans) >= 128: raise ApiError(429, 'Preview limit')
                        self.plans[nonce] = plan
                    return {'nonce': nonce, 'recipient': d['recipient'], 'task': plan['task'],
                            'referrer': self.h.code(token)['code'], 'private_note_sent': False,
                            'expires_at': ps['expires_at'],
                            'authorizations': ['BRIDGE', 'адресация', 'INVITE_ONCE', 'локальная очередь']}
        # Recipient actions resolve only a delivery addressed to THIS identity;
        # a guessed ID never selects another role or the server's owner token.
        if action == 'open':
            with self.s.tx(token) as (db, _, c):
                _, ds = self.s.root(db, d['id'], kind='INVITATION_DELIVERY')
                _, bs = self.s.root(db, ds['bridge_id'], kind='BRIDGE')
                _, ps = self.s.root(db, bs['permission_id'], kind='NEED_PERMISSION')
                if bs['recipient_id'] != uid or ps['need_id'] != need: raise ApiError(404, 'Приглашение недоступно')
            return LocalPullAdapter(self.s).deliver(token, d['id'])
        with self.s.tx(token) as (db, _, c):
            _, delivery = self.s.root(db, d['id'], kind='INVITATION_DELIVERY')
            bridge, state = self.s.root(db, delivery['bridge_id'], kind='BRIDGE')
            if state['recipient_id'] != uid: raise ApiError(404, 'Приглашение недоступно')
            if delivery['status'] != 'ACKED': raise ApiError(409, 'Сначала откройте приглашение')
            if action in ('contact', 'decline'):
                return self.command(token, 'accept_invitation' if action == 'contact' else 'decline_invitation', {'id': bridge['id']})
            if state['status'] != 'CONSENTED': raise ApiError(409, 'Сначала согласитесь на контакт')
            # Deliberately exercise the canonical contract, with NO audience
            # mutation. It currently rejects C at HelpService._row().
            request = db.execute('SELECT version FROM help_requests WHERE id=?', (need,)).fetchone()
            try:
                prepared = self.h.prepare(token, need, request['version'], 'offer', {'note': 'Добровольно предлагаю синтетическую помощь'})
            except ApiError as error:
                if error.status != 404: raise
                return {'offered': False, 'blocker': 'HELP_AUDIENCE_REQUIRED',
                    'message': 'Предложение недоступно: действующий Help требует доступа к обращению. Аудитория не менялась.'}
            return self.h.commit(token, prepared['challenge'], CONFIRM)

    def send(self, token, need, data):
        # One visible confirmation authorizes the exact displayed sequence.
        # Each existing command retains its own durable commit boundary. Never
        # flush/reinitialize privacy state to fake a multi-command transaction.
        with self.s.tx(token) as (db, uid, control):
            with self.lock: plan = self.plans.pop(data.get('nonce'), None)
            if not plan or plan['session'] != hashlib.sha256(token.encode()).hexdigest() or plan['expires_at'] <= int(time.time()):
                raise ApiError(409, 'Превью недействительно; подтвердите заново')
            permission, ps = self.permission(db, need)
            note, _ = self.s.root(db, self.seed['note_id'], uid, 'NOTE')
            request = self.h._row(db, uid, need)
            if (plan['recipient'] != data.get('recipient') or plan['task'] != data.get('text') or
                permission['id'] != plan['permission'] or permission['version'] != plan['permission_version'] or
                note['version'] != plan['note_version'] or request['version'] != plan['need_version']):
                raise ApiError(409, 'Текст или адресат изменились; подтвердите заново')
            self.s.mandate(db, uid, self.read_mandate(db, uid, permission['id']), 'MATCH_PRIVATE')
            recipient = self.member(db, data['recipient'], control)
            bridge = self.command(token, 'propose_bridge', {'note_id': note['id'], 'permission_id': permission['id']})['id']
        self.command(token, 'request_consent', {'id': bridge, 'recipient': recipient})
        mandate = self.command(token, 'grant_agent', {'agent': 'personal-agent', 'scope': 'INVITE_ONCE',
            'permission_id': permission['id'], 'bridge_id': bridge, 'expires_at': ps['expires_at']})['id']
        delivery = self.command(token, 'queue_invitation', {'mandate_id': mandate, 'bridge_id': bridge})['id']
        return {'queued': True, 'id': delivery}

    def command(self, token, action, data):
        p = self.s.prepare(token, action, data, 'browser-command-' + secrets.token_hex(16))
        return self.s.commit(token, p['challenge'], CONFIRM)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Never log cookies, source text or memory.

    def guard(self, write=False):
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        if not ipaddress.ip_address(self.client_address[0]).is_loopback or self.headers.get('Host') != origin[7:]:
            raise ApiError(403, 'Loopback only')
        if write and self.headers.get('Origin') != origin: raise ApiError(403, 'Same origin required')

    def respond(self, status, value, content_type='application/json; charset=utf-8', cookie=None):
        data = (canonical(value) if content_type.startswith('application/json') else value).encode()
        self.send_response(status)
        for k, v in {'Content-Type': content_type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items(): self.send_header(k, v)
        if cookie: self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def token(self):
        cookies = SimpleCookie(); cookies.load(self.headers.get('Cookie', ''))
        return cookies['ld021'].value if 'ld021' in cookies else ''

    def do_GET(self):
        try:
            self.guard(); path = urlsplit(self.path).path
            if path in ('/ui.js', '/ui.css'):
                return self.respond(200, (STATIC/path[1:]).read_text(), 'text/javascript' if path.endswith('.js') else 'text/css')
            if path == '/':
                try: role, _, _ = self.server.lab.identity(self.token())
                except ApiError: role = 'login'
                return self.respond(200, (STATIC/(role+'.html')).read_text(), 'text/html; charset=utf-8')
            scenario = urlsplit(self.path).query.removeprefix('scenario=')
            if scenario not in TASKS: raise ApiError(400, 'Unknown synthetic scenario')
            if path == '/api/state': return self.respond(200, self.server.lab.state(self.token(), scenario))
            if path == '/api/view': return self.respond(200, self.server.lab.s.view(self.token()))
            raise ApiError(404, 'Not found')
        except ApiError as e: self.respond(e.status, {'error': str(e)})
        except JournalUnavailable: self.respond(503, {'error': 'UNKNOWN_RECOVERY'})
        except (ValueError, KeyError, TypeError): self.respond(400, {'error': 'Invalid request'})

    def do_POST(self):
        role = None
        try:
            self.guard(True)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192 or self.headers.get('Content-Type') != 'application/json': raise ApiError(400, 'Bounded JSON required')
            d = json.loads(self.rfile.read(size))
            if urlsplit(self.path).path == '/api/login':
                if d.get('username') not in ROLES.values(): raise ApiError(403, 'Synthetic role only')
                token, _ = self.server.lab.app.login(d['username'], d['password'])
                return self.respond(200, {'signed_in': True}, cookie='ld021='+token+'; HttpOnly; SameSite=Strict; Path=/')
            if urlsplit(self.path).path != '/api/action': raise ApiError(404, 'Not found')
            role, _, csrf = self.server.lab.identity(self.token())
            if self.headers.get('X-CSRF-Token') != csrf: raise ApiError(403, 'CSRF rejected')
            if d['scenario'] not in TASKS: raise ApiError(400, 'Unknown scenario')
            result = self.server.lab.act(self.token(), d['scenario'], d['action'], d.get('data', {}))
            self.respond(200, result)
        except ApiError as e:
            self.respond(e.status, {'error': 'Приглашение недоступно' if role == 'C' else str(e)})
        except JournalUnavailable: self.respond(503, {'error': 'UNKNOWN_RECOVERY'})
        except (ValueError, KeyError, TypeError): self.respond(400, {'error': 'Invalid request'})


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, handler):
        if address[0] != '127.0.0.1': raise ValueError('Literal IPv4 loopback only')
        super().__init__(address, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--new-root', type=Path, required=True)
    p = sub.add_parser('serve'); p.add_argument('--root', type=Path, required=True)
    p.add_argument('--bind', choices=['127.0.0.1'], default='127.0.0.1')
    p.add_argument('--port', type=int, default=17861)
    p.add_argument('--enable-local-sandbox', action='store_true', required=True)
    args = parser.parse_args()
    if args.command == 'init': init(args.new_root); print(BANNER); return
    cfg = configuration(args.root); cfg.pop(UI_FLAG, None); os.environ.update(cfg)
    os.environ[UI_FLAG] = 'true'
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with custodian(args.root.resolve(), cfg):
        server = LoopbackServer((args.bind, args.port), Handler); server.lab = Lab(args.root)
        print(BANNER + ' http://127.0.0.1:' + str(server.server_port), flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()


if __name__ == '__main__': main()
