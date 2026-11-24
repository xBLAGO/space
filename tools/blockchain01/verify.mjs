import {openSync,fstatSync,readFileSync,closeSync} from 'node:fs';
import {verify,LIMIT} from './core.mjs';
// No ethers, BLAGO, SQLite, secrets or application server required.
const [file,contract,number,...flags]=process.argv.slice(2);
let result;
try{
  if(!file||!contract||!number||flags.length%2)throw Error('Usage: node verify.mjs REPORT CONTRACT NUMBER [--rpc LOOPBACK_URL] [--genesis HASH]');
  const options={};for(let i=0;i<flags.length;i+=2){if(!['--rpc','--genesis'].includes(flags[i])||flags[i] in options)throw Error('Unknown/duplicate option');options[flags[i]]=flags[i+1];}
  const fd=openSync(file,'r');let bytes;
  try{if(!fstatSync(fd).isFile()||fstatSync(fd).size>LIMIT)throw Error('Bounded regular report file required');bytes=readFileSync(fd);}finally{closeSync(fd);}
  result=await verify({bytes,contract,number,endpoint:options['--rpc'],genesis:options['--genesis']});
}catch(e){result={status:'UNKNOWN',reason:e.message,exit_code:2};}
console.log(JSON.stringify(result,null,2));process.exitCode=result.exit_code;
