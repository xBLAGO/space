"""Supplemental synthetic Bot/link/Mini session check with installed registry; no Telegram I/O."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.help_readiness_lab import initialize
from tools.unified_mvp_check import bot_update,init_data
from xblago.core.asset_registry_schema import migrate_registry
from xblago.core.integration import preflight
from xblago.core.context_fencing import SAFE_CONFLICT
from xblago.domain.asset_registry import AssetRegistry
from xblago.integrations.web import WebApp,ApiError
from xblago.integrations.telegram import TelegramBot


def run():
    with tempfile.TemporaryDirectory(prefix='blago-asset091-channel-') as directory:
        root=Path(directory)/'new';cfg=initialize(root)
        with patch.dict(os.environ,cfg):
            path=root/'app/blago.sqlite3';migrate_registry(path)
            os.environ['XBLAGO_ASSET_REGISTRY_ENABLED']='true'
            app=WebApp(preflight(path));token=app.register('synthetic_asset_channel','synthetic-password-only','synthetic-help011-only')[0]
            uid=app.session(token)[0];r=AssetRegistry(app)
            body={'label':'Синтетический инструмент','category':'tool','quantity':'1','available':'0','unit':'item','share_consent':False,'right':'OWNER','audience':'PRIVATE','conditions':'ON_REQUEST'}
            aid=r.draft(token,body,'synthetic-channel-key-01')['id'];before=r.feed(token)
            code=app.new_link_code(uid,token=token)
            bot=TelegramBot(None,root/'unused',app.store)
            update=bot_update(93000,'/link '+code);reply=bot.replies_for_update(update)
            try:r.feed(token);old_denied=False
            except ApiError:old_denied=True
            web=app.login('synthetic_asset_channel','synthetic-password-only')[0]
            binding='synthetic-asset-browser-binding';challenge=app.identity.telegram_challenge(binding)
            mini=app.telegram_login(init_data(),challenge,binding)[0]
            checks={'old_session_rotated':old_denied,'canonical_identity_preserved':app.session(web)[0]==uid==app.session(mini)[0],
                    'private_asset_visible_same_identity':r.feed(web)==before==r.feed(mini),
                    'bot_link_replay_safe_no_repeat':bot.replies_for_update(update)==[dict(item,text=SAFE_CONFLICT) for item in reply],
                    'canonical_asset_not_duplicated':len(r.feed(mini)['items'])==1}
            with app.store._locked_transaction() as db:
                assert db.execute("SELECT count(*) FROM events WHERE channel='telegram' AND external_update_id=? AND status='processed'",('93000',)).fetchone()[0]==1
            assert r.feed(web)==before
            q=r.prepare(web,aid,1,body)
            try:r.commit(mini,q['challenge'],'ПОДТВЕРЖДАЮ');substitution_denied=False
            except ApiError:substitution_denied=True
            checks['cross_session_challenge_substitution_denied']=substitution_denied
            if not all(checks.values()):raise AssertionError('Failed checks: '+','.join(k for k,v in checks.items() if not v))
            return {'format':'blago-asset091-channel-check-1','status':'PASS','checks':checks,'scope':'domain API + real Bot update handler + synthetic signed initData; not physical Telegram','external_calls':0,'real_pilot':'NO-GO'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=run();args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':result['status'],'checks':len(result['checks'])}))
