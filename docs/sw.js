/* Kill switch for the previous Vite PWA registered at /sw.js. */
self.addEventListener("install", function (event) {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        return caches.delete(key);
      }));
    }).then(function () {
      return self.registration.unregister();
    }).then(function () {
      return self.clients.matchAll({ includeUncontrolled: true, type: "window" });
    }).then(function (clients) {
      return Promise.all(clients.map(function (client) {
        return client.navigate(client.url);
      }));
    })
  );
});
