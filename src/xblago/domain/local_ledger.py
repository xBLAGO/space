"""Read-only authorized UEL projection; no publication, import or action executor."""
from contextlib import contextmanager
from decimal import Decimal,localcontext,ROUND_HALF_EVEN
import json
import sqlite3
from xblago.core import uel_schema,uel_local
from xblago.core.integration import enabled as integration_enabled
from xblago.core.security_journal import JournalUnavailable
from xblago.domain.collective_calculator import number
from xblago.domain.help_service import HelpService
from xblago.integrations.web import ApiError


class LocalLedger:
    def __init__(self,app):self.app,self.store=app,app.store

    @contextmanager
    def tx(self,token):
        if not uel_schema.enabled():raise ApiError(404,'Локальный UEL выключен')
        if not integration_enabled():raise ApiError(503,'Только synthetic integration')
        # Reuse exactly the secure local session/journal/witness boundary.
        with HelpService(self.app).tx(token) as (db,uid,c):
            uel_schema.validate(db,mandatory=True);uel_local.check(db,c)
            yield db,uid,c

    def _views(self,db,uid,c):
        heads=uel_local.heads(db);views=[]
        for key,s in sorted(uel_local.sources(db,c).items()):
            row=heads.get(key);kind=key[0];root=s['root']
            if not row or row['phase']!='OBSERVED' or row['source_version']!=root['version'] or row['source_state']!=s['state'] or uel_local.row_refs(kind,s['rows'][-1],c.journal)!=(row['source_ref'],row['payload_ref']):
                raise JournalUnavailable('UEL UNKNOWN_RECOVERY: no verified current observation')
            if kind=='ASSET':
                body=json.loads(s['rows'][-1]['body'])
                if root['owner_id']!=uid and (root['status']!='ACTIVE' or body['audience']!='LAB_COMMUNITY'):continue
                payload={k:body[k] for k in ('category','unit','quantity','available','share_consent')}
                payload.update(state=root['status'],evidence='SELF_DECLARED')
            else:
                try:HelpService(self.app)._row(db,uid,s['id'])
                except ApiError as exc:
                    if exc.status!=404:raise
                    continue
                payload={'state':root['state'],'mutual_confirmation':bool(root['owner_confirmed'] and root['helper_confirmed']),
                         'quantity':None,'btc':None,'evidence':'SERVICE_OBSERVED_SESSIONS'}
            # No raw source IDs, journal MACs, global sequence/head, or private
            # content. This local view handle is viewer-scoped and not portable.
            views.append({'kind':kind,'observation':c.journal.tag('uel-user-view-v2',uid,row['record_ref']),
                          'source_version':row['source_version'],'coverage':row['coverage'],'claim':payload})
        return views

    def observations(self,token):
        with self.tx(token) as (db,uid,c):
            return {'format':'uel-local-view-v2','scope':'current authorized managed sources only','items':self._views(db,uid,c),
                    'historical_coverage':'UNKNOWN','federation':False}

    def balance(self,token):
        try:return self._verified_balance(token)
        except (JournalUnavailable,sqlite3.DatabaseError):
            return {'format':'uel-local-balance-v2','integrity':'UNKNOWN_RECOVERY','coverage':'UNKNOWN',
                    'conflicts':'SOURCE_OR_INDEX_UNVERIFIED','balance':None,'stock_btc':None,'flow_btc':None}

    def _verified_balance(self,token):
        with self.tx(token) as (db,uid,c):
            views=self._views(db,uid,c);stock={};assets=needs=completed=disputed=obligations=0
            with localcontext() as ctx:
                ctx.prec=90
                for v in views:
                    b=v['claim'];state=b['state']
                    if v['kind']=='ASSET':
                        if state!='ACTIVE':continue
                        assets+=1;k=b['category']+':'+b['unit']
                        q=stock.setdefault(k,{'known_stock':Decimal(0),'known_available_self_declared':Decimal(0),'unknown_stock':0,'unknown_available':0})
                        if b['quantity'] is None:
                            q['unknown_stock']+=1;q['unknown_available']+=int(b['share_consent'])
                        else:
                            q['known_stock']+=number(b['quantity'])
                            if b['share_consent']:q['known_available_self_declared']+=number(b['available'])
                    elif state not in ('DRAFT','WITHDRAWN'):
                        needs+=1;disputed+=int(state=='DISPUTED');obligations+=int(state in ('ACCEPTED','AWAITING_CONFIRMATION'))
                        completed+=int(state=='COMPLETED' and b['mutual_confirmation'])
                categories={k:{'known_stock':str(v['known_stock']),'known_available_self_declared':str(v['known_available_self_declared']),
                               'stock_total':None if v['unknown_stock'] else str(v['known_stock']),
                               'available_total':None if v['unknown_available'] else str(v['known_available_self_declared']),
                               'unknown_stock_objects':v['unknown_stock'],'unknown_available_objects':v['unknown_available']} for k,v in sorted(stock.items())}
                share=None if not needs else format((Decimal(completed)/needs).quantize(Decimal('0.00000001'),rounding=ROUND_HALF_EVEN),'f')
            return {'format':'uel-local-balance-v2','scope':'authorized current managed source observations; not census',
                    'integrity':'VERIFIED_LOCAL','coverage':'PARTIAL','historical_coverage':'UNKNOWN','retired_or_inaccessible_data':'UNKNOWN',
                    'baseline_current_objects':sum(v['coverage']=='BASELINE_CURRENT' for v in views),
                    'registered_asset_claims':assets,'categories':categories,'needs_visible':needs,'obligations_active':obligations,
                    'fulfilled_occurrences':completed,'disputed':disputed,'completion_share_visible':share,
                    'stock_btc':None,'flow_btc':None,'verified_available':None,'measured_need_deficit':None,
                    'confirmed_quantity':None,'total_wealth':None,'conflicts':'FAIL_CLOSED_UNKNOWN_RECOVERY_ON_MISMATCH'}
