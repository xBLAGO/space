"""Synthetic latency/availability and fencing proof; never pilot approval."""
import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch
from tools.integration_fixture import create
from tools.help_worker_probe import probe
from xblago.core.integration import preflight
from xblago.integrations.web import WebApp


def benchmark(fenced):
    with tempfile.TemporaryDirectory(prefix='blago-context-bench-') as root:
        cfg=create(Path(root)/'new',help_enabled=True,context_fencing=fenced)
        cfg['XBLAGO_WEB_INVITE_CODE']='synthetic-benchmark'
        with patch.dict(os.environ,cfg):
            app=WebApp(preflight(Path(cfg['XBLAGO_DATA_DIR'])/'blago.sqlite3'))
            token=app.register('synthetic_benchmark','synthetic-password-only','synthetic-benchmark')[0];uid=app.session(token)[0]
            timings=[]
            for _ in range(8):
                start=time.perf_counter();app.chat(uid,'Почему небо голубое?');timings.append(round(1000*(time.perf_counter()-start),3))
            assert len(app.history(uid))==16
            return {'turns':8,'median_ms':statistics.median(timings),'max_ms':max(timings),'turn_ms':timings,'provider':'local, zero external LLM calls'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    before=benchmark(False);after=benchmark(True);proof=probe()
    result={'format':'blago-context-fencing-check-1','legacy_local':before,'fenced_local':after,
            'median_delta_ms':round(after['median_ms']-before['median_ms'],3),'cross_worker':proof,
            'real_pilot':'NO-GO','limits':'Local synthetic wall-clock only, not live LLM/network or production capacity benchmark'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    if proof['status']!='PASS':raise SystemExit(1)
