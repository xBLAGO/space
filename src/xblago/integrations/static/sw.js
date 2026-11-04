/* Cache only the public app shell. Never cache authenticated API/data. */
const CACHE='blago-shell-v1';
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/app/icon.svg','/app/manifest.webmanifest']))));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{if(event.request.url.includes('/api/')||event.request.mode==='navigate')return;});
