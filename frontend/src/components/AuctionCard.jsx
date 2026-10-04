import { motion } from "framer-motion";
import { Clock, Gavel, ExternalLink, Trash2, Sparkles, TrendingDown, TrendingUp, Minus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { badgeClass, formatSEK, timeLeft } from "@/lib/sources";

const positionChip = {
  under: { cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30", icon: TrendingDown, label: "Bra köpläge" },
  fair: { cls: "bg-blue-500/15 text-blue-400 border-blue-500/30", icon: Minus, label: "Marknadspris" },
  over: { cls: "bg-red-500/15 text-red-400 border-red-500/30", icon: TrendingUp, label: "Över värde" },
};

export const AuctionCard = ({ auction, onOpen, onDelete }) => {
  const tl = timeLeft(auction.ends_at);
  const val = auction.valuation;
  const pos = val && positionChip[val.market_position];

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.3 }}
      whileHover={{ y: -4 }}
      data-testid={`auction-card-${auction.id}`}
      className="group relative flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#111827] hover:border-slate-700 transition-colors"
    >
      <div className="relative h-44 overflow-hidden bg-slate-900">
        <img
          src={auction.image}
          alt={auction.title}
          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
          onError={(e) => { e.target.src = "https://images.unsplash.com/photo-1603816885871-c122a9ff8d40?w=800&q=80"; }}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#111827] via-transparent to-transparent" />
        <Badge className={`absolute left-3 top-3 border font-mono text-[11px] font-semibold shadow-lg backdrop-blur-md ${badgeClass(auction.source)}`} style={{ backgroundColor: "rgba(11,15,25,0.65)" }}>
          {auction.source}
        </Badge>
        {tl && (
          <div className={`absolute right-3 top-3 flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold backdrop-blur-md ${tl.ended ? "bg-slate-900/80 text-slate-400" : "bg-amber-500/90 text-slate-950"}`}>
            <Clock className="h-3 w-3" /> {tl.label}
          </div>
        )}
        {!auction.scraped && (
          <span className="absolute bottom-2 right-3 rounded bg-slate-900/80 px-1.5 py-0.5 text-[10px] font-mono text-slate-400">simulerad data</span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <h3 className="font-heading text-base font-semibold leading-tight text-slate-100 line-clamp-2" title={auction.title}>
          {auction.title}
        </h3>

        <div className="flex items-end justify-between">
          <div>
            <p className="text-[11px] font-mono uppercase tracking-wider text-slate-500">Nuvarande bud</p>
            <p className="font-heading text-2xl font-bold text-amber-400">{formatSEK(auction.current_bid)}</p>
          </div>
          <div className="flex items-center gap-1 rounded-md bg-slate-800/60 px-2 py-1 text-xs text-slate-300">
            <Gavel className="h-3.5 w-3.5" /> {auction.bid_count} bud
          </div>
        </div>

        <div className="rounded-lg bg-slate-800/40 px-3 py-2 text-xs text-slate-400">
          Totalt inkl. moms & avgift: <span className="font-semibold text-slate-200">{formatSEK(auction.total_now)}</span>
        </div>

        {val ? (
          <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-3">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1 text-[11px] font-mono uppercase tracking-wider text-violet-300">
                <Sparkles className="h-3 w-3" /> AI-värdering
              </span>
              {pos && (
                <span className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${pos.cls}`}>
                  <pos.icon className="h-3 w-3" /> {pos.label}
                </span>
              )}
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Nypris <span className="font-semibold text-slate-200">{formatSEK(val.new_price_estimate)}</span></span>
              <span className="text-slate-400">Begagnat <span className="font-semibold text-slate-200">{formatSEK(val.used_price_estimate)}</span></span>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-center text-xs text-slate-500">
            Ingen AI-värdering ännu
          </div>
        )}

        <div className="mt-auto flex items-center gap-2 pt-1">
          <Button
            size="sm"
            onClick={() => onOpen(auction)}
            data-testid={`open-auction-${auction.id}`}
            className="flex-1 bg-amber-500 text-slate-950 hover:bg-amber-600"
          >
            Detaljer & AI
          </Button>
          {auction.url && (
            <Button size="sm" variant="outline" asChild className="border-slate-700 text-slate-300 hover:bg-slate-800" data-testid={`external-auction-${auction.id}`}>
              <a href={auction.url} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /></a>
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => onDelete(auction)}
            data-testid={`delete-auction-${auction.id}`}
            className="border-slate-700 text-slate-400 hover:bg-red-500/10 hover:text-red-400"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </motion.div>
  );
};

export default AuctionCard;
