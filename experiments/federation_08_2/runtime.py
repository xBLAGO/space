"""Synthetic filesystem lab only. Never imported by the application.
Durability assumes POSIX flock, atomic rename and honest fsync on separate volumes.
"""
import base64
import copy
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import sys
import time
from contextlib import contextmanager
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'docs/protocol_0_1/conformance'))
import validator_a as a
import validator_b as b

SCHEMA_HASH = '64667aa8fb6abd535e4a2f93d17d6ed56c46d31ab548c84eb56f4cc80a86b85a'
def encode(x): return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
def digest(x): return 'sha256:' + hashlib.sha256(x).hexdigest()
def b64(x): return base64.urlsafe_b64encode(x).decode().rstrip('=')
def nonce(): return b64(secrets.token_bytes(32))
def handle(prefix, size=16): return prefix + secrets.token_hex(size)
def sign(body, key):
    return {'alg': 'Ed25519', 'body': body, 'signature': b64(key.sign(b'BOP/0.1-lab.1/' + body['kind'].upper().encode() + b'\0' + a.canonical(body)))}
def wire(obj): return a.canonical(obj).hex()

class Failure(Exception): pass
class Unknown(Failure): pass
class Fault:
    """Operator-side fault injector; never accepted in a network message."""
    def __init__(self, stage=None, crash=False, errno=5): self.stage, self.crash, self.errno = stage, crash, errno
    def hit(self, stage):
        if self.stage == stage:
            if self.crash: os._exit(71)
            raise OSError(self.errno, 'synthetic storage failure at ' + stage)

@contextmanager
def lock(path):
    # Never create missing stores during normal operation.
    with open(path, 'r+b') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)

def write(path, data, fault=None):
    fault = fault or Fault()
    fault.hit('write')
    temporary = path.with_name('.stage-' + secrets.token_hex(16))
    try:
        with open(temporary, 'xb') as f:
            os.chmod(temporary, 0o600)
            f.write(data); f.flush(); fault.hit('fsync'); os.fsync(f.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

def load(path):
    raw = path.read_bytes()
    if len(raw) > 8_000_000: raise Unknown('UNKNOWN_RECOVERY')
    obj = json.loads(raw)
    if encode(obj) != raw: raise Unknown('UNKNOWN_RECOVERY')
    return obj

class Retained:
    """Local stand-in for independently retained CAS. No automatic re-bootstrap.
    It is NOT an independent operator or a rollback-resistant remote service.
    """
    def __init__(self, path, witness=None):
        self.path = Path(path)
        self.witness = Path(witness) if witness else self.path.with_name(self.path.name + '-witness')
    @classmethod
    def bootstrap(cls, path, config_ref):
        p = Path(path); p.mkdir(mode=0o700, parents=True, exist_ok=False)
        (p/'lock').touch(mode=0o600)
        initial = {'config': config_ref, 'index': 0, 'head': None, 'chain': []}
        witness = p.with_name(p.name + '-witness')
        witness.mkdir(mode=0o700, exist_ok=False)
        write(p/'head', encode(initial))
        write(witness/'head', encode(initial))
        return cls(p)
    def read(self):
        try:
            with lock(self.path/'lock'):
                head = load(self.path/'head')
                if head != load(self.witness/'head'): raise Unknown('UNKNOWN_RECOVERY')
                return head
        except (OSError, ValueError, KeyError) as exc: raise Unknown('UNKNOWN_RECOVERY') from exc
    def cas(self, old, new, fault):
        with lock(self.path/'lock'):
            if load(self.path/'head') != old or load(self.witness/'head') != old: raise Unknown('UNKNOWN_RECOVERY')
            fault.hit('before_commit')
            fault.hit('control_write')
            write(self.path/'head', encode(new))
            fault.hit('between_checkpoints')
            fault.hit('witness_write')
            write(self.witness/'head', encode(new))
            fault.hit('after_commit')
    def contains(self, index, state_ref):
        s = self.read()
        return type(index) is int and 1 <= index <= s['index'] and s['chain'][index-1] == state_ref

class Receiver:
    def __init__(self, path, retained, config, node_key, validator='B', clock=None, fault=None):
        self.path, self.retained, self.config, self.key = Path(path), retained, copy.deepcopy(config), node_key
        self.config_ref = digest(encode(config)); self.validator = a if validator == 'A' else b
        self.clock = clock or self._unknown_clock; self.fault = fault or Fault()
        schema = json.loads((ROOT/'docs/lab_08_2/receipt-schema.json').read_text())
        if hashlib.sha256(encode(schema)).hexdigest() != SCHEMA_HASH: raise Failure('SCHEMA_REVISION')
        self.schema = schema
        if a.kid(node_key.public_key().public_bytes_raw()) != config['node_key']: raise Failure('NODE_PIN')
    @staticmethod
    def _unknown_clock(): raise Unknown('UNKNOWN_CLOCK')
    @staticmethod
    def bootstrap(path):
        p = Path(path); p.mkdir(mode=0o700, parents=True, exist_ok=False)
        (p/'records').mkdir(mode=0o700); (p/'lock').touch(mode=0o600)
    def _state(self):
        try:
            tip = self.retained.read()
            if tip['config'] != self.config_ref or len(tip['chain']) != tip['index']: raise Unknown('UNKNOWN_RECOVERY')
            previous = None; last = None
            for i, h in enumerate(tip['chain'], 1):
                if not a.tag(h, 'sha256:', 64): raise Unknown('UNKNOWN_RECOVERY')
                record = load(self.path/'records'/h[7:])
                if digest(b'LAB/08.2/STATE\0' + encode(record)) != h or record['index'] != i or record['previous'] != previous or record['config'] != self.config_ref: raise Unknown('UNKNOWN_RECOVERY')
                previous, last = h, record['state']
            if previous != tip['head']: raise Unknown('UNKNOWN_RECOVERY')
            return tip, last or {'inbox': {}, 'uses': {}, 'pending': {}, 'revokes': {}, 'consumed': [], 'sequence': 0, 'head': self.config['genesis_head']}
        except (OSError, ValueError, KeyError, TypeError) as exc: raise Unknown('UNKNOWN_RECOVERY') from exc
    def _commit(self, tip, state):
        record = {'index': tip['index']+1, 'previous': tip['head'], 'config': self.config_ref, 'state': state}
        h = digest(b'LAB/08.2/STATE\0' + encode(record))
        write(self.path/'records'/h[7:], encode(record), self.fault)
        new = {**tip, 'index': record['index'], 'head': h, 'chain': tip['chain']+[h]}
        self.retained.cas(tip, new, self.fault)
        # Signing is strictly downstream of successful fsync + retained CAS.
        return record['index'], h
    def challenge(self, envelope_hex, kind='delivery'):
        if kind not in ('delivery', 'revoke'): raise Failure('KIND')
        obj = a.read_wire(envelope_hex); reference = a.ref(obj)
        with lock(self.path/'lock'):
            tip, state = self._state()
            key = kind + ':' + reference
            if key in state['pending'] and state['pending'][key]['until'] > self.clock(): return state['pending'][key]['nonce']
            state['pending'] = {k:v for k,v in state['pending'].items() if v['until'] > self.clock()}
            if len(state['pending']) >= 32: raise Failure('QUOTA')
            n = nonce(); state['pending'][key] = {'nonce': n, 'until': self.clock()+30}
            self._commit(tip, state)
            return n
    def _blueprint(self, kind, **fields):
        return dict(kind=kind, profile=a.PROFILE, network=self.config['network'], policy_ref=self.config['policy_ref'], node=self.config['recipient_node'], issuer_key=self.config['node_key'], receipt_id=handle('rc:', 32), authority_epoch=1, **fields)
    def _receipt(self, entry):
        index = entry['index']; tip = self.retained.read()
        if not 1 <= index <= tip['index']: raise Unknown('UNKNOWN_RECOVERY')
        body = dict(entry['receipt'], inbox_commit_index=index, inbox_state_ref=tip['chain'][index-1])
        return sign(body, self.key)
    def deliver(self, packet):
        # No caller-provided context is permitted.
        if set(packet) != {'envelope_hex','mandate_hex','authority_hex','status_hex'}: raise Failure('PACKET')
        env = a.read_wire(packet['envelope_hex']); e = env['body']; er = a.ref(env)
        with lock(self.path/'lock'):
            tip, state = self._state()
            old = state['inbox'].get(e['event_id'])
            if old:
                if old['event_ref'] != er: raise Failure('EVENT_CONFLICT')
                return self._receipt(old)
            k = 'delivery:' + er; pending = state['pending'].get(k)
            if not pending or pending['until'] <= self.clock(): raise Failure('STATUS_NONCE')
            if e['mandate_ref'] in state['revokes']: raise Failure('REVOKED')
            ctx = {k:self.config[k] for k in ('network','policy_ref','recipient_node','recipient_agent','owner_identity','owner_key_b64','owner_epoch','genesis_head')}
            ctx.update(now=self.clock(), clock_uncertainty=0, clock_ok=True, retained_available=True, floor_sequence=state['sequence'], floor_head=state['head'], expected_status_nonce=pending['nonce'], known_refs=[], seen_event_ids=list(state['inbox']), mandate_uses=state['uses'].get(e['mandate_ref'], 0))
            result = self.validator.validate({**packet, 'context': ctx})
            if result['decision'] != 'ACCEPT': raise Failure(result['code'])
            c = a.read_wire(packet['status_hex'])['body']
            entry = {'event_ref': er, 'payload_b64': e['payload_b64'], 'index': tip['index']+1, 'receipt': self._blueprint('delivery-receipt', event_id=e['event_id'], event_ref=er, mandate_ref=e['mandate_ref'], status_nonce=pending['nonce'], sequence=c['sequence'], head=c['head'], decision='committed')}
            state['inbox'][e['event_id']] = entry; state['uses'][e['mandate_ref']] = 1
            state['consumed'].append(pending['nonce']); del state['pending'][k]
            state['sequence'], state['head'] = c['sequence'], c['head']
            self._commit(tip, state)
            return self._receipt(entry)
    def revoke(self, history_hex, request_nonce):
        if type(history_hex) is not list or not 1 <= len(history_hex) <= 64: raise Failure('HISTORY')
        history = [a.read_wire(x) for x in history_hex]
        with lock(self.path/'lock'):
            tip, state = self._state(); parent = self.config['genesis_head']; states = {}; frontiers = {0:parent}
            for seq, item in enumerate(history, 1):
                v = item['body']; a.fields(v, 'kind profile network policy_ref identity issuer_key epoch sequence previous operation mandate_ref')
                if any(v[x] != self.config[y] for x,y in [('profile','profile'),('network','network'),('policy_ref','policy_ref'),('identity','owner_identity')]): raise Failure('DOMAIN')
                root = a.unb64(self.config['owner_key_b64'], 32)
                if v['kind'] != 'authority-event' or v['issuer_key'] != a.kid(root) or type(v['epoch']) is not int or v['epoch'] != 1 or type(v['sequence']) is not int or v['sequence'] != seq or v['previous'] != parent: raise Failure('HISTORY')
                a.signature(item, root); m = v['mandate_ref']
                if not a.tag(m, 'sha256:', 64): raise Failure('HISTORY')
                if v['operation'] == 'mandate.issue' and m not in states: states[m] = 'active'
                elif v['operation'] == 'mandate.revoke' and states.get(m) == 'active': states[m] = 'revoked'
                else: raise Failure('HISTORY')
                parent = a.ref(item); frontiers[seq] = parent
            last = history[-1]['body']; m = last['mandate_ref']; rr = a.ref(history[-1])
            if last['operation'] != 'mandate.revoke': raise Failure('REVOKE')
            if frontiers.get(state['sequence']) != state['head']: raise Failure('FORK')
            if m in state['revokes']:
                old = state['revokes'][m]
                if old['receipt']['revoke_ref'] != rr: raise Failure('FORK')
                return self._receipt(old)
            k = 'revoke:' + rr; pending = state['pending'].get(k)
            if not pending or pending['until'] <= self.clock() or pending['nonce'] != request_nonce: raise Failure('STATUS_NONCE')
            entry = {'index': tip['index']+1, 'receipt': self._blueprint('revocation-receipt', identity=self.config['owner_identity'], revoke_ref=rr, sequence=len(history), head=parent, request_nonce=request_nonce, decision='revocation-recorded')}
            state['revokes'][m] = entry; state['sequence'], state['head'] = len(history), parent
            state['consumed'].append(request_nonce); del state['pending'][k]
            self._commit(tip, state)
            return self._receipt(entry)


def verify_receipt(receipt_hex, public, expected, retained):
    """All body bindings are supplied by the sender's pending request, never packet pins.
    Retained object here is a local trusted capability, NOT an unauthenticated RPC.
    """
    obj = a.read_wire(receipt_hex); body = obj['body']
    kind = body.get('kind'); schema = json.loads((ROOT/'docs/lab_08_2/receipt-schema.json').read_text())
    if kind not in ('delivery-receipt','revocation-receipt'): raise Failure('RECEIPT_KIND')
    a.fields(body, ' '.join(schema[kind]['fields'])); a.signature(obj, public)
    if set(expected) != set(body)-{'receipt_id','inbox_commit_index','inbox_state_ref'}: raise Failure('BINDING_FIELDS')
    if any(type(body[k]) is not type(v) or body[k] != v for k,v in expected.items()) or body['issuer_key'] != a.kid(public): raise Failure('RECEIPT_BINDING')
    for field in ('authority_epoch','sequence','inbox_commit_index'):
        if type(body[field]) is not int or not 1 <= body[field] <= a.MAX: raise Failure('RECEIPT_TYPE')
    a.unb64(body['status_nonce'] if kind == 'delivery-receipt' else body['request_nonce'],32)
    if not a.tag(body['receipt_id'], 'rc:', 64) or not a.tag(body['inbox_state_ref'], 'sha256:',64) or not retained.contains(body['inbox_commit_index'], body['inbox_state_ref']): raise Unknown('UNKNOWN_RECOVERY')
    return body  # historical evidence only; no authority mutation


def render_inbox(state):
    """Inert HTML fragment only: no script/tool/model dispatch exists in this lab."""
    return ''.join('<pre>'+html.escape(a.unb64(entry['payload_b64']).decode('utf-8'))+'</pre>' for entry in state['inbox'].values())

class ReceiptTracker:
    """Sender-side historical receipt classification, never an authority grant.
    Caller must verify_receipt first. This cache is not a recovery authority.
    """
    def __init__(self): self.seen = {}; self.highest = 0
    def observe(self, verified):
        rid = verified['receipt_id']; h = digest(a.canonical(verified))
        if rid in self.seen:
            if self.seen[rid] != h: raise Failure('RECEIPT_CONFLICT')
            return 'DUPLICATE_HISTORICAL'
        self.seen[rid] = h
        late = verified['inbox_commit_index'] < self.highest
        self.highest = max(self.highest, verified['inbox_commit_index'])
        return 'LATE_HISTORICAL' if late else 'RECORDED_HISTORICAL'
