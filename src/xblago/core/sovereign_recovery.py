"""Denials and recovery holds only. Never recreate absent keys or infer ownership."""
from xblago.core.security_journal import JournalUnavailable


def present(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='sovereign_schema_migrations'").fetchone() is not None


def purge(db,uid):
    if present(db):
        db.execute('DELETE FROM sovereign_identities WHERE blago_user_id=?',(uid,))
        db.execute('DELETE FROM sovereign_recovery_holds WHERE blago_user_id=?',(uid,))


def replay(db,event,uid):
    if not present(db):
        raise JournalUnavailable('Backup predates Identity Core; restore a reviewed identity baseline, never auto-migrate it')
    if uid is None:
        return
    user=db.execute('SELECT status FROM users WHERE blago_user_id=?',(uid,)).fetchone()
    if user is None or user[0]!='active':
        return
    kind,target,state=event['kind'],event['target'],event['state']
    if not all(isinstance(v,str) for v in (kind,target,state)):
        raise ValueError('Invalid sovereign intent shape')
    root=db.execute("SELECT * FROM sovereign_identities WHERE blago_user_id=? AND blago_tag('sovereign_identity',identity_id)=?",(uid,target)).fetchone()
    def hold(reason):
        db.execute('INSERT OR IGNORE INTO sovereign_recovery_holds VALUES (?,?,?)',(uid,reason,event['recorded_at']))
    if kind=='sovereign_identity':
        if root is None:
            hold('missing_identity')
    elif kind=='sovereign_grant':
        if root is None:
            hold('missing_identity')
        elif not db.execute("SELECT 1 FROM identity_controllers WHERE identity_id=? AND blago_tag('sovereign_controller',controller_id,public_key,role)=?",(root['identity_id'],state)).fetchone():
            hold('missing_controller')
    elif kind=='sovereign_revoke':
        db.execute("UPDATE identity_controllers SET status='revoked',revoked_at=max(created_at,?) WHERE blago_user_id=? AND blago_tag('sovereign_controller',controller_id,public_key,role)=?",(event['recorded_at'],uid,target))
        db.execute("""UPDATE sovereign_identities SET status='suspended' WHERE blago_user_id=? AND status='active'
            AND NOT EXISTS(SELECT 1 FROM identity_controllers c WHERE c.identity_id=sovereign_identities.identity_id
                AND c.role='owner' AND c.status='active')""",(uid,))
    elif kind=='sovereign_revision':
        if not state.isascii() or not state.isdigit() or len(state)>15 or int(state)<1:
            raise ValueError('Invalid sovereign revision')
        db.execute("UPDATE sovereign_identities SET version=? WHERE blago_user_id=? AND blago_tag('sovereign_identity',identity_id)=? AND version<?",(int(state),uid,target,int(state)))
    elif kind=='sovereign_challenge':
        db.execute("UPDATE sovereign_challenges SET consumed_at=min(expires_at,max(created_at,?)) WHERE blago_user_id=? AND blago_tag('sovereign_challenge',challenge_hash)=? AND consumed_at IS NULL",(event['recorded_at'],uid,target))
    elif kind=='sovereign_withdraw':
        if root:
            db.execute("UPDATE sovereign_identities SET status='revoked' WHERE identity_id=?",(root['identity_id'],))
            db.execute("UPDATE identity_controllers SET status='revoked',revoked_at=max(created_at,?) WHERE identity_id=?",(event['recorded_at'],root['identity_id']))
    else:
        raise ValueError('Unknown sovereign critical intent')
