"""Ephemeral accounting + real guarded Help demo. Synthetic data, zero network."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.help_readiness_lab import initialize
from xblago.core.integration import preflight
from xblago.integrations.web import WebApp
from xblago.domain.help_service import HelpService
from xblago.domain.collective_calculator import (
    OfflineRateProvider, Quote, ValueEngine, LabRegistry, HelpAccountingView, quantity_coverage)


def run():
    engine = ValueEngine(OfflineRateProvider([Quote('RUB','10000000',1000,'synthetic-a','fixture-a-v1'),
                                            Quote('RUB','10000000',1000,'synthetic-b','fixture-b-v1')]),
                         {'synthetic-a':'fixture-a-v1','synthetic-b':'fixture-b-v1'})
    registry = LabRegistry(synthetic=True)
    try:
        a,b = registry.participant(),registry.participant()
        def register(category,quantity,available,price):
            body={'category':category,'quantity':quantity,'available':available,'unit':'item',
                  'right':'OWNER','share_consent':available!='0','audience':'LAB_COMMUNITY','conditions':'ON_REQUEST'}
            rid=registry.draft(a,body,key='synthetic-object-'+category,now=1000)
            c=registry.prepare(a,rid,1,body,now=1000)
            receipt=registry.commit(a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1000)
            assert receipt==registry.commit(a,c['challenge'],'ПОДТВЕРЖДАЮ',now=1001)
            value=registry.record_valuation(a,rid,engine,price,'RUB',effective_at=1000,now=1000)
            return rid,value
        computer,value=register('computer','2','1','100000')
        bicycle,_=register('bicycle','1','0','10000')
        initial=registry.balance(b,at=1000)
        registry.revoke(a,computer);revoked=registry.balance(b,at=1000)
        schema={'version':'calculator_lab_001','checksum':registry.schema_checksum,
                'tables':[{'name':r[0],'sql':r[1]} for r in registry.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")]}
        with tempfile.TemporaryDirectory(prefix='blago-calculator-lab-') as root:
            cfg=initialize(Path(root)/'new')
            with patch.dict(os.environ,cfg):
                app=WebApp(preflight(Path(root)/'new/app/blago.sqlite3'));h=HelpService(app);view=HelpAccountingView(h)
                owner,helper,outsider=[app.register('calc_demo_'+n,'synthetic-password-only','synthetic-help011-only')[0] for n in ('owner','helper','outsider')]
                rid=h.draft(owner,'Мне нужна помощь с ремонтом велосипеда',[h.code(helper)['code']],'calculator-demo-help-01')['id']
                def act(actor,action,args=None):
                    c=h.prepare(actor,rid,h.get(actor,rid)['version'],action,args or {})
                    return h.commit(actor,c['challenge'],'ПОДТВЕРЖДАЮ')
                act(owner,'publish');published=view.read(owner)
                act(helper,'offer',{'note':'Могу добровольно помочь с ремонтом'})
                act(owner,'accept',{'offer_id':h.get(owner,rid)['offers'][0]['id']})
                act(owner,'confirm');act(helper,'confirm');completed=view.read(owner)
                repeated=view.read(owner);foreign=view.read(outsider)
                act(helper,'dispute');disputed=view.read(owner)
                act(owner,'withdraw');withdrawn=view.read(owner)
                checks={'canonical_asset_resource_count':initial['registered_objects']==2,
                        'stock_btc_exact':initial['stock_btc_known']=='0.01100000',
                        'asset_not_available_by_default':initial['by_category_unit']['bicycle:item']['available_self_declared']=='0',
                        'consent_revocation':revoked['registered_objects']==1,
                        'publication_not_obligation':not published['needs'][0]['obligation'],
                        'mutual_completion':completed['completed_count']==1,
                        'repeated_projection_idempotent':completed==repeated,
                        'audience_isolation':foreign['published_count']==0,
                        'dispute_corrects_projection':disputed['completed_count']==0 and disputed['disputed_count']==1,
                        'withdraw_hides_projection':withdrawn['published_count']==0,
                        'no_invented_quantity_or_value':completed['needs'][0]['quantity'] is None and completed['flow_btc'] is None}
                # Event IDs are not personal data here, but omit even synthetic identifiers in public aggregate evidence.
                result={'format':'blago-calculator01-lab-1','base_sha':'34ed8a6c47eb7c90f7eaa5a6807bb1eee78f97a8',
                        'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'stock':initial,
                        'valuation_example':asdict(value),'measurement_example':quantity_coverage('4','1','hour'),
                        'help_counts':{'published':published['published_count'],'completed':completed['completed_count'],
                                       'after_dispute':disputed['completed_count'],'after_withdraw':withdrawn['published_count']},
                        'schema':schema,'external_calls':0,'financial_operations':0,'new_app_migrations':0,
                        'durable_registry':'NOT IMPLEMENTED; in-memory lab only; restore rejected',
                        'independent_source_verification':'NOT EXECUTED; pinned synthetic fixtures only',
                        'real_pilot':'NO-GO'}
                assert all(checks.values());return result
    finally:registry.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(result['checks']),'real_pilot':result['real_pilot']}))
