import { initializeApp, getApps } from "firebase/app";
import { getMessaging, getToken, onMessage, isSupported } from "firebase/messaging";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export function getDeviceId() {
  let id = localStorage.getItem("ab_device_id");
  if (!id) {
    id = (window.crypto?.randomUUID?.() || `dev-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    localStorage.setItem("ab_device_id", id);
  }
  return id;
}

export function isStandalone() {
  return (
    window.matchMedia?.("(display-mode: standalone)")?.matches ||
    window.navigator.standalone === true
  );
}

export function isIOS() {
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

let _msg = null;
let _cfg = null;

async function loadConfig() {
  if (_cfg) return _cfg;
  const { data } = await axios.get(`${API}/push/config`);
  _cfg = data;
  return data;
}

function ensureApp(config) {
  const app = getApps().length ? getApps()[0] : initializeApp(config);
  if (!_msg) _msg = getMessaging(app);
  return _msg;
}

export async function initMessaging(onForeground) {
  if (!(await isSupported().catch(() => false))) return false;
  try {
    const { config } = await loadConfig();
    ensureApp(config);
    if (onForeground) onMessage(_msg, onForeground);
    return true;
  } catch {
    return false;
  }
}

export function pushEnabledLocally() {
  return (
    localStorage.getItem("ab_push_enabled") === "1" &&
    typeof Notification !== "undefined" &&
    Notification.permission === "granted"
  );
}

export async function enablePush() {
  if (typeof Notification === "undefined") return { ok: false, reason: "unsupported" };
  if (!(await isSupported().catch(() => false))) return { ok: false, reason: "unsupported" };
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return { ok: false, reason: "denied" };
  const { config, vapidKey } = await loadConfig();
  ensureApp(config);
  const reg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
  const token = await getToken(_msg, { vapidKey, serviceWorkerRegistration: reg });
  if (!token) return { ok: false, reason: "notoken" };
  await axios.post(
    `${API}/push/register`,
    { token, platform: "web" },
    { headers: { "X-Device-Id": getDeviceId() } }
  );
  localStorage.setItem("ab_push_enabled", "1");
  return { ok: true };
}
