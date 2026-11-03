"""Spawned synthetic signers. Human private key never crosses its Pipe.
OS/administrator compromise is outside this local-process isolation guarantee.
"""
import multiprocessing
from runtime import a, b64, sign, encode, digest, handle
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def worker(pipe, role):
    key = Ed25519PrivateKey.generate()
    pipe.send(b64(key.public_key().public_bytes_raw()))
    while True:
        try: command, body = pipe.recv()
        except EOFError: return
        if command == 'stop': return
        allowed = ('mandate','authority-event','authority-status') if role == 'Human' else ('envelope',)
        if command != 'sign' or body.get('kind') not in allowed:
            pipe.send({'error':'FORBIDDEN'}); continue
        pipe.send(sign(body, key))

class Signer:
    def __init__(self, role):
        ctx = multiprocessing.get_context('spawn'); parent, child = ctx.Pipe()
        self.process = ctx.Process(target=worker, args=(child, role)); self.process.start(); child.close()
        self.pipe = parent; self.public = parent.recv()
    def sign(self, body):
        self.pipe.send(('sign',body)); result = self.pipe.recv()
        if 'error' in result: raise ValueError(result['error'])
        return result
    def close(self):
        self.pipe.send(('stop',{})); self.process.join(5); self.pipe.close()
        if self.process.is_alive(): self.process.terminate(); self.process.join()

class Conversation:
    """Human test driver constructs explicit approval; Agent signs only the envelope.
    No automatic response mandate or economic action exists.
    """
    def __init__(self, recipient_node=None, recipient_agent=None, network=None):
        self.human, self.agent = Signer('Human'), Signer('Agent')
        self.now = 1800000000
        policy = __import__('json').loads((__import__('runtime').ROOT/'docs/protocol_0_1/conformance/lab-policy.json').read_text())
        self.common = dict(profile=a.PROFILE, network=network or handle('fd:'), policy_ref=digest(b'BOP/0.1-lab.1/POLICY\0'+a.canonical(policy)))
        self.identity, self.agent_id = handle('id:'), handle('ag:')
        self.node, self.recipient = recipient_node or handle('nd:'), recipient_agent or handle('ag:')
        self.genesis = digest(a.canonical(dict(identity=self.identity, owner_key=self.human.public, epoch=1, network=self.common['network'])))
        self.history = []; self.channel = handle('ch:'); self.grant = None
    def config(self, node_key):
        return dict(self.common, recipient_node=self.node, recipient_agent=self.recipient, owner_identity=self.identity, owner_key_b64=self.human.public, owner_epoch=1, genesis_head=self.genesis, node_key=a.kid(node_key.public_key().public_bytes_raw()))
    def transition(self, operation):
        body = dict(self.common, kind='authority-event', identity=self.identity, issuer_key=a.kid(a.unb64(self.human.public)), epoch=1, sequence=len(self.history)+1, previous=a.ref(self.history[-1]) if self.history else self.genesis, operation=operation, mandate_ref=a.ref(self.grant))
        result = self.human.sign(body); self.history.append(result); return result
    def issue(self):
        self.grant = self.human.sign(dict(self.common, kind='mandate', mandate_id=handle('md:',32), issuer=self.identity, issuer_key=a.kid(a.unb64(self.human.public)), subject=self.agent_id, subject_key=self.agent.public, recipient_agent=self.recipient, recipient_node=self.node, scope=['note.deliver'], object=self.channel, not_before=self.now-10, expires_at=self.now+600, allow_delegation=False, parent=None, limits=dict(max_uses=1,max_payload_bytes=2048), authority_epoch=1))
        self.transition('mandate.issue'); return self.grant
    def envelope(self, payload=b'Harmless synthetic note.', event_id=None):
        return self.agent.sign(dict(self.common, kind='envelope', type='note.deliver', event_id=event_id or handle('ev:',32), issuer=self.agent_id, issuer_key=a.kid(a.unb64(self.agent.public)), subject=self.identity, recipient_agent=self.recipient, recipient_node=self.node, scope=['note.deliver'], object=self.channel, mandate_ref=a.ref(self.grant), links=[], nonce=__import__('runtime').nonce(), issued_at=self.now-1, expires_at=self.now+60, payload_schema='text.utf8.v1', payload_b64=b64(payload), payload_hash=digest(payload)))
    def packet(self, env, nonce):
        from runtime import wire
        status = self.human.sign(dict(self.common, kind='authority-status', identity=self.identity, issuer_key=a.kid(a.unb64(self.human.public)), epoch=1, sequence=len(self.history), head=a.ref(self.history[-1]), mandate_ref=a.ref(self.grant), mandate_state='revoked' if self.history[-1]['body']['operation']=='mandate.revoke' else 'active', event_ref=a.ref(env), recipient_node=self.node, status_nonce=nonce, issued_at=self.now-1, expires_at=self.now+20))
        return dict(envelope_hex=wire(env), mandate_hex=wire(self.grant), authority_hex=[wire(x) for x in self.history], status_hex=wire(status))
    def close(self): self.human.close(); self.agent.close()
