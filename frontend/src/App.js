import { ChatGPTConnection } from "@/components/ChatGPTConnection";
import { useEffect, useState, useCallback } from "react";
import "@/App.css";
import { AnimatePresence, motion } from "framer-motion";
import { Gavel, Plus, Radar, Sparkles, Wallet, Layers, TrendingDown, Loader2, Bell, BellRing, Search } from "lucide-react";
import { toast, Toaster } from "sonner";
import { Button } from "@/components/ui/button";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import AuctionCard from "@/components/AuctionCard";
import AddAuctionModal from "@/components/AddAuctionModal";
import AuctionDetail from "@/components/AuctionDetail";
import LiveFeed from "@/components/LiveFeed";
import KeywordWatches from "@/components/KeywordWatches";
import { AccountNamePanel, RestoreByNamePanel } from "@/components/AccountTools";
import { api } from "@/lib/api";
import { SOURCES, formatSEK } from "@/lib/sources";
import { PushProvider, usePush } from "@/context/push";

const NotifButton = () => {
  const push = usePush();
  const on = push?.canWatch;
  return (
    <button
      onClick={() => push?.openGate()}
      data-testid="notif-button"
      title={on ? "Notiser aktiva" : "Aktivera bevakning & notiser"}
      className={`flex h-9 shrink-0 items-center gap-1.5 rounded-xl border px-2.5 text-sm font-semibold transition-colors ${on ? "border-emerald-700/50 bg-emerald-500/10 text-emerald-400" : "border-slate-800 bg-[#111827] text-slate-300 hover:text-amber-400"}`}
    >
      {on ? <BellRing className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
      <span className="hidden sm:inline">{on ? "Notiser på" : "Notiser"}</span>
    </button>
  );
};

const StatCard = ({ icon: Icon, label, value, color }) => (
  <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-[#111827] p-4">
    <div className={`flex h-11 w-11 items-center justify-center rounded-lg ${color}`}>
      <Icon className="h-5 w-5" />
    </div>
    <div>
      <p className="text-[11px] font-mono uppercase tracking-wider text-slate-500">{label}</p>
      <p className="font-heading text-xl font-bold text-slate-100">{value}</p>
    </div>
  </div>
);

function App() {
  const [auctions, setAuctions] = useState([]);
  const [stats, setStats] = useState({ total: 0, total_value: 0, sources_tracked: 0, good_deals: 0 });
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("Alla");
  const [addOpen, setAddOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState("live");
  const [kwUnseen, setKwUnseen] = useState(0);

  useEffect(() => { document.documentElement.classList.add("dark"); }, []);

  const load = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([
        api.listAuctions(filter === "Alla" ? null : filter),
        api.stats(),
      ]);
      setAuctions(a);
      setStats(s);
    } catch (e) {
      toast.error("Kunde inte hämta bevakningar");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (view === "watch") load(); }, [view, load]);

  const loadKwUnseen = useCallback(async () => {
    try {
      const list = await api.kwList();
      setKwUnseen(list.reduce((s, w) => s + (w.unseen_count || 0), 0));
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    loadKwUnseen();
    const t = setInterval(loadKwUnseen, 60000);
    return () => clearInterval(t);
  }, [loadKwUnseen]);

  const handleAdd = async (payload) => {
    setSubmitting(true);
    try {
      await api.createAuction(payload);
      toast.success("Bevakning tillagd");
      setAddOpen(false);
      await load();
      return true;
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte lägga till bevakning");
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (a) => {
    try {
      await api.remove(a.id);
      setAuctions((prev) => prev.filter((x) => x.id !== a.id));
      toast.success("Bevakning borttagen");
      api.stats().then(setStats);
    } catch {
      toast.error("Kunde inte ta bort");
    }
  };

  const openDetail = (a) => { setSelected(a); setDetailOpen(true); };
  const openLiveDetail = (a) => openDetail({ ...a, scraped: true });

  const handleAnalyze = async (a) => {
    setAnalyzing(true);
    try {
      if (a.id) {
        const updated = await api.analyze(a.id);
        setSelected(updated);
        setAuctions((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
        api.stats().then(setStats);
      } else {
        const res = await api.liveAnalyze(a.url);
        setSelected((prev) => ({ ...(prev || a), valuation: res.valuation }));
      }
      toast.success("AI-värdering klar");
    } catch (e) {
      toast.error(e?.response?.data?.detail || "AI-analys misslyckades");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleRefresh = async (a) => {
    if (!a.id) { toast.info("Bevaka objektet för att följa budhistoriken."); return; }
    setRefreshing(true);
    try {
      const updated = await api.refresh(a.id);
      setSelected(updated);
      setAuctions((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
      toast.success("Bud uppdaterat");
    } catch {
      toast.error("Kunde inte uppdatera");
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="App min-h-screen bg-[#0B0F19] text-slate-100">
      <Toaster theme="dark" position="top-right" richColors />

      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-900/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-3 px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500 text-slate-950">
              <Gavel className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate font-heading text-base font-extrabold leading-none tracking-tight sm:text-lg">Auktiomera<span className="text-amber-400">.se</span></h1>
              <p className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                </span>
                Övervakar {SOURCES.length} källor
              </p>
            </div>
          </div>
          <ChatGPTConnection />
          <NotifButton />
          <div className="order-last flex w-full rounded-xl border border-slate-800 bg-[#111827] p-1 sm:order-none sm:w-auto" data-testid="view-toggle">
            <button
              onClick={() => setView("live")}
              data-testid="view-live"
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-colors sm:flex-none sm:px-4 ${view === "live" ? "bg-amber-500 text-slate-950" : "text-slate-400 hover:text-slate-200"}`}
            >
              <Radar className="h-4 w-4 shrink-0" /> <span className="hidden sm:inline">Live-flöde</span><span className="sm:hidden">Live</span>
            </button>
            <button
              onClick={() => setView("search")}
              data-testid="view-search"
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-colors sm:flex-none sm:px-4 ${view === "search" ? "bg-amber-500 text-slate-950" : "text-slate-400 hover:text-slate-200"}`}
            >
              <Search className="h-4 w-4 shrink-0" /> <span className="hidden sm:inline">Sökbevakning</span><span className="sm:hidden">Sök</span>
              {kwUnseen > 0 && (
                <span data-testid="kw-unseen-badge" className={`rounded-full px-1.5 text-xs font-bold ${view === "search" ? "bg-slate-900/40 text-slate-950" : "bg-rose-500 text-white"}`}>
                  {kwUnseen}
                </span>
              )}
            </button>
            <button
              onClick={() => setView("watch")}
              data-testid="view-watch"
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-colors sm:flex-none sm:px-4 ${view === "watch" ? "bg-amber-500 text-slate-950" : "text-slate-400 hover:text-slate-200"}`}
            >
              <Layers className="h-4 w-4 shrink-0" /> <span className="hidden sm:inline">Mina bevakningar</span><span className="sm:hidden">Bevakning</span> {stats.total > 0 && <span className={`rounded-full px-1.5 text-xs ${view === "watch" ? "bg-slate-900/40 text-slate-950" : "bg-slate-800 text-slate-300"}`}>{stats.total}</span>}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {view === "live" ? (
          <LiveFeed onWatchAdded={load} onOpen={openLiveDetail} />
        ) : view === "search" ? (
          <KeywordWatches onWatchAdded={load} onOpen={openLiveDetail} onUnseenChange={loadKwUnseen} />
        ) : (
        <>
        {/* Stats */}
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard icon={Layers} label="Bevakningar" value={stats.total} color="bg-amber-500/15 text-amber-400" />
          <StatCard icon={Wallet} label="Totalt värde" value={formatSEK(stats.total_value)} color="bg-blue-500/15 text-blue-400" />
          <StatCard icon={Radar} label="Aktiva källor" value={stats.sources_tracked} color="bg-emerald-500/15 text-emerald-400" />
          <StatCard icon={TrendingDown} label="Bra köplägen" value={stats.good_deals} color="bg-violet-500/15 text-violet-400" />
        </div>

        {/* Filter */}
        <div className="mb-5 flex items-center justify-between gap-3">
          <h2 className="font-heading text-xl font-bold">Dina bevakningar</h2>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger data-testid="source-filter" className="w-44 border-slate-700 bg-slate-900 text-slate-100">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-slate-700 bg-slate-900 text-slate-100">
              <SelectItem value="Alla">Alla källor</SelectItem>
              {SOURCES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {/* Account name (password-less backup) – shown when you have watches */}
        {auctions.length > 0 && <AccountNamePanel />}

        {/* Grid */}
        {loading ? (
          <div className="flex h-64 items-center justify-center text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : auctions.length === 0 ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 py-20 text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-800/60">
              <Sparkles className="h-7 w-7 text-amber-400" />
            </div>
            <h3 className="font-heading text-xl font-bold">Inga bevakningar ännu</h3>
            <p className="mt-1 max-w-sm text-sm text-slate-400">Lägg till din första auktion via URL eller sökord så hämtar vi budhistorik och AI-värdering.</p>
            <Button onClick={() => setAddOpen(true)} data-testid="empty-add-button" className="mt-5 bg-amber-500 text-slate-950 hover:bg-amber-600">
              <Plus className="mr-1.5 h-4 w-4" /> Bevaka första auktionen
            </Button>
            <RestoreByNamePanel onRestored={() => { load(); loadKwUnseen(); }} />
          </motion.div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" data-testid="auctions-grid">
            <AnimatePresence mode="popLayout">
              {auctions.map((a) => (
                <AuctionCard key={a.id} auction={a} onOpen={openDetail} onDelete={handleDelete} />
              ))}
            </AnimatePresence>
          </div>
        )}
        </>
        )}
      </main>

      <AddAuctionModal open={addOpen} onOpenChange={setAddOpen} onSubmit={handleAdd} submitting={submitting} />
      <AuctionDetail
        auction={selected}
        open={detailOpen}
        onOpenChange={setDetailOpen}
        onAnalyze={handleAnalyze}
        onRefresh={handleRefresh}
        analyzing={analyzing}
        refreshing={refreshing}
      />
    </div>
  );
}

export default function AppWithProviders() {
  return (
    <PushProvider>
      <App />
    </PushProvider>
  );
}
