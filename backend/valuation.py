"""AI price valuation using the Emergent Universal Key.

Estimates new (nypris) and used (begagnatpris) price for an auction item and
produces comparable products as if sourced from Prisjakt/Google, plus a Swedish
plain-language insight comparing the current bid to market value.
"""
import os
import json
import re

try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
except ImportError:
    LlmChat = None
    UserMessage = None

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")


class ValuationUnavailable(RuntimeError):
    """AI valuation needs local configuration before it can run."""


def require_configured():
    if not EMERGENT_LLM_KEY:
        raise ValuationUnavailable(
            "AI-värdering saknar API-nyckel. Lägg till EMERGENT_LLM_KEY i backend/.env och återskapa backendcontainern."
        )
    if LlmChat is None or UserMessage is None:
        raise ValuationUnavailable(
            "AI-värderingens bibliotek saknas i backendinstallationen."
        )

SYSTEM = (
    "Du är en svensk auktions- och prisanalytiker. Du uppskattar nypris och "
    "begagnatpris för produkter på svenska andrahands- och auktionsmarknaden "
    "(du känner till prisnivåer från Prisjakt.se, Blocket och Google Shopping). "
    "Svara ALLTID endast med giltig JSON, ingen övrig text, ingen markdown."
)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group(0)
    return json.loads(text)


async def valuate(title: str, current_bid: int, source: str, total_now: int,
                  comps: list | None = None, complete=None) -> dict:
    if complete is None:
        require_configured()
    comps = comps or []
    comps_block = ""
    if comps:
        rows = []
        for c in comps[:8]:
            tf = c.get("total_final") or c.get("final_bid")
            bc = f", {c['bid_count']} bud" if c.get("bid_count") else ""
            rows.append(f'- "{c.get("title")}" ({c.get("source")}): '
                        f'slutbud {c.get("final_bid")} SEK, totalt {tf} SEK{bc}')
        comps_block = (
            "\n\nVERKLIGA SLUTPRISER från liknande auktioner i vår egen databas. "
            "Detta är den MEST exakta källan till begagnatvärde – grunda din "
            "begagnatprisuppskattning (used_price_estimate) PRIMÄRT på dessa "
            "verkliga slutpriser (t.ex. deras median/nivå):\n" + "\n".join(rows)
        )
    prompt = f"""Analysera denna auktion och uppskatta marknadsvärde.

Produkt: "{title}"
Auktionskälla: {source}
Nuvarande bud: {current_bid} SEK
Totalpris nu (inkl. moms + avgift): {total_now} SEK{comps_block}

Returnera JSON med EXAKT dessa fält:
{{
  "new_price_estimate": <heltal, uppskattat nypris i SEK>,
  "used_price_estimate": <heltal, uppskattat begagnatpris i SEK – om verkliga slutpriser finns ovan, använd deras nivå>,
  "confidence": <heltal 0-100; 70-95 om verkliga slutpriser finns, annars 30-60>,
  "currency": "SEK",
  "market_position": "<under | fair | over>",
  "savings_pct": <heltal, hur många % under/över begagnat-marknadsvärde totalpriset ligger, negativt om över>,
  "comparables": [
    {{"name": "<liknande produkt>", "price": <heltal SEK>, "source": "Prisjakt", "condition": "ny"}},
    {{"name": "<liknande produkt>", "price": <heltal SEK>, "source": "Google", "condition": "ny"}},
    {{"name": "<liknande begagnad>", "price": <heltal SEK>, "source": "Blocket", "condition": "begagnad"}}
  ],
  "insight": "<1-2 meningar på svenska. Nämn om bedömningen bygger på verkliga auktionsslutpriser.>"
}}"""

    if complete is not None:
        resp = await complete(SYSTEM, prompt)
    else:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id="valuation",
            system_message=SYSTEM,
        ).with_model("openai", "gpt-5.4")
        resp = await chat.send_message(UserMessage(text=prompt))
    data = _extract_json(resp if isinstance(resp, str) else str(resp))
    data.setdefault("currency", "SEK")
    # Pass through the REAL sold comparables (not AI-generated) so the UI can
    # show verified slutpriser alongside the external (Prisjakt/Google) ones.
    data["sold_comparables"] = [
        {"url": c.get("url"), "title": c.get("title"), "source": c.get("source"),
         "final_bid": c.get("final_bid"),
         "total_final": c.get("total_final") or c.get("final_bid"),
         "bid_count": c.get("bid_count"), "ends_at": c.get("ends_at")}
        for c in comps[:8]
    ]
    data["based_on_real_sales"] = bool(comps)
    finals = sorted(c.get("final_bid") for c in comps if c.get("final_bid"))
    totals = sorted((c.get("total_final") or c.get("final_bid")) for c in comps
                    if (c.get("total_final") or c.get("final_bid")))

    def _median(xs):
        n = len(xs)
        if n == 0:
            return None
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2
    data["sold_summary"] = {
        "count": len(finals),
        "median_final": _median(finals),
        "median_total": _median(totals),
        "min_final": finals[0] if finals else None,
        "max_final": finals[-1] if finals else None,
    } if finals else None
    return data
