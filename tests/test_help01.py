import concurrent.futures
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import unittest
from unittest.mock import patch
from tools.integration_fixture import create
from xblago.core.help_schema import migrate_help
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.help_service import HelpService,suggest_draft
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.privacy import PrivacyService


def crashed_commit(config,token,challenge):
    os.environ.update(config)
    from xblago.core.privacy_control import PrivacyControl
    original=PrivacyControl.flush
    def crash(control):
        has=control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='help_state'").fetchone()
        original(control)
        if has:os._exit(73)
    with patch.object(PrivacyControl,'flush',crash):HelpService(WebApp(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3'))).commit(token,challenge,'ПОДТВЕРЖДАЮ')

def concurrent_help_commit(config,token,challenge,connection):
    os.environ.update(config)
    try:
        h=HelpService(WebApp(preflight(Path(config['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        try:connection.send(h.commit(token,challenge,'ПОДТВЕРЖДАЮ')['state'])
        except ApiError:connection.send('CONFLICT')
    finally:connection.close()

class Help01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'new';self.cfg=create(self.root,help_enabled=True)
        self.cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-help-invite'
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.path=Path(self.cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3';self.app=WebApp(preflight(self.path));self.h=HelpService(self.app)
        self.a=self.app.register('synthetic_author','synthetic-password-only','synthetic-help-invite')[0]
        self.b=self.app.register('synthetic_helper','synthetic-password-only','synthetic-help-invite')[0]
        self.c=self.app.register('synthetic_unrelated','synthetic-password-only','synthetic-help-invite')[0]
        self.bc=self.h.code(self.b)['code'];self.cc=self.h.code(self.c)['code']
    def draft(self,audience=None):return self.h.draft(self.a,'Нужна помощь с ремонтом велосипеда',audience or [self.bc],secrets.token_hex(16))['id']
    def prepare(self,token,rid,action,args=None):return self.h.prepare(token,rid,self.h.get(token,rid)['version'],action,args or {})
    def action(self,token,rid,action,args=None):
        p=self.prepare(token,rid,action,args);return self.h.commit(token,p['challenge'],'ПОДТВЕРЖДАЮ')
    def offered(self):
        rid=self.draft();self.action(self.a,rid,'publish');self.action(self.b,rid,'offer',{'note':'Могу помочь с ремонтом'})
        oid=self.h.get(self.a,rid)['offers'][0]['id'];return rid,oid
    def accepted(self):
        rid,oid=self.offered();self.action(self.a,rid,'accept',{'offer_id':oid});return rid
    def test_successful_two_party_cycle_and_statistics(self):
        rid=self.accepted();self.assertEqual(self.h.get(self.b,rid)['state'],'ACCEPTED')
        self.action(self.a,rid,'confirm');self.assertEqual(self.h.get(self.a,rid)['state'],'AWAITING_CONFIRMATION')
        self.assertEqual(self.h.feed(self.a)['completed_owned'],0)
        self.action(self.b,rid,'confirm');view=self.h.get(self.a,rid)
        self.assertEqual(view['state'],'COMPLETED');self.assertEqual(self.h.feed(self.a)['completed_owned'],1)
        self.assertEqual([e['action'] for e in view['events']],['draft','publish','offer','accept','confirm','confirm'])
        with self.app.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM blago_contribution_events').fetchone()[0],0)
    def test_draft_private_and_exact_publication_preview(self):
        rid=self.draft();self.assertEqual(self.h.feed(self.b)['items'],[])
        p=self.prepare(self.a,rid,'publish')
        self.assertEqual(p['request']['text'],'Нужна помощь с ремонтом велосипеда');self.assertEqual(p['request']['audience'],[self.bc]);self.assertEqual(p['request']['version'],1)
        with self.assertRaises(ApiError):self.h.commit(self.a,p['challenge'],'да')
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT')
        self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ');self.assertEqual(len(self.h.feed(self.b)['items']),1)
    def test_idor_and_impersonation(self):
        rid=self.draft();self.action(self.a,rid,'publish')
        with self.assertRaises(ApiError):self.h.get(self.c,rid)
        with self.assertRaises(ApiError):self.prepare(self.c,rid,'offer',{'note':'Чужая помощь'})
        p=self.prepare(self.a,rid,'withdraw')
        with self.assertRaises(ApiError):self.h.commit(self.b,p['challenge'],'ПОДТВЕРЖДАЮ')
    def test_duplicate_draft_and_delivery(self):
        key=secrets.token_hex(16);first=self.h.draft(self.a,'Помощь с велосипедом',[self.bc],key);self.assertEqual(first,self.h.draft(self.a,'Помощь с велосипедом',[self.bc],key))
        with self.assertRaises(ApiError):self.h.draft(self.a,'Другой текст',[self.bc],key)
        p=self.prepare(self.a,first['id'],'publish');receipt=self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(receipt,self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ'));self.assertEqual(len(self.h.get(self.a,first['id'])['events']),2)
    def test_concurrent_accepts_single_executor(self):
        rid=self.draft([self.bc,self.cc]);self.action(self.a,rid,'publish')
        self.action(self.b,rid,'offer',{'note':'Первая помощь'});self.action(self.c,rid,'offer',{'note':'Вторая помощь'})
        offers=self.h.get(self.a,rid)['offers'];previews=[self.prepare(self.a,rid,'accept',{'offer_id':o['id']}) for o in offers]
        def commit(p):
            try:return self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')['state']
            except ApiError:return 'CONFLICT'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(commit,previews))
        self.assertEqual(sorted(results),['ACCEPTED','CONFLICT']);self.assertEqual(sum(o['status']=='ACCEPTED' for o in self.h.get(self.a,rid)['offers']),1)
    def test_decline_and_reoffer_requires_new_confirmation(self):
        rid,oid=self.offered();self.action(self.a,rid,'decline',{'offer_id':oid});self.assertEqual(self.h.get(self.a,rid)['state'],'PUBLISHED')
        self.action(self.b,rid,'offer',{'note':'Новое добровольное предложение'});self.assertEqual(self.h.get(self.a,rid)['state'],'OFFERED')
    def test_withdraw_each_stage(self):
        for stage in ('draft','published','offered','accepted','waiting','completed','disputed'):
            rid=self.draft()
            if stage!='draft':self.action(self.a,rid,'publish')
            if stage in ('offered','accepted','waiting','completed','disputed'):self.action(self.b,rid,'offer',{'note':'Могу помочь'})
            if stage in ('accepted','waiting','completed','disputed'):self.action(self.a,rid,'accept',{'offer_id':self.h.get(self.a,rid)['offers'][0]['id']})
            if stage in ('waiting','completed'):self.action(self.a,rid,'confirm')
            if stage=='completed':self.action(self.b,rid,'confirm')
            if stage=='disputed':self.action(self.b,rid,'dispute')
            self.action(self.a,rid,'withdraw');v=self.h.get(self.a,rid);self.assertEqual(v['text'],'');self.assertEqual(v['state'],'WITHDRAWN')
    def test_helper_withdraw_pending_and_accepted(self):
        rid,oid=self.offered();self.action(self.b,rid,'withdraw_offer');self.assertEqual(self.h.get(self.a,rid)['state'],'PUBLISHED')
        self.action(self.b,rid,'offer',{'note':'Могу снова помочь'});self.action(self.a,rid,'accept',{'offer_id':oid});self.action(self.b,rid,'withdraw_offer')
        self.assertEqual(self.h.get(self.a,rid)['state'],'WITHDRAWN')
    def test_dispute_removes_completed_statistic(self):
        rid=self.accepted();self.action(self.a,rid,'confirm');self.action(self.b,rid,'confirm');self.action(self.b,rid,'dispute')
        self.assertEqual(self.h.get(self.a,rid)['state'],'DISPUTED');self.assertEqual(self.h.feed(self.a)['completed_owned'],0)
    def test_audience_change_revokes_old_member_and_previews(self):
        rid,oid=self.offered();p=self.prepare(self.b,rid,'withdraw_offer')
        self.action(self.a,rid,'revise',{'text':'Уточнённая помощь с ремонтом','audience':[self.cc]})
        with self.assertRaises(ApiError):self.h.get(self.b,rid)
        with self.assertRaises(ApiError):self.h.commit(self.b,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.h.feed(self.c)['items'],[]);self.action(self.a,rid,'publish');self.assertEqual(len(self.h.feed(self.c)['items']),1)
    def test_accepted_audience_cannot_silently_change(self):
        rid=self.accepted()
        with self.assertRaises(ApiError):self.action(self.a,rid,'revise',{'text':'Тихое изменение','audience':[self.cc]})
    def test_old_backup_never_republishes_withdrawal(self):
        rid=self.draft();self.action(self.a,rid,'publish');backup=self.app.store.backup_database();self.action(self.a,rid,'withdraw')
        restored=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        # Restore invalidates sessions; authenticate again, do not reuse old token.
        app=WebApp(restored);token=app.login('synthetic_author','synthetic-password-only')[0];h=HelpService(app)
        self.assertEqual(h.get(token,rid)['state'],'WITHDRAWN');self.assertEqual(h.get(token,rid)['text'],'')
        with self.assertRaises(ApiError):h.prepare(token,rid,h.get(token,rid)['version'],'publish',{})
    def test_old_backup_before_help_migration_requires_offline_migration(self):
        # An existing baseline copy cannot bypass unknown retained Help checkpoints.
        root=Path(self.tmp.name)/'before';cfg=create(root)
        self.assertTrue(migrate_help(self.path) is False)
        with patch.dict(os.environ,cfg):
            os.environ['XBLAGO_HELP_ENABLED']='true'
            with self.assertRaises(JournalUnavailable):preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')
    def test_restart_and_postcommit_lost_ack_idempotent(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish');receipt=self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        h=HelpService(WebApp(preflight(self.path)));self.assertEqual(h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ'),receipt)
    def test_crash_after_journal_before_sql_commit_is_closed(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        proc=multiprocessing.get_context('spawn').Process(target=crashed_commit,args=(self.cfg,self.a,p['challenge']));proc.start();proc.join(20)
        self.assertEqual(proc.exitcode,73)
        h=HelpService(WebApp(preflight(self.path)));self.assertEqual(h.get(self.a,rid)['state'],'WITHDRAWN')
        with self.assertRaises(ApiError):h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
    def test_journal_failure_no_success(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        from xblago.core.security_journal import SecurityJournal
        with patch.object(SecurityJournal,'append',side_effect=JournalUnavailable('synthetic failure')):
            with self.assertRaises(JournalUnavailable):self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        self.assertEqual(self.h.get(self.a,rid)['state'],'DRAFT')
    def test_export_and_account_delete_scrub_other_party(self):
        rid=self.accepted();self.assertNotIn('велосипеда',json.dumps(PrivacyService(self.app).export(self.b),ensure_ascii=False))
        privacy=PrivacyService(self.app);preview=privacy.preview(self.a);self.assertTrue(privacy.delete(self.a,preview['challenge'],'УДАЛИТЬ')['deleted'])
        view=self.h.get(self.b,rid);self.assertEqual(view['text'],'');self.assertEqual(view['state'],'WITHDRAWN')
    def test_no_memory_history_llm_contamination(self):
        rid=self.accepted();self.h.feed(self.b);self.h.get(self.b,rid)
        uid=self.app.session(self.b)[0];self.assertEqual(self.app.store.all_memory(uid),{});self.assertEqual(self.app.history(uid),[])
        self.assertIsNone(suggest_draft('У соседа сломался велосипед'))
        self.assertIsNotNone(suggest_draft('Мне нужна помощь с велосипедом'))
    def test_missing_witness_blocks_reads_and_writes(self):
        rid=self.draft()
        for flag in ('XBLAGO_INTEGRATION_MODE','XBLAGO_PILOT_MODE','XBLAGO_REQUIRE_SECURE_IDENTITY','XBLAGO_REQUIRE_PRIVACY_JOURNAL'):
            with patch.dict(os.environ,{flag:''}):
                with self.assertRaises(ApiError) as blocked:self.h.get(self.a,rid)
                self.assertEqual(blocked.exception.status,503)
        with patch.dict(os.environ,{'XBLAGO_JOURNAL_WITNESS_DIR':str(self.root/'absent')}):
            with self.assertRaises(JournalUnavailable):self.h.get(self.a,rid)
    def test_expired_confirmation_and_sensitive_text(self):
        rid=self.draft();p=self.prepare(self.a,rid,'publish')
        with patch('xblago.domain.help_service.time.time',return_value=20000000000):
            with self.assertRaises(ApiError):self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        with self.assertRaises(ApiError):self.h.draft(self.a,'Мой пароль secret123456',[self.bc],secrets.token_hex(16))

    def test_http_retries_csrf_idor_and_bad_llm(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from xblago.integrations.web import BlagoWebHandler
        from xblago.core.llm import LlmProviderError
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=self.app
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        def request(path,body,token=None,csrf=True):
            token=token or self.a;headers={'Content-Type':'application/json','Cookie':'blago_session='+token}
            if csrf:headers['X-CSRF-Token']=self.app.session(token)[1]
            r=Request('http://127.0.0.1:'+str(server.server_port)+'/api/v1/'+path,data=json.dumps(body).encode(),headers=headers)
            try:
                with urlopen(r,timeout=10) as response:return response.status,json.load(response)
            except HTTPError as e:return e.code,json.load(e)
        try:
            body={'text':'Помощь с велосипедом','audience':[self.bc],'key':secrets.token_hex(16)}
            self.assertEqual(request('help/drafts',body,csrf=False)[0],403)
            first=request('help/drafts',body);self.assertEqual(first[0],201);self.assertEqual(first,request('help/drafts',body))
            rid=first[1]['id'];self.assertEqual(len(self.h.get(self.a,rid)['events']),1)
            self.assertEqual(request('help/drafts',dict(body,blago_user_id='other'))[0],400)
            self.assertEqual(request('help/prepare',{'rid':rid,'version':1,'action':'publish','args':{}},self.c)[0],404)
            preview=request('help/prepare',{'rid':rid,'version':1,'action':'publish','args':{}})[1]
            approved={'challenge':preview['challenge'],'confirmation':'ПОДТВЕРЖДАЮ'}
            self.assertEqual(request('help/commit',approved),request('help/commit',approved))
            with patch('xblago.integrations.web.build_chat_provider') as provider:
                provider.return_value.generate.side_effect=LlmProviderError('synthetic unavailable')
                response=request('chat/messages',{'text':'Мне нужна помощь с ремонтом'})
            self.assertEqual(response[0],200);self.assertIsNotNone(response[1]['help_suggestion'])
            with self.app.store._connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],1)
        finally:server.shutdown();server.server_close();t.join(5)

    def test_migration_rollback_and_backup_before_help_with_retained_events(self):
        from xblago.core import help_schema
        root=Path(self.tmp.name)/'offline';cfg=create(root);path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,cfg):
            with patch.object(help_schema,'DDL',help_schema.DDL+('INVALID SYNTHETIC SQL',)):
                with self.assertRaises(Exception):migrate_help(path)
            with preflight(path)._connect() as db:self.assertFalse(help_schema.present(db))
        # Backup of THIS control lineage before Help migration, then later retained grants.
        other=Path(self.tmp.name)/'same';base_cfg=create(other);basepath=Path(base_cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,base_cfg):
            app=WebApp(preflight(basepath));token=app.register('before_help','synthetic-password-only','synthetic-help-invite')[0]
            backup=app.store.backup_database();migrate_help(basepath)
            os.environ['XBLAGO_HELP_ENABLED']='true';h=HelpService(WebApp(preflight(basepath)))
            other_token=h.app.register('after_help','synthetic-password-only','synthetic-help-invite')[0]
            rid=h.draft(token,'Синтетическая потребность',[h.code(other_token)['code']],secrets.token_hex(16))['id']
            p=h.prepare(token,rid,1,'publish',{});h.commit(token,p['challenge'],'ПОДТВЕРЖДАЮ')
            with self.assertRaises(JournalUnavailable):preflight(backup)
            migrate_help(backup) # offline migration reconciles absent event state as holds
            with preflight(backup)._connect() as db:
                self.assertTrue(db.execute('SELECT 1 FROM help_holds').fetchone())
                self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],0)

    def test_export_after_audience_revocation_has_no_later_state(self):
        rid,oid=self.offered();self.action(self.a,rid,'revise',{'text':'Новая закрытая формулировка','audience':[self.cc]});self.action(self.a,rid,'publish')
        exported=PrivacyService(self.app).export(self.b)
        self.assertEqual(exported['tables']['help'],[])
        self.assertNotIn('Новая закрытая',json.dumps(exported,ensure_ascii=False))
        self.action(self.a,rid,'withdraw')
        self.assertEqual(self.h.feed(self.b)['items'],[])
        self.assertEqual(PrivacyService(self.app).export(self.b)['tables']['help'],[])

    def test_multiprocess_acceptance_and_atomic_event_rollback(self):
        rid=self.draft([self.bc,self.cc]);self.action(self.a,rid,'publish')
        self.action(self.b,rid,'offer',{'note':'Первая помощь'});self.action(self.c,rid,'offer',{'note':'Вторая помощь'})
        previews=[self.prepare(self.a,rid,'accept',{'offer_id':o['id']}) for o in self.h.get(self.a,rid)['offers']]
        ctx=multiprocessing.get_context('spawn');workers=[]
        try:
            for p in previews:
                parent,child=ctx.Pipe();worker=ctx.Process(target=concurrent_help_commit,args=(self.cfg,self.a,p['challenge'],child));worker.start();child.close();workers.append((worker,parent))
            results=[]
            for worker,pipe in workers:
                self.assertTrue(pipe.poll(20));results.append(pipe.recv());worker.join(10);self.assertEqual(worker.exitcode,0)
            self.assertEqual(sorted(results),['ACCEPTED','CONFLICT'])
        finally:
            for worker,pipe in workers:
                pipe.close()
                if worker.is_alive():worker.terminate();worker.join()
        second=self.draft();p=self.prepare(self.a,second,'publish');original=self.h.emit
        def fail(*args):original(*args);raise RuntimeError('synthetic after event insert')
        with patch.object(self.h,'emit',side_effect=fail):
            with self.assertRaises(RuntimeError):self.h.commit(self.a,p['challenge'],'ПОДТВЕРЖДАЮ')
        view=self.h.get(self.a,second);self.assertEqual(view['state'],'DRAFT');self.assertEqual(len(view['events']),1)

    def test_malformed_action_types_are_rejected_without_changes(self):
        rid=self.draft()
        for action,args in [([],{}),({},{}),('accept',{'offer_id':[]}),('pay',{})]:
            with self.assertRaises(ApiError) as caught:self.h.prepare(self.a,rid,1,action,args)
            self.assertEqual(caught.exception.status,400)
        self.assertEqual(self.h.get(self.a,rid)['version'],1)
