import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp import settings
from webapp.db import execute, fetch_all


def normalize_pairs(values: list[str], blocked: set[str]) -> list[str]:
    allowed = {pair["symbol"] for pair in settings.RECOMMENDED_PAIR_OPTIONS}
    selected: list[str] = []
    for value in values:
        symbol = value.strip().upper()
        if symbol in allowed and symbol not in blocked and symbol not in selected:
            selected.append(symbol)
    defaults = [symbol for symbol in settings.DEFAULT_WATCHLIST if symbol in allowed and symbol not in blocked]
    return selected or defaults


def main() -> None:
    disabled_by_plan = {
        "free": settings.FREE_PLAN_DISABLED_PAIRS,
        "start": settings.START_PLAN_DISABLED_PAIRS,
        "premium": set(),
    }
    rows = fetch_all(
        """
        SELECT s.id, s.watchlist, u.role, u.plan
        FROM ai_user_strategy_settings s
        JOIN ai_users u ON u.id=s.user_id
        """
    )
    updated = 0
    for row in rows:
        blocked = set() if row.get("role") == "admin" else disabled_by_plan.get(str(row.get("plan") or "free"), set())
        if not blocked:
            continue
        current = [item.strip().upper() for item in str(row.get("watchlist") or "").split(",") if item.strip()]
        sanitized = normalize_pairs(current, blocked)
        if sanitized != current:
            execute("UPDATE ai_user_strategy_settings SET watchlist=%s WHERE id=%s", (",".join(sanitized), int(row["id"])))
            updated += 1
    print(f"sanitized_watchlists={updated}")


if __name__ == "__main__":
    main()
