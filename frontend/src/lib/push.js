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
  const reg = await registerSW();
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

// Bump on EVERY change to public/firebase-messaging-sw.js (CDN caches public/ files).
export const SW_REV = 2;

function bundleHash() {
  const s = [...document.scripts].map((x) => x.src).find((src) => /\/static\/js\/main\.[^.]+\.js/.test(src));
  return s ? s.match(/main\.([^.]+)\.js/)[1] : "dev";
}

export function swUrl() {
  return `/firebase-messaging-sw.js?v=${bundleHash()}-r${SW_REV}`;
}

export async function registerSW() {
  if (!("serviceWorker" in navigator)) return null;
  return navigator.serviceWorker.register(swUrl(), { updateViaCache: "none" });
}

const NAV_CACHE = "pending-nav-v1";
const NAV_KEY = "/__pending_nav__";
const NAV_TTL = 60000;

export async function clearPendingNav() {
  if (!window.caches) return;
  try { await (await caches.open(NAV_CACHE)).delete(NAV_KEY); } catch { /* ignore */ }
}

export async function consumePendingNav() {
  if (!window.caches) return null;
  try {
    const c = await caches.open(NAV_CACHE);
    const r = await c.match(NAV_KEY);
    if (!r) return null;
    await c.delete(NAV_KEY);
    const { path, ts } = await r.json();
    const fresh = Date.now() - ts < NAV_TTL;
    await appLog("app_pending_nav", { path, fresh });
    return fresh ? path : null;
  } catch {
    return null;
  }
}

const _appLogs = [];
export async function appLog(event, extra) {
  _appLogs.push({ ts: Date.now(), src: "app", event, ...(extra || {}) });
}

export async function flushDiag() {
  let swLogs = [];
  try {
    if (window.caches) {
      const c = await caches.open("sw-telemetry-v1");
      const r = await c.match("/__sw_log__");
      if (r) { swLogs = await r.json(); await c.delete("/__sw_log__"); }
    }
  } catch { /* ignore */ }
  const logs = [...swLogs.map((l) => ({ ...l, src: "sw" })), ..._appLogs.splice(0)];
  if (!logs.length) return;
  try {
    await axios.post(`${API}/push/diag`, { logs }, { headers: { "X-Device-Id": getDeviceId() } });
  } catch { /* ignore */ }
}

export function bootInfo() {
  return {
    bundle: bundleHash(),
    sw: navigator.serviceWorker?.controller?.scriptURL || null,
    permission: typeof Notification !== "undefined" ? Notification.permission : "unsupported",
    standalone: isStandalone(),
  };
}
