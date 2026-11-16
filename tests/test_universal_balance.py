"""Synthetic capital arithmetic and source-isolated planning tests."""
from dataclasses import replace
import unittest
from xblago.domain.universal_balance import (
    _PlanningModel, AccountingError, Demand, Option, RolePolicy, Scenario, Step, kopecks)

ASSET = 'a' * 64
NEED = 'b' * 64


def views(quantity='10', state='PUBLISHED', consent=True):
    return [
        {'kind': 'ASSET', 'observation': ASSET, 'claim': {'state': 'ACTIVE', 'unit': 'item',
         'quantity': quantity, 'available': quantity, 'share_consent': consent}},
        {'kind': 'HELP', 'observation': NEED, 'claim': {'state': state}},
    ]


def option(**changes):
    return replace(Option('repair', NEED, 10, '100.00', 5, 10, '2', 'ONE_OFF',
                          (Demand(ASSET, '1', 'item'),)), **changes)


def policy():
    return RolePolicy('fixture-v1', (('p', ('VIEW', 'PROPOSE')), ('a', ('VIEW', 'APPROVE')),
                                   ('e', ('VIEW', 'EXECUTE')), ('other', ('VIEW',))))


def steps(identifier='repair', end='CONFIRM_RESULT'):
    return (Step('propose-' + identifier, 'PROPOSE', identifier, 'p'),
            Step('approve-' + identifier, 'APPROVE', identifier, 'a'),
            Step('reserve-' + identifier, 'RESERVE', identifier, 'e'),
            Step('execute-' + identifier, 'EXECUTE', identifier, 'e'),
            Step('result-' + identifier, end, identifier, 'a'))


def run(options=None, actions=None, capital='10000000.00', level='INDEPENDENTLY_VERIFIED', source=None):
    scenario = Scenario('fixture-revision', capital, level, options or (option(),), actions or steps())
    return _PlanningModel(source or views(), scenario, policy()).run(lambda step: step.token)


class ModelTests(unittest.TestCase):
    def test_каждая_копейка_сохраняется(self):
        result = run()
        self.assertEqual(result['capital']['simulated_spent_rub'], '1000.00')
        self.assertEqual(result['capital']['available_rub'], '9999000.00')
        for state in result['evaluations']:
            self.assertEqual(state['available_kopecks'] + state['reserved_kopecks'] + state['spent_kopecks'], 1000000000)

    def test_точность_рублей_и_запрет_float(self):
        self.assertEqual(kopecks('0.01'), 1)
        for invalid in (0.1, '0.001', '-1', 'NaN', '1e5', True):
            with self.assertRaises(AccountingError):
                kopecks(invalid)

    def test_частичное_обеспечение_по_капиталу(self):
        result = run(capital='350.00')
        self.assertEqual(result['forecast_need_units'], 3)
        self.assertEqual(result['capital']['available_rub'], '50.00')

    def test_частичное_обеспечение_по_ресурсу(self):
        self.assertEqual(run(source=views('2'))['forecast_need_units'], 2)

    def test_неизвестное_количество_не_становится_нулём(self):
        result = run(source=views(None))
        self.assertEqual(result['capital']['simulated_spent_rub'], '0.00')
        self.assertIn('UNKNOWN_OR_REVOKED_RESOURCE', [row['status'] for row in result['evaluations']])

    def test_неподтверждённый_капитал_закрыт(self):
        for level in ('UNKNOWN', 'SELF_DECLARED', 'REVOKED', 'DISPUTED'):
            result = run(level=level)
            self.assertIsNone(result['capital']['available_rub'])
            self.assertEqual(result['capital']['simulated_spent_rub'], '0.00')
            self.assertIn('UNVERIFIED_CAPITAL', [r['status'] for r in result['evaluations']])

    def test_доступность_не_превращается_в_собственность(self):
        result = run()
        self.assertFalse(result['durable_capital_ledger'])
        self.assertEqual(result['source_operations_performed'], 0)
        self.assertEqual(result['confirmed_help_fulfillments_created'], 0)

    def test_нет_согласия_нет_распределения(self):
        self.assertEqual(run(source=views(consent=False))['forecast_need_units'], 0)

    def test_разные_единицы_не_смешиваются(self):
        result = run(options=(option(demands=(Demand(ASSET, '1', 'hour'),)),))
        self.assertIn('UNIT_MISMATCH', [r['status'] for r in result['evaluations']])

    def test_ресурс_не_удваивается_через_два_алиаса(self):
        with self.assertRaises(AccountingError):
            run(options=(option(demands=(Demand(ASSET, '1', 'item'), Demand(ASSET, '2', 'item'))),))

    def test_повторная_доставка_ничего_не_расходует(self):
        actions = steps()
        first = run(actions=actions)
        second = run(actions=actions + actions)
        self.assertEqual(first['capital'], second['capital'])
        self.assertEqual(first['forecast_need_units'], second['forecast_need_units'])

    def test_коллизия_ключа_повтора_отклоняется(self):
        with self.assertRaises(AccountingError):
            run(actions=(Step('same', 'PROPOSE', 'repair', 'p'), Step('same', 'APPROVE', 'repair', 'a')))

    def test_самосогласование_не_создаёт_резерв(self):
        configured = RolePolicy('fixture', (('p', ('VIEW', 'PROPOSE', 'APPROVE')), ('e', ('EXECUTE',))))
        scenario = Scenario('r', '1000.00', 'INDEPENDENTLY_VERIFIED', (option(),),
                            (Step('p', 'PROPOSE', 'repair', 'p'), Step('a', 'APPROVE', 'repair', 'p')))
        result = _PlanningModel(views(), scenario, configured).run(lambda step: step.token)
        self.assertEqual(result['capital']['reserved_rub'], '0.00')
        self.assertEqual(result['plan_states']['repair']['state'], 'PROPOSED')

    def test_отдельные_права_исполнения(self):
        actions = steps()[:3] + (Step('bad-execute', 'EXECUTE', 'repair', 'a'),)
        result = run(actions=actions)
        self.assertIn('AUTHORITY_DENIED', [r['status'] for r in result['evaluations']])

    def test_отзыв_согласования_освобождает_резерв(self):
        result = run(actions=steps()[:3] + (Step('revoke', 'REVOKE_APPROVAL', 'repair', 'a'),))
        self.assertEqual(result['capital']['available_rub'], '10000000.00')
        self.assertEqual(result['capital']['reserved_rub'], '0.00')

    def test_после_отзыва_исполнение_отклоняется(self):
        result = run(actions=steps()[:3] + (Step('revoke', 'REVOKE_APPROVAL', 'repair', 'a'),
                                          Step('late', 'EXECUTE', 'repair', 'e')))
        self.assertEqual(result['capital']['simulated_spent_rub'], '0.00')

    def test_отмена_до_исполнения_возвращает_ресурс(self):
        result = run(actions=steps()[:3] + (Step('cancel', 'CANCEL', 'repair', 'p'),))
        self.assertEqual(result['capital']['reserved_rub'], '0.00')

    def test_отмена_после_расхода_не_создаёт_денег(self):
        result = run(actions=steps()[:4] + (Step('cancel', 'CANCEL', 'repair', 'p'),))
        self.assertEqual(result['capital']['simulated_spent_rub'], '1000.00')

    def test_неудача_проекта_сохраняет_расход(self):
        result = run(actions=steps(end='FAIL'))
        self.assertEqual(result['forecast_need_units'], 0)
        self.assertEqual(result['capital']['simulated_spent_rub'], '1000.00')

    def test_конкурирующие_альтернативы_не_дублируют_потребность(self):
        second = option(id='alternative')
        result = run(options=(option(), second), actions=steps()[:3] + steps('alternative'))
        self.assertEqual(result['capital']['reserved_rub'], '1000.00')
        self.assertEqual(result['capital']['simulated_spent_rub'], '0.00')

    def test_согласованная_единица_потребности(self):
        result = run(options=(option(), option(id='alternative', units=20)), actions=steps()[:3])
        self.assertIn('CONFLICTING_NEED_QUANTITY', [r['status'] for r in result['evaluations']])

    def test_подтверждённый_Help_не_планируется_повторно(self):
        self.assertEqual(run(source=views(state='COMPLETED'))['forecast_need_units'], 0)

    def test_зависимости_требуют_результата(self):
        result = run(options=(option(dependencies=('infrastructure',)),))
        self.assertIn('DEPENDENCY_UNCONFIRMED', [r['status'] for r in result['evaluations']])

    def test_приоритеты_объяснимы_без_рейтинга_человека(self):
        result = run(options=(option(id='capacity', urgency=1, horizon='REUSABLE_CAPACITY'), option(id='basic', urgency=5)))
        self.assertEqual(result['recommendations'][0]['option'], 'basic')
        self.assertIsNone(result['single_welfare_score'])

    def test_восстановление_модели_детерминировано(self):
        self.assertEqual(run(), run())

    def test_долговременная_мощность_сохраняет_физический_запас(self):
        first = option(demands=(Demand(ASSET, '1', 'item', reusable=True),))
        second_need = 'c' * 64
        source = views('1') + [{'kind': 'HELP', 'observation': second_need, 'claim': {'state': 'PUBLISHED'}}]
        result = run(options=(first, option(id='next', need=second_need, demands=first.demands)),
                     actions=steps() + steps('next'), source=source)
        self.assertEqual(result['forecast_need_units'], 2)


    def test_нулевая_цена_без_возможности_не_создаёт_благо(self):
        with self.assertRaises(AccountingError):
            run(options=(option(cost_per_unit_rub='0.00', demands=()),))

    def test_переоценка_не_создаёт_денежный_остаток(self):
        from xblago.domain.collective_calculator import ValueEngine, OfflineRateProvider, Quote
        def value(rate, at):
            return ValueEngine(OfflineRateProvider([
                Quote('RUB', rate, at, 'a', 'fixture-a'),
                Quote('RUB', rate, at, 'b', 'fixture-b')]), {'a': 'fixture-a', 'b': 'fixture-b'})
        before = run()
        old = value('9000000', 100).evaluate('100000.00', 'RUB', effective_at=100, now=100)
        new = value('10000000', 200).evaluate('100000.00', 'RUB', effective_at=200, now=200, previous=old)
        self.assertNotEqual(old.btc, new.btc)
        self.assertEqual(old.btc, '0.01111111')
        self.assertEqual(before['capital'], run()['capital'])


import json
import os
from unittest.mock import patch
import tests.test_uel_checkpoint as checkpoint_fixture
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.universal_balance import UniversalCollectiveBalance
from xblago.integrations.privacy import PrivacyService
from xblago.integrations.web import ApiError, WebApp


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = checkpoint_fixture.CheckpointTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.lab = self.fixture.lab
        env = patch.dict(os.environ, {'XBLAGO_UNIVERSAL_BALANCE_ENABLED': 'true'})
        env.start()
        self.addCleanup(env.stop)
        self.policy = RolePolicy('synthetic-v1', tuple(
            (self.lab.app.session(token)[0], roles) for token, roles in (
                (self.lab.a, ('VIEW', 'PROPOSE')),
                (self.lab.b, ('VIEW', 'APPROVE')),
                (self.lab.c, ('VIEW', 'EXECUTE')))))
        self.model = UniversalCollectiveBalance(self.lab.app, self.policy)
        self.asset = self.lab.publish()
        codes = [self.lab.h.code(token)['code'] for token in (self.lab.b, self.lab.c)]
        self.request = self.lab.h.draft(self.lab.a, 'Синтетическая помощь', codes, 'foundation09-need-001')['id']
        self.lab.act(self.lab.a, self.request, 'publish')
        self.scenario = self.make_scenario()

    def make_scenario(self):
        snapshot = self.model.inspect(self.lab.a)
        asset = next(v['observation'] for v in snapshot['items'] if v['kind'] == 'ASSET')
        need = next(v['observation'] for v in snapshot['items'] if v['kind'] == 'HELP')
        item = Option('repair', need, 1, '100.00', 5, 10, '1', 'ONE_OFF', (Demand(asset, '1', 'item'),))
        actions = tuple(Step(key, action, 'repair', token) for key, action, token in (
            ('p', 'PROPOSE', self.lab.a), ('a', 'APPROVE', self.lab.b), ('r', 'RESERVE', self.lab.c),
            ('e', 'EXECUTE', self.lab.c), ('f', 'CONFIRM_RESULT', self.lab.b)))
        return Scenario(snapshot['source_revision'], '10000000.00', 'INDEPENDENTLY_VERIFIED', (item,), actions)

    def test_флаг_по_умолчанию_выключен(self):
        with patch.dict(os.environ, {'XBLAGO_UNIVERSAL_BALANCE_ENABLED': ''}):
            with self.assertRaises(ApiError) as raised:
                self.model.inspect(self.lab.a)
            self.assertEqual(raised.exception.status, 404)

    def test_чужой_id_не_служит_сессией(self):
        uid = self.lab.app.session(self.lab.a)[0]
        with self.assertRaises(ApiError):
            self.model.inspect(uid)

    def test_план_не_меняет_источники_и_UEL(self):
        before = self.lab.records()
        first = self.model.simulate(self.lab.a, self.scenario)
        second = self.model.simulate(self.lab.a, self.scenario)
        self.assertEqual(first, second)
        self.assertEqual(self.lab.records(), before)
        self.assertEqual(self.lab.h.get(self.lab.a, self.request)['state'], 'PUBLISHED')
        self.assertEqual(first['capital']['simulated_spent_rub'], '100.00')

    def test_отзыв_ресурса_инвалидирует_план(self):
        self.lab.r.revoke(self.lab.a, self.asset)
        with self.assertRaises(AccountingError):
            self.model.simulate(self.lab.a, self.scenario)

    def test_удаление_памяти_сохраняет_G09(self):
        uid = self.lab.app.session(self.lab.a)[0]
        self.lab.app.store.set_memory(uid, 'city', 'Синтетический город')
        with self.assertRaises(AccountingError):
            self.model.simulate(self.lab.a, self.scenario)

    def test_ротация_сессии_отклоняет_старый_token(self):
        self.lab.app.login('uel_synthetic_owner', checkpoint_fixture.fixture.PASSWORD, old_token=self.lab.a)
        with self.assertRaises(ApiError):
            self.model.simulate(self.lab.a, self.scenario)

    def test_аудитория_проверяется_для_каждого_согласующего(self):
        codes = [self.lab.h.code(self.lab.b)['code']]
        rid = self.lab.h.draft(self.lab.a, 'Частная синтетическая помощь', codes, 'foundation09-private-001')['id']
        self.lab.act(self.lab.a, rid, 'publish')
        snapshot = self.model.inspect(self.lab.a)
        original = self.scenario.options[0].need
        private = next(v['observation'] for v in snapshot['items'] if v['kind'] == 'HELP' and v['observation'] != original)
        scenario = replace(self.scenario, source_revision=snapshot['source_revision'],
                           options=(replace(self.scenario.options[0], need=private),))
        with self.assertRaises(ApiError):
            self.model.simulate(self.lab.a, scenario)

    def test_смена_политики_требует_пересмотра(self):
        changed = UniversalCollectiveBalance(self.lab.app, replace(self.policy, version='new-v2'))
        with self.assertRaises(AccountingError):
            changed.simulate(self.lab.a, self.scenario)

    def test_потеря_checkpoint_блокирует_план(self):
        self.fixture.stop_server()
        with self.assertRaises(JournalUnavailable):
            self.model.simulate(self.lab.a, self.scenario)

    def test_удаление_аккаунта_не_возвращает_план(self):
        privacy = PrivacyService(self.lab.app)
        challenge = privacy.preview(self.lab.a)
        privacy.delete(self.lab.a, challenge['challenge'], 'УДАЛИТЬ')
        with self.assertRaises(ApiError):
            self.model.simulate(self.lab.a, self.scenario)

    def test_старый_backup_не_восстанавливает_полномочия_плана(self):
        backup = self.lab.app.store.backup_database(self.fixture.root / 'foundation09-copy')
        self.lab.r.erase(self.lab.a, self.asset)
        app = WebApp(restore(backup, self.lab.journal))
        new_token = app.login('uel_synthetic_owner', checkpoint_fixture.fixture.PASSWORD)[0]
        new_model = UniversalCollectiveBalance(app, self.policy)
        with self.assertRaises(AccountingError):
            new_model.simulate(new_token, self.scenario)

    def test_выдача_не_содержит_сессии_или_uid(self):
        result = self.model.simulate(self.lab.a, self.scenario)
        encoded = json.dumps(result)
        for token in (self.lab.a, self.lab.b, self.lab.c):
            self.assertNotIn(token, encoded)
        for uid, _ in self.policy.grants:
            self.assertNotIn(uid, encoded)

    def test_пять_представлений_различают_неизвестность(self):
        result = self.model.inspect(self.lab.a)
        for name in ('personal', 'collective', 'projects', 'capabilities', 'wellbeing'):
            self.assertIn(name, result)
        self.assertIsNone(result['collective']['capital_rub'])
        self.assertIsNone(result['wellbeing']['unmet_quantity'])
        self.assertIsNone(result['wellbeing']['human_ranking'])


    def test_два_процесса_строят_прогноз_без_двойной_записи(self):
        ctx = multiprocessing.get_context('spawn')
        before = self.lab.records()
        jobs = []
        try:
            for _ in range(2):
                parent, child = ctx.Pipe()
                proc = ctx.Process(target=forecast_worker, args=(self.lab.cfg, self.policy, self.scenario,
                                   self.lab.a, self.lab.frozen_at, child))
                proc.start()
                child.close()
                jobs.append((proc, parent))
            for _, pipe in jobs:
                self.assertTrue(pipe.poll(30))
                self.assertEqual(pipe.recv(), 'ready')
            for _, pipe in jobs:
                pipe.send('go')
            answers = []
            for proc, pipe in jobs:
                self.assertTrue(pipe.poll(30))
                answers.append(pipe.recv())
                proc.join(10)
                self.assertEqual(proc.exitcode, 0)
            self.assertEqual(answers[0], answers[1])
            self.assertEqual(self.lab.records(), before)
        finally:
            for proc, pipe in jobs:
                pipe.close()
                if proc.is_alive():
                    proc.kill()
                    proc.join()

    def test_смерть_процесса_не_фиксирует_расход(self):
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=forecast_worker, args=(self.lab.cfg, self.policy, self.scenario,
                           self.lab.a, self.lab.frozen_at, child, True))
        before = self.lab.records()
        try:
            proc.start()
            child.close()
            self.assertTrue(parent.poll(30))
            self.assertEqual(parent.recv(), 'ready')
            parent.send('go')
            proc.join(30)
            self.assertEqual(proc.exitcode, 79)
            self.assertEqual(self.lab.records(), before)
            self.assertEqual(self.model.simulate(self.lab.a, self.scenario)['capital']['simulated_spent_rub'], '100.00')
        finally:
            parent.close()
            if proc.is_alive():
                proc.kill()
                proc.join()


import multiprocessing
from pathlib import Path
from xblago.core.integration import preflight


def forecast_worker(cfg, policy, scenario, token, clock, pipe, crash=False):
    os.environ.update(cfg)
    os.environ['XBLAGO_UNIVERSAL_BALANCE_ENABLED'] = 'true'
    with patch('time.time', return_value=clock):
        app = WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR']) / 'blago.sqlite3'))
        pipe.send('ready')
        pipe.recv()
        result = UniversalCollectiveBalance(app, policy).simulate(token, scenario)
        if crash:
            os._exit(79)
        pipe.send(result['capital'])
        pipe.close()
