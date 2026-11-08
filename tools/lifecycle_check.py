"""Synthetic privacy/retention demonstration; no production data or provider calls."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.core.data_lifecycle import sweep
from xblago.core.privacy_maintenance import restore
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp


def run():
    with tempfile.TemporaryDirectory(prefix='blago-lifecycle-check-') as d:
        cfg=create(Path(d)/'new',help_enabled=True,context_fencing=True,lifecycle=True);cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-lifecycle'
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'));h=HelpService(app)
            a=app.register('synthetic_lifecycle_a','synthetic-password-only','synthetic-lifecycle')[0]
            b=app.register('synthetic_lifecycle_b','synthetic-password-only','synthetic-lifecycle')[0]
            uid=app.session(a)[0]
            rid=h.draft(a,'Синтетическая потребность',[h.code(b)['code']],'synthetic-lifecycle-key')['id']
            p=h.prepare(a,rid,1,'publish',{});h.commit(a,p['challenge'],'ПОДТВЕРЖДАЮ')
            app.create_item('needs',uid,{'title':'Синтетическая старая запись'})
            with app.store._locked_transaction() as db:
                db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00'")
            backup=app.store.backup_database();counts=sweep(app.store)
            recovered=restore(backup,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
            with recovered._connect() as db:
                checks={'old_need_not_restored':db.execute('SELECT count(*) FROM blago_needs').fetchone()[0]==0,
                        'recent_help_retained':db.execute('SELECT count(*) FROM help_requests').fetchone()[0]==1,
                        'no_financial_actions':db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0]==0,
                        'integrity':db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','foreign_keys':not db.execute('PRAGMA foreign_key_check').fetchall()}
            return {'format':'blago-lifecycle-check-1','checks':checks,'counts':counts,'policy_approval':'NOT APPROVED FOR REAL USERS',
                    'external_copies_erased':'UNKNOWN','provider_deletion':'NOT VERIFIED','G04':'UNKNOWN; local synthetic mechanisms only','real_pilot':'NO-GO'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    if not all(result['checks'].values()):raise SystemExit(1)
