import { useEffect, useState, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { RefreshCw, ExternalLink, Plus, Search, Loader2 } from "lucide-react";
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
        {a.fixed_price ? (
          <>
            <p className="text-[10px] font-mono uppercase tracking-wider text-pink-400">Fastpris · Köp nu</p>
            <p className="font-heading text-lg font-bold text-slate-100">{formatSEK(a.starting_price || a.current_bid)}</p>
          </>
        ) : a.current_bid > 0 ? (
          <>
            <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">Aktuellt bud{a.bid_count ? ` · ${a.bid_count} bud` : ""}</p>
            <p className="font-heading text-lg font-bold text-amber-400">{formatSEK(a.current_bid)}</p>
          </>
        ) : a.starting_price ? (
          <>
            <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">Utropspris · inga bud</p>
            <p className="font-heading text-lg font-bold text-slate-200">{formatSEK(a.starting_price)}</p>
          </>
        ) : (
          <>
            <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">{a.source === "Kronofogden" ? "Exekutiv försäljning" : "Inga bud ännu"}</p>
            <p className="font-heading text-sm font-semibold text-slate-300">{a.source === "Kronofogden" ? "Se auktion för detaljer" : "Se auktion"}</p>
          </>
        )}
        {!a.fixed_price && (a.current_bid > 0 || a.starting_price > 0) && a.total_now > 0 && (
          <p className="mt-1 text-[10px] text-slate-500" title={a.fee_note || ""}>
            ≈ <span className="font-semibold text-slate-300">{formatSEK(a.total_now)}</span>{" "}
            {a.fee_known ? "att betala (ca)" : "inkl. moms · + avgift"}
          </p>
        )}
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

export const LiveFeed = ({ onWatchAdded }) => {
  const [status, setStatus] = useState(null);
  const [items, setItems] = useState([]);
  const [source, setSource] = useState("Alla");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [watching, setWatching] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [limit, setLimit] = useState(120);
  const qRef = useRef(q);
  qRef.current = q;
  const srcRef = useRef(source);
  srcRef.current = source;
  const limitRef = useRef(limit);
  limitRef.current = limit;

  const load = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([
        api.liveStatus(),
        api.liveAuctions({ source: srcRef.current, q: qRef.current || undefined, limit: limitRef.current }),
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

  // Reset paging when the filter/search changes
  useEffect(() => { setLimit(120); }, [source]);
  useEffect(() => { load(); }, [source, limit, load]);

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
      onWatchAdded?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Kunde inte bevaka");
    } finally {
      setWatching(null);
    }
  };

  const sources = status?.sources || {};

  return (
    <div data-testid="live-feed">
      {/* Status bar (compact) */}
      <div className="mb-4 rounded-lg border border-slate-800 bg-[#111827] px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <span className="relative flex h-2 w-2">
              {status?.running && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />}
              <span className={`relative inline-flex h-2 w-2 rounded-full ${status?.running ? "bg-emerald-500" : "bg-red-500"}`} />
            </span>
            Auto-crawler
          </span>
          <span className="text-xs text-slate-400" data-testid="crawler-status-text">
            {status?.live_count ?? 0} objekt · uppdaterar 1 var {status?.interval_seconds || 10}:e sekund · {status?.last_action || "…"}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {Object.entries(sources).map(([name, s]) => {
            const chip = STATUS_CHIP[s.status] || STATUS_CHIP.planned;
            return (
              <span key={name} title={s.note} className={`cursor-help rounded border px-1.5 py-0.5 text-[10px] ${chip.cls}`} data-testid={`source-status-${name.replace(/\s+/g, "-").toLowerCase()}`}>
                {name}
              </span>
            );
          })}
        </div>
      </div>

      {/* Controls */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (setLimit(120), load())}
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

      {!loading && items.length >= limit && (
        <div className="mt-6 flex justify-center">
          <Button variant="outline" onClick={() => setLimit((l) => l + 120)} data-testid="load-more-button"
            className="border-slate-700 text-slate-200 hover:bg-slate-800">
            Ladda fler objekt
          </Button>
        </div>
      )}
    </div>
  );
};

export default LiveFeed;
