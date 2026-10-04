"""AI price valuation using the Emergent Universal Key.

Estimates new (nypris) and used (begagnatpris) price for an auction item and
produces comparable products as if sourced from Prisjakt/Google, plus a Swedish
plain-language insight comparing the current bid to market value.
"""
import os
import json
import re

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

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


async def valuate(title: str, current_bid: int, source: str, total_now: int) -> dict:
    prompt = f"""Analysera denna auktion och uppskatta marknadsvärde.

Produkt: "{title}"
Auktionskälla: {source}
Nuvarande bud: {current_bid} SEK
Totalpris nu (inkl. moms + avgift): {total_now} SEK

Returnera JSON med EXAKT dessa fält:
{{
  "new_price_estimate": <heltal, uppskattat nypris i SEK>,
  "used_price_estimate": <heltal, uppskattat begagnatpris i SEK>,
  "confidence": <heltal 0-100, hur säker du är>,
  "currency": "SEK",
  "market_position": "<under | fair | over>",
  "savings_pct": <heltal, hur många % under/över begagnat-marknadsvärde totalpriset ligger, negativt om över>,
  "comparables": [
    {{"name": "<liknande produkt>", "price": <heltal SEK>, "source": "Prisjakt", "condition": "ny"}},
    {{"name": "<liknande produkt>", "price": <heltal SEK>, "source": "Google", "condition": "ny"}},
    {{"name": "<liknande begagnad>", "price": <heltal SEK>, "source": "Blocket", "condition": "begagnad"}}
  ],
  "insight": "<1-2 meningar på svenska, t.ex. 'Budet ligger 18% under marknadsvärde. Bra köpläge.'>"
}}"""

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"valuation",
        system_message=SYSTEM,
    ).with_model("openai", "gpt-5.4")

    resp = await chat.send_message(UserMessage(text=prompt))
    data = _extract_json(resp if isinstance(resp, str) else str(resp))
    data.setdefault("currency", "SEK")
    return data
