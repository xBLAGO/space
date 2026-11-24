import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync,rmSync,mkdirSync,copyFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import net from 'node:net';
import http from 'node:http';
import {spawnSync} from 'node:child_process';
import {fingerprint,localURL,verify,CHAIN} from './core.mjs';
import {startNode,stopNode,deploy,reverted} from './lab.mjs';

test('frozen SHA256 byte vector; formatting and amount are significant',()=>{
  const v=JSON.parse(readFileSync(new URL('./hash-vector.json',import.meta.url)));
  const b=Buffer.from(v.file_utf8);assert.equal(fingerprint(b),v.expected_digest);
  assert.notEqual(fingerprint(Buffer.from(v.file_utf8.trim())),v.expected_digest);
  assert.notEqual(fingerprint(Buffer.from(v.file_utf8.replace('100','101'))),v.expected_digest);
  assert.throws(()=>fingerprint(Buffer.alloc(20*1024*1024+1)));
});
test('RPC is literal loopback only; no URL credentials or remote redirects',()=>{
  for(const u of ['https://127.0.0.1/','http://localhost/','http://example.com/','http://127.0.0.1@evil.invalid/','http://a:b@127.0.0.1/','http://127.0.0.1/?x=1'])assert.throws(()=>localURL(u));
  assert.equal(localURL('http://127.0.0.1:18545/').hostname,'127.0.0.1');
});
test('HTTP redirect is UNKNOWN and is never followed',async()=>{
  let requests=0;
  const server=http.createServer((req,res)=>{requests++;res.writeHead(302,{Location:'http://127.0.0.1:'+server.address().port+'/'});res.end();});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  try{
    const r=await verify({bytes:Buffer.from('unit'),contract:'0x'+'11'.repeat(20),number:1,endpoint:'http://127.0.0.1:'+server.address().port+'/'});
    assert.equal(r.status,'UNKNOWN');assert.equal(requests,1);
  }finally{await new Promise(r=>server.close(r));}
});
test('actual EVM access control, monotonic immutable points and stdlib verifier',async()=>{
  const port=await new Promise((ok,bad)=>{const s=net.createServer();s.on('error',bad);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>ok(p));});});
  const node=await startNode(port),tmp=mkdtempSync(join(tmpdir(),'bc01-unit-'));
  const bytes=Buffer.from('synthetic unit bytes\n'),digest=fingerprint(bytes);
  let address;
  try{
    const d=await deploy(node.provider);address=await d.contract.getAddress();
    assert.equal(await node.provider.send('eth_chainId',[]),'0x'+CHAIN.toString(16));
    assert.equal((await verify({bytes,contract:address,number:1,endpoint:node.endpoint})).status,'UNKNOWN');
    await (await d.contract.record(1,digest)).wait();
    assert.equal((await verify({bytes,contract:address,number:1,endpoint:node.endpoint})).status,'MATCH');
    assert.equal((await verify({bytes:Buffer.from('changed'),contract:address,number:1,endpoint:node.endpoint})).status,'MISMATCH');
    assert.equal((await reverted(d.contract,await node.provider.getSigner(1),2,digest)).receipt_status,0);
    assert.equal((await reverted(d.contract,await node.provider.getSigner(0),1,digest)).receipt_status,0);
    assert.equal((await reverted(d.contract,await node.provider.getSigner(0),0,digest)).receipt_status,0);
    assert.equal((await reverted(d.contract,await node.provider.getSigner(0),2,'0x'+'00'.repeat(32))).receipt_status,0);
    await (await d.contract.record(3,digest)).wait();
    assert.equal(await d.contract.last(),3n);assert.equal((await d.contract.get(1))[1],digest);
    assert.equal((await verify({bytes,contract:address,number:2,endpoint:node.endpoint})).reason,'POINT_ABSENT');
    assert.equal((await verify({bytes,contract:address,number:1,endpoint:node.endpoint,genesis:'0x'+'00'.repeat(32)})).status,'UNKNOWN');
    assert.equal((await verify({bytes,contract:'0x'+'11'.repeat(20),number:1,endpoint:node.endpoint})).status,'UNKNOWN');
    for(const [value,status,exit] of [[bytes,'MATCH',0],[Buffer.from('changed'),'MISMATCH',1]]){
      const file=join(tmp,'report.txt');writeFileSync(file,value);
      const result=spawnSync(process.execPath,[new URL('./verify.mjs',import.meta.url).pathname,file,address,'1','--rpc',node.endpoint],{encoding:'utf8',env:{PATH:process.env.PATH}});
      assert.equal(result.status,exit,result.stderr);assert.equal(JSON.parse(result.stdout).status,status);
    }
    const client=join(tmp,'client');mkdirSync(client);
    for(const name of ['verify.mjs','core.mjs','artifact.json'])copyFileSync(new URL('./'+name,import.meta.url),join(client,name));
    const file=join(tmp,'original.txt');writeFileSync(file,bytes);
    const result=spawnSync(process.execPath,[join(client,'verify.mjs'),file,address,'1','--rpc',node.endpoint],{cwd:client,encoding:'utf8',env:{PATH:process.env.PATH}});
    assert.equal(result.status,0,result.stderr);assert.equal(JSON.parse(result.stdout).status,'MATCH');
  }finally{await stopNode(node);rmSync(tmp,{recursive:true,force:true});}
  assert.equal((await verify({bytes,contract:address,number:1,endpoint:node.endpoint})).status,'UNKNOWN');
});
