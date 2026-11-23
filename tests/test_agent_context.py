"""Synthetic-only evidence for audit #38 C01–C14; no model quality claim."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.integration_fixture import create
from xblago.core.agent_context import explain
from xblago.core.agent_context_schema import FLAG, migrate
from xblago.core.context_fencing import Generation, ContextConflict
from xblago.core.integration import preflight
from xblago.core.llm import OpenAICompatibleProvider, LlmProviderError
from xblago.core.memory_policy import assertion_text, grounded
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.integrations.web import WebApp
from tests.test_context_fencing import ContextFencingTests as _FencingTests


class Capture:
    def __init__(self):
        self.contexts = []
    def generate(self, context):
        self.contexts.append(context)
        return 'Обсудим это по существу.'


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {FLAG: 'false'}):
            self.cfg = create(Path(self.tmp.name)/'new', help_enabled=True, context_fencing=True, lifecycle=True)
        self.cfg['XBLAGO_WEB_INVITE_CODE'] = 'synthetic-context-only'
        self.env = patch.dict(os.environ, self.cfg | {FLAG: 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = Path(self.cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        migrate(self.path)
        os.environ[FLAG] = 'true'
        self.app = WebApp(preflight(self.path))
        self.token = self.app.register('synthetic_context_owner', 'synthetic-password-only', 'synthetic-context-only')[0]
        self.uid = self.app.session(self.token)[0]
        self.provider = Capture()
        self.providers = patch('xblago.integrations.web.build_chat_provider', return_value=self.provider)
        self.providers.start()
        self.addCleanup(self.providers.stop)
    def chat(self, text):
        return self.app.chat(self.uid, text, self.token)
    def context(self, query='Где я живу?'):
        self.chat(query)
        return self.provider.contexts[-1]
    def prompt(self, context):
        return OpenAICompatibleProvider._system_prompt(None, context)
    def test_C01_profile_is_participant_not_agent(self):
        self.app.update_me(self.uid, {'display_name': 'Синтетическая Анна'})
        context = self.context('Продолжим')
        self.assertEqual(context.participant.profile['display_name'], 'Синтетическая Анна')
        self.assertNotEqual(context.agent_profile.name, 'Синтетическая Анна')
        self.assertNotIn(self.uid, self.prompt(context))
    def test_C02_old_assertion_selected_after_long_dialogue_and_restart(self):
        self.chat('Я живу в Казани')
        for i in range(35):
            self.chat(f'Обсудим ремонт механизма номер {i}')
        self.app = WebApp(preflight(self.path))
        context = self.context()
        evidence = explain(context.participant, context.memory, context.user_text)['city']
        self.assertEqual(evidence['source']['quote'], 'Казани')
        self.assertEqual(evidence['source']['turn_id'], 1)
        self.assertEqual(evidence['reason'], 'query_relevance')
        self.assertNotIn('Я живу в Казани', [t.text for t in context.history])
        self.assertIn('Казани', self.prompt(context))
    def test_C03_name_core_on_generic_continuation(self):
        self.chat('Меня зовут Анна')
        context = self.context('Продолжим')
        facts = explain(context.participant, context.memory, context.user_text)
        self.assertEqual(facts['name']['value'], 'Анна')
        self.assertEqual(facts['name']['reason'], 'stable_address_or_style')
    def test_C12_displayed_name_source_before_and_after_history_clear(self):
        self.chat('Меня зовут Алина')
        count = len(self.provider.contexts)
        known = self.chat('Откуда ты знаешь моё имя?')
        self.assertIn('Алина', known)
        self.assertIn('точный фрагмент: «Алина»', known)
        self.assertNotIn('задавали вопрос', known)
        self.chat('/clear-history')
        unknown = self.chat('Откуда ты знаешь моё имя?')
        self.assertIn('Алина', unknown)
        self.assertIn('Исходная реплика сейчас недоступна', unknown)
        self.assertNotIn('не знаю', unknown)
        self.assertEqual(self.app.store.all_memory(self.uid)['name'], 'Алина')
        self.assertEqual(len(self.provider.contexts), count)
        self.app.delete_memory(self.uid, 'name')
        missing = self.chat('Откуда ты знаешь моё имя?')
        self.assertNotIn('Алина', missing)
        self.assertIn('нет этих сведений', missing)

    def test_C12_city_source_quotes_only_retained_value(self):
        self.chat('Я живу в Перми')
        reply = self.chat('Откуда ты знаешь, в каком городе я живу?')
        self.assertIn('«Перми»', reply)
        self.assertIn('твоё сообщение', reply)

    def test_C12_source_reply_cannot_commit_after_concurrent_deletion(self):
        from xblago.core.messages import UserMessage
        self.chat('Меня зовут Алина')
        generation = Generation(self.app.store, self.uid)
        response = generation.agent(self.provider).respond(UserMessage('Откуда ты знаешь моё имя?'))
        self.app.delete_memory(self.uid, 'name')
        with self.assertRaises(ContextConflict):
            generation.commit()
        self.assertNotIn('name', self.app.store.all_memory(self.uid))

    def test_source_question_about_someone_else_is_not_owner_lookup(self):
        self.chat('Меня зовут Алина')
        count = len(self.provider.contexts)
        answer = self.chat('Откуда ты знаешь имя моего друга?')
        self.assertEqual(len(self.provider.contexts), count+1)
        self.assertNotIn('точный фрагмент', answer)
    def test_conflicting_chosen_names_use_neutral_address(self):
        self.app.update_me(self.uid,{'display_name':'Синтетическая Анна'})
        self.chat('Меня зовут Вера')
        context = self.context('Продолжим')
        self.assertTrue(context.participant.profile['address_ambiguity'])
        self.assertNotIn('name',explain(context.participant,context.memory,'Продолжим'))
        self.assertNotIn('Синтетическая Анна',self.prompt(context))
    def test_forgotten_name_not_reintroduced_by_profile_or_backup(self):
        self.app.update_me(self.uid,{'display_name':'Анна'})
        self.chat('Меня зовут Анна')
        backup=self.app.store.backup_database()
        self.app.delete_memory(self.uid,'name')
        context=self.context('Продолжим')
        self.assertNotIn('Анна',self.prompt(context))
        self.app=WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        self.token=self.app.login('synthetic_context_owner','synthetic-password-only')[0]
        self.assertNotIn('Анна',self.prompt(self.context('Продолжим')))
        self.app.update_me(self.uid,{'display_name':'Анна'})
        self.assertEqual(self.context('Продолжим').participant.profile['display_name'],'Анна')
    def test_multiple_facts_in_source_do_not_leak_a_deleted_sibling(self):
        class Structured(OpenAICompatibleProvider):
            def __init__(self):pass
            def generate(self,ctx):
                if ctx.purpose!='memory_extraction':return 'Синтетический ответ.'
                return json.dumps({'items':[
                    {'key':key,'value':value,'category':'fact','claim':'assertion','subject':'self',
                     'confidence':1.0,'evidence':evidence}
                    for key,value,evidence in [('name','Анна','Меня зовут Анна'),('city','Казани','Я живу в Казани')]]})
        with patch('xblago.integrations.web.build_chat_provider',return_value=Structured()):
            self.chat('Меня зовут Анна; Я живу в Казани')
        self.app.delete_memory(self.uid,'city')
        context=self.context('Продолжим')
        self.assertEqual(context.participant.origins['name']['quote'],'Анна')
        self.assertNotIn('Казани',self.prompt(context))
    def test_history_barrier_does_not_reintroduce_welcome(self):
        self.chat('Я живу в Казани')
        self.app.delete_memory(self.uid,'city')
        self.assertEqual(self.chat('Привет'),'Я здесь.')
        self.assertTrue(self.context('Продолжим').conversation.continuing)
    def test_C04_C05_direct_and_mixed_question(self):
        self.chat('Я живу в Казани, что здесь посмотреть?')
        self.assertEqual(self.app.store.all_memory(self.uid)['city'], 'Казани')
        context = self.context()
        self.assertEqual(context.participant.origins['city']['quote'], 'Казани')
        self.assertTrue(grounded('Казани', 'Я живу в Казани', 'Я живу в Казани, что здесь посмотреть?'))
        self.assertFalse(grounded('Москву', 'Я живу в Казани, как добраться в Москву?', 'Я живу в Казани, как добраться в Москву?'))
    def test_C06_hypotheses_quotes_other_people_do_not_become_owner_facts(self):
        for text in ('Мой друг живёт в Казани', 'Возможно, я живу в Казани', '«Я живу в Казани»',
                     'Если я живу в Казани, что посмотреть?', 'Я не живу в Казани, что посмотреть?',
                     'Я живу в Казани, но это предположение', 'Я живу в Казани, что не следует делать?'):
            with self.subTest(text=text):
                self.chat(text)
                self.assertNotIn('city', self.app.store.all_memory(self.uid))
    def test_C07_correction_removes_old_prompt_and_origin(self):
        self.chat('Я живу в Москве')
        self.chat('Я больше не живу в Москве, теперь живу в Казани')
        context = self.context()
        self.assertEqual(context.memory['city'], 'Казани')
        self.assertEqual(context.participant.origins['city']['turn_id'], 3)
        self.assertNotIn('Москве', self.prompt(context))
        self.assertFalse(any('Москве' in t.text for t in context.history))
        current = [r for r in self.app.store.all_semantic_memory(self.uid) if r.key == 'city']
        self.assertEqual(len(current), 1)

    def test_C07_failed_correction_is_explicit_and_shared_across_channels(self):
        import threading
        from http.server import ThreadingHTTPServer
        from tools.unified_mvp_check import Client, init_data, bot_update
        from xblago.integrations.web import BlagoWebHandler
        from xblago.integrations.telegram import TelegramBot
        os.environ['TELEGRAM_BOT_TOKEN'] = '123:synthetic-help011-only'
        os.environ['XBLAGO_COOKIE_SECURE'] = 'false'  # Disposable localhost HTTP only.
        class Controlled(Capture):
            fail = False
            def generate(provider, context):
                provider.contexts.append(context)
                if provider.fail:
                    raise LlmProviderError('Synthetic controlled failure', category='unavailable')
                return 'В памяти указан город проживания: '+context.memory.get('city','UNKNOWN')+'.'
        provider = Controlled()
        self.providers.stop()
        self.providers = patch('xblago.integrations.web.build_chat_provider',return_value=provider)
        self.providers.start()
        self.addCleanup(self.providers.stop)
        with patch('xblago.integrations.telegram.build_chat_provider',return_value=provider):
            server = ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler)
            server.blago_web_app = self.app
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                web,mini = [Client('http://127.0.0.1:'+str(server.server_port)) for _ in range(2)]
                web.ok('POST','auth/login',{'username':'synthetic_context_owner','password':'synthetic-password-only'})
                def say(client,text):
                    return client.ok('POST','chat/messages',{'text':text})['reply']
                say(web,'Я живу в Перми')
                code = web.ok('POST','auth/link-code',{})['code']
                bot = TelegramBot(None,Path(self.tmp.name)/'unused',self.app.store)
                bot.replies_for_update(bot_update(97100,'/link '+code))
                web.ok('POST','auth/login',{'username':'synthetic_context_owner','password':'synthetic-password-only'})
                challenge = mini.ok('POST','auth/telegram/challenge',{})
                mini.ok('POST','auth/telegram',{'challenge':challenge['challenge'],'init_data':init_data()})
                self.assertEqual(mini.ok('GET','me')['profile']['blago_user_id'],self.uid)
                correction = 'Я больше не живу в Перми, теперь живу в Томске'
                provider.fail = True
                failed = say(web,correction)
                self.c07_evidence = {'failed_reply':failed,'pending_answers':{},'saved_answers':{}}
                self.assertIn('не сохранена',failed)
                self.assertIn('Перми',failed)
                self.assertIn('Томске',failed)
                self.assertEqual(self.app.store.all_memory(self.uid)['city'],'Перми')
                # Reopen the store; process-local state must not carry the warning.
                server.blago_web_app = WebApp(preflight(self.path))
                bot = TelegramBot(None,Path(self.tmp.name)/'unused',server.blago_web_app.store)
                for channel,answer in [('web',say(web,'Где я живу?')),
                        ('bot','\n'.join(r['text'] for r in bot.replies_for_update(bot_update(97101,'Где я живу?')))),
                        ('mini',say(mini,'Где я живу?'))]:
                    with self.subTest(channel=channel,phase='failed'):
                        self.c07_evidence['pending_answers'][channel] = answer
                        self.assertIn('Перми',answer)
                        self.assertIn('Томске',answer)
                        self.assertIn('не сохранена',answer)
                provider.fail = False
                say(mini,correction)
                for channel,answer in [('web',say(web,'Где я живу?')),
                        ('bot','\n'.join(r['text'] for r in bot.replies_for_update(bot_update(97102,'Где я живу?')))),
                        ('mini',say(mini,'Где я живу?'))]:
                    with self.subTest(channel=channel,phase='saved'):
                        self.c07_evidence['saved_answers'][channel] = answer
                        self.assertIn('Томске',answer)
                        self.assertNotIn('Перми',answer)
                        self.assertNotIn('не сохранена',answer)
                context = provider.contexts[-1]
                origin = context.participant.origins['city']
                with self.app.store._connect() as db:
                    source = db.execute('SELECT text FROM dialogue_turns WHERE id=? AND blago_user_id=?',
                                        (origin['turn_id'],self.uid)).fetchone()[0]
                self.assertEqual(source,correction)
                self.assertEqual(origin['quote'],'Томске')
                self.assertEqual(source[slice(*origin['span'])],'Томске')
                self.assertFalse(context.participant.pending_corrections)
                self.c07_evidence['source'] = origin
                self.c07_evidence['source_turn_text'] = source
                self.c07_evidence['canonical_city'] = self.app.store.all_memory(self.uid)['city']
            finally:
                server.shutdown();server.server_close();thread.join(5)

    def test_C07_pending_correction_removed_by_deletion_and_old_backup_restore(self):
        self.chat('Я живу в Перми')
        with patch.object(self.provider,'generate',side_effect=LlmProviderError('Synthetic failure',category='unavailable')):
            self.assertIn('не сохранена',self.chat('Я живу в Томске'))
        backup = self.app.store.backup_database()
        self.assertEqual(self.context('Продолжим').participant.pending_corrections['city']['value'],'Томске')
        self.app.delete_memory(self.uid,'city')
        self.app = WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        self.token = self.app.login('synthetic_context_owner','synthetic-password-only')[0]
        context = self.context('Продолжим')
        self.assertNotIn('city',context.memory)
        self.assertFalse(context.participant.pending_corrections)
        self.assertNotIn('Томске',self.prompt(context))

    def test_C07_pending_reply_rejected_if_fact_deleted_before_commit(self):
        from xblago.core.messages import UserMessage
        self.chat('Я живу в Перми')
        with patch.object(self.provider,'generate',side_effect=LlmProviderError('Synthetic failure',category='incomplete')):
            self.chat('Я больше не живу в Перми, теперь живу в Томске')
        generation = Generation(self.app.store,self.uid)
        generation.agent(self.provider).respond(UserMessage('Где я живу?'))
        self.app.delete_memory(self.uid,'city')
        with self.assertRaises(ContextConflict):
            generation.commit()

    def test_C07_reaffirmed_old_city_supersedes_pending_without_automatic_retry(self):
        self.chat('Я живу в Перми')
        with patch.object(self.provider,'generate',side_effect=LlmProviderError('Synthetic failure',category='unavailable')):
            self.chat('Я живу в Томске')
        self.chat('Я живу в Перми')
        self.assertFalse(self.context('Продолжим').participant.pending_corrections)
    def test_current_correction_never_generates_from_old_memory_or_history(self):
        self.chat('Я живу в Москве')
        self.chat('Я живу в Казани')
        context=self.provider.contexts[-1]
        self.assertEqual(context.memory['city'],'Казани')
        self.assertFalse(context.history)
        self.assertNotIn('Москве',self.prompt(context))
        self.chat('Я больше не живу в Казани')
        context=self.provider.contexts[-1]
        self.assertNotIn('city',context.memory)
        self.assertFalse(context.history)
    def test_C08_veto_keeps_dialogue_without_fact(self):
        self.assertTrue(self.chat('Не запоминай это, но я живу в Казани, что здесь посмотреть?'))
        self.assertEqual(self.app.store.all_memory(self.uid), {})
    def test_delete_excludes_old_quotes_and_repeated_assistant_mentions(self):
        self.chat('Я живу в Казани')
        self.app.store.append_history(self.uid, 'assistant', 'В Казани можно гулять.')
        before = self.app.history(self.uid)
        self.app.delete_memory(self.uid, 'city')
        context = self.context()
        self.assertNotIn('city', context.memory)
        self.assertNotIn('city', context.participant.origins)
        self.assertFalse(any('Казани' in t.text for t in context.history))
        self.assertGreaterEqual(len(self.app.history(self.uid)), len(before))
        self.assertNotIn('Казани', self.prompt(context))
    def test_C12_real_source_unknown_legacy_and_no_invented_quote(self):
        self.app.store.set_memory(self.uid, 'city', 'Казани')
        context = self.context()
        self.assertEqual(explain(context.participant, context.memory, context.user_text)['city']['source'], 'UNKNOWN')
        self.assertEqual(explain(context.participant, context.memory, context.user_text)['city']['assertion'], 'UNVERIFIED_STORED_CLAIM')
        self.chat('Меня зовут Анна')
        context = self.context('Продолжим')
        self.assertFalse(explain(context.participant, context.memory, 'Продолжим')['name']['independently_verified'])
        with self.app.store._locked_transaction() as db:
            db.execute("UPDATE semantic_memory SET source='self_assertion|context_turn:999999' WHERE blago_user_id=?", (self.uid,))
        self.assertEqual(self.context('Продолжим').participant.origins, {})
    def test_deleted_source_not_reconstructed_from_other_turn(self):
        self.chat('Я живу в Казани')
        self.app.store.clear_history(self.uid)
        context = self.context()
        self.assertEqual(context.participant.origins, {})
        self.assertEqual(context.memory['city'], 'Казани')
    def test_C11_foreign_profile_memory_history_and_source_excluded(self):
        token = self.app.register('synthetic_context_other', 'synthetic-password-only', 'synthetic-context-only')[0]
        other = self.app.session(token)[0]
        self.app.chat(other, 'Я живу в Омске', token)
        context = self.context()
        self.assertNotIn('Омске', self.prompt(context))
        self.assertFalse(context.participant.origins)
        with self.assertRaises(ContextConflict):
            self.app.chat(self.uid, 'Продолжим', token)
    def test_C13_provider_failure_cannot_write_source_or_fact(self):
        with patch.object(self.provider, 'generate', side_effect=LlmProviderError('synthetic unavailable')):
            self.chat('Я живу в Казани')
        self.assertFalse(self.app.store.all_memory(self.uid))
        self.assertFalse(self.context().participant.origins)
    def test_no_action_or_sensitive_data_from_context_projection(self):
        self.chat('Мне нужен ноутбук')
        self.chat('Мой пароль synthetic-secret-only')
        context = self.context()
        self.assertNotIn('synthetic-secret-only', self.prompt(context))
        with self.app.store._connect() as db:
            for table in ('help_requests', 'blago_needs', 'blago_resources', 'blago_payment_requests'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
    def test_idempotent_migration_and_old_checksums(self):
        self.assertFalse(migrate(self.path))
        from xblago.core.context_schema import migrate_context
        from xblago.core.lifecycle_schema import migrate_lifecycle
        self.assertFalse(migrate_context(self.path))
        self.assertFalse(migrate_lifecycle(self.path))
        with self.app.store._connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
    def test_flag_off_after_install_fails_closed(self):
        with patch.dict(os.environ, {FLAG: 'false'}), self.assertRaises(JournalUnavailable):
            self.chat('Продолжим')
    def test_source_pointer_cannot_read_foreign_turn(self):
        token = self.app.register('synthetic_foreign_source','synthetic-password-only','synthetic-context-only')[0]
        other = self.app.session(token)[0]
        self.app.chat(other,'Я живу в Казани',token)
        self.chat('Я живу в Казани')
        with self.app.store._locked_transaction() as db:
            foreign = db.execute("SELECT id FROM dialogue_turns WHERE blago_user_id=? AND role='user'",(other,)).fetchone()[0]
            db.execute('UPDATE semantic_memory SET source=? WHERE blago_user_id=?',('self_assertion|context_turn:'+str(foreign),self.uid))
        self.assertNotIn('city',self.context().participant.origins)
    def test_old_backup_without_optional_schema_requires_explicit_migration(self):
        from xblago.core import agent_context_schema
        with self.app.store._locked_transaction() as db:
            for sql in reversed(agent_context_schema.DDL):
                kind = 'TABLE' if sql.startswith('CREATE TABLE') else 'TRIGGER'
                db.execute('DROP '+kind+' '+sql.split()[2])
        with self.assertRaises(JournalUnavailable):
            preflight(self.path)
        self.assertTrue(migrate(self.path))
        self.context()
    def test_migration_failure_rolls_back_and_no_startup_repair(self):
        from xblago.core import agent_context_schema
        with patch.dict(os.environ,{FLAG:'false'}):
            cfg = create(Path(self.tmp.name)/'rollback',context_fencing=True)
        path = Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'
        with patch.dict(os.environ,cfg):
            with patch.object(agent_context_schema,'DDL',agent_context_schema.DDL+('INVALID SQL',)):
                with self.assertRaises(sqlite3.Error):
                    migrate(path)
            with sqlite3.connect(path) as db:
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_context_migrations'").fetchone())
            self.assertTrue(migrate(path))
            self.assertFalse(migrate(path))
    def test_backup_restore_retains_floor_and_does_not_rebind_source(self):
        self.chat('Я живу в Казани')
        backup = self.app.store.backup_database()
        self.app.delete_memory(self.uid, 'city')
        self.app = WebApp(restore(backup, Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        self.token = self.app.login('synthetic_context_owner','synthetic-password-only')[0]
        context = self.context()
        self.assertNotIn('city', context.memory)
        self.assertFalse(any('Казани' in t.text for t in context.history))
        self.chat('Я живу в Омске')
        context = self.context()
        self.assertEqual(context.participant.origins['city']['quote'], 'Омске')
    def test_backup_before_first_turn_does_not_reuse_retained_turn_ids(self):
        backup = self.app.store.backup_database()
        self.chat('Я живу в Казани')
        self.app.delete_memory(self.uid,'city')
        self.app = WebApp(restore(backup,Path(self.cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        self.token = self.app.login('synthetic_context_owner','synthetic-password-only')[0]
        self.chat('Я живу в Омске')
        context = self.context()
        source = context.participant.origins['city']
        self.assertGreater(source['turn_id'],2)
        self.assertEqual(source['quote'],'Омске')
    def test_source_marker_and_pair_rollback_together(self):
        from xblago.core.messages import UserMessage
        g = Generation(self.app.store, self.uid)
        g.agent(self.provider).respond(UserMessage('Я живу в Казани'))
        with patch.object(self.app.store, 'append_exchange', side_effect=sqlite3.OperationalError('synthetic full disk')):
            with self.assertRaises(sqlite3.OperationalError):
                g.commit()
        self.assertFalse(self.app.store.all_memory(self.uid))
        self.assertEqual(self.app.history(self.uid), [])
        g.reject()
        with self.assertRaises(ContextConflict):
            g.commit()  # journal write-ahead may reject this uncommitted ticket


class ReliableContextFencingTests(_FencingTests):
    """Repeat all original G09 fault/worker/recovery tests with projection enabled."""
    def setUp(self):
        super().setUp()
        migrate(self.path)
        self.cfg[FLAG] = 'true'
        os.environ[FLAG] = 'true'


# Avoid unittest collecting the imported base a third time.
del _FencingTests
