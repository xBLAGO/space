"""Pure reference arithmetic over ALREADY ELIGIBLE synthetic source snapshots.
Does not authenticate any event, derive Help transitions, or execute user actions.
"""
from decimal import Decimal, localcontext
import re


class Invalid(ValueError):pass


def dec(value):
    if type(value) is not str or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,8})?',value) or len(value)>32:raise Invalid('DECIMAL')
    return Decimal(value)


def project(data):
    # Seen stable canonical event refs first; identical copies do not add anything.
    source={};latest={};forks=set();unknown=0
    for event in data['snapshots']:
        key=event['canonical_object'];eid=event['source_event']
        if eid in source and source[eid]!=event:raise Invalid('EVENT_CONFLICT')
        source[eid]=event
        identity=(key,event['version'])
        if identity in latest and latest[identity]!=event:forks.add(key)
        latest[identity]=event
    current={}
    for event in source.values():
        key=event['canonical_object']
        if key not in current or event['version']>current[key]['version']:current[key]=event
    stock={};available={};valued=0;assets=0;stock_sats=0;flow_sats=0;flow_unknown=0;needs=0;done=0;disputed=0
    with localcontext() as ctx:
        ctx.prec=90
        for key,event in sorted(current.items()):
            if key in forks:continue
            if event['kind']=='ASSET':
                if event['state']!='ACTIVE':continue
                assets+=1
                group=event['category']+':'+event['unit'];stock.setdefault(group,Decimal(0));available.setdefault(group,Decimal(0))
                if event['quantity'] is None:unknown+=1
                else:
                    q=dec(event['quantity']);a=dec(event['available'])
                    if q<=0 or a>q or (a and not event['share_consent']):raise Invalid('AVAILABILITY')
                    stock[group]+=q
                    if event['share_consent']:available[group]+=a
                value=event['valuation']
                if value is not None and event['quantity'] is not None and value['basis_quantity']==event['quantity'] and value['effective_at']<=data['valuation_at'] and value['assessed_at']<=data['valuation_at']:
                    if not re.fullmatch('[0-9]+',value['satoshis']):raise Invalid('DECIMAL')
                    stock_sats+=int(value['satoshis']);valued+=1
            elif event['kind']=='HELP':
                # Current state is an input attested by existing Help adapter. No transition engine.
                if event['state'] in ('DRAFT','WITHDRAWN','ERASED'):continue
                needs+=1
                if event['state']=='DISPUTED':disputed+=1
                if event['state']=='COMPLETED' and event['evidence']=='COUNTERPARTY_CONFIRMED':
                    done+=1
                    if event['flow_satoshis'] is None:flow_unknown+=1
                    else:
                        if not re.fullmatch('[0-9]+',event['flow_satoshis']):raise Invalid('DECIMAL')
                        flow_sats+=int(event['flow_satoshis'])
            else:raise Invalid('UNSUPPORTED_KIND')
    return {'known_stock':{k:format(v,'f') for k,v in sorted(stock.items())},
            'available_self_declared':{k:format(v,'f') for k,v in sorted(available.items())},
            'unknown_stock_objects':unknown,'quarantined_objects':len(forks),
            'stock_satoshis_known':str(stock_sats),'valued_objects':valued,'unvalued_objects':assets-valued,
            'needs_visible':needs,'completed_confirmed':done,'disputed':disputed,
            'flow_satoshis_known':str(flow_sats),'flow_unknown_occurrences':flow_unknown,
            'total_wealth':None,'complete':not unknown and not forks and flow_unknown==0 and assets==valued}
