"""Synthetic calculator invariants; not real-user release authorization."""
from dataclasses import asdict, FrozenInstanceError
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xblago.domain.collective_calculator import (
    AccountingError, Quote, OfflineRateProvider, ValueEngine, LabRegistry,
    HelpAccountingView, quantity_coverage)


def engine(currency='RUB', rate='10000000', at=1000):
    return ValueEngine(OfflineRateProvider([Quote(currency, rate, at, 'mock-a', 'fixture-a-v1'),
                                           Quote(currency, rate, at, 'mock-b', 'fixture-b-v1')]),
                       {'mock-a': 'fixture-a-v1', 'mock-b': 'fixture-b-v1'})


def body(**kw):
    return dict({'category': 'computer', 'quantity': '2', 'unit': 'item', 'available': '1',
                 'share_consent': True, 'right': 'OWNER', 'audience': 'LAB_COMMUNITY',
                 'conditions': 'ON_REQUEST'}, **kw)


class ValueTests(unittest.TestCase):
    def test_разные_валюты(self):
        for currency in ('RUB','USD','EUR','JPY','GBP','CHF','CNY'):
            with self.subTest(currency=currency):
                self.assertEqual(engine(currency).evaluate('100',currency,effective_at=1000,now=1000).btc,'0.00001000')
    def test_точность_и_округление_половин_до_сатоши(self):
        for amount,btc in [('0.05','0.00000000'),('0.15','0.00000002'),('0.25','0.00000002')]:
            self.assertEqual(engine().evaluate(amount,'RUB',effective_at=1000,now=1000).btc,btc)
    def test_float_отрицательные_и_несуществующие_коды_запрещены(self):
        for amount,currency in [(1.1,'RUB'),('-1','RUB'),('NaN','RUB'),('1','AAA'),('1','BTC'),('1.01','JPY'),('1e3','RUB')]:
            with self.subTest(amount=amount,currency=currency),self.assertRaises(AccountingError):
                engine().evaluate(amount,currency,effective_at=1000,now=1000)
    def test_нет_курсов_и_единственный_источник(self):
        for quotes in ([],[Quote('RUB','100',1000,'a','p')]):
            with self.assertRaises(AccountingError):
                ValueEngine(OfflineRateProvider(quotes),{'a':'p'}).evaluate('1','RUB',effective_at=1000,now=1000)
    def test_несогласованные_курсы(self):
        quotes=[Quote('RUB','100',1000,'a','p'),Quote('RUB','110',1000,'b','q')]
        with self.assertRaises(AccountingError):
            ValueEngine(OfflineRateProvider(quotes),{'a':'p','b':'q'}).evaluate('1','RUB',effective_at=1000,now=1000)
    def test_устаревшие_и_будущие_котировки(self):
        for effective in (999,1301):
            with self.assertRaises(AccountingError):
                engine().evaluate('1','RUB',effective_at=effective,now=1400)
    def test_недостоверное_происхождение_и_повтор_источника(self):
        for quotes in ([Quote('RUB','100',1000,'a','bad'),Quote('RUB','100',1000,'b','q')],
                       [Quote('RUB','100',1000,'a','p'),Quote('RUB','100',1000,'a','p')]):
            with self.assertRaises(AccountingError):
                ValueEngine(OfflineRateProvider(quotes),{'a':'p','b':'q'}).evaluate('1','RUB',effective_at=1000,now=1000)
    def test_историческая_оценка_неизменна_переоценка_отдельна(self):
        a=engine().evaluate('100','RUB',effective_at=1000,now=1000)
        b=engine(rate='20000000',at=1100).evaluate('100','RUB',effective_at=1100,now=1100,previous=a)
        self.assertEqual(a.btc,'0.00001000');self.assertEqual(b.btc,'0.00000500');self.assertEqual(b.supersedes,a.id)
        with self.assertRaises(FrozenInstanceError):a.btc='0'
    def test_нельзя_поменять_исходную_цену_при_переоценке(self):
        a=engine().evaluate('100','RUB',effective_at=1000,now=1000)
        with self.assertRaises(AccountingError):engine().evaluate('200','RUB',effective_at=1000,now=1000,previous=a)
    def test_идентичность_результата_независима_от_порядка_котировок(self):
        a=engine();b=ValueEngine(OfflineRateProvider(reversed(a.provider.quotes)),a.pins)
        self.assertEqual(a.evaluate('100','RUB',effective_at=1000,now=1000),b.evaluate('100','RUB',effective_at=1000,now=1000))
    def test_частичное_удовлетворение_и_разные_единицы(self):
        for unit in ('item','kg','g','hour','minute'):
            v=quantity_coverage('4','1',unit);self.assertEqual(v['remaining'],'3');self.assertEqual(v['share'],'0.25000000')
        with self.assertRaises(AccountingError):quantity_coverage('4','5','item')
        with self.assertRaises(AccountingError):quantity_coverage('4','1','unknown')


class RegistryTests(unittest.TestCase):
    def setUp(self):
        clock=patch('xblago.domain.collective_calculator.time.time',return_value=1000);clock.start();self.addCleanup(clock.stop)
        self.r=LabRegistry(synthetic=True);self.addCleanup(self.r.close)
        self.a=self.r.participant();self.b=self.r.participant()
    def active(self,**kw):
        data=body(**kw);rid=self.r.draft(self.a,data);c=self.r.prepare(self.a,rid,1,data,now=1000)
        self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000);return rid
    def test_выключен_по_умолчанию(self):
        with self.assertRaises(AccountingError):LabRegistry()
    def test_черновик_не_в_балансе_и_нужна_точная_версия(self):
        rid=self.r.draft(self.a,body());self.assertEqual(self.r.balance(self.a,at=1000)['registered_objects'],0)
        with self.assertRaises(AccountingError):self.r.prepare(self.a,rid,2,body(),now=1000)
    def test_нет_двойного_учёта_актива_и_ресурса(self):
        self.active();v=self.r.balance(self.b,at=1000)
        self.assertEqual(v['registered_objects'],1);self.assertEqual(v['by_category_unit']['computer:item'],{'stock':'2','available_self_declared':'1'})
    def test_актив_не_является_общим_имуществом(self):
        self.active(available='0',share_consent=False)
        self.assertEqual(self.r.balance(self.b,at=1000)['by_category_unit']['computer:item']['available_self_declared'],'0')
    def test_нет_доступности_без_согласия(self):
        with self.assertRaises(AccountingError):self.r.draft(self.a,body(share_consent=False))
        with self.assertRaises(AccountingError):self.r.draft(self.a,body(available='3'))
    def test_приватность_и_изоляция(self):
        rid=self.active(audience='PRIVATE');self.assertEqual(self.r.balance(self.b,at=1000)['registered_objects'],0)
        for action in (lambda:self.r.export_own('external-user'),lambda:self.r.prepare(self.b,rid,2,body(),now=1000),lambda:self.r.erase(self.b,rid)):
            with self.assertRaises(AccountingError):action()
    def test_подтверждение_привязано_к_автору_и_содержимому(self):
        rid=self.r.draft(self.a,body());c=self.r.prepare(self.a,rid,1,body(),now=1000)
        for actor,confirmation in ((self.b,'ПОДТВЕРЖДАЮ'),(self.a,'да')):
            with self.assertRaises(AccountingError):self.r.commit(actor,c['challenge'],confirmation,now=1000)
        with self.assertRaises(AccountingError):self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1120)
    def test_повтор_квитанции_без_дублирования_события(self):
        rid=self.r.draft(self.a,body());c=self.r.prepare(self.a,rid,1,body(),now=1000)
        x=self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
        self.assertEqual(x,self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1001))
        self.assertEqual(self.r.db.execute('SELECT count(*) FROM events').fetchone()[0],1)
    def test_конкурентные_подготовки_принимают_одну_версию(self):
        rid=self.active();c=[self.r.prepare(self.a,rid,2,body(available=x),now=1000) for x in ('0','2')]
        self.r.commit(self.a,c[0]['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
        with self.assertRaises(AccountingError):self.r.commit(self.a,c[1]['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
    def test_сбой_перед_commit_откатывает_событие(self):
        rid=self.r.draft(self.a,body());c=self.r.prepare(self.a,rid,1,body(),now=1000)
        with self.assertRaises(OSError):self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000,fail_before_commit=True)
        self.assertEqual(self.r.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
    def test_изменение_доступности_и_отзыв(self):
        rid=self.active();c=self.r.prepare(self.a,rid,2,body(available='2'),now=1000)
        self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
        self.assertEqual(self.r.balance(self.b,at=1000)['by_category_unit']['computer:item']['available_self_declared'],'2')
        self.r.revoke(self.a,rid);self.r.revoke(self.a,rid)
        self.assertEqual(self.r.balance(self.b,at=1000)['registered_objects'],0)
        with self.assertRaises(AccountingError):self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
    def test_оценки_на_дату_и_stock_не_смешан_с_flow(self):
        rid=self.active();a=self.r.record_valuation(self.a,rid,engine(),'100','RUB',effective_at=1000,now=1000)
        self.r.record_valuation(self.a,rid,engine(rate='20000000',at=1100),'100','RUB',effective_at=1100,now=1100,previous_id=a.id)
        self.assertEqual(self.r.balance(self.a,at=1000)['stock_btc_known'],'0.00001000')
        self.assertEqual(self.r.balance(self.a,at=1100)['stock_btc_known'],'0.00000500')
        self.assertIsNone(self.r.balance(self.a,at=1100)['flow_btc'])
        self.assertEqual(len(self.r.export_own(self.a)['valuations']),2)
    def test_равные_цены_разных_объектов_не_сливаются(self):
        for _ in range(2):
            rid=self.active();self.r.record_valuation(self.a,rid,engine(),'100','RUB',effective_at=1000,now=1000)
        self.assertEqual(self.r.balance(self.a,at=1000)['stock_btc_known'],'0.00002000')
    def test_неизвестная_оценка_не_ноль(self):
        self.active();v=self.r.balance(self.a,at=1000)
        self.assertEqual(v['unvalued_objects'],1);self.assertEqual(v['confirmed_available'].split(':')[0],'UNKNOWN')
    def test_удаление_экспорт_и_запрет_импорта_backup(self):
        rid=self.active();snapshot=self.r.export_own(self.a);self.r.erase(self.a,rid)
        self.assertEqual(self.r.export_own(self.a)['goods'],[])
        with self.assertRaises(AccountingError):self.r.restore(snapshot)
        self.assertEqual(self.r.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
    def test_повторная_регистрация_не_возвращает_данные(self):
        self.active();self.r.delete_participant(self.a);new=self.r.participant()
        self.assertNotEqual(new,self.a);self.assertEqual(self.r.export_own(new)['goods'],[])
    def test_целостность_и_отсутствие_новых_таблиц_пользователей(self):
        self.active();self.assertEqual(self.r.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.r.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.r.db.execute("SELECT count(*) FROM sqlite_master WHERE name='users'").fetchone()[0],0)
    def test_сырой_текст_и_предположения_не_подтверждаются(self):
        for kw in ({'category':'Мой сосед богат'}, {'conditions':'Я думаю'}, {'right':'MAYBE'}, {'extra':'secret'}):
            with self.assertRaises(AccountingError):self.r.draft(self.a,body(**kw))

    def test_дубликат_черновика_по_ключу_и_удалённый_ключ(self):
        key='synthetic-idempotency-01';rid=self.r.draft(self.a,body(),key=key)
        self.assertEqual(rid,self.r.draft(self.a,body(),key=key))
        with self.assertRaises(AccountingError):self.r.draft(self.a,body(quantity='3'),key=key)
        self.r.erase(self.a,rid)
        with self.assertRaises(AccountingError):self.r.draft(self.a,body(),key=key)
    def test_изменение_количества_не_сохраняет_старую_оценку_лота(self):
        rid=self.active();self.r.record_valuation(self.a,rid,engine(),'100','RUB',effective_at=1000,now=1000)
        c=self.r.prepare(self.a,rid,2,body(quantity='3'),now=1000)
        self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
        self.assertEqual(self.r.balance(self.a,at=1000)['unvalued_objects'],1)
    def test_единицы_не_суммируются_в_один_необоснованный_показатель(self):
        self.active(unit='kg',quantity='2',available='1');self.active(unit='hour',quantity='2',available='1')
        self.assertEqual(set(self.r.balance(self.a,at=1000)['by_category_unit']),{'computer:kg','computer:hour'})
    def test_видимость_отзывается_с_новой_версией(self):
        rid=self.active();c=self.r.prepare(self.a,rid,2,body(audience='PRIVATE'),now=1000)
        self.r.commit(self.a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
        self.assertEqual(self.r.balance(self.b,at=1000)['registered_objects'],0)
        self.assertEqual(len(self.r.export_own(self.a)['events']),2)
    def test_потерянный_процесс_не_восстанавливает_старое_согласие(self):
        self.active();snapshot=self.r.export_own(self.a)
        fresh=LabRegistry(synthetic=True)
        try:
            actor=fresh.participant();self.assertEqual(fresh.balance(actor,at=1000)['registered_objects'],0)
            with self.assertRaises(AccountingError):fresh.restore(snapshot)
        finally:fresh.close()
    def test_повторная_оценка_не_создаёт_двойной_стоимости(self):
        rid=self.active()
        for _ in range(2):self.r.record_valuation(self.a,rid,engine(),'100','RUB',effective_at=1000,now=1000)
        self.assertEqual(self.r.db.execute('SELECT count(*) FROM valuations').fetchone()[0],1)
        self.assertEqual(self.r.balance(self.a,at=1000)['stock_btc_known'],'0.00001000')

    def test_откат_часов_не_обновляет_согласие(self):
        rid=self.active()
        with self.assertRaises(AccountingError):self.r.prepare(self.a,rid,2,body(),now=999)
        with patch('xblago.domain.collective_calculator.time.time',return_value=999):
            with self.assertRaises(AccountingError):self.r.revoke(self.a,rid)
        self.r.revoke(self.a,rid)
        row=self.r.export_own(self.a)['goods'][0]
        self.assertGreaterEqual(row['updated_at'],row['created_at'])
    def test_неверные_типы_не_записываются(self):
        for kw in ({'unit':[]},{'available':1.2},{'audience':{}},{'quantity':True}):
            with self.assertRaises(AccountingError):self.r.draft(self.a,body(**kw))
        self.assertEqual(self.r.db.execute('SELECT count(*) FROM goods').fetchone()[0],0)


class HelpProjectionTests(unittest.TestCase):
    def setUp(self):
        from tools.help_readiness_lab import initialize
        from xblago.core.integration import preflight
        from xblago.integrations.web import WebApp
        from xblago.domain.help_service import HelpService
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)/'new';cfg=initialize(root)
        self.env=patch.dict(os.environ,cfg);self.env.start();self.addCleanup(self.env.stop)
        self.app=WebApp(preflight(root/'app/blago.sqlite3'));self.h=HelpService(self.app)
        self.a,self.b,self.c=[self.app.register('calculator_'+x,'synthetic-password-only','synthetic-help011-only')[0] for x in ('author','helper','outsider')]
        self.rid=self.h.draft(self.a,'Мне нужна помощь с велосипедом',[self.h.code(self.b)['code']],'calculator-help-request-01')['id']
        self.view=HelpAccountingView(self.h)
    def action(self,actor,action,args=None):
        p=self.h.prepare(actor,self.rid,self.h.get(actor,self.rid)['version'],action,args or {})
        return self.h.commit(actor,p['challenge'],'ПОДТВЕРЖДАЮ')
    def complete(self):
        self.action(self.a,'publish');self.action(self.b,'offer',{'note':'Могу помочь с ремонтом'})
        self.action(self.a,'accept',{'offer_id':self.h.get(self.a,self.rid)['offers'][0]['id']})
        self.action(self.a,'confirm');self.action(self.b,'confirm')
    def test_публикация_не_обязательство_и_изоляция_аудитории(self):
        self.assertEqual(self.view.read(self.a)['published_count'],0);self.action(self.a,'publish')
        self.assertFalse(self.view.read(self.b)['needs'][0]['obligation'])
        self.assertEqual(self.view.read(self.c)['published_count'],0)
    def test_подтверждённое_исполнение_и_повтор_не_удваивают_flow(self):
        self.complete();a=self.view.read(self.a);b=self.view.read(self.a)
        self.assertEqual(a,b);self.assertEqual(a['completed_count'],1)
        self.assertEqual(a['needs'][0]['evidence'],'COUNTERPARTY_CONFIRMED');self.assertEqual(a['satisfied_share'],'1.00000000')
        self.assertIsNone(a['flow_btc']);self.assertIsNone(a['needs'][0]['quantity'])
    def test_спор_и_отзыв_корректируют_живую_проекцию(self):
        self.complete();self.action(self.b,'dispute')
        self.assertEqual(self.view.read(self.a)['completed_count'],0);self.assertEqual(self.view.read(self.a)['disputed_count'],1)
        self.action(self.a,'withdraw');self.assertEqual(self.view.read(self.a)['published_count'],0)
    def test_сессия_потеряна_и_чужой_id_не_авторизуют_агрегат(self):
        from xblago.integrations.web import ApiError
        for token in ('invalid',self.app.session(self.a)[0]):
            with self.assertRaises(ApiError):self.view.read(token)
    def test_не_создаётся_второй_реестр_и_память_не_читается(self):
        self.complete()
        with patch.object(self.app.store,'all_memory',side_effect=AssertionError('memory must remain private')):
            self.assertEqual(self.view.read(self.b)['completed_count'],1)
        with self.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM help_requests').fetchone()[0],1)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='goods'").fetchone())

    def test_потеря_witness_закрывает_аналитику(self):
        from xblago.core.security_journal import JournalUnavailable
        self.complete();directory=Path(os.environ['XBLAGO_JOURNAL_WITNESS_DIR']);held=directory.with_name('held-witness')
        directory.rename(held)
        try:
            with self.assertRaises(JournalUnavailable):self.view.read(self.a)
        finally:held.rename(directory)
    def test_старый_backup_не_возвращает_исполнение_в_проекции(self):
        from xblago.core.privacy_maintenance import restore
        from xblago.integrations.web import WebApp,ApiError
        from xblago.domain.help_service import HelpService
        self.complete();backup=self.app.store.backup_database(Path(self.tmp.name)/'backup')
        self.action(self.a,'withdraw')
        recovered=restore(backup,Path(os.environ['XBLAGO_SECURITY_JOURNAL_DIR']))
        h=HelpService(WebApp(recovered));v=HelpAccountingView(h)
        with self.assertRaises(ApiError):v.read(self.a)
        token=h.app.login('calculator_author','synthetic-password-only')[0]
        self.assertEqual(v.read(token)['completed_count'],0);self.assertEqual(v.read(token)['published_count'],0)
