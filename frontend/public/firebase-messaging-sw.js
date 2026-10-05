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

// CommHub sends an FCM *notification* payload (title/body), which the browser
// displays automatically in the background. Do NOT call showNotification() here
// as well — that produced TWO identical notifications per message. Keep the
// handler for logging only so exactly one notification is shown.
messaging.onBackgroundMessage((payload) => {
  // eslint-disable-next-line no-console
  console.debug("[push] background message", payload);
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(clients.openWindow(url));
});
