"""Foundation 08, synthetic accounts only; no provider, wallet or deployment."""
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
import urllib.error
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from tools.pilot_fixture import fixture
from test_telegram import FakeTelegramApi
from xblago.core.genesis import Deliberation, ZERO
from xblago.core.genesis_schema import migrate_genesis, validate
from xblago.core.genesis_interfaces import UnconfiguredConsensus, UnconfiguredSolanaAnchor
from xblago.core.privacy_control import PrivacyControl
from xblago.core.privacy_maintenance import restore, cleanup
from xblago.core.security_journal import JournalUnavailable, canonical
from xblago.core.storage import SQLiteBlagoStore
from xblago.integrations.web import WebApp, ApiError, BlagoWebHandler
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.privacy import PrivacyService


class GenesisTests(unittest.TestCase):
    def setUp(self):
        self.context=fixture(origin='https://blago.test',rp_id='blago.test'); self.f=self.context.__enter__()
        self.addCleanup(self.context.__exit__,None,None,None)
        self.env=patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false','XBLAGO_IDENTITY_CORE_ENABLED':'false'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.app,self.store=self.f['app'],self.f['store']
        migrate_genesis(self.f['path'])
        os.environ['XBLAGO_GENESIS_ENABLED']='true'
        self.token,self.csrf=self.app.register('genesis_author','synthetic-long-password','synthetic-pilot-invite')
        self.uid=self.app.session(self.token)[0]
        self.service=Deliberation(self.store)

    def say(self,text,key=None,uid=None):
        reference=self.service.pending(uid or self.uid) if text=='Да, публикуй этот текст' else None
        return self.service.converse(uid or self.uid,text,request_id=key,preview_ref=reference)

    def proposal(self,text='открыть общую мастерскую'):
        self.say('Предлагаю '+text)
        return self.service.own(self.uid)['streams'][-1]['stream_id']

    def publish(self,text='открыть общую мастерскую'):
        sid=self.proposal(text); self.say('Да, публикуй этот текст'); return sid

    def test_заявленный_актив_без_платежа_или_доказательств(self):
        reply=self.app.chat(self.uid,'У меня есть 5000 рублей',token=self.token)
        self.assertIn('5000 RUB',reply)
        asset=self.app.assets(self.uid)[0]
        self.assertEqual(asset['verification_status'],'self_declared_unverified')
        self.assertEqual(asset['use_permission'],'owner_only')
        self.assertEqual(asset['visibility'],'private')
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM blago_payment_requests').fetchone()[0],0)
        event=self.service.own(self.uid)['events'][0]
        self.assertEqual(json.loads(event['payload'])['amount_minor'],500000)
        self.assertEqual(self.store.all_memory(self.uid),{})
        self.assertEqual(len(self.app.history(self.uid)),2)

    def test_повтор_реплики_и_идемпотентного_запроса(self):
        a=self.say('У меня есть 5000 рублей','once')
        self.assertEqual(a,self.say('У меня есть 5000 рублей','once'))
        self.assertEqual(len(self.app.history(self.uid)),2)
        self.say('У меня есть 5000 рублей','twice')
        self.assertEqual(len(self.app.assets(self.uid)),1)
        self.assertEqual(len(self.service.own(self.uid)['events']),1)
        with self.assertRaises(ApiError): self.say('У меня есть 6000 рублей','once')

    def test_отрицание_цитата_гипотеза_чужие_деньги_и_запрет(self):
        for text in ('У меня нет 5000 рублей','«У меня есть 5000 рублей»','Если бы у меня были 5000 рублей',
                     'У Петра есть 5000 рублей','Не запоминай это: у меня есть 5000 рублей','У меня есть 5000 рублей?'):
            with self.subTest(text=text): self.assertIsNone(self.say(text))
        self.assertEqual(self.service.own(self.uid)['events'],[])

    def test_размышление_не_создаёт_потребность(self):
        for text in ('Мне нужен велосипед','Может быть, стоит открыть мастерскую','Не хочу ничего публиковать'):
            self.assertIsNone(self.say(text))
        self.assertEqual(self.app.rows('needs',self.uid),[])
        self.say('Запиши потребность: нужен велосипед')
        self.assertEqual(len(self.app.rows('needs',self.uid)),1)
        self.assertEqual(self.app.rows('needs',self.uid)[0]['visibility'],'private')

    def test_только_явное_утверждение_показанного_текста(self):
        sid=self.proposal()
        with self.assertRaises(ApiError): self.service.public(sid)
        self.assertIsNone(self.say('Да'))
        self.assertIsNone(self.say('Хорошо'))
        self.say('Да, публикуй этот текст')
        data=self.service.public(sid)
        self.assertEqual(data['current']['text'],'открыть общую мастерскую')
        self.assertNotIn(self.uid,canonical(data))
        self.assertNotIn('identity_id',canonical(data))
        self.assertEqual(data['current']['authorship'],'authenticated_account_pseudonym')

    def test_контекст_изменился_подтверждение_не_публикует(self):
        sid=self.proposal()
        self.app.chat(self.uid,'Спасибо',token=self.token)
        self.assertIn('актуальный текст',self.say('Да, публикуй этот текст'))
        with self.assertRaises(ApiError): self.service.public(sid)

    def test_отмена_и_истечение_согласия(self):
        sid=self.proposal(); self.say('Не публикуй'); self.say('Да, публикуй этот текст')
        with self.assertRaises(ApiError): self.service.public(sid)
        sid=self.proposal()
        with self.store._locked_transaction() as db: db.execute('UPDATE genesis_previews SET expires_at=0')
        self.say('Да, публикуй этот текст')
        with self.assertRaises(ApiError): self.service.public(sid)

    def test_новая_версия_не_публикуется_до_согласия(self):
        sid=self.publish()
        self.say(f'Измени предложение {sid}: открыть бесплатную мастерскую')
        self.assertEqual(self.service.public(sid)['current']['text'],'открыть общую мастерскую')
        self.say('Да, публикуй этот текст')
        data=self.service.public(sid)
        self.assertEqual(len(data['versions']),2)
        previous=ZERO
        for version in data['versions']:
            self.assertEqual(version['previous_hash'],previous)
            payload={key:version[key] for key in ('revision','previous_hash','publication')}
            previous=hashlib.sha256(canonical(payload).encode()).hexdigest()
            self.assertEqual(previous,version['public_hash'])
        self.assertEqual(data['current']['text'],'открыть бесплатную мастерскую')

    def test_аргумент_другого_участника_только_после_его_согласия(self):
        sid=self.publish()
        token,_=self.app.register('other_genesis','synthetic-long-password','synthetic-pilot-invite')
        other=self.app.session(token)[0]
        self.say(f'Аргумент к предложению {sid}: это поможет чинить велосипеды',uid=other)
        self.assertEqual(self.service.public(sid)['arguments'],[])
        self.say('Да, публикуй этот текст',uid=other)
        data=self.service.public(sid)
        self.assertEqual(len(data['arguments']),1)
        with self.assertRaises(ApiError): self.say(f'Измени предложение {sid}: чужая правка',uid=other)
        self.assertNotIn(other,canonical(data))

    def test_снятие_публикации_и_старый_backup(self):
        sid=self.publish(); backup=self.store.backup_database(self.f['root']/'old')
        self.say('Сними с публикации '+sid)
        with self.assertRaises(ApiError): self.service.public(sid)
        recovered=restore(backup,self.f['journal'])
        with self.assertRaises(JournalUnavailable): Deliberation(recovered).public(sid)

    def test_свежий_backup_перезапуск_и_аннулирование_preview(self):
        sid=self.publish(); self.proposal('новый частный текст')
        backup=self.store.backup_database(self.f['root']/'fresh')
        recovered=restore(backup,self.f['journal'])
        self.assertEqual(Deliberation(recovered).public(sid)['current']['text'],'открыть общую мастерскую')
        token,_=WebApp(recovered).login('genesis_author','synthetic-long-password')
        uid=WebApp(recovered).session(token)[0]
        self.assertIn('актуальный текст',Deliberation(recovered).converse(uid,'Да, публикуй этот текст'))
        with recovered._connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_удаление_аккаунта_экспорт_и_невоскрешение(self):
        sid=self.publish(); backup=self.store.backup_database(self.f['root']/'before-delete')
        privacy=PrivacyService(self.app)
        exported=privacy.export(self.token)
        self.assertIn('genesis_events',exported['tables'])
        preview=privacy.preview(self.token)
        self.assertTrue(privacy.delete(self.token,preview['challenge'],'УДАЛИТЬ')['deleted'])
        with self.assertRaises(ApiError): self.service.public(sid)
        recovered=restore(backup,self.f['journal'])
        with self.assertRaises(ApiError): Deliberation(recovered).public(sid)
        with recovered._connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM genesis_events').fetchone()[0],0)

    def test_сбой_после_journal_append_до_commit_блокирует_старое_состояние(self):
        original=PrivacyControl.flush
        def fail(control):
            original(control)
            raise RuntimeError('synthetic crash after durable intent')
        with patch.object(PrivacyControl,'flush',fail), self.assertRaises(RuntimeError): self.say('У меня есть 5000 рублей')
        self.assertEqual(self.app.history(self.uid),[])
        with self.assertRaises(JournalUnavailable): self.service.own(self.uid)
        self.assertEqual(self.app.assets(self.uid),[])

    def test_параллельные_повторы_не_дублируют_события_или_историю(self):
        def request(_): return Deliberation(SQLiteBlagoStore(self.f['path'])).converse(self.uid,'У меня есть 5000 рублей',request_id='same')
        with concurrent.futures.ThreadPoolExecutor(4) as pool: replies=list(pool.map(request,range(4)))
        self.assertEqual(len(set(replies)),1)
        self.assertEqual(len(self.service.own(self.uid)['events']),1)
        self.assertEqual(len(self.app.history(self.uid)),2)

    def test_параллельные_подтверждения_публикуют_одну_версию(self):
        sid=self.proposal()
        reference=self.service.pending(self.uid)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i:Deliberation(SQLiteBlagoStore(self.f['path'])).converse(self.uid,'Да, публикуй этот текст',request_id=str(i),preview_ref=reference),range(2)))
        self.assertEqual(len(self.service.public(sid)['versions']),1)

    def test_ошибка_до_commit_не_оставляет_актив_и_половину_истории(self):
        from xblago.core import genesis
        with patch.object(genesis,'emit',side_effect=RuntimeError('synthetic write failure')), self.assertRaises(RuntimeError): self.say('У меня есть 5000 рублей')
        self.assertEqual(self.app.assets(self.uid),[])
        self.assertEqual(self.app.history(self.uid),[])
        self.assertEqual(self.service.own(self.uid)['events'],[])

    def test_схема_идемпотентна_и_неизменяемый_event(self):
        with patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false'}): self.assertFalse(migrate_genesis(self.f['path']))
        self.say('У меня есть 5000 рублей')
        with self.assertRaises(sqlite3.IntegrityError),self.store._locked_transaction() as db:
            db.execute("UPDATE genesis_events SET payload='{}'")
        with self.store._connect() as db: self.assertTrue(validate(db))

    def test_миграция_rollback_при_ошибке(self):
        from xblago.core import genesis_schema as schema
        with fixture() as f,patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false'}):
            with patch.object(schema,'DDL',schema.DDL+['THIS IS INVALID SQL']),self.assertRaises(sqlite3.Error): schema.migrate_genesis(f['path'])
            with f['store']._connect() as db: self.assertFalse(schema.validate(db))
            self.assertTrue(schema.migrate_genesis(f['path']))

    def test_удалённая_публичная_версия_и_drift_обнаруживаются(self):
        sid=self.publish(); self.say(f'Измени предложение {sid}: новая редакция'); self.say('Да, публикуй этот текст')
        with self.store._locked_transaction() as db: db.execute('DELETE FROM genesis_publications WHERE revision=2')
        with self.assertRaises(JournalUnavailable): self.service.public(sid)
        with self.store._connect() as db: db.execute('DROP TRIGGER genesis_event_immutable')
        with self.assertRaises(JournalUnavailable): self.service.own(self.uid)

    def test_секреты_и_неположительные_суммы_не_сохраняются(self):
        for text in ('Предлагаю: мой пароль qwerty12345','Предлагаю: мой токен hf_abcdefghijklmnopqrstuv','У меня есть 0 рублей'):
            with self.subTest(text=text),self.assertRaises(ApiError): self.say(text)
        self.assertEqual(self.app.history(self.uid),[])

    def test_старыe_api_активов_и_потребностей_используют_тот_же_журнал(self):
        self.app.create_asset(self.uid,{'title':'Мой велосипед','asset_type':'vehicle'})
        self.app.create_item('needs',self.uid,{'title':'Нужна цепь'})
        self.assertEqual({e['event_type'] for e in self.service.own(self.uid)['events']},{'asset.declared','need.declared'})

    def test_флаг_off_и_никакого_consensus_или_mainnet(self):
        with patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false'}):
            self.assertIsNone(self.say('У меня есть 5000 рублей'))
            with self.assertRaises(ApiError): self.service.own(self.uid)
        with self.assertRaises(NotImplementedError): UnconfiguredConsensus().verify(())
        with self.assertRaises(NotImplementedError): UnconfiguredSolanaAnchor().prepare(None)
        with self.store._connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='sovereign_identities'").fetchone())

    def test_очистка_ttl_не_удаляет_историю_версий(self):
        sid=self.publish(); self.proposal('частный черновик')
        with self.store._locked_transaction() as db: db.execute('UPDATE genesis_previews SET expires_at=0')
        self.assertEqual(cleanup(self.store)['genesis_previews'],1)
        self.assertEqual(len(self.service.public(sid)['versions']),1)

    def test_telegram_web_miniapp_общая_история_и_повтор_update(self):
        bot=TelegramBot(FakeTelegramApi(),memory_dir=self.f['root']/'unused-memory',store=self.store)
        # Bind to the existing participant with the established link procedure.
        link=self.app.new_link_code(self.uid,self.token)
        def update(i,text): return {'update_id':i,'message':{'from':{'id':10001,'first_name':'Synthetic'},'chat':{'id':10001,'type':'private'},'text':text}}
        bot.replies_for_update(update(1,'/link '+link))
        declaration=update(2,'У меня есть 5000 рублей')
        a=bot.replies_for_update(declaration); b=bot.replies_for_update(declaration)
        self.assertEqual(a,b)
        self.app.chat(self.uid,'Предлагаю: общая мастерская')
        bot.replies_for_update(update(3,'Публикуй: общая мастерская'))
        sid=next(s['stream_id'] for s in self.service.own(self.uid)['streams'] if s['kind']=='proposal')
        self.assertEqual(self.service.public(sid)['current']['text'],'общая мастерская')
        self.assertEqual(len(self.app.assets(self.uid)),1)
        with self.store._connect() as db:
            channels={r[0] for r in db.execute('SELECT DISTINCT channel FROM dialogue_turns WHERE blago_user_id=?',(self.uid,))}
        self.assertEqual(channels,{'telegram','web'})
        self.assertEqual(WebApp(SQLiteBlagoStore(self.f['path'])).history(self.uid),self.app.history(self.uid))

    def test_очистка_истории_не_возвращает_preview_из_кэша(self):
        self.say('Предлагаю: частная мысль','draft-once')
        backup=self.store.backup_database(self.f['root']/'before-clear')
        self.store.clear_history(self.uid)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM genesis_receipts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM genesis_previews').fetchone()[0],0)
        recovered=restore(backup,self.f['journal'])
        with recovered._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM genesis_receipts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM dialogue_turns WHERE blago_user_id=?',(self.uid,)).fetchone()[0],0)

    def test_потерянный_witness_блокирует_публикацию_и_просмотр(self):
        sid=self.publish()
        witness=self.f['witness']
        os.rename(witness,witness.with_name('temporarily-unavailable'))
        self.addCleanup(os.rename,witness.with_name('temporarily-unavailable'),witness)
        with self.assertRaises(JournalUnavailable): self.service.public(sid)
        with self.assertRaises(JournalUnavailable): self.say('У меня есть 5000 рублей')

    def test_backup_до_08_нельзя_автоматически_признать_совместимым(self):
        with fixture() as f,patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false'}):
            token,_=f['app'].register('old_backup','synthetic-long-password','synthetic-pilot-invite')
            uid=f['app'].session(token)[0]
            backup=f['store'].backup_database(f['root']/'pre08')
            migrate_genesis(f['path'])
            with patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'true'}): Deliberation(f['store']).converse(uid,'У меня есть 5000 рублей')
            with self.assertRaises(JournalUnavailable): restore(backup,f['journal'])

    def test_перенос_авторства_через_merge_без_согласия_запрещён(self):
        self.publish()
        token,_=self.app.register('target_merge','synthetic-long-password','synthetic-pilot-invite')
        target=self.app.session(token)[0]
        with self.assertRaises(ApiError),self.store._locked_transaction() as db:
            self.app.identity.merge(db,self.uid,target)
        self.assertEqual(self.service.own(self.uid)['streams'][0]['blago_user_id'],self.uid)

    def test_задержанное_подтверждение_не_утверждает_другой_текст(self):
        self.proposal('первая мысль')
        old=self.service.pending(self.uid)
        sid=self.proposal('совсем другая мысль')
        self.service.converse(self.uid,'Да, публикуй этот текст',preview_ref=old)
        with self.assertRaises(ApiError): self.service.public(sid)
        self.proposal('новый текст')
        with self.assertRaises(ApiError): self.service.converse(self.uid,'Публикуй: подменённый текст')

    def test_новый_частный_черновик_не_отменяет_отзыв_публикации(self):
        sid=self.publish()
        self.say('Сними с публикации '+sid)
        self.say(f'Измени предложение {sid}: новая частная редакция')
        with self.assertRaises(ApiError): self.service.public(sid)
        self.say('Да, публикуй этот текст')
        self.assertEqual(self.service.public(sid)['current']['text'],'новая частная редакция')

    def test_http_public_readonly_csrf_idor_xss(self):
        sid=self.publish('<script>alert(1)</script> общественная идея')
        server=ThreadingHTTPServer(('127.0.0.1',0),BlagoWebHandler);server.blago_web_app=self.app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,body=None,headers=None):
            request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
            try:
                with urllib.request.urlopen(request) as response: return response.status,response.read().decode(),dict(response.headers)
            except urllib.error.HTTPError as exc: return exc.code,exc.read().decode(),dict(exc.headers)
        self.assertEqual(request('/api/v1/public/proposals/'+sid)[0],200)
        status,html,headers=request('/proposals/'+sid)
        self.assertEqual(status,200);self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertIn("default-src 'none'",headers['Content-Security-Policy'])
        self.assertEqual(request('/api/v1/genesis/records')[0],401)
        headers={'Content-Type':'application/json','Cookie':'blago_session='+self.token,'Origin':'https://blago.test'}
        self.assertEqual(request('/api/v1/chat/messages',{'text':'Предлагаю: без CSRF'},headers)[0],403)
        headers['X-CSRF-Token']=self.csrf
        self.assertEqual(request('/api/v1/chat/messages',{'text':'У меня есть 5000 рублей','request_id':'web-once','blago_user_id':'other'},headers)[0],200)
        self.assertEqual(request('/api/v1/public/proposals/'+sid,{},headers)[0],404)
        self.assertEqual(request('/api/v1/public/proposals/%27%20OR%201=1')[0],404)
