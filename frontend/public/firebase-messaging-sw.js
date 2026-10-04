/* eslint-disable no-undef */
/* global importScripts, firebase, clients */
/* Firebase Cloud Messaging service worker (background push).
   Firebase web config is public by design – safe to ship to the client. */
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

messaging.onBackgroundMessage((payload) => {
  const n = payload.notification || payload.data || {};
  self.registration.showNotification(n.title || "Auktionsbevakning", {
    body: n.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: payload.data || {},
  });
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(clients.openWindow(url));
});
