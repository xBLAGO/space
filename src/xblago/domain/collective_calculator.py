"""Offline accounting core, not money. No runtime routes or persistent user store.

LabRegistry deliberately has no file/restore constructor: crash destroys drafts,
consents and projections. A future durable store requires privacy/recovery review.
Help remains the only request/obligation/fulfillment state machine.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from functools import wraps
import hashlib
from itertools import islice
import json
import re
import secrets
import sqlite3
import time
from uuid import uuid4

ALGORITHM = 'blago-value-1-half-even-satoshi'
SATOSHI = Decimal('0.00000001')
# Deliberately bounded ISO 4217 subset, not an invented three-letter-code check.
CURRENCIES = {'RUB': 2, 'USD': 2, 'EUR': 2, 'GBP': 2, 'CNY': 2, 'JPY': 0, 'CHF': 2}
UNITS = {'item': ('count', Decimal(1)), 'kg': ('mass', Decimal(1)),
         'g': ('mass', Decimal('0.001')), 'hour': ('time', Decimal(1)),
         'minute': ('time', None)}
LEVELS = {'SELF_DECLARED', 'COUNTERPARTY_CONFIRMED', 'INDEPENDENTLY_VERIFIED', 'DISPUTED', 'REVOKED'}


class AccountingError(ValueError):
    """Invalid/unknown data never silently becomes a zero valuation."""


def number(value, *, positive=False):
    if type(value) is not str or len(value) > 40 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        raise AccountingError('Exact nonnegative decimal string required')
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise AccountingError('Invalid decimal') from exc
    if not d.is_finite() or d > Decimal('1000000000000000') or (positive and d <= 0):
        raise AccountingError('Decimal outside lab bounds')
    return d


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exact_arithmetic(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        with localcontext() as ctx:
            ctx.prec = 90
            return method(*args, **kwargs)
    return wrapped


@dataclass(frozen=True)
class Quote:
    currency: str
    local_per_btc: str
    observed_at: int
    source: str
    provenance: str


@dataclass(frozen=True)
class Valuation:
    id: str
    original_price: str
    currency: str
    effective_at: int
    assessed_at: int
    btc: str
    quotes: tuple
    algorithm: str = ALGORITHM
    rounding: str = 'ROUND_HALF_EVEN; total to 8 BTC decimal places'
    purpose: str = 'HISTORICAL'
    supersedes: str | None = None


class OfflineRateProvider:
    """Pinned offline fixtures only; provenance is configuration, not attestation."""
    def __init__(self, quotes):
        self.quotes = tuple(islice(quotes, 9))
        if len(self.quotes) > 8 or any(type(q) is not Quote for q in self.quotes):
            raise AccountingError('At most eight typed offline quotes required')

    def at(self, currency):
        return tuple(q for q in self.quotes if q.currency == currency)


class ValueEngine:
    def __init__(self, provider, pins, *, max_age=300, minimum_sources=2, max_spread='0.02'):
        if type(provider) is not OfflineRateProvider:
            raise AccountingError('Only offline fixture provider supported')
        if type(max_age) is not int or not 1 <= max_age <= 86400 or type(minimum_sources) is not int or minimum_sources < 2:
            raise AccountingError('Bounded age and at least two independent configured sources required')
        self.provider, self.pins, self.max_age, self.minimum_sources = provider, dict(pins), max_age, minimum_sources
        self.max_spread = number(max_spread)
        if self.max_spread > Decimal('0.10'):
            raise AccountingError('Spread policy too loose')

    def evaluate(self, price, currency, *, effective_at, now, previous=None):
        if currency not in CURRENCIES or type(now) is not int or type(effective_at) is not int or not 0 <= effective_at <= now:
            raise AccountingError('Unsupported currency or time')
        amount = number(price)
        if amount.as_tuple().exponent < -CURRENCIES[currency]:
            raise AccountingError('Price exceeds currency minor-unit precision')
        if previous is not None and (not isinstance(previous, Valuation) or previous.currency != currency or previous.original_price != price):
            raise AccountingError('Revaluation must retain original price and currency')
        quotes = self.provider.at(currency)
        if not self.minimum_sources <= len(quotes) <= 8 or len({q.source for q in quotes}) != len(quotes):
            raise AccountingError('Missing or duplicate independent sources')
        rates = []
        for q in quotes:
            if not isinstance(q, Quote) or q.currency != currency or not q.provenance or type(q.source) is not str or type(q.provenance) is not str or len(q.source) > 80 or len(q.provenance) > 128 or self.pins.get(q.source) != q.provenance:
                raise AccountingError('Untrusted source provenance')
            if type(q.observed_at) is not int or not 0 <= effective_at - q.observed_at <= self.max_age:
                raise AccountingError('Stale or future quote')
            rates.append(number(q.local_per_btc, positive=True))
        with localcontext() as ctx:
            ctx.prec = 90
            if (max(rates) - min(rates)) / min(rates) > self.max_spread:
                raise AccountingError('Conflicting rates')
            # Equal-weight deterministic median; stable source ordering in evidence.
            rates.sort(); n = len(rates)
            rate = rates[n//2] if n % 2 else (rates[n//2-1] + rates[n//2])/2
            btc = (amount/rate).quantize(SATOSHI, rounding=ROUND_HALF_EVEN)
        evidence = tuple(sorted(quotes, key=lambda q: q.source))
        payload = {'price': price, 'currency': currency, 'effective_at': effective_at, 'assessed_at': now,
                   'quotes': [asdict(q) for q in evidence], 'algorithm': ALGORITHM,
                   'previous': previous.id if previous else None}
        return Valuation(digest(payload), price, currency, effective_at, now, format(btc, '.8f'), evidence,
                         purpose='REVALUATION' if previous else 'HISTORICAL', supersedes=previous.id if previous else None)


class LabRegistry:
    """In-memory synthetic registry/API. No production ID or arbitrary path inputs.

    One canonical asset row also describes its offered portion (RESOURCE view).
    No duplicate NEED table; those come from authorized Help reads only.
    """
    def __init__(self, *, synthetic=False):
        if synthetic is not True:
            raise AccountingError('Synthetic lab explicit opt-in required')
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE principals(id TEXT PRIMARY KEY);
        CREATE TABLE goods(id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES principals(id),
          version INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE challenges(id TEXT PRIMARY KEY, actor TEXT NOT NULL REFERENCES principals(id),
          target TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, expires INTEGER NOT NULL,
          receipt TEXT);
        CREATE TABLE events(id TEXT PRIMARY KEY, target TEXT NOT NULL, version INTEGER NOT NULL,
          body TEXT NOT NULL, UNIQUE(target,version));
        CREATE TABLE valuations(id TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES goods(id), quantity TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE denials(target TEXT PRIMARY KEY);
        CREATE TABLE metadata(version TEXT PRIMARY KEY);
        INSERT INTO metadata VALUES ('calculator_lab_001');
        ''')
        self.schema_checksum = digest([r[0] for r in self.db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")])

    def close(self):
        self.db.close()

    def participant(self):
        if self.db.execute('SELECT count(*) FROM principals').fetchone()[0] >= 32:
            raise AccountingError('Synthetic participant limit')
        principal = 'synthetic-' + str(uuid4())
        self.db.execute('INSERT INTO principals VALUES (?)', (principal,))
        return principal

    def _actor(self, actor):
        if not self.db.execute('SELECT 1 FROM principals WHERE id=?', (actor,)).fetchone():
            raise AccountingError('Unknown synthetic principal')

    def _row(self, actor, target):
        self._actor(actor)
        row = self.db.execute('SELECT * FROM goods WHERE id=? AND owner=?', (target, actor)).fetchone()
        if row is None or self.db.execute('SELECT 1 FROM denials WHERE target=?', (target,)).fetchone():
            raise AccountingError('Record unavailable')
        return row

    @staticmethod
    def _body(body):
        required = {'category', 'quantity', 'unit', 'available', 'share_consent', 'right', 'audience', 'conditions'}
        if type(body) is not dict or set(body) != required:
            raise AccountingError('Exact reviewed fields required')
        if type(body['category']) is not str or not re.fullmatch('[a-z][a-z0-9_-]{0,39}', body['category']):
            raise AccountingError('Bounded category code required; no free personal text')
        if body['unit'] not in UNITS or body['right'] not in {'OWNER', 'USE', 'DISPOSAL'} or body['audience'] not in {'PRIVATE', 'LAB_COMMUNITY'}:
            raise AccountingError('Unknown unit, right or audience')
        quantity, available = number(body['quantity'], positive=True), number(body['available'])
        if available > quantity or type(body['share_consent']) is not bool or (available and not body['share_consent']):
            raise AccountingError('Availability exceeds quantity or lacks consent')
        # No location/free-form notes are collected by this minimal prototype.
        if body['conditions'] not in {'ON_REQUEST', 'SCHEDULE_REQUIRED'}:
            raise AccountingError('Unsupported availability condition')
        return dict(body)

    def draft(self, actor, body, *, key=None, now=None):
        self._actor(actor); body = self._body(body)
        if key is not None and (type(key) is not str or not re.fullmatch('[A-Za-z0-9_-]{16,100}', key)):
            raise AccountingError('Bounded idempotency key required')
        target = digest([actor, key]) if key is not None else str(uuid4())
        if self.db.execute('SELECT 1 FROM denials WHERE target=?', (target,)).fetchone():
            raise AccountingError('Deleted key cannot be reused')
        old = self.db.execute('SELECT * FROM goods WHERE id=?', (target,)).fetchone()
        if old:
            if old['body'] != canonical(body) or old['status'] != 'DRAFT':
                raise AccountingError('Idempotency key reused for different record')
            return target
        if self.db.execute('SELECT count(*) FROM goods').fetchone()[0] >= 128:
            raise AccountingError('Synthetic object limit')
        now = int(time.time()) if now is None else now
        if type(now) is not int or now < 0:
            raise AccountingError('Invalid timestamp')
        self.db.execute('INSERT INTO goods VALUES (?,?,1,?,?,?,?)', (target, actor, canonical(body), 'DRAFT', now, now))
        return target

    def prepare(self, actor, target, version, body, *, now):
        row = self._row(actor, target); body = self._body(body)
        if type(version) is not int or row['version'] != version or row['status'] == 'REVOKED' or type(now) is not int:
            raise AccountingError('Stale record')
        self.db.execute('DELETE FROM challenges WHERE expires<=? AND receipt IS NULL', (now,))
        if self.db.execute('SELECT count(*) FROM challenges WHERE actor=?', (actor,)).fetchone()[0] >= 128:
            raise AccountingError('Synthetic challenge limit')
        challenge = secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO challenges VALUES (?,?,?,?,?,?,NULL)',
                        (digest(challenge), actor, target, version, canonical(body), now+120))
        return {'challenge': challenge, 'target': target, 'version': version, 'exact_body': body,
                'confirmation': 'ПОДТВЕРЖДАЮ'}

    def commit(self, actor, challenge, confirmation, *, now, fail_before_commit=False):
        self._actor(actor)
        if confirmation != 'ПОДТВЕРЖДАЮ' or type(now) is not int:
            raise AccountingError('Explicit exact confirmation required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            c = self.db.execute('SELECT * FROM challenges WHERE id=? AND actor=?', (digest(challenge), actor)).fetchone()
            if c is None:
                raise AccountingError('Challenge unavailable')
            r = self._row(actor, c['target'])
            if c['receipt']:
                self.db.commit(); return json.loads(c['receipt'])
            if not c['expires']-120 <= now < c['expires'] or r['version'] != c['version'] or r['status'] == 'REVOKED':
                raise AccountingError('Expired or stale confirmation')
            receipt = {'id': str(uuid4()), 'target': r['id'], 'version': r['version']+1, 'at': now,
                       'level': 'SELF_DECLARED'}
            self.db.execute("UPDATE goods SET version=?,body=?,updated_at=?,status='ACTIVE' WHERE id=?",
                            (receipt['version'], c['body'], now, r['id']))
            self.db.execute('INSERT INTO events VALUES (?,?,?,?)', (receipt['id'], r['id'], receipt['version'], canonical(dict(receipt, record=json.loads(c['body'])))))
            self.db.execute('UPDATE challenges SET receipt=? WHERE id=?', (canonical(receipt), c['id']))
            if fail_before_commit:
                raise OSError('Synthetic storage interruption')
            self.db.commit(); return receipt
        except BaseException:
            self.db.rollback(); raise

    def revoke(self, actor, target):
        # Removing availability is deliberately simpler than granting rights.
        r = self._row(actor, target)
        if r['status'] == 'REVOKED':
            return
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            body = json.loads(r['body']); body['available'] = '0'; body['share_consent'] = False
            self.db.execute("UPDATE goods SET status='REVOKED',version=version+1,body=? WHERE id=?", (canonical(body), target))
            event = {'id': str(uuid4()), 'target': target, 'version': r['version']+1, 'level': 'REVOKED', 'record': body, 'at': int(time.time())}
            self.db.execute('INSERT INTO events VALUES (?,?,?,?)', (event['id'], target, event['version'], canonical(event)))
            self.db.execute('DELETE FROM challenges WHERE target=?', (target,))

    def erase(self, actor, target):
        self._row(actor, target)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for table in ('challenges', 'events', 'valuations'):
                self.db.execute('DELETE FROM '+table+' WHERE target=?', (target,))
            self.db.execute('DELETE FROM goods WHERE id=?', (target,))
            self.db.execute('INSERT INTO denials VALUES (?)', (target,))

    def delete_participant(self, actor):
        self._actor(actor)
        for r in self.db.execute('SELECT id FROM goods WHERE owner=?', (actor,)).fetchall():
            self.erase(actor, r[0])
        self.db.execute('DELETE FROM principals WHERE id=?', (actor,))

    def record_valuation(self, actor, target, engine, price, currency, *, effective_at, now, previous_id=None):
        r = self._row(actor, target)
        if r['status'] != 'ACTIVE' or not isinstance(engine, ValueEngine):
            raise AccountingError('Active asset and configured offline engine required')
        previous = None
        if previous_id:
            row = self.db.execute('SELECT body FROM valuations WHERE id=? AND target=? AND quantity=?', (previous_id, target, json.loads(r['body'])['quantity'])).fetchone()
            if row is None:
                raise AccountingError('Unknown historical valuation')
            data = json.loads(row[0]); data['quotes'] = tuple(Quote(**q) for q in data['quotes'])
            previous = Valuation(**data)
        value = engine.evaluate(price, currency, effective_at=effective_at, now=now, previous=previous)
        quantity = json.loads(r['body'])['quantity']
        value = Valuation(**dict(asdict(value), id=digest([target, quantity, value.id]), quotes=value.quotes))
        body = canonical(asdict(value))
        old = self.db.execute('SELECT * FROM valuations WHERE id=?', (value.id,)).fetchone()
        if old and (old['target'] != target or old['body'] != body):
            raise AccountingError('Valuation substitution')
        if not old and not previous and self.db.execute('SELECT 1 FROM valuations WHERE target=? AND quantity=?', (target, quantity)).fetchone():
            raise AccountingError('Existing historical value requires explicit revaluation link')
        self.db.execute('INSERT OR IGNORE INTO valuations VALUES (?,?,?,?)', (value.id, target, quantity, body))
        return value

    def export_own(self, actor):
        self._actor(actor)
        rows = [dict(r) for r in self.db.execute('SELECT * FROM goods WHERE owner=?', (actor,))]
        return {'scope': 'synthetic private export; not importable', 'goods': rows,
                'events': [dict(v) for r in rows for v in self.db.execute('SELECT * FROM events WHERE target=? ORDER BY version', (r['id'],))],
                'valuations': [dict(v) for r in rows for v in self.db.execute('SELECT * FROM valuations WHERE target=?', (r['id'],))]}

    @staticmethod
    def restore(_snapshot):
        raise AccountingError('UNKNOWN_RECOVERY: import/restore disabled; fresh empty lab required')

    @exact_arithmetic
    def balance(self, viewer, *, at):
        self._actor(viewer)
        if type(at) is not int:
            raise AccountingError('Exact observation time required')
        # Actor-relative collective view; never an all-user administrative aggregate.
        rows = self.db.execute("SELECT * FROM goods WHERE status='ACTIVE'").fetchall()
        grouped = {}; stock_btc = Decimal(0); valued = 0; count = 0
        for row in rows:
            b = json.loads(row['body'])
            if b['audience'] != 'LAB_COMMUNITY' and row['owner'] != viewer:
                continue
            count += 1; quantity = number(b['quantity']); available = number(b['available']) if b['share_consent'] else Decimal(0)
            # Keep units separate: no silent precision loss for recurring conversions.
            key = b['category'] + ':' + b['unit']
            item = grouped.setdefault(key, {'stock': Decimal(0), 'available_self_declared': Decimal(0)})
            item['stock'] += quantity; item['available_self_declared'] += available
            values = [json.loads(v[0]) for v in self.db.execute('SELECT body FROM valuations WHERE target=? AND quantity=?', (row['id'], b['quantity']))]
            # Historical as-of; future estimates never fill missing past data.
            values = [v for v in values if v['effective_at'] <= at and v['assessed_at'] <= at]
            if values:
                latest = max(values, key=lambda v: (v['effective_at'], v['assessed_at'], v['id']))
                stock_btc += Decimal(latest['btc']); valued += 1
        return {'scope': 'authorized synthetic lab view, not whole-community census', 'valuation_as_of': at,
                'stock_scope': 'CURRENT visibility and availability; not historical stock reconstruction',
                'deduplication': 'Canonical object IDs only; physical duplicates and ownership unverified',
                'registered_objects': count, 'by_category_unit': {k: {x: str(y) for x,y in v.items()} for k,v in grouped.items()},
                'stock_btc_known': format(stock_btc, '.8f'), 'valued_objects': valued, 'unvalued_objects': count-valued,
                'confirmed_available': 'UNKNOWN: self declarations are not verification',
                'flow_btc': None, 'need_deficit': None, 'wellbeing_score': None,
                'completeness': 'PARTIAL; missing values are not zero', 'unit': 'BTC accounting only; no backing or transfer'}


class HelpAccountingView:
    """Read-only Python API over current authenticated, integrity-checked Help.

    No persisted projection, cursor, new grants, joins to memory, or LLM context.
    Each call rechecks session, audience, holds and retained journal frontier.
    """
    def __init__(self, help_service):
        self.help = help_service

    def read(self, token):
        with self.help.tx(token) as (db, uid, control):
            from xblago.integrations.web import ApiError
            rows = db.execute('SELECT * FROM help_requests ORDER BY id').fetchall()
            needs = []
            for r in rows:
                # Use exactly the Help access predicate; hold prevents resurrection.
                if db.execute('SELECT 1 FROM help_holds WHERE target_tag=?', (control.journal.tag('help-request', r['id']),)).fetchone():
                    continue
                try:
                    self.help._row(db, uid, r['id'])
                except ApiError as exc:
                    if exc.status != 404:
                        raise
                    continue
                if r['state'] in {'DRAFT', 'WITHDRAWN'}:
                    continue
                events = [{'id': e['event_id'], 'version': e['version'], 'action': e['action']} for e in db.execute('SELECT * FROM help_events WHERE request_id=? ORDER BY version', (r['id'],))]
                state = r['state']
                needs.append({'id': r['id'], 'version': r['version'], 'state': state,
                              'obligation': state in {'ACCEPTED','AWAITING_CONFIRMATION','COMPLETED','DISPUTED'},
                              'obligation_status': 'ACTIVE' if state in {'ACCEPTED','AWAITING_CONFIRMATION'} else 'FULFILLED' if state == 'COMPLETED' else 'CONTESTED' if state == 'DISPUTED' else 'NONE',
                              'fulfillment': 'CONFIRMED' if state == 'COMPLETED' else 'DISPUTED' if state == 'DISPUTED' else 'UNCONFIRMED',
                              'evidence': 'COUNTERPARTY_CONFIRMED' if state == 'COMPLETED' else 'DISPUTED' if state == 'DISPUTED' else 'SELF_DECLARED',
                              'quantity': None, 'unit': None, 'btc': None, 'events': events})
            completed = sum(x['state'] == 'COMPLETED' for x in needs)
            disputed = sum(x['state'] == 'DISPUTED' for x in needs)
            return {'scope': 'current actor-authorized Help only; not global', 'needs': needs,
                    'published_count': len(needs), 'unmet_count': sum(x['state'] not in {'COMPLETED','DISPUTED'} for x in needs),
                    'completed_count': completed, 'disputed_count': disputed,
                    'satisfied_share': quantity_coverage(str(len(needs)), str(completed), 'item')['share'] if needs else None,
                    'partial_quantities': 'UNKNOWN: Help01 has no mutually confirmed quantity model',
                    'flow_btc': None, 'collective_census': False}


def quantity_coverage(required, fulfilled, unit):
    """Pure measurement, not a Help transition or proof of fulfillment.

    A future adapter must supply consented, mutually confirmed measurements.
    Help01 lacks that model; this function must not infer quantities from text.
    """
    if unit not in UNITS:
        raise AccountingError('Unknown unit')
    need, done = number(required, positive=True), number(fulfilled)
    if done > need:
        raise AccountingError('Fulfillment exceeds measured need')
    with localcontext() as ctx:
        ctx.prec = 90
        return {'required': required, 'fulfilled': fulfilled, 'remaining': str(need-done),
                'unit': unit, 'share': str((done/need).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_EVEN)),
                'proof': 'MEASUREMENT_ONLY_NOT_CONFIRMATION'}
