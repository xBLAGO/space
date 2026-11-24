// Explicit local synthetic experiment. Never connects to an existing node.
import {spawn,spawnSync} from 'node:child_process';
import {mkdirSync,writeFileSync,readFileSync,copyFileSync,existsSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import net from 'node:net';
import {ContractFactory,Contract,JsonRpcProvider} from 'ethers';
import {fingerprint,verify,PUBLISHER,CHAIN,hash} from './core.mjs';

const dir=fileURLToPath(new URL('./',import.meta.url));
export async function startNode(port){
  if(!Number.isInteger(port)||port<1024||port>65535)throw Error('Invalid local port');
  await new Promise((ok,bad)=>{const s=net.createServer();s.once('error',bad);s.listen(port,'127.0.0.1',()=>s.close(ok));});
  const child=spawn(process.execPath,[join(dir,'local-network.mjs'),String(port)],{stdio:['ignore','ignore','pipe']});
  let error='';child.stderr.on('data',b=>{error=(error+b.toString()).slice(-2000);});
  child.on('error',e=>{error=e.message;});
  const provider=new JsonRpcProvider('http://127.0.0.1:'+port+'/',Number(CHAIN),{staticNetwork:true});
  for(let i=0;i<100;i++){
    if(child.exitCode!==null)break;
    try{await provider.getBlockNumber();return {child,provider,endpoint:'http://127.0.0.1:'+port+'/'};}catch{}
    await new Promise(r=>setTimeout(r,100));
  }
  child.kill('SIGTERM');provider.destroy();throw Error('Local Ganache start failed: '+error);
}
export async function stopNode(node){
  node.provider.destroy();
  if(node.child.exitCode!==null)return;
  const exited=new Promise(r=>node.child.once('exit',r));node.child.kill('SIGTERM');
  let timer;await Promise.race([exited,new Promise(r=>{timer=setTimeout(()=>{node.child.kill('SIGKILL');r();},3000);})]);clearTimeout(timer);
}
export async function deploy(provider){
  const a=JSON.parse(readFileSync(join(dir,'artifact.json'))),signer=await provider.getSigner(0);
  if((await signer.getAddress()).toLowerCase()!==PUBLISHER)throw Error('Unexpected local test account');
  const contract=await new ContractFactory(a.abi,a.bytecode,signer).deploy(PUBLISHER);await contract.waitForDeployment();
  return {contract,artifact:a,deployment:await contract.deploymentTransaction().wait()};
}
export async function reverted(contract,signer,number,digest){
  try{
    const tx=await contract.connect(signer).record(number,digest,{gasLimit:200000});
    try{const receipt=await tx.wait();if(receipt.status!==0)throw Error('Unauthorized transaction succeeded');return {tx_hash:tx.hash,receipt_status:0};}
    catch(e){if(e.receipt?.status===0)return {tx_hash:tx.hash,receipt_status:0};throw e;}
  }catch(e){throw Error('Expected MINED reverted transaction: '+e.message);}
}
async function cli(){
  const args=process.argv.slice(2),options={};
  for(let i=0;i<args.length;i++){
    const k=args[i];if(['--enable-synthetic-local','--serve'].includes(k)){options[k]=true;continue;}
    if(!['--new-root','--port'].includes(k)||!args[i+1]||k in options)throw Error('Unknown/duplicate option');options[k]=args[++i];
  }
  if(!options['--enable-synthetic-local']||!options['--new-root'])throw Error('Explicit --enable-synthetic-local and --new-root required');
  const root=resolve(options['--new-root']);if(existsSync(root))throw Error('New output directory required');
  mkdirSync(root,{mode:0o700});const report=join(root,'report.json');
  // Producer runs to completion in a separate process, with temporary stores.
  // Only a synthetic report crosses this boundary. No BLAGO HTTP server exists.
  const python=process.env.BLAGO_LAB_PYTHON||'python3';
  const arch=process.platform==='darwin'&&process.arch==='x64';
  const producerStarted=new Date().toISOString();
  const producer=spawnSync(arch?'/usr/bin/arch':python,[...(arch?['-x86_64',python]:[]),'-m','tools.blockchain01_export','--output',report,'--synthetic-only'],{cwd:resolve(dir,'../..'),encoding:'utf8',maxBuffer:2*1024*1024});
  const producerEnded=new Date().toISOString();
  if(producer.status!==0)throw Error('Synthetic producer failed: '+producer.stderr);
  const evidence={format:'blago-blockchain01-lab-1',source_sha:spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8',cwd:resolve(dir,'../..')}).stdout.trim(),
    source_clean:spawnSync('git',['status','--porcelain'],{encoding:'utf8',cwd:resolve(dir,'../..')}).stdout.trim()==='',
    synthetic_only:true,blago:{producer_pid:producer.pid,exit_status:producer.status,started_at:producerStarted,ended_at:producerEnded,server_started:false,producer_exited_before_chain_start:true},
    versions:{node:process.version,compiler:JSON.parse(readFileSync(join(dir,'artifact.json'))).compiler,producer:JSON.parse(producer.stdout.trim())},scenarios:[],real_users:'NO-GO',independent_operator:'NOT EXECUTED'};
  const bytes=readFileSync(report),digest=fingerprint(bytes),port=Number(options['--port']||18545);
  const client=join(root,'standalone-verifier');mkdirSync(client,{mode:0o700});
  for(const name of ['verify.mjs','core.mjs','artifact.json'])copyFileSync(join(dir,name),join(client,name));
  let node;
  try{
    evidence.chain_started_after_producer_at=new Date().toISOString();
    node=await startNode(port);const d=await deploy(node.provider),address=await d.contract.getAddress();
    const genesis=(await node.provider.getBlock(0)).hash;
    evidence.network={endpoint:node.endpoint,chain_id:String(CHAIN),genesis,contract:address,publisher:PUBLISHER,
      deployment_tx:d.deployment.hash,ganache:JSON.parse(readFileSync(join(dir,'node_modules/ganache/package.json'))).version};
    const tx=await d.contract.record(1,digest),receipt=await tx.wait();
    evidence.publication={checkpoint:1,digest,tx_hash:tx.hash,block_number:receipt.blockNumber,receipt_status:receipt.status};
    const call=(file,number)=>{
      // A fresh stdlib-only verifier process gets no BLAGO imports or tokens.
      const result=spawnSync(process.execPath,[join(client,'verify.mjs'),file,address,String(number),'--rpc',node.endpoint,'--genesis',genesis],{cwd:client,encoding:'utf8',env:{PATH:process.env.PATH}});
      const value=JSON.parse(result.stdout);if(result.status!==value.exit_code)throw Error('Verifier exit mismatch');return value;
    };
    evidence.scenarios.push({id:'S1',expected:'MATCH',actual:call(report,1)});
    const original=bytes.toString('utf8'),amount=JSON.parse(original).verified.balance.available_minor;
    const needle='"available_minor": '+amount;
    if(original.split(needle).length!==2)throw Error('Tamper must change exactly one amount');
    const tampered=join(root,'changed-report.json');writeFileSync(tampered,original.replace(needle,'"available_minor": '+(amount+1)),{mode:0o600});
    evidence.scenarios.push({id:'S2',expected:'MISMATCH',changed_field:'verified.balance.available_minor',delta_minor:1,actual:call(tampered,1)});
    evidence.scenarios.push({id:'S3',expected:'REVERT',actual:await reverted(d.contract,await node.provider.getSigner(1),2,digest)});
    evidence.scenarios.push({id:'S4',expected:'REVERT',actual:await reverted(d.contract,await node.provider.getSigner(0),1,'0x'+'12'.repeat(32))});
    if((await d.contract.get(1))[1]!==digest||await d.contract.last()!==1n)throw Error('Rejected transaction changed checkpoint');
    evidence.scenarios.push({id:'S5a',expected:'UNKNOWN',actual:call(report,999)});
    // Verify no network on an unused local port while the test chain is available.
    const spare=await new Promise((ok,bad)=>{const s=net.createServer();s.once('error',bad);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>ok(p));});});
    evidence.scenarios.push({id:'S5b',expected:'UNKNOWN',actual:await verify({bytes,contract:address,number:1,endpoint:'http://127.0.0.1:'+spare+'/'})});
    const expected=['MATCH','MISMATCH',0,0,'UNKNOWN','UNKNOWN'];
    if(evidence.scenarios.some((s,i)=>(s.actual.status??s.actual.receipt_status)!==expected[i]))throw Error('Scenario did not meet expected result');
    evidence.status='PASS_LOCAL_SYNTHETIC_VERSION_ANCHOR_ONLY';
    evidence.files={report_sha256:hash(bytes),changed_report_sha256:hash(readFileSync(tampered)),
      package_lock_sha256:hash(readFileSync(join(dir,'package-lock.json'))),contract_source_sha256:hash(readFileSync(join(dir,'contracts/FundCheckpoint.sol'))),
      standalone_verifier:Object.fromEntries(['verify.mjs','core.mjs','artifact.json'].map(n=>[n,hash(readFileSync(join(client,n)))]))};
    writeFileSync(join(root,'evidence.json'),JSON.stringify(evidence,null,2)+'\n',{mode:0o600});
    console.log(JSON.stringify({status:evidence.status,root,network:evidence.network,scenarios:evidence.scenarios},null,2));
    if(options['--serve']){
      console.log('LOCAL TEST CHAIN ONLY remains available; BLAGO producer is OFF. Ctrl-C stops this owned chain.');
      await new Promise(r=>{process.once('SIGINT',r);process.once('SIGTERM',r);});
    }
  }finally{if(node)await stopNode(node);}
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))cli().catch(e=>{console.error(e.message);process.exitCode=1;});
