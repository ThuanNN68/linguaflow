self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || 'LinguaFlow', {
    body: data.body || 'You have a new notification.',
    icon: '/brand/brand-mark.svg',
    badge: '/brand/brand-mark.svg',
    data: { url: data.url || '/chat' },
    tag: data.url || 'linguaflow-message',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/chat';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
    const existing = windows.find((client) => new URL(client.url).pathname === new URL(url, self.location.origin).pathname);
    return existing ? existing.focus() : clients.openWindow(url);
  }));
});
