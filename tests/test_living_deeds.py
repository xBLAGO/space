"""Synthetic consent, private bridge, canonical Help/Asset, durable UEL and faults."""
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch
import tests.test_uel_checkpoint as checkpoint_fixture
from tests.test_uel_local import body, PASSWORD, INVITE, CONFIRM
from xblago.core import living_schema, uel_schema, uel_local
from xblago.core.integration import preflight
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.living_deeds import LivingDeeds
from xblago.domain.help_service import HelpService
from xblago.domain.asset_registry import AssetRegistry
from xblago.domain.local_ledger import LocalLedger
from xblago.integrations.web import WebApp, ApiError


def reserve_worker(cfg,token,challenge,clock,pipe):
    os.environ.update(cfg)
    with patch('time.time',return_value=clock):
        service=LivingDeeds(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        pipe.send('ready');pipe.recv()
        try:pipe.send({'ok':service.commit(token,challenge,CONFIRM)})
        except ApiError as exc:pipe.send({'denied':exc.status})
        except Exception as exc:pipe.send({'error':type(exc).__name__})

def crash_worker(cfg,token,challenge,clock,phase):
    os.environ.update(cfg)
    with patch('time.time',return_value=clock):
        service=LivingDeeds(WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')))
        original=PrivacyControl.flush
        def flush(control):
            pending=control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='living_state'").fetchone()
            original(control)
            if pending and phase=='after_journal':os._exit(81)
        with patch.object(PrivacyControl,'flush',flush):service.commit(token,challenge,CONFIRM)
        os._exit(82)

class LivingTests(unittest.TestCase):
    def setUp(self):
        off=patch.dict(os.environ,{living_schema.FLAG:'false'});off.start();self.addCleanup(off.stop)
        self.cp=checkpoint_fixture.CheckpointTests();self.addCleanup(self.cp.doCleanups);self.cp.setUp()
        self.lab=self.cp.lab;self.cfg=self.lab.cfg;self.path=self.lab.path
        self.before_living=self.lab.app.store.backup_database(self.cp.root/'before-living')
        self.assertTrue(living_schema.migrate(self.path))
        self.cfg[living_schema.FLAG]='true';os.environ.update(self.cfg)
        self.app=WebApp(preflight(self.path));self.s=LivingDeeds(self.app);self.h=HelpService(self.app);self.r=AssetRegistry(self.app)
        self.a,self.b,self.lena=self.lab.a,self.lab.b,self.lab.c
        self.n=0;self.now=int(time.time());self.transcript=[]
    def key(self):self.n+=1;return 'synthetic-living-operation-'+str(self.n)
    def code(self,token):return self.h.code(token)['code']
    def run_action(self,token,action,data,key=None):
        p=self.s.prepare(token,action,data,key or self.key());return self.s.commit(token,p['challenge'],CONFIRM)['id']
    def help_action(self,token,rid,action,args=None):
        p=self.h.prepare(token,rid,self.h.get(token,rid)['version'],action,args or {})
        return self.h.commit(token,p['challenge'],CONFIRM)
    def need(self,owner=None,audience=None):
        owner=owner or self.b;audience=audience or [self.a,self.lena]
        rid=self.h.draft(owner,'Нужна синтетическая помощь с ремонтом велосипеда',[self.code(t) for t in audience],self.key())['id']
        self.help_action(owner,rid,'publish');return rid
    def resource(self,owner=None,audience=None):
        owner=owner or self.lena;audience=audience or [self.b]
        b=body(quantity='1',category='bicycle',available='0',audience='PRIVATE')
        aid=self.r.draft(owner,b,self.key())['id']
        p=self.r.prepare(owner,aid,1,dict(b,available='1',share_consent=True))
        self.r.commit(owner,p['challenge'],CONFIRM)
        rid=self.run_action(owner,'offer_resource',{'asset_id':aid,'audience':[self.code(t) for t in audience],
            'starts_at':self.now+30,'ends_at':self.now+60,'expires_at':self.now+120})
        return aid,rid
    def offer(self,rid,resource,helper=None):
        helper=helper or self.lena
        self.help_action(helper,rid,'offer',{'note':'Готов помочь добровольно в согласованный слот'})
        offer=next(o['id'] for o in self.h.get(helper,rid)['offers'] if o['mine'])
        self.run_action(helper,'attach_offer',{'resource_id':resource,'need_id':rid,'offer_id':offer})
        return offer
    def cycle(self):
        need=self.need();asset,resource=self.resource();offer=self.offer(need,resource)
        reservation=self.run_action(self.b,'reserve',{'resource_id':resource,'need_id':need,'offer_id':offer})
        self.help_action(self.b,need,'confirm');self.help_action(self.lena,need,'confirm')
        return need,asset,resource,reservation
    def bridge(self):
        need=self.need(audience=[self.a])
        note=self.run_action(self.a,'note',{'text':'PRIVATE_NOTE_LENA умеет чинить велосипеды','expires_at':self.now+300})
        permit=self.run_action(self.b,'need_permission',{'need_id':need,'topic':'bicycle_repair',
                'invitation_text':'AUTHOR_APPROVED: нужна помощь с велосипедом','expires_at':self.now+300})
        bridge=self.run_action(self.a,'propose_bridge',{'note_id':note,'permission_id':permit})
        return need,note,permit,bridge
    def test_positive_canonical_cycle_and_no_double_count(self):
        need,asset,resource,reservation=self.cycle()
        self.assertEqual(self.h.get(self.b,need)['state'],'COMPLETED')
        view=self.s.view(self.b);self.assertEqual(view['summary']['completed'],1)
        balance=LocalLedger(self.app).balance(self.b)
        self.assertEqual(balance['fulfilled_occurrences'],1)
        self.assertNotIn('PRIVATE_NOTE',json.dumps(balance))
        self.demonstration={'canonical_help_state':'COMPLETED','closed_deeds':view['summary']['completed'],
                            'flow_count':balance['fulfilled_occurrences'],'available_slots':view['summary']['available_resource_slots'],
                            'evidence':'MUTUAL_SESSION_CONFIRMATION; not independent verification'}
        with self.app.store._locked_transaction() as db:
            events=[json.loads(r[0])['type'] for r in db.execute('SELECT body FROM living_versions')]
            self.assertIn('ResourceReserved',events);self.assertIn('FulfillmentConfirmed',events)
            self.assertEqual(db.execute('SELECT count(*) FROM blago_assets WHERE id=?',(asset,)).fetchone()[0],1)
    def test_private_note_two_consents_then_same_help_lifecycle(self):
        need,note,permit,bridge=self.bridge()
        before=json.dumps(self.s.view(self.b),ensure_ascii=False)
        self.assertNotIn('PRIVATE_NOTE_LENA',before);self.assertNotIn(bridge,before)
        self.run_action(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)})
        invitation=next(i for i in self.s.view(self.lena)['items'] if i['kind']=='INVITATION')
        self.assertEqual(invitation['referrer'],self.code(self.a))
        self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps(invitation))
        self.assertEqual(before,json.dumps(self.s.view(self.b),ensure_ascii=False))
        self.run_action(self.lena,'accept_invitation',{'id':bridge})
        asset,resource=self.resource()
        # B now explicitly extends exact Help audience; invitation did not bypass it.
        self.help_action(self.b,need,'revise',{'text':'Нужна синтетическая помощь с ремонтом велосипеда',
                        'audience':[self.code(self.a),self.code(self.lena)]})
        self.help_action(self.b,need,'publish');offer=self.offer(need,resource)
        self.run_action(self.b,'reserve',{'resource_id':resource,'need_id':need,'offer_id':offer})
        self.help_action(self.b,need,'confirm');self.help_action(self.lena,need,'confirm')
        self.assertEqual(self.s.view(self.b)['summary']['completed'],1)
        self.demonstration={'private_note_hidden_from_B':True,'invitation_names_referrer':True,
                            'two_distinct_consents':True,'B_exact_audience_extension':True,'closed_deeds':1}
    def test_decline_is_private_neutral_and_suppresses_repeat(self):
        need,note,permit,bridge=self.bridge();before=self.s.view(self.b)
        self.run_action(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)})
        self.run_action(self.lena,'decline_invitation',{'id':bridge})
        self.assertEqual(before,self.s.view(self.b))
        self.assertEqual(self.s.view(self.b)['matches'],'NO_AVAILABLE_MATCHES')
        self.demonstration={'B_before_equals_after':True,'neutral_status':'NO_AVAILABLE_MATCHES',
                            'private_negative_label_published':False,'same_topic_repeat':'DENIED'}
        again=self.run_action(self.a,'propose_bridge',{'note_id':note,'permission_id':permit})
        with self.assertRaises(ApiError):self.s.prepare(self.a,'request_consent',{'id':again,'recipient':self.code(self.lena)},self.key())
    def test_foreign_user_cannot_consent_or_read_private_note(self):
        need,note,permit,bridge=self.bridge()
        self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps(self.s.view(self.lena)))
        self.run_action(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)})
        with self.assertRaises(ApiError):self.s.prepare(self.b,'accept_invitation',{'id':bridge},self.key())
    def test_replay_receipt_and_conflicting_key(self):
        key=self.key();d={'text':'PRIVATE_SYNTHETIC_NOTE','expires_at':self.now+300}
        p=self.s.prepare(self.a,'note',d,key);first=self.s.commit(self.a,p['challenge'],CONFIRM)
        self.assertEqual(first,self.s.commit(self.a,p['challenge'],CONFIRM))
        with self.assertRaises(ApiError):self.s.prepare(self.a,'note',dict(d,text='different'),key)
        with self.app.store._locked_transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM living_versions').fetchone()[0],1)
    def test_direct_help_accept_cannot_bypass_resource_reservation(self):
        need=self.need();_,resource=self.resource();offer=self.offer(need,resource)
        with self.assertRaises(ApiError):self.help_action(self.b,need,'accept',{'offer_id':offer})
        self.assertEqual(self.h.get(self.b,need)['state'],'OFFERED')
    def test_dispute_and_correction_preserve_event_history(self):
        need,asset,resource,reservation=self.cycle();self.help_action(self.b,need,'dispute')
        self.assertEqual(self.s.view(self.b)['summary']['completed'],0);self.assertEqual(self.s.view(self.b)['summary']['disputed'],1)
        note=self.run_action(self.a,'note',{'text':'Исправляемая частная заметка','expires_at':self.now+300})
        with self.app.store._locked_transaction() as db:
            original=dict(db.execute('SELECT * FROM living_versions WHERE record_id=?',(note,)).fetchone())
        self.run_action(self.a,'correct_note',{'id':note,'text':'Исправленный частный текст','causation_id':original['event_id']})
        with self.app.store._locked_transaction() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM living_versions WHERE record_id=? ORDER BY version',(note,))]
        self.assertEqual(rows[0],original);self.assertEqual(json.loads(rows[-1]['body'])['causation_id'],original['event_id'])
    def test_concurrent_independent_processes_cannot_double_reserve(self):
        other=self.app.register('living_second_requester',PASSWORD,INVITE)[0]
        _,resource=self.resource(audience=[self.b,other]);needs=[self.need(),self.need(owner=other)]
        offers=[self.offer(n,resource) for n in needs]
        qs=[self.s.prepare(t,'reserve',{'resource_id':resource,'need_id':n,'offer_id':o},self.key())['challenge'] for t,n,o in zip([self.b,other],needs,offers)]
        ctx=multiprocessing.get_context('spawn');parents=[];workers=[]
        for token,q in zip([self.b,other],qs):
            p,ch=ctx.Pipe();w=ctx.Process(target=reserve_worker,args=(self.cfg,token,q,self.now,ch));w.start();parents.append(p);workers.append(w)
        for p in parents:self.assertTrue(p.poll(30));self.assertEqual(p.recv(),'ready')
        for p in parents:p.send('go')
        results=[]
        for p in parents:self.assertTrue(p.poll(60));results.append(p.recv());p.close()
        for w in workers:w.join(30);self.assertEqual(w.exitcode,0)
        self.assertEqual(sum('ok' in r for r in results),1);self.assertEqual(sum(r.get('denied')==409 for r in results),1)
    def test_rollback_before_uel_does_not_leave_note_or_authority(self):
        p=self.s.prepare(self.a,'note',{'text':'MUST_ROLL_BACK','expires_at':self.now+300},self.key())
        with patch.object(uel_local,'append',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):self.s.commit(self.a,p['challenge'],CONFIRM)
        self.assertNotIn('MUST_ROLL_BACK',json.dumps(self.s.view(self.a)))
    def test_current_backup_and_restart_preserve_verified_closed_deed(self):
        self.cycle();before=self.s.view(self.b)
        backup=self.app.store.backup_database();app=WebApp(restore(backup,self.lab.journal))
        token=app.login('uel_synthetic_helper',PASSWORD)[0]
        after=LivingDeeds(app).view(token);self.assertEqual(before,after)
    def test_old_backup_never_reconstructs_missing_positive_consent(self):
        backup=self.app.store.backup_database()
        self.run_action(self.a,'note',{'text':'NOT_IN_OLD_BACKUP','expires_at':self.now+300})
        app=WebApp(restore(backup,self.lab.journal));token=app.login('uel_synthetic_owner',PASSWORD)[0]
        with self.assertRaises(JournalUnavailable):LivingDeeds(app).view(token)
    def test_migration_idempotent_and_flag_off_still_checks_data(self):
        self.assertFalse(living_schema.migrate(self.path));self.assertFalse(uel_schema.migrate_local(self.path))
        self.run_action(self.a,'note',{'text':'Private payload','expires_at':self.now+300})
        with patch.dict(os.environ,{living_schema.FLAG:'false'}):
            with self.assertRaises(ApiError):self.s.view(self.a)
            self.assertTrue(preflight(self.path))

    def test_private_note_and_headers_are_not_a_public_catalog_or_llm_memory(self):
        note=self.run_action(self.a,'note',{'text':'PRIVATE_NOTE_LENA умеет ремонтировать','expires_at':self.now+300})
        self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps(LocalLedger(self.app).observations(self.b)))
        self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps(self.app.store.all_memory(self.app.session(self.a)[0])))
        with self.app.store._locked_transaction() as db:
            self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps([dict(r) for r in db.execute('SELECT * FROM uel_events')]))
        proposed=LivingDeeds.suggest('Знакомая Лена умеет чинить велосипеды')
        self.assertEqual(proposed['type'],'PRIVATE_NOTE_DRAFT');self.assertFalse(proposed['committed'])

    def test_revoked_resource_blocks_prepared_reservation_and_confirmations(self):
        need=self.need();asset,resource=self.resource();offer=self.offer(need,resource)
        p=self.s.prepare(self.b,'reserve',{'resource_id':resource,'need_id':need,'offer_id':offer},self.key())
        self.run_action(self.lena,'withdraw',{'id':resource})
        with self.assertRaises(ApiError):self.s.commit(self.b,p['challenge'],CONFIRM)
        self.assertEqual(self.h.get(self.b,need)['state'],'OFFERED')
        self.assertFalse(any(i['kind']=='RESOURCE' for i in self.s.view(self.b)['items']))

    def test_need_revision_invalidates_invitation_authority(self):
        need,note,permit,bridge=self.bridge()
        p=self.s.prepare(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)},self.key())
        self.help_action(self.b,need,'revise',{'text':'Другая явно подтверждаемая задача','audience':[self.code(self.a)]})
        self.help_action(self.b,need,'publish')
        with self.assertRaises(ApiError):self.s.commit(self.a,p['challenge'],CONFIRM)
        self.assertFalse(any(i['kind']=='INVITATION' for i in self.s.view(self.lena)['items']))

    def test_full_uel_index_rebuild_keeps_identical_verified_projection(self):
        from xblago.core.uel_recovery import repair_index
        self.cycle();before=self.s.view(self.b)
        with self.app.store._locked_transaction() as db:headers=[dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')]
        with sqlite3.connect(self.path) as db:
            for sql in uel_schema.DDL:
                if sql.startswith('CREATE TRIGGER'):db.execute('DROP TRIGGER '+sql.split()[2])
            db.execute('DELETE FROM uel_events');db.execute('UPDATE uel_state SET sequence=0,head=?',('0'*64,))
        repair_index(self.path)
        app=WebApp(preflight(self.path));self.assertEqual(LivingDeeds(app).view(self.b),before)
        with app.store._locked_transaction() as db:self.assertEqual(headers,[dict(r) for r in db.execute('SELECT * FROM uel_events ORDER BY sequence')])

    def test_missing_checkpoint_evidence_fails_closed(self):
        self.cp.stop_server()
        with self.assertRaises(JournalUnavailable):self.s.view(self.b)

    def test_configuration_without_retained_enrollment_is_not_authority(self):
        with self.assertRaises(JournalUnavailable):living_schema.require_enrollment([])

    def test_invitation_preview_binds_exact_task_without_exposing_private_note(self):
        need,note,permit,bridge=self.bridge()
        p=self.s.prepare(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)},self.key())
        self.assertEqual(p['preview']['review']['task'],'AUTHOR_APPROVED: нужна помощь с велосипедом')
        self.assertNotIn('PRIVATE_NOTE_LENA',json.dumps(p))
        self.s.commit(self.a,p['challenge'],CONFIRM)
        p=self.s.prepare(self.lena,'accept_invitation',{'id':bridge},self.key())
        self.assertTrue(p['preview']['review']['contact_only']);self.assertFalse(p['preview']['review']['publishes_resource'])

    def test_bad_confirmation_expired_challenge_and_substitution_do_not_write(self):
        d={'text':'Private note requiring confirmation','expires_at':self.now+300}
        p=self.s.prepare(self.a,'note',d,self.key())
        with self.assertRaises(ApiError):self.s.commit(self.a,p['challenge'],'yes')
        with self.assertRaises(ApiError):self.s.commit(self.b,p['challenge'],CONFIRM)
        with patch('time.time',return_value=self.now+121):
            with self.assertRaises(ApiError):self.s.commit(self.a,p['challenge'],CONFIRM)
        with self.app.store._locked_transaction() as db:
            db.execute("UPDATE living_commands SET body=replace(body,'Private note','Forged note') WHERE actor_id=?",(self.app.session(self.a)[0],))
        with self.assertRaises(JournalUnavailable):self.s.commit(self.a,p['challenge'],CONFIRM)

    def test_erased_private_payload_does_not_return_from_old_backup(self):
        note=self.run_action(self.a,'note',{'text':'PRIVATE_ERASE_ME','expires_at':self.now+300})
        backup=self.app.store.backup_database()
        self.run_action(self.a,'erase',{'id':note})
        app=WebApp(restore(backup,self.lab.journal));token=app.login('uel_synthetic_owner',PASSWORD)[0]
        self.assertNotIn('PRIVATE_ERASE_ME',json.dumps(LivingDeeds(app).view(token)))
        with app.store._locked_transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM living_versions WHERE record_id=?',(note,)).fetchone()[0],0)

    def test_completed_previews_do_not_retain_private_text_after_erasure(self):
        note=self.run_action(self.a,'note',{'text':'PRIVATE_ERASE_ALL_PREVIEWS','expires_at':self.now+300})
        self.run_action(self.a,'erase',{'id':note})
        with self.app.store._locked_transaction() as db:
            for table in ('living_records','living_versions','living_commands'):
                self.assertNotIn('PRIVATE_ERASE_ALL_PREVIEWS',json.dumps([dict(r) for r in db.execute('SELECT * FROM '+table)]))

    def test_correction_cannot_reuse_stale_privacy_context_or_lost_session(self):
        p=self.s.prepare(self.a,'note',{'text':'MUST_NOT_COMMIT_AFTER_DELETE','expires_at':self.now+300},self.key())
        uid=self.app.session(self.a)[0]
        self.app.store.set_memory(uid,'city','Синтетический город')
        with self.assertRaises(ApiError):self.s.commit(self.a,p['challenge'],CONFIRM)
        self.app.logout(self.a)
        with self.assertRaises(ApiError):self.s.view(self.a)

    def test_expiry_blocks_invitation_then_explicit_cleanup_erases_payload(self):
        note=self.run_action(self.a,'note',{'text':'PRIVATE_EXPIRING_NOTE','expires_at':self.now+5})
        with patch('time.time',return_value=self.now+6):
            self.assertNotIn('PRIVATE_EXPIRING_NOTE',json.dumps(self.s.view(self.a)))
            self.assertEqual(self.s.expire(self.a)['expired_records'],1)
        with self.app.store._locked_transaction() as db:self.assertFalse(db.execute('SELECT 1 FROM living_versions WHERE record_id=?',(note,)).fetchone())

    def test_atomic_migration_failure_preserves_original_headers_and_ledgers(self):
        path=self.before_living
        with patch.dict(os.environ,{living_schema.FLAG:'false'}),patch.object(living_schema,'DDL',living_schema.DDL+('INVALID SQL FOR FAULT TEST',)):
            with self.assertRaises(sqlite3.OperationalError):living_schema.migrate(path)
        with sqlite3.connect(path) as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='living_migrations'").fetchone())

    def test_replay_after_restart_returns_original_receipt_without_new_events(self):
        p=self.s.prepare(self.a,'note',{'text':'PRIVATE_RESTART_NOTE','expires_at':self.now+300},self.key())
        result=self.s.commit(self.a,p['challenge'],CONFIRM)
        self.s=LivingDeeds(WebApp(preflight(self.path)))
        with self.app.store._locked_transaction() as db:before=db.execute('SELECT count(*) FROM uel_events').fetchone()[0]
        self.assertEqual(result,self.s.commit(self.a,p['challenge'],CONFIRM))
        with self.app.store._locked_transaction() as db:self.assertEqual(before,db.execute('SELECT count(*) FROM uel_events').fetchone()[0])

    def test_account_deletion_purges_owned_payloads_and_commands(self):
        from xblago.integrations.privacy import PrivacyService
        self.run_action(self.a,'note',{'text':'PRIVATE_ACCOUNT_ERASE','expires_at':self.now+300})
        privacy=PrivacyService(self.app);p=privacy.preview(self.a)
        privacy.delete(self.a,p['challenge'],'УДАЛИТЬ')
        with self.app.store._locked_transaction() as db:
            for table in ('living_records','living_versions','living_commands'):
                self.assertNotIn('PRIVATE_ACCOUNT_ERASE',json.dumps([dict(r) for r in db.execute('SELECT * FROM '+table)]))
        self.assertTrue(self.s.view(self.b))

    def test_crash_after_commit_replay_has_one_source_event(self):
        p=self.s.prepare(self.a,'note',{'text':'PRIVATE_CRASH_COMMITTED','expires_at':self.now+300},self.key())
        worker=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.cfg,self.a,p['challenge'],self.now,'after_commit'))
        worker.start();worker.join(45);self.assertEqual(worker.exitcode,82)
        self.s=LivingDeeds(WebApp(preflight(self.path)))
        self.s.commit(self.a,p['challenge'],CONFIRM)
        with self.app.store._locked_transaction() as db:self.assertEqual(db.execute('SELECT count(*) FROM living_versions').fetchone()[0],1)

    def test_crash_after_journal_before_sql_never_replays_a_grant(self):
        p=self.s.prepare(self.a,'note',{'text':'PRIVATE_CRASH_NOT_COMMITTED','expires_at':self.now+300},self.key())
        worker=multiprocessing.get_context('spawn').Process(target=crash_worker,args=(self.cfg,self.a,p['challenge'],self.now,'after_journal'))
        worker.start();worker.join(45);self.assertEqual(worker.exitcode,81)
        self.s=LivingDeeds(WebApp(preflight(self.path)))
        with self.assertRaises(JournalUnavailable):self.s.view(self.a)

    def test_withdrawal_of_completed_help_does_not_free_consumed_slot(self):
        need,asset,resource,reservation=self.cycle();self.help_action(self.b,need,'withdraw')
        self.assertFalse(next(i for i in self.s.view(self.b)['items'] if i['kind']=='RESOURCE')['available'])

    def test_exports_redact_foreign_internal_identity_references(self):
        from xblago.integrations.privacy import PrivacyService
        need,note,permit,bridge=self.bridge()
        self.run_action(self.a,'request_consent',{'id':bridge,'recipient':self.code(self.lena)})
        foreign_uid=self.app.session(self.lena)[0]
        self.assertNotIn(foreign_uid,json.dumps(self.s.view(self.a)))
        self.assertNotIn(foreign_uid,json.dumps(PrivacyService(self.app).export(self.a)))

    def test_erasing_a_reserved_resource_does_not_bypass_help_confirmation_guard(self):
        need=self.need();asset,resource=self.resource();offer=self.offer(need,resource)
        self.run_action(self.b,'reserve',{'resource_id':resource,'need_id':need,'offer_id':offer})
        backup=self.app.store.backup_database()
        self.run_action(self.lena,'erase',{'id':resource})
        with self.assertRaises(ApiError):self.help_action(self.b,need,'confirm')
        app=WebApp(restore(backup,self.lab.journal));token=app.login('uel_synthetic_helper',PASSWORD)[0]
        self.h=HelpService(app)
        with self.assertRaises(ApiError):self.help_action(token,need,'confirm')
        with self.assertRaises(JournalUnavailable):LivingDeeds(app).view(token)

    def test_own_skill_hour_is_a_consented_canonical_capability_not_a_third_party_note(self):
        claim=body(label='Синтетический навык ремонта',category='bicycle_repair',quantity='1',unit='hour',right='USE')
        aid=self.r.draft(self.lena,claim,self.key())['id']
        p=self.r.prepare(self.lena,aid,1,dict(claim,available='1',share_consent=True))
        self.r.commit(self.lena,p['challenge'],CONFIRM)
        resource=self.run_action(self.lena,'offer_resource',{'asset_id':aid,'audience':[self.code(self.b)],
                    'starts_at':self.now+30,'ends_at':self.now+3630,'expires_at':self.now+7200})
        self.assertEqual(next(i for i in self.s.view(self.b)['items'] if i['kind']=='RESOURCE')['unit'],'hour')
        need=self.need();offer=self.offer(need,resource)
        self.run_action(self.b,'reserve',{'resource_id':resource,'need_id':need,'offer_id':offer})
        self.help_action(self.b,need,'confirm');self.help_action(self.lena,need,'confirm')
        self.assertEqual(self.s.view(self.b)['summary']['completed'],1)
