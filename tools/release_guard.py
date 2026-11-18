"""Source preparation only. No publisher, application import or SQLite migration."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO='xBLAGO/space'
WORKFLOW='.github/workflows/huggingface-space.yml'
MAX=16*1024*1024
FILES={'candidate.tar.gz','manifest.json','baseline.json','config.json'}

class Blocked(ValueError):pass

def need(ok,message):
    if not ok:raise Blocked(message)

def sha(b):return hashlib.sha256(b).hexdigest()
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def encode(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def strict(b):
    need(len(b)<=MAX,'Input too large')
    def pairs(xs):
        d={}
        for k,v in xs:
            need(k not in d,'Duplicate JSON key');d[k]=v
        return d
    return json.loads(b,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Blocked('Non-finite JSON')))

def full_sha(s):need(type(s) is str and re.fullmatch('[0-9a-f]{40}',s),'Exact full source SHA required');return s

def git(root,*args):return subprocess.check_output(['git',*args],cwd=root)
def source(root,commit,path):return git(root,'show',full_sha(commit)+':'+path)
def sources(root,commit):
    tree=git(root,'ls-tree','-rz',commit).split(b'\0');result={}
    for row in tree:
        if not row:continue
        spec,name=row.split(b'\t');mode,kind,oid=spec.decode().split();p=name.decode()
        if p.startswith('src/') or p in ('Dockerfile','pyproject.toml','README.md','deploy/huggingface-space/README.md'):
            need(kind=='blob' and mode in ('100644','100755'),'Source symlink/submodule forbidden')
            need(not p.startswith('src/') or p.endswith('.py'),'Unreviewed runtime payload')
            result[p]=source(root,commit,p)
    return result

def check_baseline(files,baseline):
    need(baseline.get('format')=='blago-source-baseline-1' and baseline.get('production_ready') is False,'Unconfirmed baseline')
    expected=baseline.get('runtime_files_sha256');need(type(expected) is dict and expected,'Missing baseline sources')
    need({p:sha(b) for p,b in files.items()}==expected,'Baseline drift; explicit review required')
    need(baseline['migrations']['release_execution']=='FORBIDDEN','No migration permission')

def build(root,commit,output,*,baseline_bytes=None,config_bytes=None):
    full_sha(commit);need(git(root,'rev-parse',commit+'^{commit}').decode().strip()==commit,'Commit mismatch')
    bb=source(root,commit,'release/baseline.json') if baseline_bytes is None else baseline_bytes
    cb=source(root,commit,'release/config.json') if config_bytes is None else config_bytes
    b=strict(bb);c=strict(cb);files=sources(root,commit);check_baseline(files,b);check_config(c)
    raw=io.BytesIO()
    with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as gz:
        with tarfile.open(fileobj=gz,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            for path,data in sorted(files.items()):
                info=tarfile.TarInfo(path);info.size=len(data);info.mode=0o644;info.mtime=0;tar.addfile(info,io.BytesIO(data))
    bundle=raw.getvalue()
    m={'format':'blago-source-artifact-1','source_sha':commit,'tree_sha':git(root,'rev-parse',commit+'^{tree}').decode().strip(),
       'baseline_sha256':sha(bb),'config_sha256':sha(cb),'bundle_sha256':sha(bundle),'files':{p:{'sha256':sha(data),'git_blob_sha':blob(data)} for p,data in files.items()},
       'dependencies':b['dependencies'],'schema':b['schema'],'migrations':b['migrations'],'runtime_attestation':'UNKNOWN','artifact_kind':c['artifact_kind']}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    for name,data in {'candidate.tar.gz':bundle,'manifest.json':encode(m),'baseline.json':bb,'config.json':cb}.items():(output/name).write_bytes(data)
    return m

def check_config(c):
    need(c=={'format':'blago-release-config-1','repository':REPO,'source_ref':'refs/heads/main','environment':'production-release','ci_workflow':'.github/workflows/ci.yml','artifact_name':'blago-source-candidate','artifact_kind':'SOURCE_BUNDLE_NOT_RUNTIME_IMAGE','publication_enabled':False,'migration_execution':'FORBIDDEN','owner_controls_status':'UNKNOWN','tag_prefix':'release-preparations/','runtime_attestation':'UNKNOWN'},'Unsupported release configuration; explicit guard review required')

def unpack(data):
    need(len(data)<=MAX,'Archive bound')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rows=z.infolist();need(len(rows)==4 and {x.filename for x in rows}==FILES,'Artifact members')
        need(sum(x.file_size for x in rows)<=MAX and all(not stat.S_ISLNK(x.external_attr>>16) for x in rows),'Artifact expansion/link')
        return {x.filename:z.read(x) for x in rows}

def verify_package(data,req,tree,trusted_baseline,trusted_config):
    need(sha(data)==req['artifact_digest'].removeprefix('sha256:'),'Downloaded artifact digest mismatch')
    parts=unpack(data);m=strict(parts['manifest.json']);b=strict(parts['baseline.json']);c=strict(parts['config.json'])
    need(parts['baseline.json']==trusted_baseline and parts['config.json']==trusted_config,'Artifact policy substitution')
    check_config(c)
    need(set(m)=={'format','source_sha','tree_sha','baseline_sha256','config_sha256','bundle_sha256','files','dependencies','schema','migrations','runtime_attestation','artifact_kind'},'Manifest schema')
    need(m['format']=='blago-source-artifact-1' and m['source_sha']==req['source_sha'] and m['tree_sha']==tree['sha'],'Source/tree mismatch')
    need(m['config_sha256']==req['config_digest']==sha(trusted_config) and m['baseline_sha256']==sha(trusted_baseline),'Config/baseline digest mismatch')
    need(m['bundle_sha256']==sha(parts['candidate.tar.gz']),'Bundle digest mismatch')
    need(all(m[k]==b[k] for k in ('dependencies','schema','migrations')) and m['runtime_attestation']=='UNKNOWN' and m['artifact_kind']==c['artifact_kind'],'False attestation')
    need(tree.get('truncated') is False,'Incomplete remote source tree')
    remote={x['path']:x for x in tree['tree']}
    need(set(m['files'])==set(b['runtime_files_sha256']) and len(m['files'])<=256,'Unapproved bundle paths')
    for p,f in m['files'].items():
        need(set(f)=={'sha256','git_blob_sha'} and f['sha256']==b['runtime_files_sha256'][p],'Manifest file mismatch')
        need(p in remote and remote[p]['type']=='blob' and remote[p]['mode'] in ('100644','100755') and remote[p]['sha']==f['git_blob_sha'],'Git object mismatch')
    need(remote.get('release/config.json',{}).get('sha')==blob(trusted_config) and remote.get('release/baseline.json',{}).get('sha')==blob(trusted_baseline),'Trusted policy not in selected source')
    with gzip.GzipFile(fileobj=io.BytesIO(parts['candidate.tar.gz'])) as gz:raw=gz.read(MAX+1)
    need(len(raw)<=MAX,'Tar expansion bound');found={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
        for x in tar:
            need(x.isfile() and x.name in m['files'] and x.name not in found and x.size<=MAX,'Unsafe tar member')
            content=tar.extractfile(x).read(MAX+1);need(len(content)==x.size and sha(content)==m['files'][x.name]['sha256'] and blob(content)==m['files'][x.name]['git_blob_sha'],'Tar content mismatch');found[x.name]=content
    check_baseline(found,b);return m,c

def context_check(req,ctx):
    full_sha(req['source_sha'])
    need(ctx['repository']==REPO and ctx['event']=='workflow_dispatch' and ctx['ref']=='refs/heads/main','Manual main preparation only')
    need(ctx['workflow_ref']==REPO+'/'+WORKFLOW+'@refs/heads/main','Untrusted workflow ref')
    need(ctx['enabled']=='true' and ctx['run_attempt']==1,'Disabled or rerun; inspect prior intent')
    need(ctx['checkout_sha']==req['source_sha'],'Workflow source does not match requested main')
    need(req['confirmation']=='PREPARE_SOURCE_ONLY','Explicit preparation confirmation required')
    need(type(req['ci_run_id']) is int and req['ci_run_id']>0 and type(req['artifact_id']) is int and req['artifact_id']>0,'CI/artifact identity required')
    need(type(req['artifact_digest']) is str and re.fullmatch('sha256:[0-9a-f]{64}',req['artifact_digest']) and type(req['config_digest']) is str and re.fullmatch('[0-9a-f]{64}',req['config_digest']),'Exact artifact/config digest required')
    need(type(ctx['run_id']) is int and ctx['run_id']>0 and re.fullmatch('[A-Za-z0-9-]{1,39}',ctx['actor']),'Manual actor/run required')

def validate(req,ctx,api,root):
    context_check(req,ctx)
    role=api.get('/collaborators/'+ctx['actor']+'/permission')
    need(role.get('permission')=='admin' or role.get('role_name') in ('admin','maintain'),'Actor must be admin/maintain; UNKNOWN denies')
    need(api.get('/commits/main')['sha']==req['source_sha'],'Requested SHA is not current main')
    run=api.get('/actions/runs/'+str(req['ci_run_id']))
    need(run['head_sha']==req['source_sha'] and run['head_branch']=='main' and run['event']=='push' and run['status']=='completed' and run['conclusion']=='success' and run['path']=='.github/workflows/ci.yml' and run['head_repository']['full_name']==REPO,'Trusted main CI required')
    a=api.get('/actions/artifacts/'+str(req['artifact_id']))
    need(a['id']==req['artifact_id'] and a['name']=='blago-source-candidate' and a['expired'] is False and a.get('digest')==req['artifact_digest'] and a['workflow_run']['id']==req['ci_run_id'] and a['workflow_run']['head_sha']==req['source_sha'] and a['workflow_run']['head_branch']=='main','Artifact is absent/unconfirmed/stale/wrong run')
    data=api.download('/actions/artifacts/'+str(req['artifact_id'])+'/zip')
    commit=api.get('/git/commits/'+req['source_sha']);need(commit['sha']==req['source_sha'],'Git commit identity mismatch')
    tree=api.get('/git/trees/'+full_sha(commit['tree']['sha'])+'?recursive=1')
    m,c=verify_package(data,req,tree,source(root,req['source_sha'],'release/baseline.json'),source(root,req['source_sha'],'release/config.json'))
    return {'format':'blago-preparation-intent-1','scope':'SOURCE_PREPARATION_ONLY','production_authorized':False,'source_sha':req['source_sha'],'tree_sha':m['tree_sha'],'artifact_id':req['artifact_id'],'artifact_digest':req['artifact_digest'],'bundle_sha256':m['bundle_sha256'],'config_digest':req['config_digest'],'ci_run_id':req['ci_run_id'],'actor':ctx['actor'],'manual_run_id':ctx['run_id'],'owner_controls':'UNKNOWN_UNTIL_OWNER_CHECKLIST','runtime_attestation':'UNKNOWN','publisher':'ABSENT'}

def reserve(intent,api):
    ref='tags/release-preparations/'+full_sha(intent['source_sha'])
    need(api.get_optional('/git/ref/'+ref) is None,'REPLAY_OR_CONFLICT: preparation already reserved')
    need(api.get('/commits/main')['sha']==intent['source_sha'],'Main advanced; prepare again after review')
    obj=api.post('/git/tags',{'tag':'release-preparations/'+intent['source_sha'],'message':encode(intent).decode(),'object':intent['source_sha'],'type':'commit'})
    # Atomic remote create, never patch/delete/force a ref. A timeout is ambiguous.
    result=api.post('/git/refs',{'ref':'refs/'+ref,'sha':obj['sha']})
    need(result['object']['sha']==obj['sha'],'UNKNOWN_RESERVATION: receipt mismatch')
    return dict(intent,reservation_ref=result['ref'],reservation_tag_sha=obj['sha'])

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        u=urllib.parse.urlsplit(newurl)
        need(u.scheme=='https' and (u.hostname=='api.github.com' or u.hostname.endswith(('.blob.core.windows.net','.actions.githubusercontent.com','.githubusercontent.com'))),'Untrusted artifact redirect')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(req.full_url).netloc!=u.netloc:redirected.remove_header('Authorization')
        return redirected

class API:
    def __init__(self,token):
        need(bool(token),'Missing GitHub authorization');self.token=token;self.opener=urllib.request.build_opener(SafeRedirect())
    def call(self,path,method='GET',payload=None,raw=False,optional=False):
        need(path.startswith('/') and not path.startswith('//'),'API path')
        request=urllib.request.Request('https://api.github.com/repos/'+REPO+path,data=None if payload is None else encode(payload),method=method,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=30) as response:data=response.read(MAX+1)
        except urllib.error.HTTPError as exc:
            if optional and exc.code==404:return None
            raise Blocked('GitHub request denied/conflicted; inspect immutable intent') from None
        except (OSError,TimeoutError) as exc:raise Blocked('UNKNOWN_REMOTE_STATE; do not assume success') from exc
        need(len(data)<=MAX,'API response bound');return data if raw else strict(data)
    def get(self,path):return self.call(path)
    def get_optional(self,path):return self.call(path,optional=True)
    def post(self,path,data):return self.call(path,'POST',data)
    def download(self,path):return self.call(path,raw=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('build');b.add_argument('--output',type=Path,required=True)
    sub.add_parser('prepare');args=p.parse_args();root=Path(__file__).resolve().parents[1]
    if args.action=='build':
        commit=git(root,'rev-parse','HEAD').decode().strip();m=build(root,commit,args.output);print(json.dumps({'source_sha':commit,'bundle_sha256':m['bundle_sha256'],'config_digest':m['config_sha256'],'runtime_attestation':'UNKNOWN'}));return
    e=os.environ
    req={'source_sha':e.get('REQUEST_SOURCE_SHA'),'ci_run_id':int(e.get('REQUEST_CI_RUN_ID','0')),'artifact_id':int(e.get('REQUEST_ARTIFACT_ID','0')),'artifact_digest':e.get('REQUEST_ARTIFACT_DIGEST'),'config_digest':e.get('REQUEST_CONFIG_DIGEST'),'confirmation':e.get('REQUEST_CONFIRMATION')}
    ctx={'repository':e.get('GITHUB_REPOSITORY'),'event':e.get('GITHUB_EVENT_NAME'),'ref':e.get('GITHUB_REF'),'workflow_ref':e.get('GITHUB_WORKFLOW_REF'),'enabled':e.get('PREPARATION_ENABLED'),'checkout_sha':e.get('GITHUB_SHA'),'actor':e.get('GITHUB_ACTOR'),'run_id':int(e.get('GITHUB_RUN_ID','0')),'run_attempt':int(e.get('GITHUB_RUN_ATTEMPT','0'))}
    context_check(req,ctx);api=API(e.get('GH_TOKEN'));intent=validate(req,ctx,api,root);receipt=reserve(intent,api)
    print(json.dumps(receipt,sort_keys=True))
if __name__=='__main__':
    try:main()
    except (Blocked,KeyError,ValueError,TypeError,zipfile.BadZipFile,tarfile.TarError,OSError) as exc:raise SystemExit('Preparation BLOCKED: '+str(exc))
