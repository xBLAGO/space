"""Disposable 100-participant, ten-million-RUB planning model on existing UEL."""
import argparse
from contextlib import ExitStack, contextmanager
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import tempfile
import time
from unittest.mock import patch

from tools.help_readiness_lab import initialize
from xblago.core import uel_checkpoint as cp, uel_schema
from xblago.core.asset_registry_schema import migrate_registry
from xblago.core.integration import preflight
from xblago.core.privacy_maintenance import restore
from xblago.core.security_journal import SecurityJournal, canonical
from xblago.domain.asset_registry import AssetRegistry
from xblago.domain.collective_calculator import ValueEngine, OfflineRateProvider, Quote
from xblago.domain.help_service import HelpService
from xblago.domain.universal_balance import (
    AccountingError, Demand, Option, RolePolicy, Scenario, Step, UniversalCollectiveBalance)
from xblago.integrations.web import WebApp

PASSWORD = 'synthetic-foundation09-password'
INVITE = 'synthetic-help011-only'


def serve(cfg, directory, endpoint):
    os.environ.update(cfg)
    cp.serve(directory, endpoint)


@contextmanager
def lab(participants=100):
    if type(participants) is not int or not 4 <= participants <= 100:
        raise ValueError('Four to 100 synthetic participants required')
    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='f09-', dir='/tmp')))
        clock = time.time()
        stack.enter_context(patch('time.time', return_value=clock))
        stack.enter_context(patch.dict(os.environ, {'XBLAGO_UEL_CHECKPOINT_CONFIG': '',
                                                    'XBLAGO_UEL_LOCAL_ENABLED': 'false'}))
        cfg = initialize(root / 'source')
        stack.enter_context(patch.dict(os.environ, cfg))
        path = Path(cfg['XBLAGO_DATA_DIR']) / 'blago.sqlite3'
        migrate_registry(path)
        cfg.update(XBLAGO_ASSET_REGISTRY_ENABLED='true')
        os.environ.update(cfg)
        uel_schema.migrate_local(path)
        cfg.update(XBLAGO_UEL_LOCAL_ENABLED='true', XBLAGO_UNIVERSAL_BALANCE_ENABLED='true')
        os.environ.update(cfg)
        app = WebApp(preflight(path))
        tokens = [app.register(f'foundation09_synthetic_{i:03d}', PASSWORD, INVITE)[0] for i in range(participants)]
        grants = []
        for index, token in enumerate(tokens):
            uid = app.session(token)[0]
            role = ('PROPOSE',) if index == 0 else ('APPROVE',) if index == 1 else ('EXECUTE',) if index == 2 else ()
            grants.append((uid, ('VIEW',) + role))
        registry, help_service = AssetRegistry(app), HelpService(app)
        resources = []
        for index in range(min(12, participants)):
            unit = 'hour' if index % 3 == 2 else 'item'
            quantity = '8' if unit == 'hour' else '3'
            body = {'label': f'Synthetic capability {index}', 'category': f'capability_{index}',
                    'quantity': quantity, 'available': '0', 'unit': unit, 'right': 'OWNER',
                    'share_consent': False, 'audience': 'PRIVATE', 'conditions': 'ON_REQUEST'}
            aid = registry.draft(tokens[index], body, f'foundation09-asset-{index:03d}')['id']
            body.update(available=quantity, share_consent=True, audience='LAB_COMMUNITY')
            preview = registry.prepare(tokens[index], aid, 1, body)
            registry.commit(tokens[index], preview['challenge'], 'ПОДТВЕРЖДАЮ')
            resources.append(aid)
        requests = []
        codes = [help_service.code(token)['code'] for token in tokens[:4]]
        for index in range(min(12, participants)):
            author = tokens[index]
            audience = [code for member, code in zip(tokens[:4], codes) if member != author]
            rid = help_service.draft(author, f'Синтетическая потребность {index}', audience,
                                     f'foundation09-request-{index:03d}')['id']
            preview = help_service.prepare(author, rid, 1, 'publish', {})
            help_service.commit(author, preview['challenge'], 'ПОДТВЕРЖДАЮ')
            requests.append(rid)
        # One actual existing Help cycle; source fulfillment remains separate.
        rid = requests[0]
        for token, action, args in (
            (tokens[3], 'offer', {'note': 'Синтетическое предложение помощи'}),
            (tokens[0], 'accept', None),
            (tokens[0], 'confirm', {}),
            (tokens[3], 'confirm', {}),
        ):
            state = help_service.get(token, rid)
            if args is None:
                args = {'offer_id': state['offers'][0]['id']}
            preview = help_service.prepare(token, rid, state['version'], action, args)
            help_service.commit(token, preview['challenge'], 'ПОДТВЕРЖДАЮ')
        source_key = root / 'source-signing.key'
        source_public = cp.create_key(source_key)
        stream = secrets.token_hex(32)
        directory, endpoint = root / 'custody', root / 'socket'
        custodian_public = cp.initialize(directory, stream, source_public)
        config = {'stream': stream, 'custodian_key': custodian_public,
                  'source_key_file': str(source_key), 'endpoint': str(endpoint)}
        config_path = root / 'checkpoint.json'
        config_path.write_bytes(cp.encode(config))
        config_path.chmod(0o600)
        cfg['XBLAGO_UEL_CHECKPOINT_CONFIG'] = str(config_path)
        os.environ.update(cfg)
        server = multiprocessing.get_context('spawn').Process(target=serve, args=(cfg, directory, endpoint))
        server.start()

        def stop():
            if server.is_alive():
                server.terminate()
            server.join(10)
        stack.callback(stop)
        deadline = time.monotonic() + 15
        while not endpoint.exists():
            if not server.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Synthetic custodian failed')
            time.sleep(0.02)
        cp.enroll(SecurityJournal(Path(cfg['XBLAGO_SECURITY_JOURNAL_DIR'])))
        policy = RolePolicy('synthetic-discussion-v1', tuple(grants))
        yield {'app': app, 'cfg': cfg, 'tokens': tokens, 'registry': registry, 'help': help_service,
               'model': UniversalCollectiveBalance(app, policy), 'policy': policy, 'resources': resources,
               'requests': requests, 'root': root, 'clock': clock}


def scenario_for(state):
    model, tokens = state['model'], state['tokens']
    snapshot = model.inspect(tokens[0])
    needs = [row['observation'] for row in snapshot['items']
             if row['kind'] == 'HELP' and row['claim']['state'] in ('PUBLISHED', 'OFFERED')]
    assets = {row['claim']['category']: row for row in snapshot['items'] if row['kind'] == 'ASSET'}
    if len(needs) < 8:
        raise ValueError('Eight visible needs required')
    tool = assets['capability_1']['observation']
    skill = assets['capability_2']['observation']
    options = (
        Option('infrastructure', needs[0], 1, '3000000.00', 4, 10, '100', 'REUSABLE_CAPACITY'),
        Option('infrastructure-alternative', needs[0], 1, '2500000.00', 4, 10, '120', 'REUSABLE_CAPACITY'),
        Option('repair', needs[1], 5, '10000.00', 5, 2, '2', 'ONE_OFF', (Demand(tool, '1', 'item'),)),
        Option('training', needs[2], 10, '0.00', 3, 15, '1', 'REUSABLE_CAPACITY',
               (Demand(skill, '1', 'hour', True),), ('infrastructure',)),
        Option('failed-project', needs[3], 1, '4000000.00', 2, 30, '100', 'REUSABLE_CAPACITY'),
        Option('cancelled-project', needs[4], 1, '2000000.00', 2, 20, '10', 'ONE_OFF'),
        Option('future-reserve', needs[5], 1, '1000000.00', 3, 15, '5', 'ONE_OFF'),
        Option('pending-infrastructure', needs[7], 1, '500000.00', 3, 20, '20', 'REUSABLE_CAPACITY'),
        Option('unaffordable', needs[6], 1, '12000000.00', 1, 40, '20', 'REUSABLE_CAPACITY'),
    )
    actions = []

    def action(name, verb, actor, suffix=''):
        actions.append(Step(name + '-' + verb.lower() + suffix, verb, name, tokens[actor]))

    for name in ('training', 'infrastructure', 'infrastructure-alternative', 'repair',
                 'failed-project', 'cancelled-project', 'future-reserve', 'unaffordable', 'pending-infrastructure'):
        action(name, 'PROPOSE', 0)
        action(name, 'APPROVE', 1)
    action('training', 'RESERVE', 2)  # dependency not yet confirmed
    action('infrastructure', 'RESERVE', 2)
    action('infrastructure-alternative', 'RESERVE', 2)  # same need cannot allocate twice
    action('infrastructure', 'EXECUTE', 2)
    action('infrastructure', 'CONFIRM_RESULT', 1)
    for name in ('repair', 'training', 'failed-project'):
        action(name, 'RESERVE', 2, '-after-dependency')
        action(name, 'EXECUTE', 2)
        action(name, 'FAIL' if name == 'failed-project' else 'CONFIRM_RESULT', 1)
    action('cancelled-project', 'RESERVE', 2)
    action('cancelled-project', 'CANCEL', 0)
    action('future-reserve', 'RESERVE', 2)
    action('future-reserve', 'REVOKE_APPROVAL', 1)
    action('future-reserve', 'EXECUTE', 2)  # revoked
    action('unaffordable', 'RESERVE', 2)
    action('pending-infrastructure', 'RESERVE', 2)
    actions.append(actions[0])  # exact duplicate
    actions.append(Step('wrong-executor', 'EXECUTE', 'repair', tokens[3]))
    return Scenario(snapshot['source_revision'], '10000000.00', 'INDEPENDENTLY_VERIFIED', options, tuple(actions)), snapshot


def run_lab():
    with lab(100) as state:
        scenario, snapshot = scenario_for(state)
        app, model, token = state['app'], state['model'], state['tokens'][0]
        with app.store._connect() as db:
            before = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                      for table in ('users', 'assetreg_records', 'help_events', 'uel_events')}
        result = model.simulate(token, scenario)
        replay = model.simulate(token, scenario)
        assert canonical(result) == canonical(replay)
        with app.store._connect() as db:
            after = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in before}
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert before == after and before['users'] == 100
        assert result['capital']['simulated_spent_rub'] == '7030000.00'
        assert result['capital']['available_rub'] == '2470000.00'
        assert result['capital']['reserved_rub'] == '500000.00'
        assert snapshot['wellbeing']['confirmed_help_occurrences'] == 1
        backup = app.store.backup_database(state['root'] / 'old-copy')
        state['registry'].revoke(token, state['resources'][0])
        try:
            model.simulate(token, scenario)
        except AccountingError:
            stale_denied = True
        else:
            raise AssertionError('Stale model accepted')
        restored = WebApp(restore(backup, Path(state['cfg']['XBLAGO_SECURITY_JOURNAL_DIR'])))
        fresh = restored.login('foundation09_synthetic_000', PASSWORD)[0]
        restored_model = UniversalCollectiveBalance(restored, state['policy'])
        try:
            restored_model.simulate(fresh, scenario)
        except AccountingError:
            restore_denied = True
        else:
            raise AssertionError('Old model recovered authority')
        rate = ValueEngine(OfflineRateProvider([
            Quote('RUB', '9000000', 100, 'fixture-a', 'offline-a'),
            Quote('RUB', '9000000', 100, 'fixture-b', 'offline-b')]),
            {'fixture-a': 'offline-a', 'fixture-b': 'offline-b'})
        historical = rate.evaluate('100000.00', 'RUB', effective_at=100, now=100)
        return {'format': 'foundation09-synthetic-evidence-1', 'participants': 100,
                'scenario_capital_rub': '10000000.00', 'status': 'PASS',
                'source_counts_unchanged_by_planning': before == after,
                'actual_source_help_confirmations': 1,
                'simulation': result, 'exact_recompute': True, 'stale_source_denied': stale_denied,
                'old_backup_plan_denied': restore_denied,
                'valuation_example': {'historical_btc': historical.btc, 'affects_cash': False},
                'source_revision_exported': False, 'durable_capital_ledger': 'NOT IMPLEMENTED',
                'real_users': 'NO-GO', 'independent_operator': 'NOT EXECUTED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run_lab()
    result['simulation'].pop('source_revision', None)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('Synthetic 100-participant / 10M RUB planning and source recovery: PASS')


if __name__ == '__main__':
    main()
