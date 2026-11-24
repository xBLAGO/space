// Read verifier dependencies are Node standard library ONLY.
import {createHash} from 'node:crypto';
import http from 'node:http';
import {readFileSync} from 'node:fs';
export const DOMAIN=Buffer.from('BLAGO/FUND-REPORT-CHECKPOINT/1\0','ascii');
export const CHAIN=31337n;
export const PUBLISHER='0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266';
export const LIMIT=20*1024*1024;
export const hash=b=>createHash('sha256').update(b).digest('hex');
export function fingerprint(bytes){
  if(bytes.length>LIMIT)throw Error('Report exceeds 20 MiB');
  const length=Buffer.alloc(8);length.writeBigUInt64BE(BigInt(bytes.length));
  return '0x'+hash(Buffer.concat([DOMAIN,length,bytes]));
}
export function localURL(value){
  const url=new URL(value);
  if(url.protocol!=='http:'||!['127.0.0.1','[::1]'].includes(url.hostname)||url.username||url.password||url.hash||url.search||url.pathname!=='/')throw Error('Literal loopback HTTP RPC only');
  return url;
}
export function rpc(endpoint,method,params=[]){
  const url=localURL(endpoint);
  if(!['eth_chainId','eth_getCode','eth_call','eth_getBlockByNumber'].includes(method))throw Error('Read-only RPC method required');
  return new Promise((resolve,reject)=>{
    const body=JSON.stringify({jsonrpc:'2.0',id:1,method,params});
    const req=http.request(url,{method:'POST',headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{
      let n=0;const chunks=[];
      res.on('data',x=>{n+=x.length;if(n>2*1024*1024){res.destroy();reject(Error('RPC response bound'));}else chunks.push(x);});
      res.on('error',reject);res.on('end',()=>{try{
        if(res.statusCode!==200)throw Error('RPC HTTP status (no redirects)');
        const v=JSON.parse(Buffer.concat(chunks).toString());
        if(v.id!==1||v.jsonrpc!=='2.0'||v.error||!('result' in v))throw Error('RPC unavailable or invalid response');
        resolve(v.result);
      }catch(e){reject(e);}});
    });
    req.setTimeout(2500,()=>req.destroy(Error('RPC timeout')));req.on('error',reject);req.end(body);
  });
}
export async function verify({bytes,contract,number,endpoint='http://127.0.0.1:18545/',genesis=null}){
  try{
    if(!/^0x[0-9a-fA-F]{40}$/.test(contract)||!/^\d+$/.test(String(number)))throw Error('Invalid contract/checkpoint');
    const n=BigInt(number);if(n<=0n||n>=(1n<<256n))throw Error('Checkpoint out of range');
    const digest=fingerprint(bytes),artifact=JSON.parse(readFileSync(new URL('./artifact.json',import.meta.url)));
    if(BigInt(await rpc(endpoint,'eth_chainId'))!==CHAIN)throw Error('Wrong local chain');
    const block=await rpc(endpoint,'eth_getBlockByNumber',['0x0',false]);
    if(!block?.hash||genesis && genesis.toLowerCase()!==block.hash.toLowerCase())throw Error('Genesis unavailable/mismatch');
    const code=await rpc(endpoint,'eth_getCode',[contract,'latest']);
    if(code.toLowerCase()!==artifact.deployedBytecode.toLowerCase())throw Error('Contract code absent/substituted');
    const call=async(signature,args='')=>rpc(endpoint,'eth_call',[{to:contract,data:'0x'+artifact.selectors[signature]+args},'latest']);
    const publisher=await call('publisher()');
    if(publisher.toLowerCase()!=='0x'+'0'.repeat(24)+PUBLISHER.slice(2))throw Error('Unexpected test publisher');
    const value=await call('get(uint256)',n.toString(16).padStart(64,'0'));
    if(!/^0x[0-9a-fA-F]{128}$/.test(value)||!['0'.repeat(64),'0'.repeat(63)+'1'].includes(value.slice(2,66)))throw Error('Invalid checkpoint response');
    if(BigInt('0x'+value.slice(2,66))===0n)return {status:'UNKNOWN',reason:'POINT_ABSENT',exit_code:2};
    const anchored='0x'+value.slice(66).toLowerCase();
    return {status:anchored===digest?'MATCH':'MISMATCH',exit_code:anchored===digest?0:1,
      checkpoint:String(n),contract,chain_id:String(CHAIN),genesis:block.hash,publisher:PUBLISHER,
      report_digest:digest,anchored_digest:anchored,contract_code_sha256:hash(Buffer.from(code.slice(2),'hex')),
      meaning:'REPORT_VERSION_ONLY; NOT ACCOUNTING_TRUTH_OR_AUTHORITY'};
  }catch(e){return {status:'UNKNOWN',reason:e.code==='ECONNREFUSED'?'NETWORK_UNAVAILABLE':e.message,exit_code:2};}
}
