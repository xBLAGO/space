"""Explicit maintenance for test/offline copies; never scheduled at startup."""
import argparse
import os
import time
from pathlib import Path

from xblago.core.privacy_schema import migrate_privacy
from xblago.core.security_journal import SecurityJournal
from xblago.core.storage import SQLiteBlagoStore


def cleanup(store, *, now=None):
    from xblago.core.privacy_control import PrivacyControl
    now = int(time.time()) if now is None else now
    if not isinstance(now,int) or now>int(time.time())+1:
        raise ValueError('Cleanup cannot expire future-valid records')
    counts = {}
    with store._locked_transaction() as db:
        if PrivacyControl.open(db) is None:
            raise ValueError('Foundation 05.3 is required for verified cleanup')
        for table in ('auth_challenges','telegram_exchanges','telegram_proofs','privacy_delete_challenges'):
            counts[table] = db.execute(f'DELETE FROM {table} WHERE expires_at<?',(now,)).rowcount
        for table in ('activity_events','security_signals'):
            counts[table] = db.execute(f'DELETE FROM {table} WHERE expires_at<=?',(now,)).rowcount
        counts['account_link_codes'] = db.execute("DELETE FROM account_link_codes WHERE CAST(strftime('%s',expires_at) AS INTEGER)<=?",(now,)).rowcount
        counts['web_sessions'] = db.execute("""DELETE FROM web_sessions WHERE last_seen_at<=? OR created_at<=?
            OR CAST(strftime('%s',expires_at) AS INTEGER)<=?
            OR blago_user_id IN (SELECT blago_user_id FROM users WHERE status<>'active')""",(now-1800,now-604800,now)).rowcount
        # Telegram update dedupe, dialogue, financial records, consents and the
        # independent journal are deliberately outside technical-event TTL.
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Cleanup foreign key check failed')
    return counts


def restore(path: Path, journal_directory: Path):
    """Offline restore: bind the original journal, replay denials, invalidate tokens."""
    if not path.is_file():
        raise ValueError('Restore requires an existing offline database copy')
    configured = os.getenv('XBLAGO_SECURITY_JOURNAL_DIR','')
    if configured and Path(configured).resolve()!=journal_directory.resolve():
        raise ValueError('Configured journal differs from restore journal')
    # Setting this process-local option never changes deployment configuration.
    previous = os.environ.get('XBLAGO_SECURITY_JOURNAL_DIR')
    os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal_directory)
    try:
        migrate_privacy(path,journal_directory)
        store = SQLiteBlagoStore(path)  # reconcile before serving any auth/data
        with store._locked_transaction():
            pass
        # Use the guarded writer so restore revocations are durable as well.
        with store._locked_transaction() as db:
            for table in ('web_sessions','auth_challenges','telegram_exchanges','privacy_delete_challenges','account_link_codes'):
                db.execute(f'DELETE FROM {table}')
            db.execute('UPDATE identity_recovery_state SET telegram_resume_at=? WHERE singleton=1',(int(time.time())+3661,))
        cleanup(store)
        return store
    finally:
        if previous is None:
            os.environ.pop('XBLAGO_SECURITY_JOURNAL_DIR',None)
        else:
            os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('init-journal','migrate','restore','cleanup'))
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--database',type=Path)
    args = parser.parse_args()
    if args.operation=='init-journal':
        SecurityJournal.initialize(args.journal)
        print('Independent journal initialized. Keep it and its key outside application backups.')
        return
    if args.database is None or not args.database.is_file():
        parser.error('An existing offline/test --database is required')
    if args.operation=='migrate':
        print('applied' if migrate_privacy(args.database,args.journal) else 'already applied')
    elif args.operation=='restore':
        restore(args.database,args.journal)
        print('Critical intents replayed; sessions invalidated; Telegram recovery quarantine active')
    else:
        configured = os.environ.get('XBLAGO_SECURITY_JOURNAL_DIR')
        if configured and Path(configured).resolve()!=args.journal.resolve():
            parser.error('Configured journal differs from requested journal')
        os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(args.journal)
        print(cleanup(SQLiteBlagoStore(args.database)))


if __name__=='__main__':
    main()
