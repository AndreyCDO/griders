"""Загрузка исторического фандинга Bybit для всех пар бектеста.

Bybit отдаёт фандинг через публичный эндпоинт /v5/market/funding/history
(без авторизации). Ставка применяется каждые 8 часов к НОМИНАЛЬНОЙ позиции
(notional = qty * price), не к марже. Поэтому при плече x10 фандинг
значительно влияет на PnL сделок, которые держатся часы/сутки.

Кэшируем в .cache/backtests/bybit_funding/<PAIR>.json.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT", "ZECUSDT",
    "ONDOUSDT", "XRPUSDT", "SUIUSDT", "FILUSDT", "TAOUSDT", "RENDERUSDT",
    "ADAUSDT", "INJUSDT", "LITUSDT", "ENAUSDT", "LINKUSDT", "AVAXUSDT",
    "JUPUSDT", "ARBUSDT",
]

# Период бектеста: 2025-06-11 -> 2026-06-11 (+ запас)
START_MS = 1749600000000   # 2025-06-11 UTC
END_MS = 1781136000000     # 2026-06-11 UTC

CACHE_DIR = Path(".cache/backtests/bybit_funding")


def fetch_funding(symbol: str) -> list[dict]:
    """Получить всю историю фандинга по паре пагинацией (limit=200 за запрос)."""
    url = "https://api.bybit.com/v5/market/funding/history"
    out: list[dict] = []
    end = END_MS
    seen: set[str] = set()
    for _ in range(200):  # защита от бесконечного цикла
        params = f"category=linear&symbol={symbol}&startTime={START_MS}&endTime={end}&limit=200"
        req = urllib.request.Request(f"{url}?{params}", headers={"User-Agent": "backtest"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            print(f"  {symbol}: ошибка {e}")
            break
        rows = data.get("result", {}).get("list", [])
        if not rows:
            break
        new = []
        oldest = end
        for row in rows:
            ts = str(row.get("fundingRateTimestamp"))
            if ts in seen:
                continue
            seen.add(ts)
            new.append({
                "ts": int(ts),
                "rate": float(row.get("fundingRate") or 0),
            })
            oldest = min(oldest, int(ts))
        out.extend(new)
        if len(rows) < 200:
            break
        end = oldest - 1
        time.sleep(0.3)  # вежливость к API
    out.sort(key=lambda x: x["ts"])
    return out


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Загрузка фандинга для {len(PAIRS)} пар ({START_MS} -> {END_MS})...")
    for sym in PAIRS:
        cache_path = CACHE_DIR / f"{sym}.json"
        if cache_path.exists():
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"  {sym}: кэш есть ({len(existing)} записей), пропуск")
            continue
        rows = fetch_funding(sym)
        cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        if rows:
            avg_rate = sum(r["rate"] for r in rows) / len(rows)
            print(f"  {sym}: {len(rows)} записей, средняя ставка {avg_rate:.6f} ({avg_rate*100:.4f}%)")
        else:
            print(f"  {sym}: НЕТ данных фандинга")
        time.sleep(0.5)
    print("Готово.")


if __name__ == "__main__":
    main()
