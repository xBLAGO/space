"""Logical retention through the existing authenticated denial journal. Synthetic only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from xblago.core import lifecycle_schema as schema
from xblago.core.security_journal import JournalUnavailable,canonical

class RetentionBlocked(ValueError):pass


def require(db):
    from xblago.core.context_schema import validate
    from xblago.core.integration import enabled
    from xblago.core.privacy_control import PrivacyControl
    if not enabled():raise RetentionBlocked('Synthetic lifecycle only')
    schema.validate(db,mandatory=True);validate(db,mandatory=True)
    c=PrivacyControl.open(db)
    if c is None or c.journal.witness_directory is None:raise JournalUnavailable('Lifecycle requires original journal and witness')
    return c


def intent(db,kind,target,state):
    db.execute('INSERT INTO privacy_outbox VALUES (?,?)',(secrets.token_hex(16),canonical({'kind':kind,'owner':'','target':target,'state':state,'recorded_at':0})))


def retire_help(db,rid,journal, *, privacy=False,evidence=None):
    from xblago.domain.help_service import scrub
    r=db.execute('SELECT state FROM help_requests WHERE id=?',(rid,)).fetchone()
    if evidence is None and r:
        outcome='privacy_removed' if privacy else {'COMPLETED':'mutual_confirmation_recorded','DISPUTED':'disputed','WITHDRAWN':'withdrawn'}.get(r[0],'incomplete')
        evidence={'outcome':outcome,'event_count':db.execute('SELECT count(*) FROM help_events WHERE request_id=?',(rid,)).fetchone()[0],'protocol':'help01'}
    target=journal.tag('help-request',rid)
    if evidence is not None:
        db.execute('INSERT OR IGNORE INTO lifecycle_help_evidence VALUES (?,?,?,?)',(target,evidence['outcome'],evidence['event_count'],evidence['protocol']))
    db.execute('INSERT OR IGNORE INTO help_holds VALUES (?,?)',(journal.tag('help-request',rid),'lifecycle-retired'))
    if r:
        scrub(db,rid)
        for table in ('help_challenges','help_operations','help_audience','help_offers','help_events'):
            db.execute('DELETE FROM '+table+' WHERE request_id=?',(rid,))
        db.execute('DELETE FROM help_requests WHERE id=?',(rid,))
    return target,evidence


def retire_user_help(db,uid,journal):
    ids=[r[0] for r in db.execute('SELECT id FROM help_requests WHERE author_id=? UNION SELECT request_id FROM help_offers WHERE helper_id=? UNION SELECT request_id FROM help_audience WHERE member_id=?',(uid,uid,uid))]
    for rid in ids:
        target,evidence=retire_help(db,rid,journal,privacy=True)
        intent(db,'lifecycle_help',target,canonical(evidence))


def apply_event(db,event,journal):
    schema.validate(db,mandatory=True)
    kind,target,state=event['kind'],event['target'],event['state']
    if kind=='lifecycle_delete':
        if state not in schema.ROWS:raise ValueError('Unknown retention target')
        key=schema.ROWS[state]
        if state=='semantic_memory':
            db.execute("UPDATE semantic_memory SET supersedes_id=NULL WHERE supersedes_id IN (SELECT id FROM semantic_memory WHERE blago_tag('lifecycle-row','semantic_memory',id)=?)",(target,))
        db.execute('INSERT OR IGNORE INTO lifecycle_holds VALUES (?,?)',(target,state))
        db.execute(f"DELETE FROM {state} WHERE blago_tag('lifecycle-row','{state}',{key})=?",(target,))
    elif kind=='lifecycle_help':
        evidence=json.loads(state)
        if set(evidence)!={'outcome','event_count','protocol'} or type(evidence['event_count']) is not int:raise ValueError('Invalid minimal evidence')
        db.execute('INSERT OR IGNORE INTO help_holds VALUES (?,?)',(target,'lifecycle-retired'))
        row=next((r for r in db.execute('SELECT id FROM help_requests') if journal.tag('help-request',r[0])==target),None)
        if row:retire_help(db,row[0],journal,evidence=evidence)
        db.execute('INSERT OR IGNORE INTO lifecycle_help_evidence VALUES (?,?,?,?)',(target,evidence['outcome'],evidence['event_count'],evidence['protocol']))
    elif kind=='lifecycle_backup':
        if state!='retired':raise ValueError('Unknown backup state')
        db.execute('INSERT OR IGNORE INTO lifecycle_holds VALUES (?,?)',(target,'backup'))
        db.execute("UPDATE lifecycle_backups SET state='RETIRED' WHERE blago_tag('lifecycle-backup',content_hash)=?",(target,))
    else:raise ValueError('Unknown lifecycle event')


def sweep(store, *, now=None):
    now=int(time.time()) if now is None else now
    if type(now) is not int or now<0 or now>int(time.time()):raise ValueError('Retention cannot use future or invalid time')
    p=schema.POLICY;counts={}
    with store._locked_transaction() as db:
        c=require(db)
        # Economic/legacy ownership policy is deliberately not guessed.
        if any(db.execute('SELECT 1 FROM '+t+' LIMIT 1').fetchone() for t in ('blago_payment_requests','blago_payment_request_offers','blago_fulfillment_events','blago_contribution_events')):raise RetentionBlocked('Economic retention requires approved review')
        if db.execute("SELECT 1 FROM users u JOIN profiles p USING(blago_user_id) WHERE u.status='merged' AND NOT EXISTS(SELECT 1 FROM privacy_aliases a WHERE a.source_uid=u.blago_user_id)").fetchone():raise RetentionBlocked('Legacy merge ownership unresolved')
        expired=[r[0] for r in db.execute("SELECT id FROM dialogue_turns WHERE CAST(strftime('%s',created_at) AS INTEGER)<=?",(now-p['history_days']*86400,))]
        remove=set();uids=set()
        for tid in expired:
            pair=db.execute('SELECT user_turn,assistant_turn FROM lifecycle_history_pairs WHERE user_turn=? OR assistant_turn=?',(tid,tid)).fetchone()
            if not pair:raise RetentionBlocked('Legacy history exchange boundaries require review')
            remove.update(pair)
        for tid in remove:
            row=db.execute('SELECT blago_user_id FROM dialogue_turns WHERE id=?',(tid,)).fetchone()
            if row:uids.add(row[0])
        counts['history']=0
        for tid in sorted(remove):counts['history']+=db.execute('DELETE FROM dialogue_turns WHERE id=?',(tid,)).rowcount
        for uid in uids:db.execute('UPDATE events SET response_json=NULL,error=NULL WHERE blago_user_id=?',(uid,))
        # Never expire confirmed active facts implicitly. Old versions/candidates only.
        ids=[r[0] for r in db.execute("SELECT id FROM semantic_memory WHERE (status='candidate' AND CAST(strftime('%s',updated_at) AS INTEGER)<=?) OR (status IN ('superseded','negated') AND CAST(strftime('%s',updated_at) AS INTEGER)<=?)",(now-p['candidate_days']*86400,now-p['superseded_days']*86400))]
        for sid in ids:
            uid=db.execute('SELECT blago_user_id FROM semantic_memory WHERE id=?',(sid,)).fetchone()[0]
            target=c.journal.tag('lifecycle-row','semantic_memory',sid)
            db.execute('INSERT OR IGNORE INTO lifecycle_holds VALUES (?,?)',(target,'semantic_memory'))
            intent(db,'lifecycle_delete',target,'semantic_memory')
            # User forget deletes descendants; archive expiry must retain live children.
            # Suppress that OLD frozen trigger for this narrow controlled operation.
            c.replaying=True
            try:
                db.execute('UPDATE semantic_memory SET supersedes_id=NULL WHERE supersedes_id=?',(sid,))
                db.execute('DELETE FROM semantic_memory WHERE id=?',(sid,))
            finally:c.replaying=False
            db.execute('UPDATE context_versions SET revision=revision+1,epoch=lower(hex(randomblob(16))) WHERE blago_user_id=?',(uid,))
        counts['semantic_archives']=len(ids)
        for table in ('blago_needs','blago_resources','blago_assets','blago_resource_offers'):
            counts[table]=db.execute(f"DELETE FROM {table} WHERE CAST(strftime('%s',created_at) AS INTEGER)<=?",(now-p['legacy_record_days']*86400,)).rowcount
        counts['receipt_bodies']=db.execute("UPDATE events SET response_json=NULL,error=NULL WHERE (response_json IS NOT NULL OR error IS NOT NULL) AND CAST(strftime('%s',updated_at) AS INTEGER)<=?",(now-p['receipt_seconds'],)).rowcount
        from xblago.core.help_schema import present
        if present(db):
            rows=db.execute("SELECT r.id,r.state,r.created_at,COALESCE(max(e.occurred_at),r.created_at) last_event FROM help_requests r LEFT JOIN help_events e ON e.request_id=r.id GROUP BY r.id").fetchall()
            retired=0
            for row in rows:
                days=p['help_terminal_days'] if row['state'] in ('COMPLETED','WITHDRAWN','DISPUTED') else p['help_idle_days']
                if row['last_event']<=now-days*86400 or row['created_at']<=now-p['help_max_days']*86400:
                    target,evidence=retire_help(db,row['id'],c.journal);intent(db,'lifecycle_help',target,canonical(evidence));retired+=1
            counts['help_retired']=retired
        counts['generations']=db.execute("DELETE FROM context_generations WHERE generation_id IN (SELECT g.generation_id FROM context_generations g JOIN lifecycle_generation_times t USING(generation_id) WHERE t.created_at<=? OR (g.state='PREPARED' AND t.created_at<=?))",(now-p['generation_seconds'],now-p['pending_generation_seconds'])).rowcount
        counts['activity_unconsented']=db.execute("DELETE FROM activity_events WHERE COALESCE((SELECT decision FROM consent_records c WHERE c.blago_user_id=activity_events.blago_user_id AND purpose='optional_activity' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'").rowcount
        counts['labels_unconsented']=db.execute("UPDATE user_devices SET label=NULL WHERE label IS NOT NULL AND COALESCE((SELECT decision FROM consent_records c WHERE c.blago_user_id=user_devices.blago_user_id AND purpose='device_label' ORDER BY recorded_at DESC,CASE decision WHEN 'withdrawn' THEN 1 ELSE 0 END DESC LIMIT 1),'withdrawn')<>'granted'").rowcount
        counts['revoked_credentials']=db.execute('DELETE FROM passkey_credentials WHERE revoked_at IS NOT NULL AND revoked_at<=?',(now-p['revoked_credential_days']*86400,)).rowcount
        db.execute('UPDATE user_devices SET label=NULL WHERE revoked_at IS NOT NULL AND revoked_at<=?',(now-p['revoked_credential_days']*86400,))
        counts['revoked_devices']=db.execute('DELETE FROM user_devices WHERE revoked_at IS NOT NULL AND revoked_at<=? AND NOT EXISTS(SELECT 1 FROM passkey_credentials c WHERE c.device_id=user_devices.device_id AND c.blago_user_id=user_devices.blago_user_id)',(now-p['revoked_credential_days']*86400,)).rowcount
        for r in db.execute("SELECT backup_id,content_hash FROM lifecycle_backups WHERE state='ACTIVE' AND expires_at<=?",(now,)).fetchall():
            db.execute("UPDATE lifecycle_backups SET state='RETIRED' WHERE backup_id=?",(r[0],));target=c.journal.tag('lifecycle-backup',r[1]);db.execute('INSERT OR IGNORE INTO lifecycle_holds VALUES (?,?)',(target,'backup'));intent(db,'lifecycle_backup',target,'retired')
        from xblago.core.privacy_maintenance import cleanup_rows
        counts['technical']=cleanup_rows(db,now)
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Lifecycle FK failure')
    return counts


def backup_root(store):
    app=store.path.resolve().parent;root=app/'backups'/'sqlite'
    if root.resolve()!=root or root.is_symlink():raise ValueError('Unsafe managed backup root')
    return root


def managed_backup(store):
    backup_root(store)
    with store._locked_transaction() as db:require(db)
    path=store.backup_database();digest=hashlib.sha256(path.read_bytes()).hexdigest();bid=secrets.token_hex(16)
    with store._locked_transaction() as db:
        c=require(db)
        if db.execute('SELECT 1 FROM lifecycle_holds WHERE target_tag=?',(c.journal.tag('lifecycle-backup',digest),)).fetchone():raise RetentionBlocked('Retired backup content')
        db.execute("INSERT INTO lifecycle_backups VALUES (?,?,?,?,'ACTIVE')",(bid,path.name,digest,int(time.time())+schema.POLICY['backup_seconds']))
    return {'backup_id':bid,'path':str(path),'expires_in_seconds':schema.POLICY['backup_seconds']}


def assert_backup_not_retired(path,journal):
    # Only exact managed bytes are recognizable here. Per-row denials still apply
    # to modified/unmanaged copies, independent of this auxiliary digest check.
    digest=hashlib.sha256(Path(path).read_bytes()).hexdigest();target=journal.tag('lifecycle-backup',digest)
    rows,_=journal.read()
    if any(json.loads(r[2])['kind']=='lifecycle_backup' and json.loads(r[2])['target']==target for r in rows):raise JournalUnavailable('Retired managed backup; restore denied')


def retire_backup(store,bid, *, unlink=False):
    with store._locked_transaction() as db:
        c=require(db);r=db.execute('SELECT * FROM lifecycle_backups WHERE backup_id=?',(bid,)).fetchone()
        if r is None:raise ValueError('Unknown managed backup')
        target=c.journal.tag('lifecycle-backup',r['content_hash']);db.execute("UPDATE lifecycle_backups SET state='RETIRED' WHERE backup_id=?",(bid,));db.execute('INSERT OR IGNORE INTO lifecycle_holds VALUES (?,?)',(target,'backup'));intent(db,'lifecycle_backup',target,'retired');record=dict(r)
    removed=False
    if unlink:
        root=backup_root(store);path=root/record['filename']
        if path.name!=record['filename'] or path.is_symlink() or root.is_symlink():raise ValueError('Unsafe managed backup path')
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=record['content_hash']:raise ValueError('Backup bytes changed; logical retirement committed, unlink refused')
            path.unlink();fd=os.open(root,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        removed=True
    return {'retired':True,'file_removed':removed,'other_copies_erased':False}
