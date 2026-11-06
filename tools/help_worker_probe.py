"""Synthetic two-process audit probe; reports a blocker, never a pilot approval."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.integration_fixture import create
from xblago.core.integration import preflight
from xblago.integrations.web import WebApp

MARKER='SYNTHETIC_PRIVATE_CITY'


def conversation_worker(cfg,uid,pipe):
    os.environ.update(cfg)
    app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
    class HeldProvider:
        def generate(self,context):
            pipe.send({'context_loaded':MARKER in json.dumps(context.memory)})
            if pipe.recv()!='release':raise RuntimeError('probe stopped')
            return 'Сохранённое место: '+str(context.memory.get('city','нет данных'))+'.'
    try:
        with patch('xblago.integrations.web.build_chat_provider',return_value=HeldProvider()):app.chat(uid,'Где я живу?')
        pipe.send({'completed':True})
    finally:pipe.close()


def probe():
    with tempfile.TemporaryDirectory(prefix='blago-help-worker-probe-') as root:
        cfg=create(Path(root)/'new',help_enabled=True);cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-worker-invite'
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
            token=app.register('synthetic_worker','synthetic-password-only','synthetic-worker-invite')[0];uid=app.session(token)[0]
            app.store.set_memory(uid,'city',MARKER)
            ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe();proc=ctx.Process(target=conversation_worker,args=(cfg,uid,child));proc.start();child.close()
            try:
                if not parent.poll(20):raise RuntimeError('worker did not load context')
                loaded=parent.recv()['context_loaded'];deleted=app.delete_memory(uid,'city')
                parent.send('release')
                if not parent.poll(20):raise RuntimeError('worker did not finish')
                parent.recv();proc.join(10)
                if proc.exitcode!=0:raise RuntimeError('worker failed')
                stale=any(MARKER in turn['text'] for turn in app.history(uid) if turn['role']=='assistant')
                return {'format':'blago-help-worker-probe-1','scope':'two actual processes, synthetic provider and data',
                        'context_loaded':loaded,'delete_completed_before_generation_release':deleted,
                        'stale_context_written_after_delete':stale,'status':'FAIL' if stale else 'PASS',
                        'finding':'Process-local conversation locks do not serialize memory deletion against another worker',
                        'real_pilot':'NO-GO','production_access':False}
            finally:
                parent.close()
                if proc.is_alive():proc.kill();proc.join()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=probe();args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
