import { useState, useEffect } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { Sparkles, Loader2, ExternalLink, RefreshCw, TrendingDown, TrendingUp, Minus, Gavel, Clock, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { badgeClass, formatSEK, timeLeft } from "@/lib/sources";

const posMeta = {
  under: { cls: "text-emerald-400", icon: TrendingDown, label: "Under marknadsvärde" },
  fair: { cls: "text-blue-400", icon: Minus, label: "Marknadspris" },
  over: { cls: "text-red-400", icon: TrendingUp, label: "Över marknadsvärde" },
};

const ChartTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-xl">
      <p className="font-mono text-slate-400">{new Date(p.timestamp).toLocaleString("sv-SE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</p>
      <p className="font-semibold text-amber-400">{formatSEK(p.bid)}</p>
      <p className="text-slate-400">{p.bidder} {p.increment > 0 && `(+${formatSEK(p.increment)})`}</p>
    </div>
  );
};

const Metric = ({ label, value, accent }) => (
  <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
    <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">{label}</p>
    <p className={`font-heading text-lg font-bold ${accent || "text-slate-100"}`}>{value}</p>
  </div>
);

export const AuctionDetail = ({ auction, open, onOpenChange, onAnalyze, onRefresh, analyzing, refreshing }) => {
  const [vatIncl, setVatIncl] = useState(true);
  const [removed, setRemoved] = useState(() => new Set());
  const [removing, setRemoving] = useState(null);
  useEffect(() => { setRemoved(new Set()); }, [auction?.url]);
  if (!auction) return null;
  const val = auction.valuation;
  const _median = (xs) => {
    const s = [...xs].sort((a, b) => a - b);
    const n = s.length;
    if (!n) return null;
    return n % 2 ? s[(n - 1) / 2] : Math.round((s[n / 2 - 1] + s[n / 2]) / 2);
  };
  const comps = (val?.sold_comparables || []).filter((c) => !removed.has(c.url || c.title));
  const finals = comps.map((c) => c.final_bid).filter((x) => x > 0);
  const soldSummary = finals.length
    ? { count: finals.length, median_final: _median(finals), min_final: Math.min(...finals), max_final: Math.max(...finals) }
    : null;
  const handleRemoveComp = async (c) => {
    const key = c.url || c.title;
    if (!key) { toast.error("Kan inte ta bort detta objekt"); return; }
    setRemoving(key);
    try {
      await api.deleteSoldItem(c.url ? { url: c.url } : { title: c.title, source: c.source });
      setRemoved((prev) => new Set(prev).add(key));
      toast.success("Referensobjekt borttaget – används inte längre vid värdering");
    } catch {
      toast.error("Kunde inte ta bort referensobjektet");
    } finally {
      setRemoving(null);
    }
  };
  const pm = val && posMeta[val.market_position];
  const tl = timeLeft(auction.ends_at);
  const chartData = (auction.bid_history || []).map((b) => ({ ...b }));
  const highest = chartData.length ? Math.max(...chartData.map((d) => d.bid)) : auction.current_bid;
  const bidPriceDisplay = vatIncl ? auction.total_now : auction.current_bid;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto border-slate-800 bg-[#0B0F19] text-slate-100 sm:max-w-3xl" data-testid="auction-detail-modal">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Badge className={`border font-mono text-[11px] ${badgeClass(auction.source)}`}>{auction.source}</Badge>
            {tl && <span className="flex items-center gap-1 text-xs text-slate-400"><Clock className="h-3 w-3" /> {tl.label}</span>}
            {!auction.scraped && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-400">simulerad data</span>}
          </div>
          <DialogTitle className="font-heading text-xl">{auction.title}</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-5">
          <div className="sm:col-span-2">
            <img src={auction.image} alt={auction.title} className="h-48 w-full rounded-xl object-cover sm:h-full" />
          </div>
          <div className="sm:col-span-3 grid grid-cols-2 gap-3">
            <Metric label="Nuvarande bud" value={formatSEK(auction.current_bid)} accent="text-amber-400" />
            <Metric label="Antal bud" value={`${auction.bid_count} st`} />
            {soldSummary?.count > 0 && (
              <div className="col-span-2 rounded-lg border border-emerald-500/25 bg-emerald-500/5 p-3" data-testid="sold-median-inline">
                <p className="text-[10px] font-mono uppercase tracking-wider text-emerald-400/80">Median slutpris · exakt modell</p>
                <div className="flex items-baseline gap-2">
                  <p className="font-heading text-lg font-bold text-emerald-300">{formatSEK(soldSummary.median_final)}</p>
                  <span className="text-[11px] text-slate-400">{soldSummary.count} sålda · {formatSEK(soldSummary.min_final)}–{formatSEK(soldSummary.max_final)}</span>
                </div>
              </div>
            )}
            <Metric label={`Totalpris ${vatIncl ? "inkl." : "exkl."} moms`} value={formatSEK(bidPriceDisplay)} />
            <Metric label="Slagavgift" value={auction.fee_known ? formatSEK(auction.buyer_fee) : "Tillkommer"} />
            <div className="col-span-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Moms {auction.vat}% · Visa totalpris</span>
                <Button size="sm" variant="outline" onClick={() => setVatIncl(!vatIncl)} data-testid="toggle-vat" className="h-7 border-slate-700 text-slate-300 hover:bg-slate-800">
                  {vatIncl ? "Inkl. moms" : "Exkl. moms"}
                </Button>
              </div>
              {auction.fee_note && <p className="mt-2 text-[11px] text-slate-500">{auction.fee_note}</p>}
            </div>
          </div>
        </div>

        {/* Bid history */}
        <div className="mt-2 rounded-xl border border-slate-800 bg-[#111827] p-4" data-testid="bid-history-chart">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="font-heading font-semibold text-slate-100 flex items-center gap-2"><Gavel className="h-4 w-4 text-amber-400" /> Budhistorik</h4>
            <Button size="sm" variant="outline" onClick={() => onRefresh(auction)} disabled={refreshing} data-testid="refresh-auction" className="h-8 border-slate-700 text-slate-300 hover:bg-slate-800">
              {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              <span className="ml-1.5">Uppdatera</span>
            </Button>
          </div>
          <div className="h-56 w-full">
            {chartData.length < 2 ? (
              <div className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-slate-800 bg-slate-900/40 px-6 text-center" data-testid="bid-history-empty">
                <Gavel className="mb-2 h-6 w-6 text-slate-600" />
                <p className="text-sm font-medium text-slate-300">
                  {auction.bid_count > 0 ? `${auction.bid_count} bud hittills på auktionssajten` : "Inga bud ännu"}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Budhistoriken börjar spåras när du bevakar objektet – grafen fylls på automatiskt när nya bud läggs.
                </p>
              </div>
            ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="bidFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#F59E0B" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#F59E0B" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1F2937" vertical={false} />
                <XAxis dataKey="timestamp" tickFormatter={(t) => new Date(t).toLocaleDateString("sv-SE", { day: "2-digit", month: "short" })} stroke="#475569" fontSize={11} tickLine={false} />
                <YAxis tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} stroke="#475569" fontSize={11} tickLine={false} axisLine={false} width={40} />
                <Tooltip content={<ChartTooltip />} />
                <Area type="monotone" dataKey="bid" stroke="#F59E0B" strokeWidth={2} fill="url(#bidFill)" />
              </AreaChart>
            </ResponsiveContainer>
            )}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <Metric label="Högsta bud" value={formatSEK(highest)} accent="text-amber-400" />
            <Metric label="Antal bud" value={`${auction.bid_count}`} />
            <Metric label="Est. slutpris" value={val ? formatSEK(val.used_price_estimate) : "—"} accent="text-violet-300" />
          </div>
        </div>

        {/* AI valuation */}
        <div className="rounded-xl border border-violet-500/20 bg-violet-500/5 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="flex items-center gap-2 font-heading font-semibold text-violet-200">
              <Sparkles className="h-4 w-4" /> AI-prisanalys
            </h4>
            <Button size="sm" onClick={() => onAnalyze(auction)} disabled={analyzing} data-testid="ai-analyze-button" className="h-8 bg-violet-600 text-white hover:bg-violet-700">
              {analyzing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1.5 h-4 w-4" />}
              {val ? "Analysera igen" : "Kör AI-värdering"}
            </Button>
          </div>

          {analyzing && <p className="text-sm text-slate-400">AI jämför mot verkliga auktionsslutpriser, Prisjakt, Google & Blocket...</p>}

          {val && !analyzing && (
            <div className="space-y-4" data-testid="ai-price-estimate">
              {val.based_on_real_sales && (
                <Badge data-testid="real-sales-badge" className="border border-emerald-500/40 bg-emerald-500/10 text-[10px] font-semibold text-emerald-300">
                  ✓ Baserad på verkliga auktionsslutpriser
                </Badge>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">Uppskattat nypris</p>
                  <p className="font-heading text-2xl font-bold text-slate-100">{formatSEK(val.new_price_estimate)}</p>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500">Uppskattat begagnatpris</p>
                  <p className="font-heading text-2xl font-bold text-violet-300">{formatSEK(val.used_price_estimate)}</p>
                </div>
              </div>

              {pm && (
                <div className={`flex items-center gap-2 text-sm font-semibold ${pm.cls}`}>
                  <pm.icon className="h-4 w-4" /> {pm.label}
                  {typeof val.savings_pct === "number" && <span className="text-slate-400 font-normal">· {val.savings_pct > 0 ? `${val.savings_pct}% under` : `${Math.abs(val.savings_pct)}% över`} marknad</span>}
                </div>
              )}

              <div>
                <div className="mb-1 flex justify-between text-xs text-slate-400">
                  <span>Konfidensgrad</span><span className="font-mono text-slate-200">{val.confidence}%</span>
                </div>
                <Progress value={val.confidence} className="h-2 bg-slate-800" />
              </div>

              <p className="rounded-lg bg-slate-900/60 p-3 text-sm italic text-slate-300">"{val.insight}"</p>

              {comps.length > 0 && (
                <div data-testid="sold-comparables">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider text-emerald-400">
                    <Gavel className="h-3.5 w-3.5" /> Verkliga slutpriser (liknande auktioner)
                  </p>
                  <div className="space-y-2">
                    {comps.map((c) => (
                      <div key={c.url || c.title} className="flex items-center justify-between gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-sm">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-slate-200">{c.title}</p>
                          <span className="text-[11px] text-slate-500">
                            {c.source}{c.bid_count ? ` · ${c.bid_count} bud` : ""}
                            {c.ends_at ? ` · ${new Date(c.ends_at).toLocaleDateString("sv-SE", { day: "2-digit", month: "short", year: "2-digit" })}` : ""}
                          </span>
                        </div>
                        <span className="font-semibold text-emerald-300">{formatSEK(c.final_bid)}</span>
                        <button
                          onClick={() => handleRemoveComp(c)}
                          disabled={removing === (c.url || c.title)}
                          data-testid={`remove-comp-${encodeURIComponent(c.url || c.title || "").slice(-16)}`}
                          title="Ta bort felaktigt referensobjekt"
                          className="shrink-0 rounded-md p-1.5 text-slate-500 transition hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
                        >
                          {removing === (c.url || c.title) ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {val.comparables?.length > 0 && (
                <div>
                  <p className="mb-2 text-[11px] font-mono uppercase tracking-wider text-slate-500">Jämförbara produkter</p>
                  <div className="space-y-2">
                    {val.comparables.map((c, i) => (
                      <div key={i} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-sm">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-slate-200">{c.name}</p>
                          <span className="text-[11px] text-slate-500">{c.source} · {c.condition}</span>
                        </div>
                        <span className="ml-3 font-semibold text-slate-100">{formatSEK(c.price)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {!val && !analyzing && (
            <p className="text-sm text-slate-400">Kör AI-värderingen för att uppskatta nypris, begagnatpris och hitta liknande produkter.</p>
          )}
        </div>

        {auction.url && (
          <Button variant="outline" asChild className="w-full border-slate-700 text-slate-300 hover:bg-slate-800" data-testid="open-original-auction">
            <a href={auction.url} target="_blank" rel="noreferrer"><ExternalLink className="mr-2 h-4 w-4" /> Öppna originalauktion</a>
          </Button>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default AuctionDetail;
