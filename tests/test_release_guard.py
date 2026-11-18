"""Release preparation negatives. Network/permissions are synthetic fixtures."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from tools import release_guard as g

ROOT=Path(__file__).resolve().parents[1]
BASE='3c218dc5757879c26b3aec520af3636b2d702715'

class FakeAPI:
    def __init__(self,case):
        self.case=case;self.commit_response={'sha':case.commit,'tree':{'sha':case.manifest['tree_sha']}};self.role={'permission':'admin'};self.main=case.commit;self.ref=None;self.lock=threading.Lock();self.after_commit=False;self.barrier=None;self.writes=[]
        self.run={'head_sha':case.commit,'head_branch':'main','event':'push','status':'completed','conclusion':'success','path':'.github/workflows/ci.yml','head_repository':{'full_name':g.REPO}}
        self.artifact={'id':2,'name':'blago-source-candidate','expired':False,'digest':case.req['artifact_digest'],'workflow_run':{'id':1,'head_sha':case.commit,'head_branch':'main'}}
    def get(self,path):
        if path.startswith('/collaborators/'):return self.role
        if path=='/commits/main':return {'sha':self.main}
        if path=='/actions/runs/1':return self.run
        if path=='/actions/artifacts/2':return self.artifact
        if path.startswith('/git/commits/'):return self.commit_response
        if path.startswith('/git/trees/'):return self.case.tree
        raise g.Blocked('Unexpected API request')
    def download(self,path):return self.case.rawzip
    def get_optional(self,path):
        result=self.ref
        if self.barrier:self.barrier.wait(10)
        return result
    def post(self,path,data):
        with self.lock:
            self.writes.append((path,data))
            if path=='/git/tags':return {'sha':g.sha(g.encode(data))[:40]}
            if self.ref:raise g.Blocked('Remote CAS collision')
            self.ref={'ref':data['ref'],'object':{'sha':data['sha']}}
            if self.after_commit:raise g.Blocked('UNKNOWN_REMOTE_STATE')
            return self.ref

class ReleaseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)/'repo';cls.root.mkdir()
        # A private synthetic Git object database, never a branch of the real repo.
        baseline=json.loads((ROOT/'release/baseline.json').read_text())
        for path in baseline['runtime_files_sha256']:
            p=cls.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(g.source(ROOT,BASE,path))
        (cls.root/'release').mkdir()
        for name in ('baseline.json','config.json'):shutil.copyfile(ROOT/'release'/name,cls.root/'release'/name)
        subprocess.run(['git','init','-q',str(cls.root)],check=True)
        subprocess.run(['git','-C',str(cls.root),'add','.'],check=True)
        subprocess.run(['git','-C',str(cls.root),'-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','-c','user.name=Synthetic Test','-c','user.email=synthetic@example.invalid','commit','-qm','Synthetic source fixture'],check=True)
        cls.commit=g.git(cls.root,'rev-parse','HEAD').decode().strip();out=Path(cls.temp.name)/'candidate';cls.manifest=g.build(cls.root,cls.commit,out)
        z=io.BytesIO()
        with zipfile.ZipFile(z,'w') as archive:
            for p in sorted(out.iterdir()):archive.writestr(p.name,p.read_bytes())
        cls.original_zip=z.getvalue();cls.candidate=out
        cls.tree={'sha':cls.manifest['tree_sha'],'truncated':False,'tree':[]}
        for row in g.git(cls.root,'ls-tree','-r','HEAD').decode().splitlines():
            meta,path=row.split('\t');mode,kind,oid=meta.split();cls.tree['tree'].append({'path':path,'mode':mode,'type':kind,'sha':oid})
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.rawzip=self.original_zip
        self.req={'source_sha':self.commit,'ci_run_id':1,'artifact_id':2,'artifact_digest':'sha256:'+g.sha(self.rawzip),'config_digest':self.manifest['config_sha256'],'confirmation':'PREPARE_SOURCE_ONLY'}
        self.ctx={'repository':g.REPO,'event':'workflow_dispatch','ref':'refs/heads/main','workflow_ref':g.REPO+'/'+g.WORKFLOW+'@refs/heads/main','enabled':'true','checkout_sha':self.commit,'actor':'SyntheticOwner','run_id':3,'run_attempt':1};self.api=FakeAPI(self)
    def validated(self):return g.validate(self.req,self.ctx,self.api,self.root)
    def blocked(self):
        with self.assertRaises(g.Blocked):self.validated()
        self.assertEqual(self.api.writes,[])
    def mutate_zip(self,name,transform):
        parts=g.unpack(self.rawzip);parts[name]=transform(parts[name]);z=io.BytesIO()
        with zipfile.ZipFile(z,'w') as archive:
            for k,v in parts.items():archive.writestr(k,v)
        self.rawzip=z.getvalue();self.req['artifact_digest']='sha256:'+g.sha(self.rawzip);self.api.artifact['digest']=self.req['artifact_digest']
    def test_valid_preparation_never_authorizes_production(self):
        r=g.reserve(self.validated(),self.api);self.assertFalse(r['production_authorized']);self.assertEqual(r['publisher'],'ABSENT')
    def test_push_main_is_not_manual_preparation(self):self.ctx['event']='push';self.blocked()
    def test_open_or_updated_PR_is_not_preparation(self):self.ctx['event']='pull_request';self.blocked()
    def test_documentation_merge_push_is_not_preparation(self):self.ctx['event']='push';self.blocked()
    def test_manual_unreviewed_branch_is_rejected(self):self.ctx['ref']='refs/heads/evil';self.blocked()
    def test_modified_workflow_branch_ref_is_rejected(self):self.ctx['workflow_ref']=g.REPO+'/'+g.WORKFLOW+'@refs/heads/evil';self.blocked()
    def test_tag_named_main_cannot_bypass_branch_guard(self):
        self.ctx['ref']='refs/tags/main';self.ctx['workflow_ref']=g.REPO+'/'+g.WORKFLOW+'@refs/tags/main';self.blocked()
    def test_disabled_flag_is_closed(self):self.ctx['enabled']='false';self.blocked()
    def test_rerun_is_not_silent_retry(self):self.ctx['run_attempt']=2;self.blocked()
    def test_other_repository_is_rejected(self):self.ctx['repository']='attacker/space';self.blocked()
    def test_short_or_shell_sha_is_rejected(self):
        for value in ('main','3c218dc','0'*39,'0'*40+'; echo x','A'*40):
            self.req['source_sha']=value;self.blocked()
    def test_old_main_sha_is_rejected(self):self.api.main='a'*40;self.blocked()
    def test_wrong_checkout_is_rejected(self):self.ctx['checkout_sha']='a'*40;self.blocked()
    def test_missing_confirmation_is_rejected(self):self.req['confirmation']='';self.blocked()
    def test_no_artifact_id_is_rejected(self):self.req['artifact_id']=0;self.blocked()
    def test_unknown_artifact_digest_is_rejected(self):self.req['artifact_digest']='UNKNOWN';self.blocked()
    def test_expired_artifact_is_rejected(self):self.api.artifact['expired']=True;self.blocked()
    def test_artifact_from_different_run_is_rejected(self):self.api.artifact['workflow_run']['id']=100;self.blocked()
    def test_missing_remote_artifact_digest_is_rejected(self):self.api.artifact.pop('digest');self.blocked()
    def test_incomplete_or_failed_ci_is_rejected(self):self.api.run['conclusion']='failure';self.blocked()
    def test_PR_or_fork_CI_cannot_release(self):
        self.api.run['event']='pull_request';self.blocked();self.api.run['event']='push';self.api.run['head_repository']['full_name']='attacker/space';self.blocked()
    def test_other_workflow_is_not_CI_provenance(self):self.api.run['path']='.github/workflows/evil.yml';self.blocked()
    def test_insufficient_actor_permission(self):self.api.role={'permission':'write','role_name':'write'};self.blocked()
    def test_unknown_actor_permission(self):self.api.role={};self.blocked()
    def test_main_moves_before_reservation(self):
        intent=self.validated();self.api.main='a'*40
        with self.assertRaises(g.Blocked):g.reserve(intent,self.api)
        self.assertEqual(self.api.writes,[])
    def test_repeat_is_detected(self):
        intent=self.validated();g.reserve(intent,self.api)
        with self.assertRaises(g.Blocked):g.reserve(intent,self.api)
    def test_conflicting_reservation_is_detected(self):
        intent=self.validated();g.reserve(intent,self.api);other=dict(intent,artifact_digest='sha256:'+'0'*64)
        with self.assertRaises(g.Blocked):g.reserve(other,self.api)
    def test_concurrent_CAS_only_one_receipt(self):
        intent=self.validated();self.api.barrier=threading.Barrier(2);results=[]
        def run():
            try:g.reserve(intent,self.api);results.append('ok')
            except g.Blocked:results.append('blocked')
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(15)
        self.assertEqual(sorted(results),['blocked','ok'])
    def test_lost_reservation_response_does_not_double_release(self):
        intent=self.validated();self.api.after_commit=True
        with self.assertRaises(g.Blocked):g.reserve(intent,self.api)
        with self.assertRaises(g.Blocked):g.reserve(intent,self.api)
    def test_downloaded_bytes_must_match_digest(self):self.rawzip+=b'x';self.blocked()
    def test_config_substitution_is_rejected(self):self.mutate_zip('config.json',lambda _:b'{}');self.blocked()
    def test_false_runtime_attestation_is_rejected(self):
        def change(b):d=json.loads(b);d['runtime_attestation']='ATTESTED';return g.encode(d)
        self.mutate_zip('manifest.json',change);self.blocked()
    def test_config_digest_mismatch_is_rejected(self):self.req['config_digest']='0'*64;self.blocked()
    def test_git_tree_must_match_artifact(self):
        self.tree=copy.deepcopy(type(self).tree);self.tree['tree'][0]['sha']='a'*40;self.blocked()
    def test_commit_to_tree_resolution_is_checked(self):self.api.commit_response['sha']='0'*40;self.blocked()
    def test_truncated_source_tree_is_rejected(self):self.tree=dict(type(self).tree,truncated=True);self.blocked()
    def test_bundle_corruption_is_rejected(self):self.mutate_zip('candidate.tar.gz',lambda x:x+b'x');self.blocked()
    def test_artifact_member_path_escape_rejected(self):
        parts=g.unpack(self.rawzip);parts['../outside']=parts.pop('config.json');z=io.BytesIO()
        with zipfile.ZipFile(z,'w') as archive:
            for k,v in parts.items():archive.writestr(k,v)
        self.rawzip=z.getvalue();self.req['artifact_digest']='sha256:'+g.sha(self.rawzip);self.api.artifact['digest']=self.req['artifact_digest'];self.blocked()
    def test_unconfirmed_baseline_rejected(self):
        b=json.loads((ROOT/'release/baseline.json').read_text());b['production_ready']=True
        with self.assertRaises(g.Blocked):g.check_baseline(g.sources(self.root,self.commit),b)
    def test_runtime_drift_requires_explicit_baseline_review(self):
        f=g.sources(self.root,self.commit);f['src/xblago/cli.py']+=b'\n# drift\n'
        with self.assertRaises(g.Blocked):g.check_baseline(f,json.loads((ROOT/'release/baseline.json').read_text()))
    def test_source_artifact_is_reproducible(self):
        other=Path(self.temp.name)/'repeated'
        if other.exists():shutil.rmtree(other)
        g.build(self.root,self.commit,other)
        for p in self.candidate.iterdir():self.assertEqual(p.read_bytes(),(other/p.name).read_bytes())
    def test_untracked_personal_files_are_not_bundled(self):
        (self.root/'user.sqlite3').write_bytes(b'synthetic not a DB');self.assertNotIn('user.sqlite3',self.manifest['files'])
    def test_prepare_does_not_import_app_or_connect_sqlite(self):
        with patch('sqlite3.connect',side_effect=AssertionError('No DB allowed')):
            self.validated()
            with tempfile.TemporaryDirectory() as parent:g.build(self.root,self.commit,Path(parent)/'build')
    def test_redirect_never_forwards_token_to_artifact_host(self):
        req=g.urllib.request.Request('https://api.github.com/test',headers={'Authorization':'Bearer synthetic'})
        new=g.SafeRedirect().redirect_request(req,None,302,'',{},'https://test.blob.core.windows.net/artifact');self.assertIsNone(new.get_header('Authorization'))
    def test_untrusted_or_plaintext_redirect_is_blocked(self):
        req=g.urllib.request.Request('https://api.github.com/test')
        for url in ('http://api.github.com/a','https://attacker.invalid/a'):
            with self.assertRaises(g.Blocked):g.SafeRedirect().redirect_request(req,None,302,'',{},url)
    def test_workflows_have_no_automatic_publisher_or_prod_secret(self):
        self.assertEqual({p.name for p in (ROOT/'.github/workflows').iterdir() if p.suffix in ('.yml','.yaml')},{'ci.yml','huggingface-space.yml'})
        ci=json.loads((ROOT/'.github/workflows/ci.yml').read_text());manual=json.loads((ROOT/'.github/workflows/huggingface-space.yml').read_text())
        self.assertEqual(set(manual['on']),{'workflow_dispatch'});self.assertEqual(ci['permissions'],{'contents':'read'});self.assertNotIn('environment',ci['jobs']['baseline'])
        self.assertEqual(manual['jobs']['prepare']['environment'],'production-release');self.assertIn("github.ref == 'refs/heads/main'",manual['jobs']['prepare']['if'])
        self.assertFalse(manual['concurrency']['cancel-in-progress'])
        for d in (ci,manual):
            s=json.dumps(d)
            for x in ('secrets.','HF_TOKEN','upload_folder','huggingface_hub','pull_request_target','workflow_run','repository_dispatch'):self.assertNotIn(x,s)
            for job in d['jobs'].values():
                for step in job['steps']:
                    if 'uses' in step:self.assertRegex(step['uses'],r'@[0-9a-f]{40}$')
                    if step.get('uses','').startswith('actions/checkout@'):self.assertIs(step['with']['persist-credentials'],False)
    def test_application_imports_have_no_DB_side_effect(self):
        code="import sqlite3; sqlite3.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('import DB')); import xblago.cli, xblago.integrations.telegram, xblago.core.storage"
        subprocess.run([os.sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':str(ROOT/'src')},check=True,capture_output=True)
    def test_startup_DDL_risk_is_reported_not_hidden(self):
        from xblago.core.storage import SQLiteBlagoStore
        with tempfile.TemporaryDirectory() as directory,patch.object(SQLiteBlagoStore,'_ensure_schema') as ensure:
            SQLiteBlagoStore(Path(directory)/'synthetic.sqlite3');ensure.assert_called_once()
        b=json.loads((ROOT/'release/baseline.json').read_text());self.assertIn('PRESENT',b['schema']['startup_ddl']);self.assertEqual(b['migrations']['release_execution'],'FORBIDDEN')
    def test_runtime_matches_source_pinned_baseline(self):
        baseline=json.loads((ROOT/'release/baseline.json').read_text())
        for path,h in baseline['runtime_files_sha256'].items():self.assertEqual(g.sha((ROOT/path).read_bytes()),h)
