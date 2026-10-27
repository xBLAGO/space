"""Explicit personal export/deletion and aggregate counters. No tracking events."""
import json
import secrets
import sqlite3
import time

from xblago.core.privacy_control import PrivacyControl, DeletionBlocked, ECONOMIC, deletion_blockers, purge_user
from xblago.core.security_journal import JournalUnavailable
from xblago.integrations.identity import digest, IDLE_SECONDS, ABSOLUTE_SECONDS
from xblago.integrations.web import ApiError

MAX_EXPORT_ROWS = 20000
MAX_EXPORT_BYTES = 32*1024*1024

class PrivacyService:
    def __init__(self, app):
        self.app,self.store = app,app.store

    def require(self, db):
        control = PrivacyControl.open(db)
        if control is None:
            raise ApiError(503,'Apply the offline Foundation 05.3 migration and configure its independent journal')
        return control

    def family(self, db, uid):
        rows = db.execute('''WITH RECURSIVE family(uid) AS (SELECT ? UNION
            SELECT a.source_uid FROM privacy_aliases a JOIN family f ON a.target_uid=f.uid)
            SELECT u.blago_user_id,u.status FROM users u JOIN family f ON u.blago_user_id=f.uid''',(uid,)).fetchall()
        if any(row[0]!=uid and row[1] not in ('merged','deleted') for row in rows):
            raise JournalUnavailable('Account aliases require review')
        return [row[0] for row in rows]

    def _plan(self, db, uid):
        family = self.family(db,uid)
        blockers = sorted({blocker for member in family for blocker in deletion_blockers(db,member)})
        if db.execute("""SELECT 1 FROM users u JOIN profiles p USING(blago_user_id)
            WHERE u.status='merged' AND NOT EXISTS(SELECT 1 FROM privacy_aliases a WHERE a.source_uid=u.blago_user_id) LIMIT 1""").fetchone():
            blockers.append('legacy_merge_ownership_review')
        return family,blockers

    def preview(self, token):
        with self.store._locked_transaction() as db:
            self.require(db)
            uid = self.app.identity.session_row(db,token,fresh=True)['blago_user_id']
            family,blockers = self._plan(db,uid)
            result = {'eligible':not blockers,'blockers':blockers,'scope':'canonical_and_recorded_aliases',
                      'retained':'Minimal UUID/status tombstones and independent pseudonymous revocation records; old backup bytes require retirement'}
            if not blockers:
                challenge = secrets.token_urlsafe(32)
                db.execute('DELETE FROM privacy_delete_challenges WHERE blago_user_id=? OR expires_at<?',(uid,int(time.time())))
                db.execute('INSERT INTO privacy_delete_challenges VALUES (?,?,?,?)',(digest(challenge),uid,digest(token),int(time.time())+300))
                result.update(challenge=challenge,expires_in_seconds=300,accounts_in_scope=len(family))
            return result

    def delete(self, token, challenge, confirmation):
        if confirmation != 'УДАЛИТЬ' or not isinstance(challenge,str):
            raise ApiError(400,'Подтвердите удаление словом УДАЛИТЬ после просмотра последствий')
        try:
            with self.store.conversation_lock,self.store._locked_transaction() as db:
                self.require(db)
                uid = self.app.identity.session_row(db,token,fresh=True)['blago_user_id']
                row = db.execute('SELECT 1 FROM privacy_delete_challenges WHERE challenge_hash=? AND blago_user_id=? AND session_hash=? AND expires_at>=?',
                                 (digest(challenge),uid,digest(token),int(time.time()))).fetchone()
                if not row:
                    raise ApiError(409,'Deletion confirmation expired or belongs to another session')
                family,blockers = self._plan(db,uid)
                if blockers:
                    raise ApiError(409,'Account deletion requires ownership/economic retention review')
                for member in family:
                    purge_user(db,member)
        except (DeletionBlocked,sqlite3.IntegrityError) as exc:
            raise ApiError(409,'Account deletion cannot safely satisfy all ownership references; no deletion committed') from exc
        return {'deleted':True,'scope':'canonical_and_recorded_aliases','backup_bytes_erased':False}

    def export(self, token):
        with self.store._locked_transaction() as db:
            control = self.require(db)
            uid = self.app.identity.session_row(db,token,fresh=True)['blago_user_id']
            family = self.family(db,uid)
            marks = ','.join('?' for _ in family)
            tables = {}
            count,size = 0,0
            def take(value):
                nonlocal count,size
                count += 1
                size += len(json.dumps(value,ensure_ascii=False).encode())
                if count>MAX_EXPORT_ROWS or size>MAX_EXPORT_BYTES:
                    raise ApiError(413,'Export requires a supervised offline export; no partial download was produced')
                return value
            def collect(sql,params):
                records=[]
                for record in db.execute(sql,params):
                    records.append(take(dict(record)))
                return records
            # Whitelists deliberately exclude credential material, session/CSRF/proof hashes,
            # provider diagnostics, file paths and every independent journal secret.
            fields = {
                'users':'blago_user_id,created_at,status',
                'web_credentials':'username,blago_user_id,created_at',
                'profiles':'blago_user_id,display_name,language,timezone,updated_at',
                'channel_links':'channel,channel_user_id,channel_chat_id,chat_type,blago_user_id,username,first_name,last_name,created_at,updated_at',
                'memory_facts':'blago_user_id,key,value_json,updated_at',
                'semantic_memory':'id,blago_user_id,memory_key,value_json,category,source,status,confidence,created_at,updated_at,supersedes_id',
                'dialogue_turns':'id,blago_user_id,channel,chat_id,role,text,created_at',
                'events':'channel,external_update_id,blago_user_id,status,created_at,updated_at',
                'web_sessions':'public_id,blago_user_id,created_at,last_seen_at,expires_at,auth_method',
                'passkey_credentials':'blago_user_id,sign_count,backup_eligible,backup_state,created_at,revoked_at',
                'user_devices':'device_id,blago_user_id,label,created_at,revoked_at',
                'consent_records':'consent_id,blago_user_id,purpose,policy_version,decision,recorded_at',
                'activity_events':'event_id,blago_user_id,event_type,channel,occurred_at,expires_at',
                'security_signals':'signal_id,blago_user_id,signal_type,resolution,occurred_at,expires_at',
            }
            for table in ('blago_needs','blago_resources','blago_assets','blago_resource_offers'):
                fields[table]='*'
            from xblago.core.sovereign_recovery import present
            if present(db):
                # Own private mappings only; no public discovery and no challenge/session bindings.
                fields.update({
                    'sovereign_identities':'identity_id,blago_user_id,status,version,created_at',
                    'identity_controllers':'controller_id,identity_id,blago_user_id,algorithm,role,status,created_at,revoked_at',
                    'identity_wallet_links':'link_id,identity_id,blago_user_id,chain,network,address,role,status,created_at,revoked_at',
                    'identity_credentials':'reference_id,identity_id,blago_user_id,kind,created_at',
                    'identity_events':'event_id,identity_id,blago_user_id,action,version,recorded_at',
                    'identity_consents':'consent_id,identity_id,blago_user_id,purpose,policy_version,decision,recorded_at',
                })
            for table,columns in fields.items():
                tables[table] = collect(f'SELECT {columns} FROM {table} WHERE blago_user_id IN ({marks})',family)
            requests = []
            for table,columns in ECONOMIC.items():
                where = ' OR '.join(f'{column} IN ({marks})' for column in columns)
                params = family*len(columns)
                if table!='blago_payment_requests' and requests:
                    where += ' OR payment_request_id IN ('+','.join('?' for _ in requests)+')'
                    params += requests
                records = collect(f'SELECT * FROM {table} WHERE {where}',params)
                if table=='blago_payment_requests':
                    requests = [r['id'] for r in records]
                for row in records:
                    for key in row:
                        if key.endswith('_user_id') and row[key] is not None and row[key] not in family:
                            row[key]='other_participant'
                    for key in ('payment_reference_digest','evidence_digest'):
                        row.pop(key,None)
                tables[table]=records
            tags={control.journal.tag('user',member) for member in family}
            changes=[]
            for row in control.rows:
                event=json.loads(row[2])
                if event['owner'] in tags:
                    changes.append(take({'action':event['kind'],'recorded_at':event['recorded_at']}))
            legacy_review = db.execute("""SELECT 1 FROM users u JOIN profiles p USING(blago_user_id)
                WHERE u.status='merged' AND NOT EXISTS(SELECT 1 FROM privacy_aliases a WHERE a.source_uid=u.blago_user_id) LIMIT 1""").fetchone() is not None
            return {'format':'blago-personal-export-1','blago_user_id':uid,'accounts_in_scope':len(family),
                    'scope':'canonical_and_recorded_aliases','legacy_ownership_review_required':legacy_review,
                    'tables':tables,'security_changes':changes,
                    'excluded':['authentication_secrets','other_participant_identifiers','filesystem_paths','provider_diagnostics','journal_key_mac_and_target_tags','external_legacy_files']}

    def analytics(self, token):
        with self.store._locked_transaction() as db:
            self.require(db)
            uid = self.app.identity.session_row(db,token)['blago_user_id']
            now = int(time.time())
            sessions = db.execute("""SELECT s.blago_user_id,s.auth_method FROM web_sessions s JOIN users u USING(blago_user_id)
                WHERE u.status='active' AND s.last_seen_at>? AND s.created_at>? AND CAST(strftime('%s',s.expires_at) AS INTEGER)>?""",
                (now-IDLE_SECONDS,now-ABSOLUTE_SECONDS,now)).fetchall()
            active = {r[0] for r in sessions}
            active.update(r[0] for r in db.execute("""SELECT DISTINCT e.blago_user_id FROM events e JOIN users u USING(blago_user_id)
                WHERE u.status='active' AND e.channel='telegram' AND e.status='processed' AND CAST(strftime('%s',e.updated_at) AS INTEGER)>=?""",(now-86400,)))
            own = [r for r in sessions if r[0]==uid]
            current = {'active_sessions':len(own),'session_methods':{method:sum(r[1]==method for r in own) for method in ('password','telegram','passkey','legacy')}}
            current['available_methods'] = {
                'password':db.execute('SELECT count(*) FROM web_credentials WHERE blago_user_id=?',(uid,)).fetchone()[0],
                'passkey':db.execute('SELECT count(*) FROM passkey_credentials WHERE blago_user_id=? AND revoked_at IS NULL',(uid,)).fetchone()[0],
                'telegram':db.execute("SELECT count(*) FROM channel_links WHERE blago_user_id=? AND channel='telegram' AND channel_user_id NOT LIKE 'chat:%'",(uid,)).fetchone()[0]}
            community = {'available':len(active)>=5,'minimum_cohort':5,'active_participants':len(active) if len(active)>=5 else None,
                         'active_sessions':len(sessions) if len(active)>=5 else None}
            community['session_methods'] = {}
            for method in ('password','telegram','passkey','legacy'):
                accounts={r[0] for r in sessions if r[1]==method}
                community['session_methods'][method] = {
                    'participants':len(accounts) if len(accounts)>=5 else None,
                    'sessions':sum(r[1]==method for r in sessions) if len(accounts)>=5 else None}
            # No small-cohort per-method breakdown and no time/device/IP identifiers.
            return {'personal':current,'community':community,'definition':'Distinct active-session principals plus processed Telegram principals in the last 24h; accounts, not unique humans'}
