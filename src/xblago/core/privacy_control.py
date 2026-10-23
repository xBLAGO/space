"""Write-ahead deletion/revocation reconciliation for the shared SQLite store."""
from __future__ import annotations

import json
import os
import sqlite3
from uuid import uuid5,NAMESPACE_URL
from pathlib import Path

from xblago.core.security_journal import JournalUnavailable, SecurityJournal


class DeletionBlocked(ValueError):
    pass


OWNED = ('memory_facts','semantic_memory','dialogue_turns','events','profiles','channel_links',
         'web_sessions','web_credentials','account_link_codes','passkey_credentials','user_devices',
         'auth_challenges','telegram_proofs','activity_events','security_signals','consent_records',
         'blago_needs','blago_resources','blago_assets','blago_resource_offers','migrations','privacy_delete_challenges')
ECONOMIC = {'blago_payment_requests':('requester_user_id','executor_user_id'),
            'blago_payment_request_offers':('offered_user_id',),
            'blago_fulfillment_events':('actor_user_id',),
            'blago_contribution_events':('contributor_user_id','recipient_user_id')}


def deletion_blockers(db, uid):
    blockers = []
    for table,columns in ECONOMIC.items():
        if db.execute(f'SELECT 1 FROM {table} WHERE '+ ' OR '.join(f'{c}=?' for c in columns)+' LIMIT 1', (uid,)*len(columns)).fetchone():
            blockers.append(table)
    # Also reject references whose recorded actor differs from the owned need/offer.
    for table,column,parent in (('blago_payment_requests','need_id','blago_needs'),
                                ('blago_fulfillment_events','need_id','blago_needs'),
                                ('blago_payment_request_offers','resource_offer_id','blago_resource_offers')):
        if db.execute(f'SELECT 1 FROM {table} WHERE {column} IN (SELECT id FROM {parent} WHERE blago_user_id=?) LIMIT 1',(uid,)).fetchone():
            blockers.append(table)
    known = set(OWNED) | set(ECONOMIC) | {'users','privacy_aliases'}
    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        name = row[0]
        if name in known:
            continue
        quoted = '"'+name.replace('"','""')+'"'
        for fk in db.execute(f'PRAGMA foreign_key_list({quoted})'):
            if fk[2] == 'users':
                column = '"'+fk[3].replace('"','""')+'"'
                if db.execute(f'SELECT 1 FROM {quoted} WHERE {column}=? LIMIT 1',(uid,)).fetchone():
                    blockers.append('unreviewed_ownership_table')
    return sorted(set(blockers))


def purge_user(db, uid):
    if deletion_blockers(db,uid):
        raise DeletionBlocked('Economic/unknown ownership retention requires review before account deletion')
    for table in OWNED:
        column = 'target_user_id' if table=='account_link_codes' else 'blago_user_id'
        db.execute(f'DELETE FROM {table} WHERE {column}=?',(uid,))
    db.execute('DELETE FROM privacy_aliases WHERE source_uid=? OR target_uid=?',(uid,uid))
    db.execute("UPDATE users SET status='deleted',created_at='' WHERE blago_user_id=?",(uid,))


class PrivacyControl:
    @classmethod
    def open(cls, db):
        present = db.execute("SELECT 1 FROM sqlite_master WHERE name='privacy_state'").fetchone()
        if not present:
            if (os.getenv('XBLAGO_SECURITY_JOURNAL_DIR','') or
                    os.getenv('XBLAGO_REQUIRE_PRIVACY_JOURNAL','').lower() in ('true','1','yes')):
                raise JournalUnavailable('Privacy migration required')
            return None
        directory = os.getenv('XBLAGO_SECURITY_JOURNAL_DIR','')
        if not directory:
            raise JournalUnavailable('Independent security journal must be configured')
        return cls(db,SecurityJournal(Path(directory)))

    def __init__(self, db, journal):
        self.db, self.journal = db,journal
        self.replaying = False
        db.create_function('blago_tag',-1,journal.tag, deterministic=True)
        db.create_function('blago_replaying',0,lambda:int(self.replaying))
        from xblago.core.privacy_schema import VERSION,CHECKSUM,DDL
        rows = [tuple(r) for r in db.execute('SELECT version,checksum FROM privacy_schema_migrations')]
        if rows != [(VERSION,CHECKSUM)]:
            raise JournalUnavailable('Privacy migration version mismatch')
        actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'privacy_%' AND sql IS NOT NULL"))
        if any(actual.get(statement.split()[2])!=statement for statement in DDL):
            raise JournalUnavailable('Privacy schema/trigger drift detected')
        self.rows,self.chain = journal.read()
        state = db.execute('SELECT journal_id,applied_seq,applied_head FROM privacy_state WHERE singleton=1').fetchone()
        if (not state or state[0]!=journal.journal_id or state[1]>=len(self.chain) or self.chain[state[1]]!=state[2]):
            raise JournalUnavailable('Security journal binding/rollback mismatch')
        self.applied = state[1]
        if db.execute('SELECT 1 FROM privacy_outbox LIMIT 1').fetchone():
            raise JournalUnavailable('Uncommitted privacy protocol requires offline review')

    def reconcile(self):
        self.replaying = True
        try:
            for seq,event_id,payload,previous,mac in self.rows[self.applied:]:
                self._apply(json.loads(payload))
            changed = self.applied != len(self.rows)
            if changed:
                self.db.execute('UPDATE privacy_state SET applied_seq=?,applied_head=? WHERE singleton=1',
                                (len(self.rows),self.chain[-1]))
            return changed
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            raise JournalUnavailable('Critical privacy intent requires offline reconciliation') from exc
        finally:
            self.replaying = False

    def flush(self):
        entries = self.db.execute('SELECT event_id,payload FROM privacy_outbox ORDER BY rowid').fetchall()
        if entries:
            if self.db.execute('PRAGMA foreign_key_check').fetchall():
                raise sqlite3.IntegrityError('Privacy commit has invalid foreign keys')
            seq,head = self.journal.append([tuple(row) for row in entries])
            self.db.execute('DELETE FROM privacy_outbox')
            self.db.execute('UPDATE privacy_state SET applied_seq=?,applied_head=? WHERE singleton=1',(seq,head))

    def _uid(self, tag):
        row = self.db.execute("SELECT blago_user_id FROM users WHERE blago_tag('user',blago_user_id)=?",(tag,)).fetchone()
        return row[0] if row else None

    def _apply(self, event):
        db = self.db
        kind, owner, target, state = (event[k] for k in ('kind','owner','target','state'))
        uid = self._uid(owner)
        if kind == 'user_status' and uid:
            if state=='deleted':
                purge_user(db,uid)
            elif state in ('blocked','merged'):
                db.execute('UPDATE users SET status=? WHERE blago_user_id=?',(state,uid))
                db.execute('DELETE FROM web_sessions WHERE blago_user_id=?',(uid,))
            else:
                raise ValueError('Unknown user status')
        elif kind=='alias':
            destination = self._uid(target)
            if uid and destination:
                db.execute('INSERT INTO privacy_aliases VALUES (?,?) ON CONFLICT(source_uid) DO UPDATE SET target_uid=excluded.target_uid',(uid,destination))
        elif kind=='session':
            db.execute("DELETE FROM web_sessions WHERE blago_tag('session',session_hash)=?",(target,))
        elif kind=='password':
            db.execute("DELETE FROM web_credentials WHERE blago_tag('password',blago_user_id,password_hash,salt)=?",(target,))
        elif kind=='passkey':
            db.execute("DELETE FROM passkey_credentials WHERE blago_tag('passkey',credential_id)=?",(target,))
        elif kind=='channel_revoke':
            db.execute("DELETE FROM channel_links WHERE blago_tag('channel',channel,channel_user_id)=?",(target,))
        elif kind=='channel_grant':
            # This is control-plane identity binding, not reconstruction of ordinary writes.
            if uid:
                db.execute("UPDATE channel_links SET blago_user_id=? WHERE blago_tag('channel',channel,channel_user_id)=?",(uid,target))
        elif kind=='memory_legacy':
            db.execute("DELETE FROM memory_facts WHERE blago_tag('memory_legacy',blago_user_id,key,value_json,updated_at)=?",(target,))
        elif kind=='memory_semantic':
            # Delete descendants together so self-FKs cannot resurrect erased versions.
            db.execute("""WITH RECURSIVE removed(id,uid) AS (
                SELECT id,blago_user_id FROM semantic_memory WHERE blago_tag('memory_semantic',id)=?
                UNION SELECT s.id,s.blago_user_id FROM semantic_memory s JOIN removed r ON s.supersedes_id=r.id AND s.blago_user_id=r.uid)
                DELETE FROM semantic_memory WHERE id IN (SELECT id FROM removed)""",(target,))
        elif kind=='history':
            db.execute("DELETE FROM dialogue_turns WHERE blago_tag('history',id,blago_user_id,created_at,role,text)=?",(target,))
        elif kind=='consent_withdrawal':
            if uid:
                if state not in ('optional_activity','device_label'):
                    raise ValueError('Unknown consent purpose')
                recovery_id=str(uuid5(NAMESPACE_URL,'blago-consent-recovery:'+owner+target))
                previous=db.execute('SELECT max(recorded_at) FROM consent_records WHERE blago_user_id=? AND purpose=?',(uid,state)).fetchone()[0]
                # Recovery denial must also dominate a retained grant from the same second.
                stamp=max(event['recorded_at'],(previous or 0)+1)
                db.execute("INSERT OR IGNORE INTO consent_records VALUES (?,?,?,'recovery-tombstone','withdrawn',?)",
                           (recovery_id,uid,state,stamp))
        elif kind not in ('user_status','alias','channel_grant'):
            raise ValueError('Unknown critical intent')

    def channel_owner(self, channel, channel_id):
        target = self.journal.tag('channel',channel,channel_id)
        latest = None
        for row in self.rows:
            event = json.loads(row[2])
            if event['target']==target and event['kind'] in ('channel_grant','channel_revoke'):
                latest = event
        if latest is None:
            return None,False
        if latest['kind']=='channel_revoke':
            return None,True
        uid = self._uid(latest['owner'])
        return uid,uid is None
