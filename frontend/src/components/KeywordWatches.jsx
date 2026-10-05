import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Search, Plus, Lock, Loader2, Trash2, BellRing, Radar, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { usePush } from "@/context/push";
import { LiveCard } from "@/components/LiveFeed";

const fmtWhen = (iso) => {
  if (!iso) return "–";
  try {
    return new Date(iso).toLocaleString("sv-SE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "–";
  }
};

export const KeywordWatches = ({ onWatchAdded, onOpen, onUnseenChange }) => {
  const push = usePush();
  const locked = !push?.canWatch;
  const [watches, setWatches] = useState([]);
  const [keyword, setKeyword] = useState("");
  const [adding, setAdding] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null); // { id, keyword, items, last_check }
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [watching, setWatching] = useState(null);

  const loadList = useCallback(async () => {
    try {
      setWatches(await api.kwList());
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  const openWatch = async (w) => {
    setLoadingDetail(true);
    setSelected({ id: w.id, keyword: w.keyword, items: [], last_check: w.last_check });
    try {
      const d = await api.kwGet(w.id);
      setSelected(d);
      setWatches((prev) => prev.map((x) => (x.id === w.id ? { ...x, unseen_count: 0 } : x)));
      onUnseenChange?.();
    } catch {
      toast.error("Kunde inte hämta träffar");
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleAdd = async () => {
    const kw = keyword.trim();
    if (kw.length < 2) { toast.error("Ange minst 2 tecken"); return; }
    if (!push?.requireSetup()) return;
    setAdding(true);
    try {
      const res = await api.kwCreate(kw);
      toast.success(`Sökbevakning "${kw}" skapad · ${res.baseline} träffar registrerade som "redan sett"`);
      setKeyword("");
      await loadList();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte skapa sökbevakning");
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (w) => {
    try {
      await api.kwDelete(w.id);
      setWatches((prev) => prev.filter((x) => x.id !== w.id));
      if (selected?.id === w.id) setSelected(null);
      toast.success("Sökbevakning borttagen");
      onUnseenChange?.();
    } catch {
      toast.error("Kunde inte ta bort");
    }
  };

  const handleWatchItem = async (a) => {
    if (!push?.requireSetup()) return;
    setWatching(a.url);
    try {
      await api.liveWatch(a.url);
      toast.success(`Bevakar pris på "${a.title.slice(0, 40)}"`);
      onWatchAdded?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte bevaka");
    } finally {
      setWatching(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="keyword-watches-view">
      {/* Intro + add */}
      <div className="rounded-2xl border border-slate-800 bg-[#111827] p-5">
        <div className="flex items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-500/15 text-violet-300">
            <Search className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h2 className="font-heading text-xl font-bold">Sökbevakning</h2>
            <p className="mt-0.5 text-sm text-slate-400">
              Bevaka ett sökord (t.ex. <span className="text-slate-200">aerox</span>) i alla 10 system. Var 4:e timme kollar vi om något <span className="text-slate-200">nytt</span> dykt upp sedan du startade – och pushar dig direkt. Öppna sedan träffen och lägg till prisbevakning.
            </p>
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              placeholder="Skriv ett sökord att bevaka…"
              data-testid="keyword-watch-input"
              className="border-slate-700 bg-slate-900 pl-9 text-slate-100"
            />
          </div>
          <Button
            onClick={handleAdd}
            disabled={adding}
            data-testid="keyword-watch-add-button"
            className="bg-amber-500 text-slate-950 hover:bg-amber-600"
          >
            {adding ? <Loader2 className="h-4 w-4 animate-spin" /> : locked ? <Lock className="mr-1 h-4 w-4" /> : <Plus className="mr-1 h-4 w-4" />}
            Bevaka
          </Button>
        </div>
        {locked && (
          <p className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-400/80">
            <BellRing className="h-3.5 w-3.5" /> Installera appen och aktivera notiser för att kunna sökbevaka.
          </p>
        )}
      </div>

      {/* Watches list */}
      {loading ? (
        <div className="flex h-32 items-center justify-center text-slate-500"><Loader2 className="h-6 w-6 animate-spin" /></div>
      ) : watches.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-800 py-16 text-center">
          <Radar className="mx-auto mb-3 h-7 w-7 text-violet-400" />
          <p className="font-heading text-lg font-bold">Inga sökbevakningar ännu</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-slate-400">Lägg till ett sökord ovan så notifierar vi dig när nya objekt dyker upp i något av systemen.</p>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="keyword-watch-list">
          {watches.map((w) => (
            <motion.div
              key={w.id}
              layout
              onClick={() => openWatch(w)}
              data-testid={`keyword-watch-row-${w.id}`}
              className={`group cursor-pointer rounded-xl border p-4 transition-colors ${selected?.id === w.id ? "border-violet-500/50 bg-violet-500/5" : "border-slate-800 bg-[#111827] hover:border-slate-700"}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-heading text-base font-bold text-slate-100">{w.keyword}</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Senast kollad {fmtWhen(w.last_check)}</p>
                </div>
                <div className="flex items-center gap-1">
                  {w.unseen_count > 0 && (
                    <span data-testid={`keyword-watch-unseen-${w.id}`} className="rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-bold text-slate-950">
                      {w.unseen_count} nya
                    </span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(w); }}
                    data-testid={`keyword-watch-delete-${w.id}`}
                    title="Ta bort"
                    className="rounded-md p-1 text-slate-500 opacity-0 transition hover:bg-slate-800 hover:text-red-400 group-hover:opacity-100"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <p className="mt-2 text-[11px] text-slate-400">
                {w.match_count > 0 ? `${w.match_count} nya träffar sedan start` : "Inga nya träffar ännu"}
              </p>
            </motion.div>
          ))}
        </div>
      )}

      {/* Selected watch matches */}
      {selected && (
        <div className="space-y-3" data-testid="keyword-watch-detail">
          <div className="flex items-center justify-between">
            <h3 className="font-heading text-lg font-bold">
              Nya träffar för <span className="text-violet-300">"{selected.keyword}"</span>
            </h3>
            <Button size="sm" variant="outline" onClick={() => openWatch(selected)} disabled={loadingDetail} className="h-8 border-slate-700 text-slate-300 hover:bg-slate-800">
              {loadingDetail ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </Button>
          </div>
          {loadingDetail ? (
            <div className="flex h-32 items-center justify-center text-slate-500"><Loader2 className="h-6 w-6 animate-spin" /></div>
          ) : (selected.items || []).length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 py-12 text-center text-sm text-slate-400">
              Inga nya träffar sedan du startade bevakningen. Vi pushar så snart något nytt dyker upp.
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" data-testid="keyword-watch-matches">
              {selected.items.map((a) => (
                <LiveCard key={a.url} a={a} onWatch={handleWatchItem} watching={watching} locked={locked} onOpen={onOpen} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default KeywordWatches;
