const CACHE = 'teamcal-v5';
const STATIC = ['/manifest.json', '/dongkoo-logo_2.svg', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/') ||
      url.hostname.includes('supabase') ||
      url.hostname.includes('slack')) return;

  // HTML은 항상 네트워크 우선 (최신 배포 반영)
  if (e.request.destination === 'document' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
    return;
  }

  // 이미지/정적 파일은 캐시 우선
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});

async function getBadgeCount() {
  try {
    const cache = await caches.open('badge-store');
    const res = await cache.match('/badge-count');
    return res ? (parseInt(await res.text()) || 0) : 0;
  } catch { return 0; }
}

async function updateBadge(n) {
  try {
    const cache = await caches.open('badge-store');
    await cache.put('/badge-count', new Response(String(n)));
    if ('setAppBadge' in navigator) {
      n > 0 ? await navigator.setAppBadge(n) : await navigator.clearAppBadge();
    }
  } catch {}
}

self.addEventListener('push', e => {
  let data = {};
  try { data = e.data.json(); } catch (err) {}
  const title = data.title || 'Dongkoo Calendar';
  const options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: data.url || '/' },
  };
  e.waitUntil(
    getBadgeCount().then(n => updateBadge(n + 1)).then(() =>
      self.registration.showNotification(title, options)
    )
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window' }).then(clients => {
      for (const client of clients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) return client.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});
