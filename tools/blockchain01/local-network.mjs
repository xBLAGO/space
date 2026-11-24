import ganache from 'ganache';
// Public DEVELOPMENT mnemonic; never use these accounts on any real network.
const port=Number(process.argv[2]);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('Invalid local port');
const server=ganache.server({chain:{chainId:31337,networkId:31337,hardfork:'merge'},
  wallet:{mnemonic:'test test test test test test test test test test test junk'},logging:{quiet:true}});
await server.listen(port,'127.0.0.1');
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await server.close();process.exit(0);});
