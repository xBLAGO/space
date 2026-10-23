"""Accumulated PR #2–#6 stack on ONE existing SQLite, then pilot recovery."""
import hashlib
import hmac
import json
import io
import os
import tempfile
import subprocess
import sys
import tarfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from test_identity_runtime import TestAuthenticator
from xblago.core.identity_schema import migrate_identity_store
from xblago.core.identity_runtime_schema import migrate_identity_runtime
from xblago.core.privacy_schema import migrate_privacy
from xblago.core.pilot_schema import migrate_pilot
from xblago.core.journal_witness import initialize as initialize_witness
from xblago.core.security_journal import SecurityJournal
from xblago.core.privacy_maintenance import restore,cleanup
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.telegram import TelegramBot
from xblago.integrations.privacy import PrivacyService


class FoundationStackIntegration(unittest.TestCase):
    def test_actual_pr2_database_upgrades_without_identity_or_data_loss(self):
        sha='4bc48f2a6b23f198ca88a022e4bad0f03f709e8a'
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{
            'XBLAGO_PILOT_MODE':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'',
            'XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false',
            'XBLAGO_WEB_INVITE_CODE':'historical-fixture','XBLAGO_LLM_PROVIDER':'local'}):
            root=Path(root);old=root/'pr2';old.mkdir()
            archive=subprocess.check_output(['git','archive',sha])
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                for member in bundle.getmembers():
                    if member.isfile() and member.name.startswith('src/') and '..' not in Path(member.name).parts:
                        target=old/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(bundle.extractfile(member).read())
            path=root/'historical.sqlite3';metadata=root/'fixture.json'
            program='''import hashlib,json,sys
from pathlib import Path
import xblago.core.storage as module
from xblago.core.storage import SQLiteBlagoStore,TelegramIdentity
from xblago.integrations.web import WebApp
s=SQLiteBlagoStore(Path(sys.argv[1])); a=WebApp(s)
t,_=a.register('pr2_person','historical-test-password','historical-fixture'); uid=a.session(t)[0]
tg=s.resolve_telegram_user(TelegramIdentity(1000000001,1000000001,'private'))
s.memory_for_user(tg).set('city','Казань'); s.append_history(tg,'user','История настоящего PR2')
a.create_item('resources',uid,{'title':'Навык из PR2','visibility':'private'})
Path(sys.argv[2]).write_text(json.dumps({'token':t,'uid':uid,'tg_uid':tg,'source_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}))
'''
            import _sqlite3
            env=dict(os.environ,PYTHONPATH=str(old/'src')+os.pathsep+str(Path(_sqlite3.__file__).parent))
            result=subprocess.run([sys.executable,'-c',program,str(path),str(metadata)],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(metadata.read_text())
            self.assertEqual(data['source_sha256'],hashlib.sha256((old/'src/xblago/core/storage.py').read_bytes()).hexdigest())
            store=SQLiteBlagoStore(path);app=WebApp(store)
            self.assertEqual(app.session(data['token'])[0],data['uid'])
            self.assertEqual(store.resolve_telegram_user(TelegramIdentity(1000000001,1000000001,'private')),data['tg_uid'])
            self.assertEqual(store.all_memory(data['tg_uid']),{'city':'Казань'})
            self.assertEqual(store.recent_history(data['tg_uid'],10)[0].text,'История настоящего PR2')
            migrate_identity_store(path);migrate_identity_runtime(path)
            journal=root/'journal';SecurityJournal.initialize(journal);migrate_privacy(path,journal)
            os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal)
            migrate_pilot(path)
            self.assertEqual(app.session(app.login('pr2_person','historical-test-password')[0])[0],data['uid'])
            self.assertEqual(app.rows('resources',data['uid'])[0]['title'],'Навык из PR2')
            self.assertEqual(store.all_memory(data['tg_uid']),{'city':'Казань'})
            with store._connect() as db:
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_existing_economy_memory_cross_channel_identity_and_recovery(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{
            'XBLAGO_PILOT_MODE':'false','XBLAGO_SECURITY_JOURNAL_DIR':'','XBLAGO_JOURNAL_WITNESS_DIR':'',
            'XBLAGO_REQUIRE_PRIVACY_JOURNAL':'false','XBLAGO_REQUIRE_SECURE_IDENTITY':'false',
            'XBLAGO_WEB_INVITE_CODE':'stack-test','XBLAGO_LLM_PROVIDER':'local','TELEGRAM_BOT_TOKEN':'123:stack-test',
            'XBLAGO_PUBLIC_ORIGIN':'https://blago.test','XBLAGO_WEBAUTHN_RP_ID':'blago.test'}):
            root=Path(root);path=root/'one.sqlite3'
            store=SQLiteBlagoStore(path);app=WebApp(store)
            web_token,_=app.register('canonical','synthetic-long-password','stack-test')
            canonical=app.session(web_token)[0]
            identity=TelegramIdentity(1000000001,1000000001,'private')
            source=store.resolve_telegram_user(identity)
            helper_token,_=app.register('helper','synthetic-long-password','stack-test')
            helper=app.session(helper_token)[0]
            store.memory_for_user(source).set('city','Казань')
            store.append_history(source,'user','История Telegram до Identity Store')
            store.append_history(canonical,'user','История Web до Identity Store',channel='web')
            need=app.create_item('needs',source,{'title':'Синтетический ремонт','visibility':'community'})
            app.create_resource_offer(helper,{'title':'Синтетическая помощь','offer_type':'external_payment',
                'visibility':'community','max_amount_minor':1000,'currency':'RUB'})
            request=app.create_payment_request(source,{'need_id':need['id'],'title':'Тестовый счёт','amount_minor':100,
                'currency':'RUB','payment_purpose':'Синтетический ремонт'})
            app.offer_payment_request(source,request['id'])
            offer=app.incoming_payment_offers(helper)[0]
            app.accept_payment_offer(helper,offer['id'])
            app.simulate_payment(helper,request['id'],{'note':'Синтетическая симуляция, без денег'})
            app.confirm_fulfillment(source,request['id'],{'note':'Ручное тестовое подтверждение','xblago_rate':1,'exchange_rate_source':'test'})
            economic_before=app.economy_events(source,request['id'])
            migrate_identity_store(path);migrate_identity_runtime(path)
            journal=root/'control';witness=root/'witness'
            SecurityJournal.initialize(journal);migrate_privacy(path,journal)
            os.environ['XBLAGO_SECURITY_JOURNAL_DIR']=str(journal)
            migrate_pilot(path);initialize_witness(SecurityJournal(journal),witness)
            os.environ.update(XBLAGO_JOURNAL_WITNESS_DIR=str(witness),XBLAGO_PILOT_MODE='true')
            self.assertEqual(store.all_memory(source),{'city':'Казань'})
            self.assertEqual(app.economy_events(source,request['id']),economic_before)
            web_token,_=app.login('canonical','synthetic-long-password',web_token)
            app.redeem_telegram_link(source,app.new_link_code(canonical,web_token))
            with self.assertRaises(ApiError):
                app.session(web_token)
            web_token,_=app.login('canonical','synthetic-long-password')
            self.assertEqual(store.resolve_telegram_user(identity),canonical)
            economic_after=app.economy_events(canonical,request['id'])
            self.assertEqual(economic_after,json.loads(json.dumps(economic_before).replace(source,canonical)))
            fields={'user':json.dumps({'id':identity.telegram_user_id}),'auth_date':str(int(time.time()))}
            secret=hmac.new(b'WebAppData',b'123:stack-test',hashlib.sha256).digest()
            fields['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
            binding='synthetic-mini-app-binding'
            challenge=app.identity.telegram_challenge(binding)
            mini_token,_=app.telegram_login(urlencode(fields),challenge,binding)
            self.assertEqual(app.session(mini_token)[0],canonical)
            authenticator=TestAuthenticator()
            options=app.identity.passkey_options(binding,web_token,register=True)
            web_token,_=app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.registration(options)},binding,web_token,register=True)
            options=app.identity.passkey_options(binding)
            web_token,_=app.identity.passkey_verify({'challenge':options['challenge'],'credential':authenticator.assertion(options)},binding,web_token)
            bot=TelegramBot(None,root,store=store)
            update={'update_id':55,'message':{'from':{'id':identity.telegram_user_id},'chat':{'id':identity.chat_id,'type':'private'},'text':'Продолжим разговор'}}
            reply=bot.replies_for_update(update)
            before=store.recent_history(canonical,100)
            self.assertEqual(bot.replies_for_update(update),reply)
            self.assertEqual(store.recent_history(canonical,100),before)
            app.chat(canonical,'Продолжим из Web')
            self.assertGreaterEqual(len(store.recent_history(canonical,100)),6)
            self.assertFalse(PrivacyService(app).preview(web_token)['eligible'])
            victim,_=app.register('victim','synthetic-long-password','stack-test')
            victim_uid=app.session(victim)[0]
            store.memory_for_user(victim_uid).set('city','Удаляемая запись')
            store.memory_for_user(canonical).set('private_test','Старая версия')
            backup=store.backup_database(root/'offsite-copy')
            store.memory_for_user(canonical).set('private_test','Новая версия')
            store.delete_memory(canonical,'private_test')
            preview=PrivacyService(app).preview(victim)
            PrivacyService(app).delete(victim,preview['challenge'],'УДАЛИТЬ')
            recovered=restore(backup,journal);restarted=WebApp(recovered)
            token,_=restarted.login('canonical','synthetic-long-password')
            self.assertEqual(restarted.session(token)[0],canonical)
            self.assertNotIn('private_test',recovered.all_memory(canonical))
            self.assertEqual(recovered.all_memory(victim_uid),{})
            self.assertEqual(restarted.economy_events(canonical,request['id']),economic_after)
            self.assertEqual(len(restarted.economy_events(canonical,request['id'])['contribution_events']),1)
            with self.assertRaises(ApiError):
                restarted.session(mini_token)
            cleanup(recovered)
            with recovered._connect() as db:
                self.assertEqual(tuple(db.execute('PRAGMA integrity_check').fetchone()),('ok',))
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
