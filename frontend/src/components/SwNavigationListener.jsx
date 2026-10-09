import { useEffect } from "react";
import { appLog, bootInfo, clearPendingNav, consumePendingNav, flushDiag, registerSW } from "@/lib/push";

// Delivers push deep-links (postMessage, pending-nav cache, initial URL) to onNavigate(path).
export const SwNavigationListener = ({ onNavigate }) => {
  useEffect(() => {
    const go = (path) => { if (path) onNavigate(path); };
    const consume = async () => {
      const path = await consumePendingNav();
      if (path) { go(path); flushDiag(); }
    };
    const onMsg = (e) => {
      if (e.data?.type !== "NAVIGATE") return;
      const ack = { type: "NAVIGATE_ACK" };
      try { e.source?.postMessage(ack); } catch { /* ignore */ }
      try { navigator.serviceWorker.controller?.postMessage(ack); } catch { /* ignore */ }
      clearPendingNav();
      appLog("app_navigate_msg", { path: e.data.path });
      go(e.data.path);
      flushDiag();
    };
    const onLocal = (e) => go(e.detail);
    const onVis = () => { if (document.visibilityState === "visible") consume(); };

    if (window.location.search) go(window.location.pathname + window.location.search);
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      registerSW().catch(() => {});
    }
    navigator.serviceWorker?.addEventListener("message", onMsg);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("focus", consume);
    window.addEventListener("app-navigate", onLocal);
    appLog("app_boot", bootInfo());
    consume().finally(flushDiag);
    return () => {
      navigator.serviceWorker?.removeEventListener("message", onMsg);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("focus", consume);
      window.removeEventListener("app-navigate", onLocal);
    };
  }, [onNavigate]);
  return null;
};

export default SwNavigationListener;
