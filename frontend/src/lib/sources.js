export const SOURCES = [
  "PS Auction", "Klaravik", "Blinto", "Tovek", "Netauktion",
  "Retrade", "Budi", "Fabeo", "Riksauktioner", "Tradera", "Blocket", "Auctionet",
];

export const SOURCE_BADGES = {
  "PS Auction": "bg-amber-500/10 text-amber-400 border-amber-500/30",
  "Klaravik": "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  "Blinto": "bg-blue-500/10 text-blue-400 border-blue-500/30",
  "Tovek": "bg-red-500/10 text-red-400 border-red-500/30",
  "Netauktion": "bg-purple-500/10 text-purple-400 border-purple-500/30",
  "Retrade": "bg-indigo-500/10 text-indigo-400 border-indigo-500/30",
  "Budi": "bg-cyan-500/10 text-cyan-400 border-cyan-500/30",
  "Fabeo": "bg-orange-500/10 text-orange-400 border-orange-500/30",
  "Riksauktioner": "bg-teal-500/10 text-teal-300 border-teal-500/30",
  "Tradera": "bg-pink-500/10 text-pink-400 border-pink-500/30",
  "Blocket": "bg-lime-500/10 text-lime-400 border-lime-500/30",
  "Auctionet": "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/30",
  "Kronofogden": "bg-slate-500/10 text-slate-300 border-slate-500/30",
  "Okänd källa": "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

export const CATEGORIES = [
  "Verktyg", "Fordon", "Maskiner", "Elektronik", "Möbler",
  "Hem & Trädgård", "Industri", "Övrigt",
];

export const badgeClass = (source) =>
  SOURCE_BADGES[source] || SOURCE_BADGES["Okänd källa"];

export const formatSEK = (n) =>
  new Intl.NumberFormat("sv-SE").format(Math.round(n || 0)) + " kr";

export function timeLeft(endsAt) {
  if (!endsAt) return null;
  const diff = new Date(endsAt) - new Date();
  if (diff <= 0) return { ended: true, label: "Avslutad" };
  const h = Math.floor(diff / 3.6e6);
  const m = Math.floor((diff % 3.6e6) / 6e4);
  const d = Math.floor(h / 24);
  if (d >= 1) return { ended: false, label: `${d}d ${h % 24}h` };
  return { ended: false, label: `${h}h ${m}m` };
}
