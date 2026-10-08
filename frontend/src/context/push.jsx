import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Button } from "../components/ui/button";
import { Check, Download, Bell, Smartphone, Share, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { enablePush, isStandalone, isIOS, pushEnabledLocally, initMessaging } from "../lib/push";
import { api } from "../lib/api";

const PushCtx = createContext(null);
export const usePush = () => useContext(PushCtx);

export const PushProvider = ({ children }) => {
  const [installed, setInstalled] = useState(isStandalone());
  const [registered, setRegistered] = useState(pushEnabledLocally());
  const [deferred, setDeferred] = useState(null);
  const [gateOpen, setGateOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    initMessaging((payload) => {
      const n = payload?.notification || payload?.data || {};
      if (n.title || n.body) toast(n.title || "Auktiomera", { description: n.body });
    });
    const onPrompt = (e) => { e.preventDefault(); setDeferred(e); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    const mq = window.matchMedia("(display-mode: standalone)");
    const onMode = () => setInstalled(isStandalone());
    mq.addEventListener?.("change", onMode);
    window.addEventListener("appinstalled", onMode);
    api.pushStatus().then((s) => { if (s?.registered) setRegistered(true); }).catch(() => {});
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      mq.removeEventListener?.("change", onMode);
      window.removeEventListener("appinstalled", onMode);
    };
  }, []);

  const canWatch = installed && registered;

  const handleInstall = async () => {
    if (!deferred) return;
    deferred.prompt();
    const { outcome } = await deferred.userChoice;
    if (outcome === "accepted") toast.success("Appen installeras…");
    setDeferred(null);
  };

  const handleEnable = async () => {
    setBusy(true);
    try {
      const res = await enablePush();
      if (res.ok) {
        setRegistered(true);
        toast.success("Notiser aktiverade");
      } else if (res.reason === "denied") {
        toast.error("Notiser nekades. Tillåt notiser i webbläsarinställningarna.");
      } else {
        toast.error("Kunde inte aktivera notiser på den här enheten.");
      }
    } catch {
      toast.error("Kunde inte aktivera notiser.");
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const res = await api.pushTest();
      if (res?.sent) toast.success("Testnotis skickad – kolla din enhet");
      else if (res?.reason === "disabled") toast.warning("Push-sändning är avstängd i denna miljö");
      else toast.error("Kunde inte skicka testnotis.");
    } catch {
      toast.error("Kunde inte skicka testnotis.");
    } finally {
      setTesting(false);
    }
  };

  // Returns true if allowed to watch now; otherwise opens the gate and returns false.
  const requireSetup = useCallback(() => {
    if (installed && registered) return true;
    setGateOpen(true);
    return false;
  }, [installed, registered]);

  return (
    <PushCtx.Provider value={{ installed, registered, canWatch, requireSetup, openGate: () => setGateOpen(true) }}>
      {children}
      <Dialog open={gateOpen} onOpenChange={setGateOpen}>
        <DialogContent className="border-slate-800 bg-[#0B0F19] text-slate-100 sm:max-w-md" data-testid="push-gate-modal">
          <DialogHeader>
            <DialogTitle className="font-heading text-xl">Aktivera bevakning</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-400">
            För att bevaka auktioner installerar du appen på din enhet och aktiverar pushnotiser. Inget konto behövs – din bevakningslista är personlig för just den här enheten.
          </p>

          <Step done={installed} icon={Smartphone} title="1. Installera appen">
            {installed ? (
              <span className="text-sm text-emerald-400">Appen är installerad.</span>
            ) : deferred ? (
              <Button onClick={handleInstall} data-testid="install-app-btn" className="bg-amber-500 text-slate-950 hover:bg-amber-400">
                <Download className="mr-1.5 h-4 w-4" /> Installera appen
              </Button>
            ) : isIOS() ? (
              <p className="text-xs text-slate-400">
                I Safari: tryck på <Share className="inline h-3.5 w-3.5" /> Dela och välj <b>Lägg till på hemskärmen</b>. Öppna sedan appen från hemskärmen.
              </p>
            ) : (
              <p className="text-xs text-slate-400">
                Öppna webbläsarens meny och välj <b>Installera app</b> / <b>Lägg till på hemskärmen</b>, öppna sedan appen därifrån.
              </p>
            )}
          </Step>

          <Step done={registered} icon={Bell} title="2. Aktivera pushnotiser">
            {registered ? (
              <span className="text-sm text-emerald-400">Notiser är aktiverade.</span>
            ) : (
              <Button onClick={handleEnable} disabled={busy} data-testid="enable-push-btn" className="bg-amber-500 text-slate-950 hover:bg-amber-400">
                {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Bell className="mr-1.5 h-4 w-4" />} Aktivera notiser
              </Button>
            )}
          </Step>

          {canWatch && (
            <div className="rounded-lg border border-emerald-700/40 bg-emerald-500/10 p-3 text-sm text-emerald-300">
              <p>Klart! Du kan nu bevaka auktioner på den här enheten.</p>
              <Button onClick={handleTest} disabled={testing} variant="outline" data-testid="send-test-push-btn"
                className="mt-3 w-full border-emerald-600/40 bg-transparent text-emerald-200 hover:bg-emerald-500/10">
                {testing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Bell className="mr-1.5 h-4 w-4" />} Skicka testnotis
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </PushCtx.Provider>
  );
};

const Step = ({ done, icon: Icon, title, children }) => (
  <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <div className="mb-2 flex items-center gap-2">
      <span className={`flex h-7 w-7 items-center justify-center rounded-full ${done ? "bg-emerald-500 text-slate-950" : "bg-slate-800 text-amber-400"}`}>
        {done ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
      </span>
      <h4 className="font-semibold text-slate-100">{title}</h4>
    </div>
    <div className="pl-9">{children}</div>
  </div>
);
