"""Reproducible local operational and reidentification evidence. Synthetic only."""
import argparse
import errno
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from unittest.mock import patch

from tools.help_readiness_lab import initialize as lab_init
from xblago.core.integration import preflight
from xblago.core.retention_ops import Runner, Schedule, initialize
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import SecurityJournal
from xblago.core.data_lifecycle import sweep
from xblago.domain.help_service import HelpService
from xblago.integrations.web import WebApp
from xblago.integrations.privacy import PrivacyService


def _child(root, pipe, crash=False):
    def backup(store):
        pipe.send('LOCKED');pipe.recv()
        from xblago.core.data_lifecycle import managed_backup
        return managed_backup(store)
    def die():os._exit(85)
    result=Runner(root,enabled=True,backup=None if crash else backup,after_cleanup=die if crash else None).tick()
    pipe.send(result);pipe.close()


def reidentification_probe():
    with tempfile.TemporaryDirectory(prefix='blago-reidentification-') as d:
        root=Path(d)/'new';cfg=lab_init(root)
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(root/'app/blago.sqlite3'));h=HelpService(app)
            a=app.register('synthetic_link_a','synthetic-password-only','synthetic-help011-only')[0]
            b=app.register('synthetic_link_b','synthetic-password-only','synthetic-help011-only')[0]
            code=h.code(b)['code'];known=[]
            with patch('time.time',return_value=int(time.time())-100*86400):
                for i in range(3):
                    rid=h.draft(a,'Синтетический частный текст '+str(i),[code],'synthetic-reidentification-'+str(i))['id']
                    if i==0:
                        p=h.prepare(a,rid,1,'publish',{});h.commit(a,p['challenge'],'ПОДТВЕРЖДАЮ')
                    known.append(rid)
            backup=app.store.backup_database()
            with sqlite3.connect(backup) as db:
                # Attacker has an old backup / equivalent side information.
                old=[(r[0],r[1],r[2]) for r in db.execute('SELECT r.id,r.author_id,count(e.event_id) FROM help_requests r JOIN help_events e ON r.id=e.request_id GROUP BY r.id')]
            sweep(app.store)
            with app.store._locked_transaction() as db:
                evidence=[dict(r) for r in db.execute('SELECT * FROM lifecycle_help_evidence')]
            j=SecurityJournal(Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
            linked=all(any(e['target_tag']==j.tag('help-request',rid) for e in evidence) for rid in known)
            # No key needed to single out the one distinct event-count trajectory.
            groups={n:sum(r[2]==n for r in old) for _,_,n in old}
            unique=any(groups[e['event_count']]==1 for e in evidence)
            ambiguous=any(groups[e['event_count']]>1 for e in evidence)
            token=app.login('synthetic_link_a','synthetic-password-only')[0]
            export=json.dumps(PrivacyService(app).export(token),ensure_ascii=False)
            return {'format':'blago-help-linkage-probe-1','synthetic_requests':3,
                    'key_and_old_backup_linkable':linked,'side_information_singles_out_without_key':unique,
                    'ambiguous_metadata_not_unique':ambiguous,'anonymous':False,
                    'no_personal_fields_in_summary':all(set(e)=={'target_tag','outcome','event_count','protocol'} for e in evidence),
                    'personal_export_excludes_summary':not any(e['target_tag'] in export for e in evidence),
                    'conclusion':'PSEUDONYMOUS, NOT ANONYMOUS; backup/key and behavioral side information can reidentify',
                    'real_participants':False,'production_access':False}


def run():
    checks=[]
    def record(name,expected,passed,**details):
        checks.append({'scenario':name,'expected':expected,'actual':'PASS' if passed else 'FAIL','details':details})
    with tempfile.TemporaryDirectory(prefix='blago-retention-operations-') as d:
        root=Path(d)/'new';cfg=lab_init(root)
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(root/'app/blago.sqlite3'))
            token=app.register('synthetic_operator','synthetic-password-only','synthetic-help011-only')[0];uid=app.session(token)[0]
            def aged():
                app.create_item('needs',uid,{'title':'Синтетический старый запрос'})
                with app.store._locked_transaction() as db:db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00'")
            def count(store=None):
                with (store or app.store)._locked_transaction() as db:return db.execute('SELECT count(*) FROM blago_needs').fetchone()[0]
            now=[int(time.time())];clock=lambda:now[0]
            initialize(root,Schedule(interval=10,retry_seconds=1,max_attempts=2,overdue_seconds=20),clock=clock)
            r=Runner(root,enabled=True,clock=clock);aged();old=app.store.backup_database()
            record('disabled','no cleanup',Runner(root,enabled=False).tick()['status']=='DISABLED' and count()==1)
            now[0]+=21
            delayed=r.inspect();normal=r.tick()
            record('overdue','alert before delayed cleanup',delayed['overdue'] and normal['overdue_before'])
            record('normal','durable SUCCESS + old record removed',normal['status']=='SUCCESS' and count()==0)
            record('periodicity','no second run before due',r.tick()['status']=='WAITING')
            recovered=restore(old,Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
            record('old_application_backup','deleted record never returns',count(recovered)==0)
            now[0]+=10;aged()
            failing=Runner(root,enabled=True,clock=clock,backup=lambda s:(_ for _ in ()).throw(OSError(errno.ENOSPC,'synthetic')))
            failed=failing.tick()
            record('backup_failure_full_disk','FAILED and no cleanup',failed['status']=='FAILED' and count()==1)
            now[0]+=1;retry=r.tick();record('bounded_retry','retry after backoff succeeds',retry['status']=='SUCCESS')
            now[0]+=10;w=root/'witness';w.rename(root/'missing-witness')
            try:missing=r.tick()
            finally:(root/'missing-witness').rename(w)
            record('external_confirmation_lost','UNKNOWN_RECOVERY; no false success',missing['status']=='UNKNOWN_RECOVERY')
            # Separate real processes; reset schedule under REAL clock first.
            real=Runner(root,enabled=True,clock=lambda:max(now[0],int(time.time())))
            real.reschedule()
            ctx=multiprocessing.get_context('spawn')
            # Child clock must not be earlier than synthetic schedule. Use a new lab.
        other=Path(d)/'workers';cfg2=lab_init(other)
        with patch.dict(os.environ,cfg2):
            app2=WebApp(preflight(other/'app/blago.sqlite3'));t=app2.register('synthetic_worker','synthetic-password-only','synthetic-help011-only')[0]
            uid2=app2.session(t)[0];app2.create_item('needs',uid2,{'title':'Синтетическая запись'})
            with app2.store._locked_transaction() as db:db.execute("UPDATE blago_needs SET created_at='2000-01-01T00:00:00+00:00'")
            initialize(other,Schedule(interval=1,retry_seconds=1,overdue_seconds=2))
            parent,child=ctx.Pipe();p=ctx.Process(target=_child,args=(str(other),child));p.start();child.close()
            try:
                if not parent.poll(30):raise RuntimeError('Worker timed out')
                locked=parent.recv()=='LOCKED';busy=Runner(other,enabled=True).tick()['status']=='BUSY'
                parent.send('release')
                if not parent.poll(30):raise RuntimeError('Worker timed out')
                done=parent.recv();p.join(10)
                record('two_executors','one OS lock / one durable operation',locked and busy and done['status']=='SUCCESS' and p.exitcode==0)
            finally:
                if p.is_alive():p.kill();p.join()
                parent.close()
            # Explicit reschedule then crash after the application commit.
            Runner(other,enabled=True).reschedule()
            parent,child=ctx.Pipe();p=ctx.Process(target=_child,args=(str(other),child,True));p.start();child.close();p.join(30)
            try:
                status=Runner(other,enabled=True).inspect()['status']
                record('process_crash','exit85 and result INTERRUPTED, never SUCCESS',p.exitcode==85 and status=='INTERRUPTED')
            finally:
                if p.is_alive():p.kill();p.join()
                parent.close()
            with app2.store._locked_transaction() as db:
                record('integrity','SQLite integrity/FK intact',db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall())
    attack=reidentification_probe()
    return {'format':'blago-privacy014-operations-lab-1','scope':'local synthetic integration, same-author; no independent infrastructure claim',
            'scenarios':checks,'passed':sum(x['actual']=='PASS' for x in checks),'total':len(checks),'reidentification':attack,
            'extra_llm_calls':0,'G04':'UNKNOWN','real_pilot':'NO-GO','production_access':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    if result['passed']!=result['total'] or not result['reidentification']['key_and_old_backup_linkable']:raise SystemExit(1)
