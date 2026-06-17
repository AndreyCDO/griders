"""
tools/sentiment.py — новости и индекс страха/жадности.
Источники: CryptoPanic API + Alternative.me Fear & Greed Index.
"""

import httpx

from config import CRYPTOPANIC_BASE, CRYPTOPANIC_KEY, FEAR_GREED_URL, HTTP_TIMEOUT


async def get_news(currencies: str = "", filter_type: str = "hot", limit: int = 8) -> dict:
    """Свежие крипто-новости с сентиментом (CryptoPanic)."""
    if not CRYPTOPANIC_KEY:
        return {
            "error":    "CRYPTOPANIC_API_KEY не задан в .env",
            "articles": [],
        }
    params: dict = {
        "auth_token": CRYPTOPANIC_KEY,
        "public":     "true",
        "filter":     filter_type,
        "kind":       "news",
    }
    if currencies:
        params["currencies"] = currencies.upper()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(f"{CRYPTOPANIC_BASE}/posts/", params=params)
    r.raise_for_status()

    articles = []
    for item in r.json().get("results", [])[:limit]:
        articles.append({
            "title":      item["title"],
            "url":        item["url"],
            "published":  item["published_at"],
            "source":     item["source"]["title"],
            "currencies": [c["code"] for c in item.get("currencies", [])],
            "votes": {
                "positive":  item.get("votes", {}).get("positive", 0),
                "negative":  item.get("votes", {}).get("negative", 0),
                "important": item.get("votes", {}).get("important", 0),
            },
        })

    pos = sum(a["votes"]["positive"] for a in articles)
    neg = sum(a["votes"]["negative"] for a in articles)
    sentiment = "BULLISH" if pos > neg * 1.5 else ("BEARISH" if neg > pos * 1.5 else "NEUTRAL")

    return {
        "articles":         articles,
        "count":            len(articles),
        "market_sentiment": sentiment,
    }


async def get_fear_greed() -> dict:
    """Индекс страха и жадности (Alternative.me)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FEAR_GREED_URL, params={"limit": 2})
    r.raise_for_status()
    items = r.json().get("data", [])
    cur   = items[0] if items else {}
    val   = int(cur.get("value", 50))

    if val < 20:
        interpretation = "EXTREME_FEAR — паника, ищи разворот вверх у ключевых поддержек"
    elif val < 45:
        interpretation = "FEAR — осторожность, приоритет LONG от поддержек"
    elif val > 80:
        interpretation = "EXTREME_GREED — эйфория, высокий риск коррекции, приоритет SHORT"
    elif val > 65:
        interpretation = "GREED — рынок разогрет, осторожно с LONG"
    else:
        interpretation = "NEUTRAL — нет явного перекоса"

    return {
        "value":          val,
        "classification": cur.get("value_classification", "Unknown"),
        "interpretation": interpretation,
        "yesterday":      int(items[1]["value"]) if len(items) > 1 else None,
        "change":         val - int(items[1]["value"]) if len(items) > 1 else 0,
    }
