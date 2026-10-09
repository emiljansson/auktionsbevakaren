/* eslint-disable no-undef */
/* global importScripts, firebase, clients */
/* Firebase Cloud Messaging service worker. Bump CACHE_NAME + SW_REV (src/lib/push.js) on every change. */
const CACHE_NAME = "auktiomera-sw-v2-click-first";
const NAV_CACHE = "pending-nav-v1";
const TELEMETRY_CACHE = "sw-telemetry-v1";
const KEEP_CACHES = [CACHE_NAME, NAV_CACHE, TELEMETRY_CACHE];

async function tlog(event, extra) {
  try {
    const c = await caches.open(TELEMETRY_CACHE);
    const r = await c.match("/__sw_log__");
    const logs = r ? await r.json() : [];
    logs.push({ ts: Date.now(), sw: CACHE_NAME, event, ...(extra || {}) });
    await c.put("/__sw_log__", new Response(JSON.stringify(logs.slice(-50))));
  } catch (e) { /* ignore */ }
}

// MUST be registered BEFORE importScripts: Firebase's own click handler swallows the event.
self.addEventListener("notificationclick", (event) => {
  event.stopImmediatePropagation();
  event.notification.close();
  const raw = event.notification.data || {};
  const data = raw.FCM_MSG ? (raw.FCM_MSG.data || {}) : raw;
  const path = data.url && data.url.startsWith("/") ? data.url : "/";
  event.waitUntil((async () => {
    const c = await caches.open(NAV_CACHE);
    await c.put("/__pending_nav__", new Response(JSON.stringify({ path, ts: Date.now() })));
    await tlog("notificationclick", { path, type: data.type || null });

    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    await tlog("clients_found", { n: list.length });
    const client = list.find((w) => "focus" in w) || list[0];
    if (!client) {
      try { await self.clients.openWindow(path); await tlog("open_window_ok"); }
      catch (e) { await tlog("open_window_error", { msg: String(e) }); }
      return;
    }
    try { await client.focus(); } catch (e) { /* ignore */ }
    let acked = false;
    const onAck = (e) => { if (e.data && e.data.type === "NAVIGATE_ACK") acked = true; };
    self.addEventListener("message", onAck);
    client.postMessage({ type: "NAVIGATE", path });
    await new Promise((r) => setTimeout(r, 1200));
    self.removeEventListener("message", onAck);
    await tlog("postmessage_result", { acked });
    if (!acked) {
      try { await client.navigate(path); await tlog("hard_navigate_ok"); }
      catch (e) {
        await tlog("hard_navigate_error", { msg: String(e) });
        try { await self.clients.openWindow(path); await tlog("open_window_ok"); }
        catch (e2) { await tlog("open_window_error", { msg: String(e2) }); }
      }
    }
  })());
});

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith("auktiomera-") && !KEEP_CACHES.includes(k)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});
self.addEventListener("push", () => { tlog("push_received"); });

importScripts("https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyAYV0_CYmPOebjVg5leCgNsQ0GfW1QGmdQ",
  authDomain: "commhub-40e51.firebaseapp.com",
  projectId: "commhub-40e51",
  storageBucket: "commhub-40e51.appspot.com",
  messagingSenderId: "418795638736",
  appId: "1:418795638736:web:de137997637e62feea73d5",
});

const messaging = firebase.messaging();

// CommHub sends an FCM notification payload which the browser auto-displays.
// Do NOT call showNotification() here (caused duplicate notifications).
messaging.onBackgroundMessage(() => {});
