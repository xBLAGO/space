"""Owner-private memory drafts and recipient-pull delivery over the existing LIVING source.

No network transport, extra ledger, identity, schema or Help lifecycle. The local
adapter proves durable acceptance only in this SQLite/journal rollback domain.
"""
from contextlib import contextmanager
import json
import os
import re
import time
from uuid import NAMESPACE_URL, uuid5

from xblago.core import agent_context_schema
from xblago.core.security_journal import JournalUnavailable, canonical
from xblago.domain import living_deeds as ld
from xblago.integrations.web import ApiError

FLAG = 'XBLAGO_LIVING_AGENT_ENABLED'
DELIVERY_FLAG = 'XBLAGO_LIVING_DELIVERY_ENABLED'
FIELDS = {
    'grant_agent': {'agent', 'scope', 'permission_id', 'bridge_id', 'expires_at'},
    'revoke_agent': {'id'},
    'queue_invitation': {'mandate_id', 'bridge_id'},
    'cancel_delivery': {'id'},
    'close_silent_invitation': {'id'},
}

def enabled(flag):
    return os.getenv(flag, '').lower() in ('1', 'true', 'yes')


class AgentLivingDeeds(ld.LivingDeeds):
    """Human-authenticated commands; the model never receives a commit capability."""

    def view(self, token):
        # Listing is not opening. Never forward the legacy inbox's task,
        # referrer, topic or delivery state to an unopened recipient.
        result = super().view(token)
        result['items'] = [({'id': item['id'], 'kind': 'INVITATION',
                             'label': 'Локальное приглашение'}
                            if item['kind'] == 'INVITATION' else item)
                           for item in result['items']]
        return result

    @contextmanager
    def tx(self, token, destructive=False):
        if not enabled(FLAG):
            raise JournalUnavailable('Living agent adapter OFF')
        with super().tx(token, destructive) as tx:
            agent_context_schema.validate(tx[0], mandatory=True)
            if not agent_context_schema.requested():
                raise JournalUnavailable('Agent context OFF')
            yield tx

    def permission(self, db, uid, pid):
        r, p = self.root(db, pid, kind='NEED_PERMISSION')
        need = self.help._row(db, uid, p['need_id'])
        if not p['active'] or p['expires_at'] <= int(time.time()) or need['version'] != p['need_version'] or need['state'] not in ('PUBLISHED', 'OFFERED'):
            raise ApiError(409, 'Referral scope stale')
        return r, p, need

    def bridge_scope(self, db, uid, bid, *, invited=True):
        r, s = self.root(db, bid, kind='BRIDGE')
        if uid not in (r['owner_id'], s.get('recipient_id')):
            raise ApiError(404, 'Invitation unavailable')
        pr, p, need = self.permission(db, r['owner_id'], s['permission_id'])
        self.root(db, s['note_id'], r['owner_id'], 'NOTE')
        _, note = self.root(db, s['note_id'])
        if note['expires_at'] <= int(time.time()):
            raise ApiError(409, 'Private source expired')
        if invited and s['status'] != 'INVITED':
            raise ApiError(409, 'Invitation no longer active')
        if s.get('recipient_id'):
            denied = db.execute('SELECT 1 FROM living_contact_holds WHERE target_tag=?',
                (db.execute("SELECT blago_tag('living-no-contact',?,?,?)", (r['owner_id'], s['recipient_id'], p['topic'])).fetchone()[0],)).fetchone()
            if denied:
                raise ApiError(409, 'Contact suppressed')
            # Expiry already denies contact even before an explicit retention
            # sweep records SILENT_CLOSED. A new owner mandate cannot lift it.
            for old in db.execute("SELECT * FROM living_records WHERE owner_id=? AND kind='BRIDGE' AND id!=?", (r['owner_id'], r['id'])):
                prior = json.loads(old['state'])
                if prior.get('recipient_id') != s['recipient_id']:
                    continue
                _, old_permission = self.root(db, prior['permission_id'], kind='NEED_PERMISSION')
                if old_permission['topic'] == p['topic'] and prior['status'] == 'INVITED' and old_permission['expires_at'] <= int(time.time()):
                    raise ApiError(409, 'Prior unanswered contact expired; recipient reauthorization required')
        return r, s, pr, p, need

    def mandate(self, db, uid, mid, scope):
        r, s = self.root(db, mid, uid, 'AGENT_MANDATE')
        if not s['active'] or not s['issued_at'] <= int(time.time()) < s['expires_at'] or s['scope'] != scope:
            raise ApiError(403, 'Current scoped mandate required')
        pr, p, need = self.permission(db, uid, s['permission_id'])
        if pr['version'] != s['permission_version']:
            raise ApiError(409, 'Mandate source changed')
        refs = {'living:' + mid: r['version'], 'living:' + pr['id']: pr['version'], 'help:' + need['id']: need['version']}
        if scope == 'INVITE_ONCE':
            br, b, _, _, _ = self.bridge_scope(db, uid, s['bridge_id'])
            if br['owner_id'] != uid or br['version'] != s['bridge_version']:
                raise ApiError(409, 'Mandate invitation changed')
            refs['living:' + br['id']] = br['version']
        return r, s, refs

    @staticmethod
    def memory_source(db, uid, key, journal):
        if type(key) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', key):
            raise ApiError(400, 'Bounded memory key required')
        row = db.execute('SELECT value_json FROM memory_facts WHERE blago_user_id=? AND key=?', (uid, key)).fetchone()
        if not row:
            raise ApiError(404, 'Memory unavailable')
        # A stored claim is not independent proof. A candidate/inactive semantic
        # record must never be promoted back into a current confirmed fact.
        records = db.execute('SELECT value_json,status FROM semantic_memory WHERE blago_user_id=? AND memory_key=?', (uid, key)).fetchall()
        if records and not any(r['value_json'] == row[0] and r['status'] == 'confirmed' for r in records):
            raise ApiError(409, 'Memory not current')
        revision = list(db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?', (uid,)).fetchone())
        tag = journal.tag('living-memory-source-2', uid, key, row[0], canonical(revision))
        return json.loads(row[0]), tag

    def candidates(self, token, mandate_id, query, *, model_label=None, agent_id='personal-agent'):
        """Local owner-only lookup, no publication, invitations or persistent copy.

        model_label is deliberately not an authorization input. No LLM is called.
        """
        self.text(query)
        with self.tx(token) as (db, uid, c):
            _, mandate, _ = self.mandate(db, uid, mandate_id, 'MATCH_PRIVATE')
            if agent_id != mandate['agent']:
                raise ApiError(403, 'Mandate agent mismatch')
            items = []
            for row in db.execute('SELECT key FROM memory_facts WHERE blago_user_id=? ORDER BY key LIMIT 128', (uid,)):
                try:
                    value, tag = self.memory_source(db, uid, row[0], c.journal)
                except ApiError:
                    continue
                if query.casefold() in canonical(value).casefold():
                    items.append({'kind': 'MEMORY_DRAFT', 'memory_key': row[0], 'value': value,
                                  'source_tag': tag, 'evidence': 'STORED_PRIVATE_CLAIM'})
            for row in db.execute("SELECT * FROM living_records WHERE owner_id=? AND kind='NOTE' ORDER BY id LIMIT 128", (uid,)):
                state = json.loads(row['state'])
                if state['expires_at'] > int(time.time()) and query.casefold() in state['text'].casefold():
                    items.append({'kind': 'PRIVATE_NOTE_DRAFT', 'note_id': row['id'], 'version': row['version'], 'text': state['text']})
            return {'owner_only': True, 'published': False, 'sent': False, 'items': items}

    def _extra_review(self, db, uid, c, action, data):
        if action not in FIELDS:
            return {}
        review = {'synthetic_only': True, 'external_delivery': False}
        if action in ('grant_agent', 'queue_invitation'):
            if action == 'grant_agent':
                mandate = data
            else:
                _, mandate = self.root(db, data['mandate_id'], uid, 'AGENT_MANDATE')
            review.update(agent=mandate['agent'], scope=mandate['scope'], expires_at=mandate['expires_at'],
                          reads_owner_private_memory=mandate['scope'] == 'MATCH_PRIVATE')
            _, permission, _ = self.permission(db, uid, mandate['permission_id'])
            review['task'] = permission['invitation_text']
            if mandate['bridge_id']:
                _, bridge = self.root(db, mandate['bridge_id'], uid, 'BRIDGE')
                review.update(recipient=c.journal.tag('help-member', bridge['recipient_id']),
                              disclose_referrer=c.journal.tag('help-member', uid),
                              publishes_resource=False, contact_only=True)
        return review

    def _validate(self, db, uid, c, action, d):
        if type(action) is not str:
            raise ApiError(400, 'Action name required')
        if action not in FIELDS:
            return super()._validate(db, uid, c, action, d)
        if type(d) is not dict or set(d) != FIELDS[action]:
            raise ApiError(400, 'Exact reviewed fields required')
        now = int(time.time())
        if 'expires_at' in d and (type(d['expires_at']) is not int or not now < d['expires_at'] <= now + 604800):
            raise ApiError(400, 'Bounded future expiry required')
        if action == 'grant_agent':
            if type(d['agent']) is not str or not re.fullmatch('[a-z][a-z0-9_-]{2,39}', d['agent']) or d['scope'] not in ('MATCH_PRIVATE', 'INVITE_ONCE'):
                raise ApiError(400, 'Bounded agent and explicit scope required')
            pr, p, need = self.permission(db, uid, d['permission_id'])
            if d['expires_at'] > p['expires_at']:
                raise ApiError(400, 'Mandate exceeds permission lifetime')
            refs = {'living:' + pr['id']: pr['version'], 'help:' + need['id']: need['version']}
            if d['scope'] == 'INVITE_ONCE':
                br, s, _, _, _ = self.bridge_scope(db, uid, d['bridge_id'])
                if br['owner_id'] != uid or s['permission_id'] != pr['id']:
                    raise ApiError(403, 'Owner-authorized invitation required')
                refs['living:' + br['id']] = br['version']
            elif d['bridge_id'] is not None:
                raise ApiError(400, 'Read mandate has no invitation scope')
            return refs
        if action == 'queue_invitation':
            if not enabled(DELIVERY_FLAG):
                raise JournalUnavailable('Local delivery OFF')
            _, m, refs = self.mandate(db, uid, d['mandate_id'], 'INVITE_ONCE')
            if m['bridge_id'] != d['bridge_id']:
                raise ApiError(403, 'Mandate scope mismatch')
            # One invitation consumes one mandate, even with different keys.
            for row in db.execute("SELECT state FROM living_records WHERE kind='INVITATION_DELIVERY'"):
                if json.loads(row[0])['mandate_id'] == d['mandate_id']:
                    raise ApiError(409, 'Mandate already queued')
            return refs
        if action == 'revoke_agent':
            r, _ = self.root(db, d['id'], uid, 'AGENT_MANDATE')
        elif action == 'cancel_delivery':
            r, _ = self.root(db, d['id'], uid, 'INVITATION_DELIVERY')
        else:
            r, s = self.root(db, d['id'], uid, 'BRIDGE')
            _, p = self.root(db, s['permission_id'], kind='NEED_PERMISSION')
            if s['status'] != 'INVITED' or now < p['expires_at']:
                raise ApiError(409, 'Only expired unanswered invitations can close as silence')
        return {'living:' + r['id']: r['version']}

    def _execute(self, db, uid, c, action, d, key, token):
        if action not in FIELDS:
            return super()._execute(db, uid, c, action, d, key, token)
        rid = str(uuid5(NAMESPACE_URL, 'living:' + uid + ':' + key))
        if db.execute('SELECT 1 FROM living_holds WHERE target_tag=?', (c.journal.tag('living-record', rid),)).fetchone():
            raise ApiError(409, 'Retired command')
        s = dict(d)
        if action == 'grant_agent':
            pr, _ = self.root(db, d['permission_id'])
            s.update(active=True, issued_at=int(time.time()), permission_version=pr['version'])
            if d['bridge_id']:
                br, _ = self.root(db, d['bridge_id'])
                s['bridge_version'] = br['version']
            kind, event = 'AGENT_MANDATE', 'AgentMandateGranted'
        elif action == 'queue_invitation':
            s.update(status='QUEUED', receipt=None)
            kind, event = 'INVITATION_DELIVERY', 'InvitationQueued'
        elif action in ('revoke_agent', 'cancel_delivery'):
            rid = d['id']; _, s = self.root(db, rid, uid)
            if action == 'revoke_agent':
                s['active'] = False; event = 'AgentMandateRevoked'
            else:
                s['status'] = 'CANCELLED'; event = 'InvitationDeliveryCancelled'
            ld.update(db, c, rid, s, event)
            return rid
        else:
            rid = d['id']; r, s = self.root(db, rid, uid, 'BRIDGE')
            _, p = self.root(db, s['permission_id'], kind='NEED_PERMISSION')
            s['status'] = 'SILENT_CLOSED'
            deny = c.journal.tag('living-no-contact', uid, s['recipient_id'], p['topic'])
            db.execute('INSERT OR IGNORE INTO living_contact_holds VALUES (?)', (deny,))
            event = {'kind': 'living_contact_deny', 'owner': '', 'target': deny, 'state': 'DENY', 'recorded_at': 0}
            db.execute('INSERT INTO privacy_outbox VALUES (?,?)', (rid + ':silence', canonical(event)))
            ld.update(db, c, rid, s, 'DeclinedOrWithdrawn')
            return rid
        if db.execute('SELECT count(*) FROM living_records').fetchone()[0] >= 256:
            raise ApiError(429, 'Synthetic record limit')
        db.execute('INSERT INTO living_records VALUES (?,?,?,?,?,?)', (rid, uid, kind, 1, canonical(s), ''))
        ld.emit(db, c, rid, event)
        return rid

    def receive(self, recipient_token, delivery_id):
        """Authenticated local pull. Return only AFTER SQL + witness commit.

        Late/replayed pulls revalidate live consent before revealing any content.
        Receipt is local delivery evidence, never a new human authorization.
        """
        if not enabled(DELIVERY_FLAG):
            raise JournalUnavailable('Local delivery OFF')
        with self.tx(recipient_token) as (db, uid, c):
            r, s = self.root(db, delivery_id, kind='INVITATION_DELIVERY')
            br, b, _, p, _ = self.bridge_scope(db, uid, s['bridge_id'])
            if b['recipient_id'] != uid:
                raise ApiError(404, 'Invitation unavailable')
            self.mandate(db, r['owner_id'], s['mandate_id'], 'INVITE_ONCE')
            if s['status'] not in ('QUEUED', 'ACKED'):
                raise ApiError(409, 'Delivery closed')
            if s['status'] == 'QUEUED':
                s['status'] = 'ACKED'
                s['receipt'] = c.journal.tag('living-local-receipt-2', r['id'], br['id'], str(br['version']), s['mandate_id'])
                ld.update(db, c, r['id'], s, 'InvitationAcceptedByInbox')
            result = {'format': 'living-local-invitation-2', 'receipt': s['receipt'],
                      'task': p['invitation_text'], 'referrer': c.journal.tag('help-member', br['owner_id']),
                      'contact_only': True, 'resource_available': False}
        return result


class LocalPullAdapter:
    """Explicit local fixture adapter. No external routing or network credentials."""
    def __init__(self, service):
        self.service = service

    def deliver(self, recipient_session, opaque_delivery_id):
        return self.service.receive(recipient_session, opaque_delivery_id)
