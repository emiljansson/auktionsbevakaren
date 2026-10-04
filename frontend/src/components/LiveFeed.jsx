import { useEffect, useState, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { RefreshCw, Radar, ExternalLink, Plus, Search, Activity, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { api } from "@/lib/api";
import { badgeClass, formatSEK, timeLeft } from "@/lib/sources";

const STATUS_CHIP = {
  active: { cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30", label: "Aktiv" },
  needs_key: { cls: "bg-amber-500/15 text-amber-400 border-amber-500/30", label: "Kräver nyckel" },
  blocked: { cls: "bg-red-500/15 text-red-400 border-red-500/30", label: "Blockerad" },
  planned: { cls: "bg-slate-500/15 text-slate-400 border-slate-600/30", label: "Planerad" },
};

const LiveCard = ({ a, onWatch, watching }) => {
  const tl = timeLeft(a.ends_at);
  return (
  <motion.div
    layout
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    data-testid={`live-card-${encodeURIComponent(a.url).slice(-16)}`}
    className="group flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#111827] transition-colors hover:border-slate-700"
  >
    <div className="relative h-36 overflow-hidden bg-slate-900">
      <img src={a.image} alt={a.title} className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
        onError={(e) => { e.target.src = "https://images.unsplash.com/photo-1603816885871-c122a9ff8d40?w=600&q=80"; }} />
      <div className="absolute inset-0 bg-gradient-to-t from-[#111827] to-transparent" />
      <Badge className={`absolute left-2 top-2 border font-mono text-[10px] ${badgeClass(a.source)}`} style={{ backgroundColor: "rgba(11,15,25,0.65)" }}>{a.source}</Badge>
      {tl && (
        <div className={`absolute right-2 top-2 rounded px-1.5 py-0.5 text-[10px] font-semibold backdrop-blur-md ${tl.ended ? "bg-slate-900/80 text-slate-400" : "bg-amber-500/90 text-slate-950"}`}>
          {tl.ended ? "Avslutad" : `⏱ ${tl.label}`}
        </div>
      )}
    </div>
    <div className="flex flex-1 flex-col gap-2 p-3">
      <h4 className="line-clamp-2 text-sm font-semibold text-slate-100" title={a.title}>{a.title}</h4>
      <div className="mt-auto">
        <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">Aktuellt bud{a.bid_count ? ` · ${a.bid_count} bud` : ""}</p>
        <p className="font-heading text-lg font-bold text-amber-400">{formatSEK(a.current_bid)}</p>
      </div>
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" onClick={() => onWatch(a)} disabled={watching === a.url}
          data-testid={`watch-live-${encodeURIComponent(a.url).slice(-16)}`}
          className="h-8 flex-1 bg-amber-500 text-slate-950 hover:bg-amber-600">
          {watching === a.url ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="mr-1 h-3.5 w-3.5" />} Bevaka
        </Button>
        <Button size="sm" variant="outline" asChild className="h-8 border-slate-700 text-slate-300 hover:bg-slate-800">
          <a href={a.url} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /></a>
        </Button>
      </div>
    </div>
  </motion.div>
  );
};

export const LiveFeed = () => {
  const [status, setStatus] = useState(null);
  const [items, setItems] = useState([]);
  const [source, setSource] = useState("Alla");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [watching, setWatching] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const qRef = useRef(q);
  qRef.current = q;
  const srcRef = useRef(source);
  srcRef.current = source;

  const load = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([
        api.liveStatus(),
        api.liveAuctions({ source: srcRef.current, q: qRef.current || undefined, limit: 120 }),
      ]);
      setStatus(st);
      setItems(list);
    } catch {
      /* keep previous */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => { load(); }, [source, load]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await api.liveSync();
      toast.success("Synkning startad – nya objekt dyker upp strax");
      setTimeout(load, 3000);
    } catch {
      toast.error("Kunde inte starta synkning");
    } finally {
      setSyncing(false);
    }
  };

  const handleWatch = async (a) => {
    setWatching(a.url);
    try {
      await api.liveWatch(a.url);
      toast.success(`Bevakar "${a.title.slice(0, 40)}"`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte bevaka");
    } finally {
      setWatching(null);
    }
  };

  const sources = status?.sources || {};

  return (
    <div data-testid="live-feed">
      {/* Status bar */}
      <div className="mb-5 rounded-xl border border-slate-800 bg-[#111827] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="relative flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400">
              <Radar className="h-5 w-5" />
            </div>
            <div>
              <p className="flex items-center gap-2 font-heading font-bold text-slate-100">
                Auto-crawler
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${status?.running ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>
                  <Activity className="h-3 w-3" /> {status?.running ? "Aktiv" : "Stoppad"}
                </span>
              </p>
              <p className="text-xs text-slate-400" data-testid="crawler-status-text">
                Hämtar 1 objekt var {status?.interval_seconds || 10}:e sekund · {status?.live_count ?? 0} objekt i kö · {status?.last_action || "…"}
              </p>
            </div>
          </div>
          <Button onClick={handleSync} disabled={syncing} data-testid="sync-now-button" className="bg-amber-500 text-slate-950 hover:bg-amber-600">
            {syncing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />} Synka nu
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {Object.entries(sources).map(([name, s]) => {
            const chip = STATUS_CHIP[s.status] || STATUS_CHIP.planned;
            return (
              <span key={name} title={s.note} className={`cursor-help rounded-md border px-2 py-0.5 text-[11px] ${chip.cls}`} data-testid={`source-status-${name.replace(/\s+/g, "-").toLowerCase()}`}>
                {name}: {chip.label}
              </span>
            );
          })}
        </div>
      </div>

      {/* Controls */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()}
            placeholder="Sök i live-flödet (t.ex. grävmaskin, traktor)..." data-testid="live-search"
            className="border-slate-700 bg-slate-900 pl-9 text-slate-100 placeholder:text-slate-600" />
        </div>
        <Select value={source} onValueChange={setSource}>
          <SelectTrigger data-testid="live-source-filter" className="w-44 border-slate-700 bg-slate-900 text-slate-100"><SelectValue /></SelectTrigger>
          <SelectContent className="border-slate-700 bg-slate-900 text-slate-100">
            <SelectItem value="Alla">Alla källor</SelectItem>
            {Object.keys(sources).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={load} className="border-slate-700 text-slate-300 hover:bg-slate-800"><RefreshCw className="h-4 w-4" /></Button>
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center text-slate-500"><Loader2 className="h-6 w-6 animate-spin" /></div>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-800 py-16 text-center text-slate-400">
          Inga live-objekt ännu. Klicka "Synka nu" eller vänta medan crawlern hämtar.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" data-testid="live-grid">
          {items.map((a) => <LiveCard key={a.url} a={a} onWatch={handleWatch} watching={watching} />)}
        </div>
      )}
    </div>
  );
};

export default LiveFeed;
