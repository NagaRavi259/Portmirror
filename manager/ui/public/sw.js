// Installability only: this worker deliberately caches nothing. The dashboard is live data from
// the gateway, so a cached copy would only ever be wrong - every request goes straight to the network.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
