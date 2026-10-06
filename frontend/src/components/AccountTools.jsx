import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

const field =
  "flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-amber-500 focus:outline-none";
const pinField = field.replace("flex-1", "w-full sm:w-32");
const only4 = (v) => v.replace(/\D/g, "").slice(0, 4);

// Set/claim a personal account name (+ optional 4-digit PIN) for restoring on a
// new device. Collapses to a compact row once an account exists.
export const AccountNamePanel = () => {
  const [name, setName] = useState("");
  const [savedName, setSavedName] = useState("");
  const [pin, setPin] = useState("");
  const [hasPin, setHasPin] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getAccountName()
      .then((d) => { setName(d.name || ""); setSavedName(d.name || ""); setHasPin(!!d.has_pin); setOpen(!d.name); })
      .catch(() => {});
  }, []);

  const save = async () => {
    const n = name.trim();
    if (n.length < 2) { toast.error("Ange minst 2 tecken"); return; }
    const p = pin.trim();
    if (p && !/^\d{4}$/.test(p)) { toast.error("PIN-koden måste vara 4 siffror"); return; }
    setSaving(true);
    try {
      const res = await api.setAccountName(n, p);
      setHasPin(!!res.has_pin);
      setPin("");
      setSavedName(n);
      setOpen(false);
      toast.success("Kontonamn sparat - använd namn" + (res.has_pin ? " + PIN" : "") + " för att återställa på en ny enhet");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte spara kontonamnet");
    } finally {
      setSaving(false);
    }
  };

  if (savedName && !open) {
    return (
      <div className="mb-5 flex items-center justify-between gap-2 rounded-xl border border-slate-800 bg-[#111827] px-4 py-3" data-testid="account-name-summary">
        <p className="truncate text-sm text-slate-300">
          Konto: <span className="font-semibold text-slate-100">{savedName}</span>
          {hasPin && <span className="ml-1.5 text-emerald-400">🔒</span>}
        </p>
        <button onClick={() => setOpen(true)} data-testid="account-name-edit" className="shrink-0 text-sm font-semibold text-amber-400 hover:text-amber-300">
          Ändra
        </button>
      </div>
    );
  }

  return (
    <div className="mb-5 rounded-xl border border-slate-800 bg-[#111827] p-4" data-testid="account-name-panel">
      <p className="text-sm font-semibold text-slate-200">Ditt kontonamn</p>
      <p className="mt-1 text-xs text-slate-400">Välj ett namn och (valfritt) en 4-siffrig PIN så kan du återställa alla dina bevakningar på en ny enhet.</p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="t.ex. janne-mopeder" data-testid="account-name-input" className={field} />
        <input value={pin} onChange={(e) => setPin(only4(e.target.value))} inputMode="numeric" maxLength={4} placeholder={hasPin ? "PIN (satt)" : "PIN (valfri)"} data-testid="account-pin-input" className={pinField} />
        <Button onClick={save} disabled={saving} data-testid="account-name-save" className="bg-slate-700 text-slate-100 hover:bg-slate-600">
          {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
          Spara namn
        </Button>
      </div>
      {hasPin && <p className="mt-1.5 text-[11px] text-emerald-400">🔒 Skyddat med PIN-kod</p>}
      {savedName && (
        <button onClick={() => { setOpen(false); setName(savedName); setPin(""); }} data-testid="account-name-cancel" className="mt-2 text-xs text-slate-500 hover:text-slate-300">
          Avbryt
        </button>
      )}
    </div>
  );
};

// Restore watches by typing the account name (+ PIN if protected). Shown in the
// empty state. Calls onRestored() so the parent can reload its lists.
export const RestoreByNamePanel = ({ onRestored }) => {
  const [name, setName] = useState("");
  const [pin, setPin] = useState("");
  const [restoring, setRestoring] = useState(false);

  const restore = async () => {
    const n = name.trim();
    if (n.length < 2) { toast.error("Skriv ditt kontonamn"); return; }
    const p = pin.trim();
    if (p && !/^\d{4}$/.test(p)) { toast.error("PIN-koden måste vara 4 siffror"); return; }
    setRestoring(true);
    try {
      const res = await api.restoreByName(n, p);
      if (!res.found) { toast.error("Hittade inget konto med det namnet"); return; }
      toast.success(`Hämtade ${res.watches} bevakningar och ${res.keywords} sökbevakningar`);
      setName("");
      setPin("");
      onRestored?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte återställa - försök igen");
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div className="mt-8 w-full max-w-sm border-t border-slate-800 pt-6">
      <p className="text-sm text-slate-400">eller skriv ditt namn om du redan har bevakningar</p>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ditt kontonamn" data-testid="restore-name-input" className={field} />
        <input value={pin} onChange={(e) => setPin(only4(e.target.value))} inputMode="numeric" maxLength={4} placeholder="PIN (om satt)" data-testid="restore-pin-input" className={pinField} />
        <Button onClick={restore} disabled={restoring} data-testid="restore-name-button" className="bg-amber-500 text-slate-950 hover:bg-amber-600">
          {restoring ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
          Hämta
        </Button>
      </div>
    </div>
  );
};
