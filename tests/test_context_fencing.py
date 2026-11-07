"""Privacy 01.2: durable CAS vs deletion, independent processes, replay and DR."""
import concurrent.futures
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools.integration_fixture import create
from xblago.core.context_fencing import Generation,ContextConflict,SAFE_CONFLICT
from xblago.core.context_schema import migrate_context
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.core.storage import TelegramIdentity
from xblago.integrations.web import WebApp
from xblago.integrations.telegram import TelegramBot


def held_worker(cfg,uid,pipe,mode='web'):
    os.environ.update(cfg)
    app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
    class Provider:
        def generate(self,context):
            pipe.send({'loaded':True,'version_bound':bool(context.context_version)})
            pipe.recv()
            return 'Синтетический ответ из старого контекста.'
    try:
        target='xblago.integrations.web.build_chat_provider' if mode=='web' else 'xblago.integrations.telegram.build_chat_provider'
        with patch(target,return_value=Provider()):
            if mode=='web':app.chat(uid,'Где я живу?')
            else:
                bot=TelegramBot(None,Path(cfg['XBLAGO_DATA_DIR'])/'unused',app.store)
                bot.replies_for_update(update(4001))
        pipe.send({'rejected':False})
    except ContextConflict:pipe.send({'rejected':True})
    finally:pipe.close()


def crash_after_frontier(cfg,pipe):
    os.environ.update(cfg)
    from xblago.core.privacy_control import PrivacyControl
    store=preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')
    with store._connect() as db:uid=db.execute("SELECT blago_user_id FROM web_credentials WHERE username='synthetic_context'").fetchone()[0]
    g=Generation(store,uid);pipe.send(g.id)
    from xblago.core.messages import UserMessage
    class Provider:
        def generate(self,context):return 'Синтетический ответ.'
    g.agent(Provider()).respond(UserMessage('Где я живу?'))
    original=PrivacyControl.flush
    def flush(control):
        pending=control.db.execute("SELECT 1 FROM privacy_outbox WHERE json_extract(payload,'$.kind')='context_version'").fetchone()
        original(control)
        if pending:os._exit(76)
    with patch.object(PrivacyControl,'flush',flush):g.commit()


def crash_after_bot_commit(cfg):
    os.environ.update(cfg)
    store=preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3')
    bot=TelegramBot(None,Path(cfg['XBLAGO_DATA_DIR'])/'unused',store)
    bot.replies_for_update(update(5002));os._exit(77)


def update(identifier=4001):
    return {'update_id':identifier,'message':{'message_id':identifier,'from':{'id':70001,'first_name':'Synthetic'},'chat':{'id':70001,'type':'private'},'text':'Где я живу?'}}


class ContextFencingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=create(Path(self.tmp.name)/'new',help_enabled=True,context_fencing=True)
        self.cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-context-invite'
        self.env=patch.dict(os.environ,self.cfg);self.env.start();self.addCleanup(self.env.stop)
        self.path=Path(self.cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3';self.app=WebApp(preflight(self.path))
        self.token=self.app.register('synthetic_context','synthetic-password-only','synthetic-context-invite')[0]
        self.uid=self.app.session(self.token)[0];self.app.store.set_memory(self.uid,'city','Синтетический город')
    def integrity(self,store=None):
        with (store or self.app.store)._connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def staged(self):
        g=Generation(self.app.store,self.uid)
        class Provider:
            def generate(self,ctx):return 'Синтетический ответ.'
        from xblago.core.messages import UserMessage
        g.agent(Provider()).respond(UserMessage('Где я живу?'))
        return g
    def test_удаление_непосредственно_перед_commit_без_сиротской_реплики(self):
        g=self.staged();self.app.delete_memory(self.uid,'city')
        with self.assertRaises(ContextConflict):g.commit()
        self.assertEqual(self.app.history(self.uid),[])
        with self.app.store._connect() as db:self.assertEqual(db.execute('SELECT state FROM context_generations WHERE generation_id=?',(g.id,)).fetchone()[0],'REJECTED')
        with self.assertRaises(ContextConflict):g.commit()
        self.integrity()
    def test_буфер_памяти_не_записывается_при_конфликте(self):
        g=self.staged();g.memory.set('name','Синтетическое имя')
        self.app.delete_memory(self.uid,'city')
        with self.assertRaises(ContextConflict):g.commit()
        self.assertNotIn('name',self.app.store.all_memory(self.uid));self.assertEqual(self.app.history(self.uid),[])
    def test_две_одновременные_генерации_одна_пара_истории(self):
        first=self.staged();second=self.staged();first.commit()
        with self.assertRaises(ContextConflict):second.commit()
        self.assertEqual(len(self.app.history(self.uid)),2)
    def test_несколько_store_instances_одного_процесса(self):
        g=self.staged();other=WebApp(preflight(self.path));other.delete_memory(self.uid,'city')
        with self.assertRaises(ContextConflict):g.commit()
        self.assertEqual(other.history(self.uid),[])
    def test_два_процесса_удаление_во_время_LLM(self):
        ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe();p=ctx.Process(target=held_worker,args=(self.cfg,self.uid,child));p.start();child.close()
        try:
            self.assertTrue(parent.poll(20));self.assertTrue(parent.recv()['version_bound'])
            self.assertTrue(self.app.delete_memory(self.uid,'city'));parent.send('finish')
            self.assertTrue(parent.poll(20));self.assertTrue(parent.recv()['rejected']);p.join(10);self.assertEqual(p.exitcode,0)
            self.assertEqual(self.app.history(self.uid),[])
        finally:
            parent.close()
            if p.is_alive():p.kill();p.join()
    def test_смерть_worker_во_время_LLM_нет_записи_после_restart(self):
        ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe();p=ctx.Process(target=held_worker,args=(self.cfg,self.uid,child));p.start();child.close()
        try:
            self.assertTrue(parent.poll(20));parent.recv();self.app.delete_memory(self.uid,'city');p.kill();p.join(10)
            app=WebApp(preflight(self.path));self.assertEqual(app.history(self.uid),[])
            self.assertNotIn('city',app.store.all_memory(self.uid))
        finally:
            parent.close()
            if p.is_alive():p.kill();p.join()
    def test_изменение_и_пустое_удаление_инвалидируют(self):
        for mutate in (lambda:self.app.store.set_memory(self.uid,'city','Новый город'),lambda:self.app.delete_memory(self.uid,'absent'),lambda:self.app.store.clear_history(self.uid),lambda:self.app.store.clear_memory(self.uid)):
            g=self.staged();mutate()
            with self.assertRaises(ContextConflict):g.commit()
    def test_чужой_участник_не_инвалидирует_контекст(self):
        other=self.app.register('another_context','synthetic-password-only','synthetic-context-invite')[0]
        g=self.staged();self.app.store.set_memory(self.app.session(other)[0],'city','Другой город');g.commit()
        self.assertEqual(len(self.app.history(self.uid)),2)
    def test_нет_SQL_write_lock_на_время_LLM(self):
        g=self.staged()
        with self.app.store._locked_transaction() as db:db.execute('UPDATE profiles SET display_name=? WHERE blago_user_id=?',('Новое имя',self.uid))
        with self.assertRaises(ContextConflict):g.commit()
    def test_старый_backup_не_возвращает_версию_и_ticket(self):
        g=self.staged();backup=self.app.store.backup_database();before=g.version
        self.app.delete_memory(self.uid,'city')
        restored=restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with restored._connect() as db:
            after=tuple(db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?',(self.uid,)).fetchone())
            self.assertGreater(after[0],before[0]);self.assertNotEqual(after[1],before[1])
            self.assertEqual(db.execute('SELECT state FROM context_generations WHERE generation_id=?',(g.id,)).fetchone()[0],'REJECTED')
        g.store=restored
        with self.assertRaises(ContextConflict):g.commit()
        self.assertNotIn('city',restored.all_memory(self.uid));self.integrity(restored)
    def test_простой_preflight_старого_backup_сверяет_retained_frontier(self):
        g=self.staged();backup=self.app.store.backup_database();self.app.delete_memory(self.uid,'city')
        store=preflight(backup);g.store=store
        with self.assertRaises(ContextConflict):g.commit()
    def test_отзыв_сессии_и_удаление_аккаунта(self):
        g=self.staged();self.app.logout(self.token)
        with self.assertRaises(ContextConflict):g.commit()
        token=self.app.login('synthetic_context','synthetic-password-only')[0]
        from xblago.integrations.privacy import PrivacyService
        privacy=PrivacyService(self.app);g=self.staged();preview=privacy.preview(token);privacy.delete(token,preview['challenge'],'УДАЛИТЬ')
        with self.assertRaises(ContextConflict):g.commit()
        self.integrity()
    def test_согласие_и_блокировка_инвалидируют(self):
        g=self.staged()
        with self.app.store._locked_transaction() as db:db.execute("INSERT INTO consent_records VALUES (?,?,?,'synthetic','withdrawn',1)",(secrets.token_hex(16),self.uid,'optional_activity'))
        with self.assertRaises(ContextConflict):g.commit()
        g=self.staged()
        with self.app.store._locked_transaction() as db:db.execute("UPDATE users SET status='blocked' WHERE blago_user_id=?",(self.uid,))
        with self.assertRaises(ContextConflict):g.commit()
    def test_миграция_повтор_rollback_и_старый_backup_до_DDL(self):
        self.assertFalse(migrate_context(self.path))
        other=Path(self.tmp.name)/'old';cfg=create(other,help_enabled=True);path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,cfg):
            from xblago.core import context_schema
            with patch.object(context_schema,'DDL',context_schema.DDL+('INVALID SQL',)):
                with self.assertRaises(Exception):migrate_context(path)
            with preflight(path)._connect() as db:self.assertFalse(context_schema.present(db))
            self.assertTrue(migrate_context(path));self.assertFalse(migrate_context(path))
    def test_telegram_повтор_без_LLM_и_без_дублирования_пары(self):
        class Provider:
            calls=0
            def generate(self,ctx):self.calls+=1;return 'Безопасный синтетический ответ.'
        provider=Provider();bot=TelegramBot(None,Path(self.tmp.name)/'unused',self.app.store)
        with patch('xblago.integrations.telegram.build_chat_provider',return_value=provider):
            first=bot.replies_for_update(update());second=bot.replies_for_update(update())
            self.assertEqual(first,second);self.assertEqual(provider.calls,1)
        uid=self.app.store.resolve_telegram_user(TelegramIdentity(70001,70001,'private'))
        self.assertEqual(len(self.app.history(uid)),2)
        self.app.store.set_memory(uid,'city','Другой город')
        with patch('xblago.integrations.telegram.build_chat_provider',side_effect=AssertionError('no replay LLM')):
            stale=bot.replies_for_update(update())
        self.assertNotEqual(first,stale);self.assertIn(SAFE_CONFLICT,stale[0]['text'])
    def test_telegram_rejected_update_не_перегенерируется(self):
        bot=TelegramBot(None,Path(self.tmp.name)/'unused',self.app.store)
        app=self.app;uid=app.store.resolve_telegram_user(TelegramIdentity(70001,70001,'private'))
        class Provider:
            def generate(self,ctx):
                WebApp(preflight(app.store.path)).delete_memory(uid,'city')
                return 'Старый ответ.'
        with patch('xblago.integrations.telegram.build_chat_provider',return_value=Provider()):first=bot.replies_for_update(update())
        with patch('xblago.integrations.telegram.build_chat_provider',side_effect=AssertionError('no replay')):second=bot.replies_for_update(update())
        self.assertEqual(first,second);self.assertIn(SAFE_CONFLICT,first[0]['text']);self.assertEqual(app.history(uid),[])
    def test_сбой_при_вставке_второй_реплики_откатывает_память_и_первую(self):
        g=self.staged();g.memory.set('name','Синтетическое имя')
        with self.app.store._connect() as db:
            db.execute("CREATE TRIGGER injected_history_failure BEFORE INSERT ON dialogue_turns WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT,'synthetic'); END");db.commit()
        with self.assertRaises(Exception):g.commit()
        self.assertNotIn('name',self.app.store.all_memory(self.uid));self.assertEqual(self.app.history(self.uid),[])
        with self.app.store._connect() as db:db.execute('DROP TRIGGER injected_history_failure');db.commit()
    def test_Help_не_изменяет_семантику_подтверждений(self):
        from xblago.domain.help_service import HelpService
        h=HelpService(self.app);b=self.app.register('helper_context','synthetic-password-only','synthetic-context-invite')[0]
        rid=h.draft(self.token,'Нужна синтетическая помощь',[h.code(b)['code']],secrets.token_hex(16))['id']
        p=h.prepare(self.token,rid,1,'publish',{});receipt=h.commit(self.token,p['challenge'],'ПОДТВЕРЖДАЮ');self.assertEqual(receipt['state'],'PUBLISHED')
        self.assertEqual(receipt,h.commit(self.token,p['challenge'],'ПОДТВЕРЖДАЮ'));self.integrity()
    def test_подмена_версии_без_journal_не_принимается(self):
        with self.app.store._connect() as db:
            # Function returns replay mode to suppress journal creation: simulate illicit edit.
            db.create_function('blago_replaying',0,lambda:1)
            db.create_function('blago_tag',-1,lambda *args:'synthetic')
            db.execute('UPDATE context_versions SET revision=0 WHERE blago_user_id=?',(self.uid,));db.commit()
        with self.assertRaises(JournalUnavailable):preflight(self.path)
    def test_CAS_и_пара_истории_сериализуются_до_удаления(self):
        g=self.staged();other=WebApp(preflight(self.path));ready=threading.Event();release=threading.Event();deleted=threading.Event()
        original=self.app.store.append_exchange
        def hold(*args,**kwargs):
            ready.set();self.assertTrue(release.wait(10));return original(*args,**kwargs)
        def delete():
            other.delete_memory(self.uid,'city');deleted.set()
        with patch.object(self.app.store,'append_exchange',side_effect=hold),concurrent.futures.ThreadPoolExecutor(2) as pool:
            commit=pool.submit(g.commit);self.assertTrue(ready.wait(10));deletion=pool.submit(delete)
            self.assertFalse(deleted.wait(.1));release.set();commit.result(10);deletion.result(10)
        self.assertEqual(len(other.history(self.uid)),2);self.assertNotIn('city',other.store.all_memory(self.uid));self.integrity()
    def test_три_независимых_процесса_оба_stale_ответа_отклонены(self):
        ctx=multiprocessing.get_context('spawn');workers=[]
        try:
            for _ in range(2):
                parent,child=ctx.Pipe();proc=ctx.Process(target=held_worker,args=(self.cfg,self.uid,child));proc.start();child.close();workers.append((proc,parent))
            for proc,pipe in workers:self.assertTrue(pipe.poll(20));self.assertTrue(pipe.recv()['loaded'])
            self.app.delete_memory(self.uid,'city')
            for proc,pipe in workers:pipe.send('finish')
            for proc,pipe in workers:self.assertTrue(pipe.poll(20));self.assertTrue(pipe.recv()['rejected']);proc.join(10);self.assertEqual(proc.exitcode,0)
            self.assertEqual(self.app.history(self.uid),[])
        finally:
            for proc,pipe in workers:
                pipe.close()
                if proc.is_alive():proc.kill();proc.join()
    def test_перезапуск_после_COMMITTED_не_фиксирует_ticket_повторно(self):
        g=self.staged();g.commit();g.store=preflight(self.path)
        with self.assertRaises(ContextConflict):g.commit()
        self.assertEqual(len(self.app.history(self.uid)),2)
    def test_старый_backup_до_миграции_требует_явного_DDL_и_reconcile(self):
        root=Path(self.tmp.name)/'before-fence';cfg=create(root,help_enabled=True);path=Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(path));app.register('old_context','synthetic-password-only','synthetic-context-invite');backup=app.store.backup_database()
            migrate_context(path)
            with self.assertRaises(JournalUnavailable):preflight(backup)
            migrate_context(backup);store=preflight(backup)
            with store._connect() as db:self.assertGreater(db.execute('SELECT min(revision) FROM context_versions').fetchone()[0],0)
    def test_веб_HTTP_конфликт_409_без_истории_или_Help_suggestion(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from xblago.integrations.web import BlagoWebHandler
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=self.app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();app=self.app;uid=self.uid
        class Provider:
            def generate(self,ctx):WebApp(preflight(app.store.path)).delete_memory(uid,'city');return 'Старый ответ.'
        try:
            req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/v1/chat/messages',data=json.dumps({'text':'Мне нужна помощь'}).encode(),headers={'Content-Type':'application/json','Cookie':'blago_session='+self.token,'X-CSRF-Token':self.app.session(self.token)[1]})
            with patch('xblago.integrations.web.build_chat_provider',return_value=Provider()):
                with self.assertRaises(HTTPError) as caught:urlopen(req,timeout=10)
            self.assertEqual(caught.exception.code,409);body=json.load(caught.exception)
            self.assertEqual(body['status'],'context_conflict');self.assertNotIn('reply',body);self.assertNotIn('help_suggestion',body)
            self.assertEqual(self.app.history(self.uid),[])
        finally:server.shutdown();server.server_close();thread.join(5)
    def test_crash_после_frontier_до_SQL_не_воскрешает_пару(self):
        ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe();p=ctx.Process(target=crash_after_frontier,args=(self.cfg,child));p.start();child.close()
        try:
            self.assertTrue(parent.poll(20));gid=parent.recv();p.join(20);self.assertEqual(p.exitcode,76)
            store=preflight(self.path)
            self.assertEqual(WebApp(store).history(self.uid),[])
            with store._connect() as db:self.assertEqual(db.execute('SELECT state FROM context_generations WHERE generation_id=?',(gid,)).fetchone()[0],'REJECTED')
        finally:
            parent.close()
            if p.is_alive():p.kill();p.join()
    def test_telegram_crash_после_commit_до_ACK_replay_без_новой_генерации(self):
        p=multiprocessing.get_context('spawn').Process(target=crash_after_bot_commit,args=(self.cfg,));p.start();p.join(20)
        if p.is_alive():p.kill();p.join();self.fail('worker hung')
        self.assertEqual(p.exitcode,77)
        store=preflight(self.path);bot=TelegramBot(None,Path(self.tmp.name)/'unused',store)
        with patch('xblago.integrations.telegram.build_chat_provider',side_effect=AssertionError('no new LLM on replay')):receipt=bot.replies_for_update(update(5002))
        self.assertTrue(receipt)
        uid=store.resolve_telegram_user(TelegramIdentity(70001,70001,'private'));self.assertEqual(len(WebApp(store).history(uid)),2)
    def test_сессия_другого_участника_не_авторизует_переданный_uid(self):
        token=self.app.register('other_auth_context','synthetic-password-only','synthetic-context-invite')[0]
        with patch('xblago.integrations.web.build_chat_provider') as provider:
            with self.assertRaises(ContextConflict):self.app.chat(self.uid,'Где я живу?',token=token)
            provider.return_value.generate.assert_not_called()
    def test_естественные_исправления_памяти_и_версии_сохранены(self):
        self.app.chat(self.uid,'Я предпочитаю чай')
        self.app.chat(self.uid,'Раньше я любил чай, теперь предпочитаю кофе')
        facts=self.app.store.all_memory(self.uid)
        self.assertEqual(facts['preference:drink'],'кофе')
        history=self.app.store.all_semantic_memory(self.uid,include_inactive=True)
        self.assertTrue(any(r.status=='superseded' for r in history))
        self.app.chat(self.uid,'Забудь мой город');self.assertNotIn('city',self.app.store.all_memory(self.uid))
        self.assertEqual(len(self.app.history(self.uid)),6)
    def test_старый_worker_на_исходном_DB_отклоняется_после_restore_копии(self):
        g=self.staged();backup=self.app.store.backup_database()
        restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR']))
        with self.assertRaises(ContextConflict):g.commit()
        self.assertEqual(self.app.history(self.uid),[])
    def test_очистка_истории_сохраняет_только_атомарную_пару_команды(self):
        self.app.chat(self.uid,'Привет');self.app.chat(self.uid,'/clear-history')
        self.assertEqual(len(self.app.history(self.uid)),2)
        self.assertIn('clear-history',self.app.history(self.uid)[0]['text'])
    def test_изменение_session_expiry_или_CSRF_инвалидирует(self):
        for column in ('expires_at','csrf'):
            g=self.staged()
            with self.app.store._locked_transaction() as db:
                value='2000-01-01T00:00:00+00:00' if column=='expires_at' else secrets.token_hex(24)
                db.execute('UPDATE web_sessions SET '+column+'=? WHERE blago_user_id=?',(value,self.uid))
            with self.assertRaises(ContextConflict):g.commit()
    def test_изменение_last_seen_не_инвалидирует(self):
        g=self.staged();self.app.session(self.token);g.commit()
        self.assertEqual(len(self.app.history(self.uid)),2)
    def test_другая_копия_DB_меняет_frontier_во_время_final_commit(self):
        g=self.staged();backup=self.app.store.backup_database();other=WebApp(preflight(backup))
        ready=threading.Event();release=threading.Event();original=self.app.store.append_exchange
        def hold(*args,**kwargs):
            ready.set();self.assertTrue(release.wait(10));return original(*args,**kwargs)
        with patch.object(self.app.store,'append_exchange',side_effect=hold),concurrent.futures.ThreadPoolExecutor(1) as pool:
            pending=pool.submit(g.commit);self.assertTrue(ready.wait(10))
            other.delete_memory(self.uid,'city');release.set()
            with self.assertRaises(JournalUnavailable):pending.result(10)
        store=preflight(self.path);self.assertEqual(WebApp(store).history(self.uid),[])
        self.assertNotIn('city',store.all_memory(self.uid));self.integrity(store)
    def test_telegram_link_сохраняет_данные_и_инвалидирует_обе_генерации(self):
        source=self.app.store.resolve_telegram_user(TelegramIdentity(71001,71001,'private'))
        self.app.store.set_memory(source,'name','Синтетический участник')
        source_generation=Generation(self.app.store,source);target_generation=self.staged()
        code=self.app.new_link_code(self.uid,self.token)
        self.assertEqual(self.app.redeem_telegram_link(source,code),self.uid)
        for g in (source_generation,target_generation):
            with self.assertRaises(ContextConflict):g.commit()
        self.assertEqual(self.app.store.all_memory(self.uid)['name'],'Синтетический участник')
        self.integrity()
