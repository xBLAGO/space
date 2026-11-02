"""Reproducible synthetic Genesis dialogue. Temporary SQLite, no network or keys."""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch
from pilot_fixture import fixture
from xblago.core.genesis_schema import migrate_genesis
from xblago.core.genesis import Deliberation
from xblago.core.sqlite_safety import runtime_evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with fixture() as f,patch.dict(os.environ,{'XBLAGO_GENESIS_ENABLED':'false','XBLAGO_IDENTITY_CORE_ENABLED':'false'}):
        migrate_genesis(f['path'])
        os.environ['XBLAGO_GENESIS_ENABLED']='true'
        app=f['app']; token,_=app.register('genesis_demo','synthetic-demo-password','synthetic-pilot-invite')
        uid=app.session(token)[0]; service=Deliberation(f['store'])
        turns=[]
        for index,text in enumerate(('У меня есть 5000 рублей',
            'Мне хочется, чтобы у людей рядом было место для совместного ремонта вещей.',
            'Предлагаю открыть общую мастерскую, где участники смогут добровольно помогать друг другу.',
            'Да, публикуй этот текст')):
            # This models the Web client retaining the version it actually saw.
            reference=service.pending(uid) if index==3 else None
            reply=app.chat(uid,text,token=token,request_id='demo-'+str(index),preview_ref=reference)
            turns.append({'human':text,'agent':reply})
        sid=next(row['stream_id'] for row in service.own(uid)['streams'] if row['kind']=='proposal')
        result={'synthetic':True,'network_calls':0,'llm_provider':'local deterministic fixture',
            'identity_core_enabled':False,'dialogue':turns,'public_export':service.public(sid),
            'asset_count':len(app.assets(uid)),'payment_operations':0,'sqlite':runtime_evidence()}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'synthetic':True,'asset_count':result['asset_count'],'published_versions':len(result['public_export']['versions']),'payment_operations':0},ensure_ascii=False))


if __name__=='__main__': main()
