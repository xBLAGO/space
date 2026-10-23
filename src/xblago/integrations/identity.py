"""Deterministic identity runtime. No LLM, fingerprinting or optional telemetry."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                     ResidentKeyRequirement, UserVerificationRequirement)

from xblago.core.storage import TelegramIdentity
from xblago.integrations.web import ApiError, verify_telegram_init_data


IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 604800
FRESH_SECONDS = 300
CHALLENGE_SECONDS = 300


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class IdentityRuntime:
    def __init__(self, store):
        self.store = store

    @property
    def enabled(self):
        from xblago.core.identity_runtime_schema import VERSION, CHECKSUM
        with self.store._connect() as db:
            present = db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_runtime_migrations'").fetchone() is not None
            if present:
                rows = [tuple(r) for r in db.execute('SELECT version,checksum FROM identity_runtime_migrations')]
                if rows != [(VERSION, CHECKSUM)]:
                    raise ApiError(503, 'Identity migration version mismatch')
            elif os.getenv('XBLAGO_REQUIRE_SECURE_IDENTITY', '').lower() in ('true','1','yes'):
                raise ApiError(503, 'Secure identity migration required')
            return present

    def require_enabled(self):
        if not self.enabled:
            raise ApiError(503, 'Secure identity requires the offline Foundation 05.2 migration')

    @staticmethod
    def active(db, uid):
        row = db.execute('SELECT status FROM users WHERE blago_user_id=?', (uid,)).fetchone()
        if row is None or row[0] != 'active':
            raise ApiError(401, 'Account is not active. Please sign in again')

    def mint(self, db, uid, method, old_token=None, authenticated_at=None):
        self.active(db, uid)
        now = int(time.time())
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        if old_token:
            db.execute('DELETE FROM web_sessions WHERE session_hash=?', (digest(old_token),))
        db.execute('''INSERT INTO web_sessions
            (session_hash, blago_user_id, csrf, expires_at, public_id, created_at, last_seen_at, authenticated_at, auth_method)
            VALUES (?,?,?,?,?,?,?,?,?)''', (digest(token), uid, csrf,
            datetime.fromtimestamp(now + ABSOLUTE_SECONDS, timezone.utc).isoformat(),
            secrets.token_hex(16), now, now, now if authenticated_at is None else authenticated_at, method))
        return token, csrf

    def new_session(self, uid, method='password', old_token=None):
        with self.store._locked_transaction() as db:
            return self.mint(db, uid, method, old_token)

    def session_row(self, db, token, *, fresh=False):
        now = int(time.time())
        row = db.execute('''SELECT s.* FROM web_sessions s JOIN users u USING(blago_user_id)
            WHERE s.session_hash=? AND u.status='active'
            AND s.last_seen_at > ? AND s.created_at > ? AND s.expires_at > ?''',
            (digest(token or ''), now-IDLE_SECONDS, now-ABSOLUTE_SECONDS,
             datetime.fromtimestamp(now, timezone.utc).isoformat())).fetchone()
        if row is None:
            raise ApiError(401, 'Session expired. Please sign in again')
        if fresh and (row['authenticated_at'] <= now-FRESH_SECONDS or row['auth_method'] == 'legacy'):
            raise ApiError(403, 'Подтвердите доступ повторным входом перед изменением способов доступа')
        return row

    def session(self, token):
        with self.store._locked_transaction() as db:
            row = self.session_row(db, token)
            db.execute('UPDATE web_sessions SET last_seen_at=? WHERE session_hash=?', (int(time.time()), digest(token)))
            return row['blago_user_id'], row['csrf']

    def require_fresh(self, token, uid=None):
        self.require_enabled()
        with self.store._locked_transaction() as db:
            row = self.session_row(db, token, fresh=True)
            if uid is not None and row['blago_user_id'] != uid:
                raise ApiError(403, 'Account mismatch')
            return row['blago_user_id']

    def sessions(self, token):
        self.require_enabled()
        with self.store._locked_transaction() as db:
            current = self.session_row(db, token)
            now = int(time.time())
            rows = db.execute('''SELECT public_id,created_at,last_seen_at,expires_at,auth_method FROM web_sessions
                WHERE blago_user_id=? AND last_seen_at>? AND created_at>? AND expires_at>? ORDER BY created_at''',
                (current['blago_user_id'], now-IDLE_SECONDS, now-ABSOLUTE_SECONDS,
                 datetime.fromtimestamp(now, timezone.utc).isoformat())).fetchall()
            return [{**dict(r), 'current': r['public_id'] == current['public_id']} for r in rows]

    def revoke(self, token, public_id=None, others=False):
        self.require_enabled()
        with self.store._locked_transaction() as db:
            row = self.session_row(db, token)
            if others:
                db.execute('DELETE FROM web_sessions WHERE blago_user_id=? AND session_hash<>?',
                           (row['blago_user_id'], row['session_hash']))
            else:
                changed = db.execute('DELETE FROM web_sessions WHERE blago_user_id=? AND public_id=?',
                                     (row['blago_user_id'], public_id)).rowcount
                if not changed:
                    raise ApiError(404, 'Session not found')

    def telegram_challenge(self, binding):
        self.require_enabled()
        challenge = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.store._locked_transaction() as db:
            db.execute('DELETE FROM telegram_exchanges WHERE expires_at<?', (now,))
            db.execute('DELETE FROM telegram_proofs WHERE expires_at<?', (now,))
            if db.execute('SELECT count(*) FROM telegram_exchanges').fetchone()[0] >= 2000:
                raise ApiError(429, 'Too many pending exchanges. Please retry later')
            db.execute('INSERT INTO telegram_exchanges VALUES (?,?,?,?,NULL)',
                       (digest(challenge), digest(binding), now, now+CHALLENGE_SECONDS))
        return challenge

    def telegram_login(self, init_data, challenge, binding, old_token=None):
        self.require_enabled()
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            raise ApiError(503, 'Telegram login is not configured')
        user = verify_telegram_init_data(init_data, bot_token)
        # Canonical signed hash, not raw query ordering/encoding (which an attacker can change).
        fields = dict(parse_qsl(init_data, keep_blank_values=True))
        proof = digest(fields['hash'].lower())
        now = int(time.time())
        try:
            with self.store.conversation_lock, self.store._locked_transaction() as db:
                resume = db.execute('SELECT telegram_resume_at FROM identity_recovery_state WHERE singleton=1').fetchone()
                if resume is None or resume[0] > now:
                    raise ApiError(503, 'Telegram exchange is temporarily paused after backup recovery; use another confirmed method')
                changed = db.execute('''UPDATE telegram_exchanges SET consumed_at=? WHERE challenge_hash=?
                    AND binding_hash=? AND consumed_at IS NULL AND created_at<=? AND expires_at>=?''',
                    (now, digest(challenge), digest(binding), now, now)).rowcount
                if not changed:
                    raise ApiError(409, 'Telegram exchange expired or already used. Open Mini App again')
                if db.execute('SELECT 1 FROM telegram_proofs WHERE proof_hash=?', (proof,)).fetchone():
                    raise ApiError(409, 'Telegram proof already exchanged. Reuse your session or reopen Mini App for fresh initData')
                uid = self.store._resolve_telegram_user(db, TelegramIdentity(user['id'], user['id'], 'private',
                    user.get('username'), user.get('first_name'), user.get('last_name')))
                db.execute('INSERT INTO telegram_proofs VALUES (?,?,?)',
                           (proof, uid, int(fields['auth_date'])+3600+60))
                return self.mint(db, uid, 'telegram', old_token)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, 'Telegram exchange already used') from exc

    @staticmethod
    def rp():
        origin = os.getenv('XBLAGO_PUBLIC_ORIGIN', '').rstrip('/')
        rp_id = os.getenv('XBLAGO_WEBAUTHN_RP_ID', '')
        parsed = urlsplit(origin)
        local = parsed.hostname in ('localhost', '127.0.0.1')
        if (not rp_id or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or (parsed.scheme != 'https' and not (local and parsed.scheme == 'http'))
                or not (parsed.hostname == rp_id or parsed.hostname.endswith('.'+rp_id))):
            raise ApiError(503, 'WebAuthn RP ID and trusted origin must be configured')
        return rp_id, origin

    def passkey_options(self, binding, token=None, register=False):
        self.require_enabled()
        rp_id, origin = self.rp()
        challenge = secrets.token_bytes(32)
        now = int(time.time())
        with self.store._locked_transaction() as db:
            # Cleanup is bounded to authentication state, never optional activity.
            db.execute('DELETE FROM auth_challenges WHERE expires_at<?', (now,))
            if db.execute('SELECT count(*) FROM auth_challenges').fetchone()[0] >= 2000:
                raise ApiError(429, 'Too many pending challenges. Please retry later')
            uid, handle = None, None
            if register:
                uid = self.session_row(db, token, fresh=True)['blago_user_id']
                credentials = db.execute('SELECT credential_id,user_handle FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL', (uid,)).fetchall()
                if len(credentials) >= 20:
                    raise ApiError(409, 'Maximum of 20 active passkeys per account')
                handle = credentials[0]['user_handle'] if credentials else secrets.token_bytes(32)
                options = generate_registration_options(rp_id=rp_id, rp_name='BLAGO',
                    user_id=handle, user_name='Участник BLAGO', challenge=challenge,
                    exclude_credentials=[PublicKeyCredentialDescriptor(id=r['credential_id']) for r in credentials],
                    authenticator_selection=AuthenticatorSelectionCriteria(
                        resident_key=ResidentKeyRequirement.REQUIRED,
                        user_verification=UserVerificationRequirement.REQUIRED))
            else:
                options = generate_authentication_options(rp_id=rp_id, challenge=challenge,
                    user_verification=UserVerificationRequirement.REQUIRED)
            db.execute('INSERT INTO auth_challenges VALUES (?,?,?,?,?,?,?,?,NULL)',
                (digest(challenge), uid, 'register_passkey' if register else 'authenticate',
                 digest(binding), rp_id, origin, now, now+CHALLENGE_SECONDS))
            if register:
                db.execute('INSERT INTO webauthn_pending VALUES (?,?)', (digest(challenge), handle))
        return json.loads(options_to_json(options))

    def passkey_verify(self, payload, binding, token=None, register=False):
        self.require_enabled()
        rp_id, origin = self.rp()
        try:
            challenge = base64url_to_bytes(payload['challenge'])
            credential = payload['credential']
            if not isinstance(credential, dict) or len(challenge) != 32:
                raise ValueError('Invalid credential')
            client = json.loads(base64url_to_bytes(credential['response']['clientDataJSON']))
            if client.get('crossOrigin', False) is not False:
                raise ValueError('Cross-origin WebAuthn is not supported')
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ApiError(400, 'Invalid WebAuthn payload') from exc
        now = int(time.time())
        try:
            with self.store._locked_transaction() as db:
                row = db.execute('SELECT * FROM auth_challenges WHERE challenge_hash=? AND binding_hash=? AND consumed_at IS NULL AND created_at<=? AND expires_at>=?',
                    (digest(challenge), digest(binding), now, now)).fetchone()
                purpose = 'register_passkey' if register else 'authenticate'
                if row is None or row['purpose'] != purpose or row['rp_id'] != rp_id or row['origin'] != origin:
                    raise ApiError(409, 'WebAuthn challenge expired or already used')
                if register:
                    session = self.session_row(db, token, fresh=True)
                    uid = session['blago_user_id']
                    if uid != row['blago_user_id']:
                        raise ApiError(403, 'Account mismatch')
                    if db.execute('SELECT count(*) FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL', (uid,)).fetchone()[0] >= 20:
                        raise ApiError(409, 'Maximum of 20 active passkeys per account')
                    verified = verify_registration_response(credential=credential, expected_challenge=challenge,
                        expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
                    handle = db.execute('SELECT user_handle FROM webauthn_pending WHERE challenge_hash=?', (digest(challenge),)).fetchone()[0]
                    db.execute('''INSERT INTO passkey_credentials VALUES (?,?,NULL,?,?,?,?,?,?,NULL)''',
                        (verified.credential_id, uid, verified.credential_public_key, handle, verified.sign_count,
                         int(verified.credential_device_type.value == 'multi_device'), int(verified.credential_backed_up), now))
                    method = session['auth_method']
                else:
                    cid = base64url_to_bytes(credential['id'])
                    record = db.execute('SELECT * FROM passkey_credentials WHERE credential_id=? AND revoked_at IS NULL', (cid,)).fetchone()
                    if record is None:
                        raise ApiError(401, 'Unable to verify passkey')
                    uid = record['blago_user_id']
                    self.active(db, uid)
                    response_handle = credential['response'].get('userHandle')
                    if not response_handle or not hmac.compare_digest(base64url_to_bytes(response_handle), record['user_handle']):
                        raise ApiError(401, 'Unable to verify passkey')
                    verified = verify_authentication_response(credential=credential, expected_challenge=challenge,
                        expected_rp_id=rp_id, expected_origin=origin, credential_public_key=record['public_key_cose'],
                        credential_current_sign_count=record['sign_count'], require_user_verification=True)
                    db.execute('UPDATE passkey_credentials SET sign_count=?,backup_state=? WHERE credential_id=?',
                               (verified.new_sign_count, int(verified.credential_backed_up), cid))
                    method = 'passkey'
                db.execute('UPDATE auth_challenges SET consumed_at=? WHERE challenge_hash=?', (now, digest(challenge)))
                return self.mint(db, uid, method, token, session['authenticated_at'] if register else None)
        except ApiError:
            raise
        except Exception as exc:
            # Library diagnostics, key bytes and SQL details must never reach clients.
            raise ApiError(400, 'Unable to verify passkey') from exc

    def methods(self, token):
        self.require_enabled()
        with self.store._locked_transaction() as db:
            uid = self.session_row(db, token)['blago_user_id']
            items = [{'kind': 'passkey', 'id': bytes_to_base64url(r[0])} for r in db.execute(
                'SELECT credential_id FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL', (uid,))]
            if db.execute('SELECT 1 FROM web_credentials WHERE blago_user_id=?', (uid,)).fetchone():
                items.append({'kind': 'password', 'id': 'password'})
            for row in db.execute("SELECT channel_user_id FROM channel_links WHERE blago_user_id=? AND channel='telegram' AND channel_user_id NOT LIKE 'chat:%'", (uid,)):
                items.append({'kind': 'telegram', 'id': digest('telegram:'+row[0])})
            return items

    def remove_method(self, token, kind, method_id):
        self.require_enabled()
        with self.store._locked_transaction() as db:
            session = self.session_row(db, token, fresh=True)
            uid = session['blago_user_id']
            # Fresh proof must use a different method: a stolen credential cannot remove recovery.
            if kind == session['auth_method']:
                raise ApiError(403, 'Подтвердите доступ другим ранее привязанным способом')
            counts = db.execute('''SELECT
                (SELECT count(*) FROM web_credentials WHERE blago_user_id=?) +
                (SELECT count(*) FROM channel_links WHERE blago_user_id=? AND channel='telegram' AND channel_user_id NOT LIKE 'chat:%') +
                (SELECT count(*) FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL)''', (uid,uid,uid)).fetchone()[0]
            if counts <= 1:
                raise ApiError(409, 'Нельзя удалить последний способ доступа')
            if kind == 'passkey':
                try:
                    cid = base64url_to_bytes(method_id)
                except Exception as exc:
                    raise ApiError(400, 'Invalid passkey ID') from exc
                changed = db.execute('UPDATE passkey_credentials SET revoked_at=? WHERE credential_id=? AND blago_user_id=? AND revoked_at IS NULL',
                                     (int(time.time()), cid, uid)).rowcount
            elif kind == 'password':
                changed = db.execute('DELETE FROM web_credentials WHERE blago_user_id=?', (uid,)).rowcount
            elif kind == 'telegram':
                link = next((r[0] for r in db.execute("SELECT channel_user_id FROM channel_links WHERE blago_user_id=? AND channel='telegram' AND channel_user_id NOT LIKE 'chat:%'", (uid,))
                             if hmac.compare_digest(digest('telegram:'+r[0]), str(method_id))), None)
                changed = db.execute("DELETE FROM channel_links WHERE channel='telegram' AND channel_user_id=? AND blago_user_id=?", (link, uid)).rowcount
            else:
                raise ApiError(400, 'Unknown access method')
            if not changed:
                raise ApiError(404, 'Access method not found')
            db.execute('DELETE FROM account_link_codes WHERE target_user_id=?', (uid,))
            db.execute('DELETE FROM auth_challenges WHERE blago_user_id=?', (uid,))
            db.execute('DELETE FROM web_sessions WHERE blago_user_id=? AND session_hash<>?', (uid, session['session_hash']))
            return self.mint(db, uid, session['auth_method'], token, session['authenticated_at'])

    def set_password(self, token, username, password):
        self.require_enabled()
        username = username.strip()
        if not 3 <= len(username) <= 32 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in username):
            raise ApiError(400, 'Username must be 3-32 letters, digits, _ or -')
        if not 12 <= len(password) <= 256:
            raise ApiError(400, 'Password must contain 12-256 characters')
        salt = secrets.token_hex(16)
        encoded = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 400_000).hex()
        try:
            with self.store._locked_transaction() as db:
                session = self.session_row(db, token, fresh=True)
                uid = session['blago_user_id']
                db.execute('''INSERT INTO web_credentials VALUES (?,?,?,?,?) ON CONFLICT(blago_user_id)
                    DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,salt=excluded.salt''',
                    (username, uid, encoded, salt, datetime.now(timezone.utc).isoformat()))
                db.execute('DELETE FROM account_link_codes WHERE target_user_id=?', (uid,))
                db.execute('DELETE FROM auth_challenges WHERE blago_user_id=?', (uid,))
                db.execute('DELETE FROM web_sessions WHERE blago_user_id=?', (uid,))
                return self.mint(db, uid, session['auth_method'], authenticated_at=session['authenticated_at'])
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, 'Username is unavailable') from exc

    def merge(self, db, source, target, revoke_sessions=True):
        """Called inside the existing account merge transaction, before legacy writes."""
        self.active(db, source)
        self.active(db, target)
        known_tables = {'profiles','channel_links','memory_facts','semantic_memory','dialogue_turns','events',
            'web_credentials','web_sessions','account_link_codes','blago_needs','blago_resources','blago_assets',
            'blago_resource_offers','blago_payment_requests','blago_payment_request_offers',
            'blago_fulfillment_events','blago_contribution_events','user_devices','passkey_credentials',
            'auth_challenges','activity_events','security_signals','consent_records','telegram_proofs'}
        for record in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = record[0]
            if name in known_tables:
                continue
            quoted = '"'+name.replace('"','""')+'"'
            for fk in db.execute(f'PRAGMA foreign_key_list({quoted})'):
                if fk[2] == 'users':
                    column = '"'+fk[3].replace('"','""')+'"'
                    if db.execute(f'SELECT 1 FROM {quoted} WHERE {column}=? LIMIT 1', (source,)).fetchone():
                        raise ApiError(409, 'Account merge requires support for an additional ownership table')
        db.execute('PRAGMA defer_foreign_keys=ON')
        db.execute('DELETE FROM account_link_codes WHERE target_user_id IN (?,?)', (source, target))
        db.execute('DELETE FROM auth_challenges WHERE blago_user_id IN (?,?)', (source, target))
        # Composite device/credential ownership is validated together at commit.
        for table in ('user_devices', 'passkey_credentials', 'activity_events', 'security_signals', 'consent_records'):
            db.execute(f'UPDATE {table} SET blago_user_id=? WHERE blago_user_id=?', (target, source))
        if revoke_sessions:
            db.execute('UPDATE telegram_proofs SET blago_user_id=? WHERE blago_user_id=?', (target, source))
            db.execute('DELETE FROM web_sessions WHERE blago_user_id IN (?,?)', (source, target))
