import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";

export const ChatGPTConnection = () => {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [welcome, setWelcome] = useState(false);
  const announced = useRef(false);
  const pendingVersion = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.chatgptStatus();
      setStatus(data);
      if (data.connected && !data.welcome_seen && !announced.current) {
        announced.current = true;
        setWelcome(true);
      }
      if ((data.connected && data.connected_at !== pendingVersion.current) || data.error) setPending(false);
    } catch { /* Retry when the panel opens or the window regains focus. */ }
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);
  useEffect(() => {
    if (!open && !pending) return;
    const timer = setInterval(refresh, pending ? 2000 : 10000);
    return () => clearInterval(timer);
  }, [open, pending, refresh]);

  const connect = async (newAccount = false) => {
    const popup = window.open("", "_blank");
    if (!popup) { toast.error("Tillåt popup-fönster för att logga in med ChatGPT."); return; }
    popup.opener = null;
    setBusy(true);
    try {
      const result = await api.chatgptConnect(newAccount);
      popup.location.href = result.url;
      pendingVersion.current = status?.connected_at;
      setPending(true);
    } catch (error) {
      popup.close();
      toast.error(error?.response?.data?.detail || "Kunde inte starta ChatGPT-inloggningen.");
    } finally { setBusy(false); }
  };

  const change = async (settings) => {
    setBusy(true);
    try { setStatus(await api.chatgptSettings(settings)); }
    catch (error) { toast.error(error?.response?.data?.detail || "Kunde inte spara inställningen."); }
    finally { setBusy(false); }
  };

  const reloadModels = async () => {
    setBusy(true);
    try { setStatus(await api.chatgptModels()); }
    catch (error) { toast.error(error?.response?.data?.detail || "Kunde inte hämta modeller."); }
    finally { setBusy(false); }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      const result = await api.chatgptDisconnect();
      await refresh();
      toast.success("ChatGPT är frånkopplat på den här enheten.");
      if (!result.revocation_confirmed) toast.info("Återkallelsen kunde inte bekräftas. Koppla även från appen i ChatGPT-inställningarna.");
    } catch { toast.error("Kunde inte koppla från ChatGPT."); }
    finally { setBusy(false); }
  };

  const dismissWelcome = () => {
    setWelcome(false);
    change({ welcome_seen: true });
  };

  if (!status?.available) return null;
  const selectClass = "mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 p-2 text-sm text-slate-100";
  return (
    <>
      <Button variant="outline" onClick={() => { setOpen(true); refresh(); }} data-testid="chatgpt-settings-button"
        className="border-slate-700 text-slate-200 hover:bg-slate-800">
        <Sparkles className="mr-1.5 h-4 w-4" /> ChatGPT{status.connected ? " ✓" : ""}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="border-slate-800 bg-[#0B0F19] text-slate-100 sm:max-w-md" data-testid="chatgpt-panel">
          <DialogHeader>
            <DialogTitle>AI-värdering med ChatGPT</DialogTitle>
            <DialogDescription className="text-slate-400">
              Använd din ChatGPT-plan för AI-värderingar i den lokala appen. Kräver ett berättigat Plus- eller Pro-konto.
            </DialogDescription>
          </DialogHeader>
          {status.accounts?.length > 0 && (
            <label className="text-sm text-slate-300">ChatGPT-konto
              <select aria-label="ChatGPT-konto" value={status.active_account || ""} disabled={busy}
                onChange={(e) => change({ account: e.target.value })} className={selectClass}>
                {status.accounts.map((account) => <option key={account.id} value={account.id}>{account.label}</option>)}
              </select>
            </label>
          )}
          {status.connected ? (
            <>
              <p className="text-sm text-emerald-400" data-testid="chatgpt-connected">Använder din ChatGPT-plan</p>
              <label className="text-sm text-slate-300">Modell för AI-värdering
                <select aria-label="ChatGPT-modell" value={status.model || ""} disabled={busy}
                  onChange={(e) => change({ model: e.target.value })} className={selectClass}>
                  {!status.models?.length && <option value="">Inga tillgängliga modeller</option>}
                  {status.models?.map((model) => <option key={model.slug} value={model.slug}>{model.display_name}</option>)}
                </select>
              </label>
              <button onClick={reloadModels} disabled={busy} className="text-left text-sm text-amber-400 hover:underline">Uppdatera tillgängliga modeller</button>
              <a href={status.usage_url} target="_blank" rel="noreferrer" className="text-sm text-amber-400 underline">Hantera användning i ChatGPT</a>
              <Button variant="outline" onClick={disconnect} disabled={busy} className="border-slate-700 text-slate-200 hover:bg-slate-800">Koppla från detta konto</Button>
            </>
          ) : (
            <Button onClick={() => connect()} disabled={busy || pending} data-testid="chatgpt-connect-button" className="bg-white text-black hover:bg-slate-200">
              {busy || pending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {pending ? "Väntar på inloggning…" : "Continue with ChatGPT"}
            </Button>
          )}
          {status.accounts?.length > 0 && <button onClick={() => connect(true)} disabled={busy || pending} className="text-left text-sm text-amber-400 hover:underline">Lägg till ett annat ChatGPT-konto</button>}
          {pending && <Button variant="ghost" onClick={() => setPending(false)}>Avbryt väntan</Button>}
          {status.error && <p role="alert" className="text-sm text-rose-300">{status.error}</p>}
          <p className="text-xs text-slate-500">Inloggningen sker hos OpenAI. Åtkomstuppgifterna sparas lokalt i backend. Du kan koppla från appen i ChatGPT-inställningarna.</p>
        </DialogContent>
      </Dialog>
      <Dialog open={welcome} onOpenChange={(value) => { if (!value) dismissWelcome(); }}>
        <DialogContent className="border-slate-800 bg-[#0B0F19] text-slate-100 sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Du använder din ChatGPT-plan</DialogTitle>
            <DialogDescription className="text-slate-400">AI-värderingar i Auktiomera använder nu din ChatGPT-plan eller dina tillgängliga krediter. Hantera användningen och appens åtkomst i ChatGPT-inställningarna.</DialogDescription>
          </DialogHeader>
          <Button onClick={dismissWelcome}>Uppfattat</Button>
        </DialogContent>
      </Dialog>
    </>
  );
};
