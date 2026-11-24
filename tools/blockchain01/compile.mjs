import solc from 'solc';
import {readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('./',import.meta.url);
const source=readFileSync(new URL('contracts/FundCheckpoint.sol',root),'utf8');
const input={language:'Solidity',sources:{'FundCheckpoint.sol':{content:source}},settings:{
  optimizer:{enabled:false},evmVersion:'paris',outputSelection:{'*':{'*':['abi','evm.bytecode.object','evm.deployedBytecode.object','evm.methodIdentifiers']}}}};
const out=JSON.parse(solc.compile(JSON.stringify(input)));
if(out.errors?.some(x=>x.severity==='error'))throw Error(JSON.stringify(out.errors));
const c=out.contracts['FundCheckpoint.sol'].FundCheckpoint;
const artifact={format:'blago-local-contract-1',compiler:solc.version(),evmVersion:'paris',
  source_sha256:createHash('sha256').update(source).digest('hex'),abi:c.abi,bytecode:'0x'+c.evm.bytecode.object,
  deployedBytecode:'0x'+c.evm.deployedBytecode.object,selectors:c.evm.methodIdentifiers};
const encoded=JSON.stringify(artifact,null,2)+'\n',file=new URL('artifact.json',root);
if(process.argv.includes('--check')){
  if(readFileSync(file,'utf8')!==encoded)throw Error('Compiler artifact drift');
}else writeFileSync(file,encoded);
console.log('Contract compilation: PASS',solc.version());
