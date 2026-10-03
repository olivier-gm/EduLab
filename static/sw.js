'use strict';
const CACHE = 'edulab-pwa-v1';
const OFFLINE = '/static/offline.html';
const SHELL = [OFFLINE, '/static/img/icon-192.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('edulab-pwa-') && key !== CACHE).map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});
// Las cuentas, pagos y documentos siempre se consultan en la red, sin almacenarlos en caché.
self.addEventListener('fetch', event => {
  if (event.request.method === 'GET' && event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
  } else if (event.request.method === 'GET' && new URL(event.request.url).origin === self.location.origin &&
             SHELL.includes(new URL(event.request.url).pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
