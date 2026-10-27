"""Test-only Sovereign Identity domain. Existing sessions authenticate; human keys authorize.

No signing secrets, chain clients, biometric templates, LLMs or person matching.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from nacl.bindings import crypto_core_ed25519_is_valid_point

from xblago.core.sovereign_schema import enabled,validate
from xblago.core.privacy_control import PrivacyControl
from xblago.core.sqlite_safety import require_safe_sqlite
from xblago.core.security_journal import canonical
from xblago.integrations.web import ApiError
from xblago.integrations.identity import digest,FRESH_SECONDS

POLICY='07.1-test-v1'
PROTOCOL='blago-identity-proof-v1'
TTL=120


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def decode(value,length=None):
    if not isinstance(value,str) or not 1<=len(value)<=8192:
        raise ApiError(400,'Invalid encoded proof')
    try:
        data=base64.b64decode(value+'='*((-len(value))%4),altchars=b'-_',validate=True)
    except (ValueError,TypeError) as exc:
        raise ApiError(400,'Invalid encoded proof') from exc
    if b64(data)!=value or (length is not None and len(data)!=length):
        raise ApiError(400,'Invalid proof length or encoding')
    return data


def verify_signature(public_key,signature,message):
    if not crypto_core_ed25519_is_valid_point(public_key):
        raise ApiError(400,'Invalid controller public key')
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(decode(signature,64),message)
    except (InvalidSignature,ValueError) as exc:
        raise ApiError(403,'Unable to verify controller proof') from exc


def verify_independent_proof(public_key,signature,message,*,audience,expected_nonce,consume_nonce,now=None):
    """L1 signature/possession only, not membership/current revocation/unique human.

    The recipient must pin its expected nonce BEFORE receiving the signature and
    provide an atomic durable consume callback. No stateless bearer-proof default.
    """
    raw=decode(message)
    try:
        data=json.loads(raw)
        stamp=int(time.time()) if now is None else now
        if (set(data)!={'protocol','nonce','audience','context','action','commitment','issued_at','expires_at'}
                or canonical(data).encode()!=raw or data['protocol']!=PROTOCOL or data['action']!='prove'
                or data['context']!='offline-possession' or data['audience']!=audience
                or type(data['issued_at']) is not int or type(data['expires_at']) is not int
                or not data['issued_at']<=stamp<=data['expires_at']<=data['issued_at']+TTL):
            raise ValueError('Invalid possession context')
        if decode(data['nonce'],32)!=decode(expected_nonce,32) or not callable(consume_nonce):
            raise ValueError('Unexpected verifier challenge')
        if not isinstance(data['commitment'],str) or len(data['commitment'])!=64:
            raise ValueError('Invalid commitment')
    except (ValueError,TypeError,KeyError) as exc:
        raise ApiError(400,'Invalid possession envelope') from exc
    verify_signature(decode(public_key,32),signature,raw)
    if consume_nonce(expected_nonce,audience) is not True:
        raise ApiError(409,'Verifier challenge already used or not issued')
    return {'key_possession':True,'membership_attested':False,'person_uniqueness_attested':False}


@dataclass(frozen=True)
class WalletDescriptor:
    chain: str
    network: str
    address: str
    role: str


class WalletOwnershipAdapter(Protocol):
    """Future 07.2 interface: no implementation or network dispatch in 07.1."""
    chain: str
    def verification_message(self,wallet: WalletDescriptor,nonce: bytes) -> bytes: ...
    def verify(self,wallet: WalletDescriptor,message: bytes,signature: bytes) -> bool: ...


class SolanaWalletInterface:
    chain='solana'
    def verification_message(self,*args,**kwargs):
        raise NotImplementedError('Wallet ownership ceremony is reserved for reviewed Foundation 07.2')
    def verify(self,*args,**kwargs):
        raise NotImplementedError('No wallet verifier, RPC, seed generation or transactions in 07.1')


class SovereignIdentity:
    def __init__(self,app):
        self.app,self.store,self.runtime=app,app.store,app.identity

    @contextmanager
    def _transaction(self,token,*,fresh=True):
        if not enabled():
            raise ApiError(404,'Identity Core is disabled')
        if os.getenv('XBLAGO_IDENTITY_CORE_MODE')!='isolated_test':
            raise ApiError(503,'Identity Core requires explicit isolated_test mode; production use is unavailable')
        require_safe_sqlite()
        self.runtime.require_enabled()
        try:
            with self.store._locked_transaction() as db:
                validate(db,required=True)
                control=PrivacyControl.open(db)
                if control is None or control.journal.witness_directory is None:
                    raise ApiError(503,'Original privacy journal and independent witness required')
                from xblago.core.pilot_schema import validate as validate_pilot
                validate_pilot(db,required=True)
                session=self.runtime.session_row(db,token,fresh=fresh)
                yield db,session
        except sqlite3.IntegrityError as exc:
            raise ApiError(409,'Identity operation conflicts with retained state; nothing was committed') from exc

    @staticmethod
    def _root(db,uid,*,operational=True):
        root=db.execute('SELECT * FROM sovereign_identities WHERE blago_user_id=?',(uid,)).fetchone()
        hold=db.execute('SELECT 1 FROM sovereign_recovery_holds WHERE blago_user_id=?',(uid,)).fetchone()
        if operational and hold:
            raise ApiError(409,'Identity recovery requires reviewed original control state; no new rights can be issued')
        if root is None:
            raise ApiError(404,'Identity Core not created')
        if operational and root['status'] in ('suspended','revoked'):
            raise ApiError(409,'Identity is suspended or revoked; session access cannot replace a human controller')
        if operational and root['status']=='active' and not db.execute("SELECT 1 FROM identity_controllers WHERE identity_id=? AND status='active' AND role='owner'",(root['identity_id'],)).fetchone():
            raise ApiError(409,'No active owner; identity authority is suspended')
        return root

    @staticmethod
    def _event(db,root,action):
        db.execute('INSERT INTO identity_events VALUES (?,?,?,?,?,?)',
            (secrets.token_hex(32),root['identity_id'],root['blago_user_id'],action,root['version'],int(time.time())))

    def create(self,token,confirmation,policy_version):
        if confirmation!='CREATE_TEST_IDENTITY' or policy_version!=POLICY:
            raise ApiError(400,'Explicit test-only identity consent required')
        with self._transaction(token) as (db,session):
            uid=session['blago_user_id']
            if db.execute('SELECT 1 FROM sovereign_recovery_holds WHERE blago_user_id=?',(uid,)).fetchone():
                raise ApiError(409,'Missing identity state cannot be replaced by a new identity')
            root=db.execute('SELECT * FROM sovereign_identities WHERE blago_user_id=?',(uid,)).fetchone()
            if root is None:
                identity_id=secrets.token_hex(32)
                db.execute("INSERT INTO sovereign_identities VALUES (?,?,'pending',1,?)",(identity_id,uid,int(time.time())))
                db.execute("INSERT INTO identity_consents VALUES (?,?,?,'isolated_identity_test',?,'granted',?)",(secrets.token_hex(32),identity_id,uid,POLICY,int(time.time())))
                root=self._root(db,uid)
                self._event(db,root,'created')
            return {'identity_id':root['identity_id'],'status':self._snapshot(db,root)['status'],'version':root['version'],'test_only':True}

    def capabilities(self,token):
        with self._transaction(token,fresh=False) as (db,session):
            uid=session['blago_user_id']
            root=db.execute('SELECT * FROM sovereign_identities WHERE blago_user_id=?',(uid,)).fetchone()
            held=bool(db.execute('SELECT 1 FROM sovereign_recovery_holds WHERE blago_user_id=?',(uid,)).fetchone())
            owner=bool(root and db.execute("SELECT 1 FROM identity_controllers WHERE identity_id=? AND status='active' AND role='owner'",(root['identity_id'],)).fetchone())
            active=bool(root and root['status']=='active' and not held and owner)
            return {'enabled':True,'test_only':True,'account_authenticated':True,'key_control_configured':active,
                    'can_request_key_proof':active,'recovery_required':held,
                    'local_user_verification':session['auth_method']=='passkey' and session['authenticated_at']>int(time.time())-FRESH_SECONDS,
                    'unique_human_verified':False,'semantic_memory_is_auth_factor':False,'wallet_operations':False}

    def inspect(self,token):
        with self._transaction(token,fresh=False) as (db,session):
            root=self._root(db,session['blago_user_id'],operational=False)
            return self._snapshot(db,root)

    @staticmethod
    def _snapshot(db,root):
        records=db.execute('SELECT controller_id,public_key,role,status,created_at,revoked_at FROM identity_controllers WHERE identity_id=? ORDER BY created_at,controller_id',(root['identity_id'],)).fetchall()
        held=bool(db.execute('SELECT 1 FROM sovereign_recovery_holds WHERE blago_user_id=?',(root['blago_user_id'],)).fetchone())
        status='recovery_required' if held else ('suspended' if root['status']=='active' and not any(r['role']=='owner' and r['status']=='active' for r in records) else root['status'])
        return {'identity_id':root['identity_id'],'status':status,'version':root['version'],'test_only':True,'recovery_required':held,
                'controllers':[{**dict(r),'public_key':b64(r['public_key'])} for r in records],
                'wallet_links':[],'wallet_interface_only':True}

    def challenge(self,token,action,authorizer=None,new_public_key=None,role='owner',target=None):
        if action not in ('bootstrap','add','rotate','revoke','prove'):
            raise ApiError(400,'Unsupported identity action')
        if role not in ('owner','proof'):
            raise ApiError(400,'Unsupported controller role')
        key=decode(new_public_key,32) if action in ('bootstrap','add','rotate') else None
        if key is not None and not crypto_core_ed25519_is_valid_point(key):
            raise ApiError(400,'Invalid controller public key')
        if action not in ('bootstrap','add','rotate') and new_public_key is not None:
            raise ApiError(400,'Unexpected new key')
        with self._transaction(token) as (db,session):
            root=self._root(db,session['blago_user_id'])
            if action=='bootstrap':
                if authorizer is not None or target is not None or role!='owner' or root['status']!='pending' or db.execute('SELECT 1 FROM identity_controllers WHERE identity_id=?',(root['identity_id'],)).fetchone():
                    raise ApiError(409,'Bootstrap is only available for a new, never-controlled identity')
            else:
                self._controller(db,root,authorizer,owner=action!='prove')
                if root['status']!='active':
                    raise ApiError(409,'An active controller is required')
            if action in ('rotate','revoke'):
                self._controller(db,root,target)
            elif target is not None:
                raise ApiError(400,'Unexpected controller target')
            if key is not None and db.execute('SELECT 1 FROM identity_controllers WHERE identity_id=? AND public_key=?',(root['identity_id'],key)).fetchone():
                raise ApiError(409,'Key already registered; revoked keys cannot be reused')
            now=int(time.time())
            db.execute('DELETE FROM sovereign_challenges WHERE expires_at<?',(now,))
            if db.execute('SELECT count(*) FROM sovereign_challenges WHERE blago_user_id=? AND consumed_at IS NULL',(session['blago_user_id'],)).fetchone()[0]>=20:
                raise ApiError(429,'Too many pending identity proofs')
            _,audience=self.runtime.rp()
            nonce=secrets.token_bytes(32)
            payload=canonical({'authorizer':authorizer,'target':target,'new_key':b64(key) if key else None,'role':role})
            message=canonical({'protocol':PROTOCOL,'nonce':b64(nonce),'audience':audience,
                'context':'offline-possession' if action=='prove' else 'identity-management','action':action,
                'commitment':hashlib.sha256(nonce+payload.encode()+str(root['version']).encode()).hexdigest(),
                'issued_at':now,'expires_at':now+TTL}).encode()
            handle=b64(secrets.token_bytes(32))
            db.execute('INSERT INTO sovereign_challenges VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                (digest(handle),root['identity_id'],session['blago_user_id'],digest(token),action,payload,message,root['version'],now,now+TTL))
            return {'challenge':handle,'message':b64(message),'action':action,'expires_in_seconds':TTL,'test_only':True,
                    'private_signing_details':{**json.loads(payload),'expected_version':root['version']}}

    @staticmethod
    def _controller(db,root,controller_id,owner=False):
        if not isinstance(controller_id,str) or len(controller_id)>128:
            raise ApiError(400,'A controller reference is required')
        record=db.execute("SELECT * FROM identity_controllers WHERE controller_id=? AND identity_id=? AND blago_user_id=? AND status='active'",(controller_id,root['identity_id'],root['blago_user_id'])).fetchone()
        if record is None or (owner and record['role']!='owner'):
            raise ApiError(403,'Controller has no authority for this operation')
        return record

    def _command(self,proof):
        """The command executed must be exactly the context the human signed."""
        try:
            data=json.loads(proof['payload']);message=json.loads(proof['message'])
            _,audience=self.runtime.rp()
            if (not isinstance(data,dict) or set(data)!={'authorizer','target','new_key','role'}
                    or canonical(data)!=proof['payload'] or not isinstance(message,dict)
                    or set(message)!={'protocol','nonce','audience','context','action','commitment','issued_at','expires_at'}
                    or canonical(message).encode()!=proof['message'] or message['protocol']!=PROTOCOL
                    or message['audience']!=audience or message['action']!=proof['action']
                    or message['context']!=('offline-possession' if proof['action']=='prove' else 'identity-management')
                    or message['issued_at']!=proof['created_at'] or message['expires_at']!=proof['expires_at']):
                raise ValueError('Stored proof context differs')
            commitment=hashlib.sha256(decode(message['nonce'],32)+proof['payload'].encode()+str(proof['expected_version']).encode()).hexdigest()
            if not hmac.compare_digest(commitment,message['commitment']):
                raise ValueError('Stored action commitment differs')
            return data
        except (ValueError,TypeError,KeyError) as exc:
            raise ApiError(409,'Identity challenge context is invalid; no action was performed') from exc

    def complete(self,token,challenge,signature=None,new_key_signature=None,confirmation=None):
        decode(challenge,32)
        with self._transaction(token) as (db,session):
            root=self._root(db,session['blago_user_id'])
            now=int(time.time())
            proof=db.execute('''SELECT * FROM sovereign_challenges WHERE challenge_hash=? AND identity_id=?
                AND blago_user_id=? AND session_hash=? AND consumed_at IS NULL AND created_at<=? AND expires_at>=?''',
                (digest(challenge),root['identity_id'],session['blago_user_id'],digest(token),now,now)).fetchone()
            if proof is None or proof['expected_version']!=root['version']:
                raise ApiError(409,'Identity proof expired, superseded, used or belongs to another session')
            action=proof['action'];data=self._command(proof)
            if confirmation!='CONFIRM_'+action.upper():
                raise ApiError(400,'Explicit confirmation must match the signed identity action')
            if action=='bootstrap':
                if root['status']!='pending' or db.execute('SELECT 1 FROM identity_controllers WHERE identity_id=?',(root['identity_id'],)).fetchone():
                    raise ApiError(409,'Bootstrap already completed or unavailable')
            else:
                authorizer=self._controller(db,root,data['authorizer'],owner=action!='prove')
                if root['status']!='active':
                    raise ApiError(409,'Active controller required')
                verify_signature(authorizer['public_key'],signature,proof['message'])
            new_id=None
            if action in ('bootstrap','add','rotate'):
                key=decode(data['new_key'],32)
                verify_signature(key,new_key_signature,proof['message'])
                new_id=secrets.token_hex(32)
                db.execute("INSERT INTO identity_controllers VALUES (?,?,?,'Ed25519',?,?,'active',?,NULL)",
                    (new_id,root['identity_id'],root['blago_user_id'],key,data['role'],now))
            if action in ('rotate','revoke'):
                target=self._controller(db,root,data['target'])
                db.execute("UPDATE identity_controllers SET status='revoked',revoked_at=? WHERE controller_id=?",(now,target['controller_id']))
            db.execute('UPDATE sovereign_challenges SET consumed_at=? WHERE challenge_hash=? AND consumed_at IS NULL',(now,digest(challenge)))
            if action!='prove':
                active=db.execute("SELECT 1 FROM identity_controllers WHERE identity_id=? AND status='active' AND role='owner'",(root['identity_id'],)).fetchone()
                db.execute('UPDATE sovereign_identities SET status=?,version=version+1 WHERE identity_id=?',('active' if active else 'suspended',root['identity_id']))
            root=self._root(db,session['blago_user_id'],operational=False)
            self._event(db,root,action)
            result={'ok':True,'action':action,'version':root['version'],'controller_id':new_id,'test_only':True}
            if action=='prove':
                result['proof']={'public_key':b64(authorizer['public_key']),'message':b64(proof['message']),'signature':signature,
                                 'scope':'L1 key possession only; no membership, person uniqueness or public identity handle'}
            return result

    def withdraw(self,token,confirmation):
        if confirmation!='WITHDRAW_TEST_IDENTITY':
            raise ApiError(400,'Explicit withdrawal confirmation required')
        with self._transaction(token) as (db,session):
            root=self._root(db,session['blago_user_id'],operational=False)
            if root['status']!='revoked':
                now=int(time.time())
                db.execute("UPDATE identity_controllers SET status='revoked',revoked_at=? WHERE identity_id=? AND status='active'",(now,root['identity_id']))
                db.execute("UPDATE sovereign_identities SET status='revoked',version=version+1 WHERE identity_id=?",(root['identity_id'],))
                db.execute("INSERT INTO identity_consents VALUES (?,?,?,'isolated_identity_test',?,'withdrawn',?)",(secrets.token_hex(32),root['identity_id'],root['blago_user_id'],POLICY,now))
                db.execute('DELETE FROM sovereign_challenges WHERE identity_id=?',(root['identity_id'],))
                self._event(db,self._root(db,session['blago_user_id'],operational=False),'withdraw')
            return {'withdrawn':True,'test_only':True}
