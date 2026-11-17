"""Foundation 09 read-only projections and disposable synthetic planning.

Source history remains Asset Registry / Help / UEL. Planning decisions have no
persistence constructor and cannot execute a payment or transfer property.
"""
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
import json
import os
import re

from xblago.core import uel_checkpoint, uel_local, uel_schema
from xblago.core.security_journal import JournalUnavailable, canonical
from xblago.domain.collective_calculator import AccountingError, number
from xblago.domain.local_ledger import LocalLedger
from xblago.integrations.web import ApiError

ALGORITHM = 'collective-plan-1'
ACTIONS = {'PROPOSE', 'APPROVE', 'RESERVE', 'EXECUTE', 'CONFIRM_RESULT', 'CANCEL', 'FAIL', 'REVOKE_APPROVAL'}
ROLES = {'VIEW', 'PROPOSE', 'APPROVE', 'EXECUTE'}
NEED_STATES = {'PUBLISHED', 'OFFERED'}


def enabled():
    return os.getenv('XBLAGO_UNIVERSAL_BALANCE_ENABLED', '').lower() in ('1', 'true', 'yes')


def kopecks(value):
    amount = number(value)
    if amount.as_tuple().exponent < -2:
        raise AccountingError('RUB requires at most two decimal places')
    with localcontext() as context:
        context.prec = 90
        return int(amount * 100)


def rubles(value):
    return f'{value // 100}.{value % 100:02d}'


def code(value):
    if type(value) is not str or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', value):
        raise AccountingError('Bounded non-personal code required')


@dataclass(frozen=True)
class RolePolicy:
    """Trusted lab configuration. Session identity never supplies its own role."""
    version: str
    grants: tuple

    def require(self, uid, role):
        if role not in ROLES or not any(actor == uid and role in roles for actor, roles in self.grants):
            raise AccountingError('AUTHORITY_DENIED')


@dataclass(frozen=True)
class Demand:
    observation: str
    quantity: str
    unit: str
    reusable: bool = False


@dataclass(frozen=True)
class Option:
    id: str
    need: str
    units: int
    cost_per_unit_rub: str
    urgency: int
    deadline: int
    duration_hours: str
    horizon: str
    demands: tuple = ()
    dependencies: tuple = ()


@dataclass(frozen=True)
class Step:
    key: str
    action: str
    option: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class Scenario:
    source_revision: str
    capital_rub: str
    capital_level: str
    options: tuple
    steps: tuple


class UniversalCollectiveBalance:
    def __init__(self, app, policy):
        if type(policy) is not RolePolicy:
            raise AccountingError('Explicit lab role policy required')
        code(policy.version)
        if type(policy.grants) is not tuple or len(policy.grants) > 1024 or len({row[0] for row in policy.grants}) != len(policy.grants):
            raise AccountingError('Immutable unique role assignments required')
        for uid, roles in policy.grants:
            if type(uid) is not str or type(roles) is not tuple or not set(roles) <= ROLES:
                raise AccountingError('Invalid configured capabilities')
        self.app, self.policy, self.ledger = app, policy, LocalLedger(app)

    def _guard(self):
        if not enabled():
            raise ApiError(404, 'Коллективное планирование выключено')
        uel_schema.require_synthetic()
        if uel_checkpoint.configuration() is None:
            raise JournalUnavailable('Collective lab requires live pinned checkpoint custody')

    def _snapshot(self, db, uid, control):
        self.policy.require(uid, 'VIEW')
        views = self.ledger._views(db, uid, control)
        context = db.execute('SELECT revision,epoch FROM context_versions WHERE blago_user_id=?', (uid,)).fetchone()
        if context is None:
            raise JournalUnavailable('Context version required')
        revision = control.journal.tag('collective-plan-input-v1', uid, canonical(views),
                                       canonical(tuple(context)), canonical(self.policy.grants), self.policy.version)
        return views, revision

    def inspect(self, token):
        self._guard()
        with self.ledger.tx(token) as (db, uid, control):
            views, revision = self._snapshot(db, uid, control)
            heads = uel_local.heads(db)
            own = set()
            shared = set()
            for row in db.execute('SELECT asset_id,owner_id,version FROM assetreg_records'):
                head = heads.get(('ASSET', control.journal.tag('asset-record', row['asset_id'])))
                if head is None or head['phase'] != 'OBSERVED':
                    continue
                ref = control.journal.tag('uel-user-view-v2', uid, head['record_ref'])
                if row['owner_id'] == uid:
                    own.add(ref)
                body = json.loads(db.execute('SELECT body FROM assetreg_versions WHERE asset_id=? AND version=?',
                                             (row['asset_id'], row['version'])).fetchone()[0])
                if body['audience'] == 'LAB_COMMUNITY':
                    shared.add(ref)
            assets = [v for v in views if v['kind'] == 'ASSET' and v['claim']['state'] == 'ACTIVE']
            needs = [v for v in views if v['kind'] == 'HELP' and v['claim']['state'] not in ('DRAFT', 'WITHDRAWN')]
            capabilities = [v for v in assets if v['observation'] in shared and v['claim']['share_consent']]
            return {
                'format': 'blago-universal-balance-lab-1', 'source_revision': revision,
                'scope': 'current authorized subset', 'coverage': 'PARTIAL', 'items': views,
                'personal': {'asset_observations': [v['observation'] for v in assets if v['observation'] in own],
                             'legal_title_verified': None, 'debts_rub': None},
                'collective': {'shared_asset_observations': [v['observation'] for v in assets if v['observation'] in shared],
                               'capital_rub': None, 'cash_reserves_rub': None},
                'projects': {'status': 'SCENARIO_ONLY', 'persisted_project_balance': None},
                'capabilities': {'observations': [v['observation'] for v in capabilities],
                                 'evidence': 'SELF_DECLARED', 'binding_commitment': False},
                'wellbeing': {'visible_needs': len(needs),
                              'confirmed_help_occurrences': sum(v['claim']['state'] == 'COMPLETED'
                                                               and v['claim']['mutual_confirmation'] for v in needs),
                              'unmet_quantity': None, 'human_ranking': None},
                'evidence': {'level': 'CALCULATED', 'basis': 'VERIFIED_LOCAL_UEL', 'real_funds_verified': False},
            }

    def simulate(self, token, scenario):
        self._guard()
        if type(scenario) is not Scenario or type(scenario.options) is not tuple or type(scenario.steps) is not tuple:
            raise AccountingError('Typed bounded scenario required')
        if len(scenario.options) > 128 or len(scenario.steps) > 512:
            raise AccountingError('Scenario limit')
        with self.ledger.tx(token) as (db, uid, control):
            views, revision = self._snapshot(db, uid, control)
            if scenario.source_revision != revision:
                raise AccountingError('STALE_SOURCE_RECOMPUTE')
            engine = _PlanningModel(views, scenario, self.policy)

            heads = uel_local.heads(db)
            record_refs = {control.journal.tag('uel-user-view-v2', uid, row['record_ref']): row['record_ref']
                           for row in heads.values() if row['phase'] == 'OBSERVED'}
            options = {option.id: option for option in scenario.options}
            visibility = {}

            def actor(step):
                row = self.app.identity.session_row(db, step.token, fresh=True)
                actor_uid = row['blago_user_id']
                self.policy.require(actor_uid, 'VIEW')
                if actor_uid not in visibility:
                    visibility[actor_uid] = {view['observation'] for view in self.ledger._views(db, actor_uid, control)}
                option = options.get(step.option)
                if option is not None:
                    refs = (option.need,) + tuple(d.observation for d in option.demands)
                    for ref in refs:
                        record = record_refs.get(ref)
                        if record is None or control.journal.tag('uel-user-view-v2', actor_uid, record) not in visibility[actor_uid]:
                            raise ApiError(404, 'Источник плана недоступен участнику')
                return actor_uid

            result = engine.run(actor)
            result['source_revision'] = revision
            return result


class _PlanningModel:
    """Temporary finite-state model of a proposed allocation, never a source log."""
    def __init__(self, views, scenario, policy):
        self.policy = policy
        self.capital = kopecks(scenario.capital_rub)
        self.capital_level = scenario.capital_level
        self.capital_verified = scenario.capital_level == 'INDEPENDENTLY_VERIFIED'
        if scenario.capital_level not in {'SELF_DECLARED', 'PARTICIPANT_CONFIRMED', 'INDEPENDENTLY_VERIFIED', 'CALCULATED', 'UNKNOWN', 'DISPUTED', 'REVOKED'}:
            raise AccountingError('Unknown capital evidence level')
        self.options = {}
        self.resources = {}
        self.needs = {v['observation']: v for v in views if v['kind'] == 'HELP'}
        for view in views:
            body = view['claim']
            if view['kind'] == 'ASSET' and body['state'] == 'ACTIVE' and body['share_consent']:
                self.resources[view['observation']] = {
                    'available': None if body['available'] is None else number(body['available']),
                    'reserved': Decimal(0), 'used': Decimal(0), 'unit': body['unit'],
                }
        for option in scenario.options:
            self._validate_option(option)
            if option.id in self.options:
                raise AccountingError('Duplicate option')
            self.options[option.id] = option
        self.steps = scenario.steps
        self.plans, self.receipts, self.need_units = {}, {}, {}
        self.reserved = self.spent = 0

    def _validate_option(self, option):
        if type(option) is not Option:
            raise AccountingError('Typed option required')
        code(option.id)
        if type(option.units) is not int or not 1 <= option.units <= 1000:
            raise AccountingError('Bounded positive integer units required')
        if type(option.urgency) is not int or not 1 <= option.urgency <= 5 or type(option.deadline) is not int or option.deadline < 0:
            raise AccountingError('Explicit urgency/deadline required')
        cost = kopecks(option.cost_per_unit_rub)
        if cost == 0 and not option.demands:
            raise AccountingError('Explicit capacity or external cost required')
        number(option.duration_hours, positive=True)
        if option.horizon not in {'ONE_OFF', 'REUSABLE_CAPACITY'}:
            raise AccountingError('Explicit benefit horizon required')
        if type(option.demands) is not tuple or type(option.dependencies) is not tuple:
            raise AccountingError('Immutable scenario inputs required')
        if len({d.observation for d in option.demands}) != len(option.demands):
            raise AccountingError('Duplicate resource alias')
        for demand in option.demands:
            if type(demand) is not Demand or type(demand.reusable) is not bool:
                raise AccountingError('Typed demand required')
            number(demand.quantity, positive=True)
        for dependency in option.dependencies:
            code(dependency)

    def _apply(self, step, actor):
        code(step.key)
        if step.action not in ACTIONS or step.option not in self.options:
            raise AccountingError('Unknown action/option')
        option = self.options[step.option]
        prior = self.plans.get(option.id)
        if step.action == 'PROPOSE':
            self.policy.require(actor, 'PROPOSE')
            need = self.needs.get(option.need)
            if need is None or need['claim']['state'] not in NEED_STATES:
                raise AccountingError('NEED_UNAVAILABLE_OR_ALREADY_COMMITTED')
            if prior is not None:
                raise AccountingError('Proposal already exists')
            self.plans[option.id] = {'state': 'PROPOSED', 'proposer': actor, 'approver': None, 'units': 0, 'cost': 0}
        elif step.action == 'APPROVE':
            self.policy.require(actor, 'APPROVE')
            if prior is None or prior['state'] != 'PROPOSED' or actor == prior['proposer']:
                raise AccountingError('Exact separate approval required')
            prior.update(state='APPROVED', approver=actor)
        elif step.action == 'RESERVE':
            self.policy.require(actor, 'EXECUTE')
            if not self.capital_verified:
                raise AccountingError('UNVERIFIED_CAPITAL')
            if prior is None or prior['state'] != 'APPROVED':
                raise AccountingError('Approval required')
            if any(self.plans.get(dep, {}).get('state') != 'CONFIRMED_SIMULATION' for dep in option.dependencies):
                raise AccountingError('DEPENDENCY_UNCONFIRMED')
            used = self.need_units.get(option.need, 0)
            # Alternatives for a need must agree on its exact requested quantity.
            if any(other.need == option.need and other.units != option.units for other in self.options.values()):
                raise AccountingError('CONFLICTING_NEED_QUANTITY')
            units = option.units - used
            cost = kopecks(option.cost_per_unit_rub)
            if cost:
                units = min(units, (self.capital - self.reserved - self.spent) // cost)
            for demand in option.demands:
                resource = self.resources.get(demand.observation)
                if resource is None or resource['available'] is None:
                    raise AccountingError('UNKNOWN_OR_REVOKED_RESOURCE')
                if demand.unit != resource['unit']:
                    raise AccountingError('UNIT_MISMATCH')
                remaining = resource['available'] - resource['reserved'] - resource['used']
                units = min(units, int(remaining // number(demand.quantity, positive=True)))
            if units <= 0:
                raise AccountingError('INSUFFICIENT_FUNDS_OR_RESOURCES')
            prior.update(state='RESERVED', units=units, cost=cost * units)
            self.reserved += prior['cost']
            self.need_units[option.need] = used + units
            for demand in option.demands:
                self.resources[demand.observation]['reserved'] += number(demand.quantity) * units
        elif step.action == 'EXECUTE':
            self.policy.require(actor, 'EXECUTE')
            if prior is None or prior['state'] != 'RESERVED':
                raise AccountingError('Current reserve required')
            if actor == prior['approver']:
                raise AccountingError('APPROVER_CANNOT_EXECUTE')
            prior['state'] = 'SPENT_SIMULATION'
            self.reserved -= prior['cost']
            self.spent += prior['cost']
            for demand in option.demands:
                resource = self.resources[demand.observation]
                quantity = number(demand.quantity) * prior['units']
                resource['reserved'] -= quantity
                resource['used'] += quantity
        elif step.action in {'CONFIRM_RESULT', 'FAIL'}:
            self.policy.require(actor, 'APPROVE')
            if prior is None or prior['state'] != 'SPENT_SIMULATION':
                raise AccountingError('Simulated execution required')
            prior['state'] = 'CONFIRMED_SIMULATION' if step.action == 'CONFIRM_RESULT' else 'FAILED_SIMULATION'
            if step.action == 'FAIL':
                self.need_units[option.need] -= prior['units']
            for demand in option.demands:
                if demand.reusable:
                    self.resources[demand.observation]['used'] -= number(demand.quantity) * prior['units']
        else:
            role = 'APPROVE' if step.action == 'REVOKE_APPROVAL' else 'PROPOSE'
            self.policy.require(actor, role)
            if prior is None or prior['state'] not in {'PROPOSED', 'APPROVED', 'RESERVED'}:
                raise AccountingError('Cannot cancel executed or closed proposal')
            if prior['state'] == 'RESERVED':
                self.reserved -= prior['cost']
                self.need_units[option.need] -= prior['units']
                for demand in option.demands:
                    self.resources[demand.observation]['reserved'] -= number(demand.quantity) * prior['units']
            prior['state'] = 'REVOKED' if step.action == 'REVOKE_APPROVAL' else 'CANCELLED'

    def _invariants(self):
        expected_reserved = sum(row['cost'] for row in self.plans.values() if row['state'] == 'RESERVED')
        spent_states = {'SPENT_SIMULATION', 'CONFIRMED_SIMULATION', 'FAILED_SIMULATION'}
        expected_spent = sum(row['cost'] for row in self.plans.values() if row['state'] in spent_states)
        if self.reserved != expected_reserved or self.spent != expected_spent:
            raise AssertionError('Capital counters disagree with plan states')
        expected_resources = {ref: [Decimal(0), Decimal(0)] for ref in self.resources}
        for identifier, plan in self.plans.items():
            for demand in self.options[identifier].demands:
                if not plan['units']:
                    continue
                quantity = number(demand.quantity) * plan['units']
                if plan['state'] == 'RESERVED':
                    expected_resources[demand.observation][0] += quantity
                elif plan['state'] == 'SPENT_SIMULATION' or (plan['state'] in spent_states and not demand.reusable):
                    expected_resources[demand.observation][1] += quantity
        for ref, resource in self.resources.items():
            if [resource['reserved'], resource['used']] != expected_resources[ref]:
                raise AssertionError('Resource counters disagree with plan states')
        available = self.capital - self.reserved - self.spent
        if min(available, self.reserved, self.spent) < 0:
            raise AssertionError('Capital conservation violated')
        for resource in self.resources.values():
            if min(resource['reserved'], resource['used']) < 0:
                raise AssertionError('Negative resource allocation')
            if resource['available'] is not None and resource['reserved'] + resource['used'] > resource['available']:
                raise AssertionError('Resource double allocation')
        return available

    def run(self, actor_provider):
        evaluations = []
        with localcontext() as context:
            context.prec = 90
            for step in self.steps:
                if type(step) is not Step:
                    raise AccountingError('Typed decision required')
                code(step.key)
                code(step.option)
                if step.action not in ACTIONS:
                    raise AccountingError('Unknown model action')
                # Resolve current identity even for replay. Token rotation/revocation wins.
                actor = actor_provider(step)
                payload = (actor, step.action, step.option)
                old = self.receipts.get(step.key)
                if old is not None:
                    if old != payload:
                        raise AccountingError('Replay key collision')
                    evaluations.append({'key': step.key, 'action': step.action, 'option': step.option, 'status': 'REPLAY_NO_EFFECT'})
                    continue
                try:
                    self._apply(step, actor)
                    status = 'APPLIED_IN_MODEL'
                except AccountingError as exc:
                    status = str(exc)
                self.receipts[step.key] = payload
                available = self._invariants()
                evaluations.append({'key': step.key, 'action': step.action, 'option': step.option, 'status': status, 'available_kopecks': available if self.capital_verified else None,
                                    'reserved_kopecks': self.reserved, 'spent_kopecks': self.spent})
        recommendations = [
            {'option': option.id, 'urgency': option.urgency, 'deadline': option.deadline,
             'duration_hours': option.duration_hours, 'cost_per_unit_rub': option.cost_per_unit_rub,
             'horizon': option.horizon, 'dependencies': list(option.dependencies),
             'basis': ['explicit scenario urgency', 'deadline', 'deterministic code tie-break'],
             'status': self.plans.get(option.id, {}).get('state', 'NOT_PROPOSED')}
            for option in sorted(self.options.values(), key=lambda item: (-item.urgency, item.deadline, item.id))
        ]
        return {
            'format': ALGORITHM, 'effect': 'DISPOSABLE_SYNTHETIC_FORECAST',
            'capital': {'scope': 'SYNTHETIC_SCENARIO', 'level': self.capital_level, 'provenance': 'SYNTHETIC_FIXTURE',
                        'real_funds_verified': False, 'initial_rub': rubles(self.capital),
                        'available_rub': rubles(self._invariants()) if self.capital_verified else None,
                        'reserved_rub': rubles(self.reserved), 'simulated_spent_rub': rubles(self.spent)},
            'forecast': {'basis': 'SCENARIO_ASSUMPTION',
                         'units_by_option': {key: value['units'] for key, value in sorted(self.plans.items())
                                             if value['state'] == 'CONFIRMED_SIMULATION'},
                         'confirmed_options': sum(value['state'] == 'CONFIRMED_SIMULATION' for value in self.plans.values()),
                         'cross_unit_total': None},
            'confirmed_help_fulfillments_created': 0,
            'evaluations': evaluations, 'recommendations': recommendations,
            'policy': self.policy.version, 'single_welfare_score': None,
            'plan_states': {key: {'state': value['state'], 'units': value['units'], 'cost_rub': rubles(value['cost'])}
                            for key, value in sorted(self.plans.items())},
            'durable_capital_ledger': False, 'source_operations_performed': 0,
            'scenario_assumptions': ['units', 'cost', 'urgency', 'deadline', 'duration', 'benefit horizon'],
        }
