"""Дозагрузка 15m свечей Bybit с 2026-07-20 до 2026-08-03 для сверки.

Bybit публичный эндпоинт /v5/market/kline, пагинация по 1000 свечей.
Дописывает данные в существующие файлы кэша (или создаёт новые), как их
ожидает найти load_pair_candles (формат PAIR_start_end.json).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "NEARUSDT", "ZECUSDT",
    "ONDOUSDT", "XRPUSDT", "SUIUSDT", "FILUSDT", "TAOUSDT", "RENDERUSDT",
    "ADAUSDT", "INJUSDT", "LITUSDT", "ENAUSDT", "LINKUSDT", "AVAXUSDT",
    "JUPUSDT", "ARBUSDT",
]

CACHE_DIR = Path(".cache/backtests/bybit_15m")
# С запасом: с конца текущего кэша (07-20) до сегодня (08-03).
START_MS = int(datetime(2026, 6, 8, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp() * 1000)
INTERVAL = "15"


def fetch_klines(symbol: str) -> list[list]:
    """Дозагрузка свечей плотными дневными окнами.

    Bybit v5 /kline при большом диапазоне отдаёт последние N свечей, а не с
    начала. Поэтому запрашиваем мелкими окнами по 1 дню (96 свечей 15m) —
    гарантированно получаем плотное покрытие без пропусков.
    """
    url = "https://api.bybit.com/v5/market/kline"
    out: list[list] = []
    day_ms = 24 * 60 * 60 * 1000
    window_start = START_MS
    while window_start < END_MS:
        window_end = min(window_start + day_ms, END_MS)
        params = (f"category=linear&symbol={symbol}&interval={INTERVAL}"
                  f"&start={window_start}&end={window_end}&limit=200")
        req = urllib.request.Request(f"{url}?{params}", headers={"User-Agent": "backtest-update"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            print(f"  {symbol}: ошибка на окне {window_start}: {e}")
            window_start = window_end
            continue
        rows = data.get("result", {}).get("list", [])
        for r in rows:
            ts = int(r[0])
            if ts < window_start or ts > window_end:
                continue
            out.append([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
        window_start = window_end
        time.sleep(0.15)
    # Дедупликация и сортировка
    seen = set()
    dedup = []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            dedup.append(c)
    dedup.sort(key=lambda x: int(x[0]))
    return dedup


def main() -> None:
    print(f"Дозагрузка свечей {datetime.fromtimestamp(START_MS/1000, timezone.utc).date()} -> {datetime.fromtimestamp(END_MS/1000, timezone.utc).date()}")
    for sym in PAIRS:
        rows = fetch_klines(sym)
        if not rows:
            print(f"  {sym}: нет новых данных")
            continue
        # Дописываем в новый файл кэша (load_pair_candles склеит по ts + уникальность)
        out_path = CACHE_DIR / f"{sym}_{START_MS}_{END_MS}.json"
        out_path.write_text(json.dumps(rows), encoding="utf-8")
        first = datetime.fromtimestamp(int(rows[0][0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(int(rows[-1][0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
        print(f"  {sym}: {len(rows)} свечей {first} -> {last}")
    print("Готово.")


if __name__ == "__main__":
    main()
