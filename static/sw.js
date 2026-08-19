const CACHE_NAME = 'laundry-app-v24';
const assetsToCache = [
  './dashboard.html',
  './index.html',
  './manifest.json',
  './sound.js',
  './native-alarm.js',
  './option.mp3',
  './start.mp3',
  'https://cdn.tailwindcss.com'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(assetsToCache))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) return caches.delete(key);
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('ngrok-free.dev')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(JSON.stringify({ error: "offline" }), {
          headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  // Always fetch the manifest and icons fresh so installs use the latest name/icon.
  const pathname = new URL(event.request.url).pathname;
  if (pathname === '/manifest.json' || pathname === '/icon.png' || pathname === '/icon.svg') {
    event.respondWith(
      fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      return cachedResponse || fetch(event.request);
    })
  );
});

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'SHOW_LAUNDRY_NOTIFICATION') {
    const title = data.title || 'Laundry timer';
    const options = {
      body: data.body || '',
      tag: data.tag || 'laundry-active-timer',
      renotify: Boolean(data.renotify),
      requireInteraction: Boolean(data.requireInteraction),
      silent: Boolean(data.silent),
      badge: './icon.png',
      icon: './icon.png',
      data: data.url ? { url: data.url } : {}
    };
    event.waitUntil(self.registration.showNotification(title, options));
  }

  if (data.type === 'CLEAR_LAUNDRY_NOTIFICATION') {
    event.waitUntil(
      self.registration.getNotifications({ tag: data.tag || 'laundry-active-timer' })
        .then((notifications) => notifications.forEach((notification) => notification.close()))
    );
  }
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: event.data ? event.data.text() : '' };
  }

  const isFinished = data.tag === 'laundry-finished';
  const options = {
    body: data.body || 'Your laundry is done!',
    icon: './icon.png',
    badge: './icon.png',
    vibrate: isFinished ? [500, 200, 500, 200, 500, 200, 500] : undefined,
    tag: data.tag || 'laundry-finished',
    renotify: Boolean(data.renotify),
    requireInteraction: Boolean(data.requireInteraction),
    silent: Boolean(data.silent),
    data: { url: data.url || './dashboard.html' }
  };
  event.waitUntil(
    self.registration.showNotification(data.title || 'Laundry Finished!', options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : './dashboard.html';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
