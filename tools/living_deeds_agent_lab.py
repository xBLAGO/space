"""Living Deeds 02 synthetic owner-memory and local pull evidence; no external delivery."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import unittest
from tools.uel03_lab_check import EvidenceResult

class Result(EvidenceResult):
    def addSuccess(self,test):
        super().addSuccess(test)
        if hasattr(test,'demonstration'):self.records[-1]['evidence']=test.demonstration

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    started=time.perf_counter()
    suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_living_deeds_agent')
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    try:sha=subprocess.check_output(['git','rev-parse','HEAD'],stderr=subprocess.DEVNULL,text=True).strip()
    except (OSError,subprocess.CalledProcessError):sha=None
    clean=not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],text=True).strip() if sha else None
    manifest=Path('integration/candidate.json')
    evidence={'format':'living-deeds-agent-lab-2','source_sha':sha,'source_identity':('CLEAN_GIT_CHECKOUT' if clean else 'DIRTY_WORKTREE_NOT_EXACT_SHA') if sha else 'UNKNOWN: packaged runtime; use CI build attestation',
              'source_worktree_clean':clean,'source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
              'memory_source_sha':'3596fc67da794b8f5e550bd2ef5de08e2c64538e','base_sha':'117e90095baaf3fbae3fc7533ca10a3540c5c7ff','tests':result.testsRun,
              'status':'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
              'seconds':round(time.perf_counter()-started,3),'records':result.records,
              'mode':'SYNTHETIC_LOCAL_ONLY','real_people':'NOT EXECUTED','independent_operators':'NOT EXECUTED',
              'live_llm':'NOT EXECUTED','telegram_delivery':'NOT EXECUTED','financial_operations':False,
              'private_source_loss':'UNKNOWN_RECOVERY; never reconstructed from public hashes','real_pilot':'NO-GO'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(0 if evidence['status']=='PASS' else 1)

if __name__=='__main__':main()
