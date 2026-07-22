"""Admin trading controls shared by UI and webhook processing."""

from __future__ import annotations

from datetime import datetime

from .db import execute, fetch_all, fetch_one


SIDES = {"long", "short"}


def normalize_side(side: str) -> str:
    value = str(side or "").strip().lower()
    if value not in SIDES:
        raise ValueError("Unknown trading side")
    return value


def set_side_block(side: str, hours: float, admin_user_id: int | None = None) -> datetime:
    normalized = normalize_side(side)
    safe_hours = max(0.01, min(float(hours), 24 * 30))
    seconds = int(safe_hours * 60 * 60)
    execute(
        """
        INSERT INTO ai_admin_side_blocks (side, blocked_until, created_by, updated_at)
        VALUES (%s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND), %s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            blocked_until=VALUES(blocked_until),
            created_by=VALUES(created_by),
            updated_at=UTC_TIMESTAMP()
        """,
        (normalized, seconds, admin_user_id),
    )
    row = active_side_block(normalized)
    return row["blocked_until"] if row and row.get("blocked_until") else datetime.utcnow()


def clear_side_block(side: str) -> None:
    normalized = normalize_side(side)
    execute(
        """
        INSERT INTO ai_admin_side_blocks (side, blocked_until, updated_at)
        VALUES (%s, NULL, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            blocked_until=NULL,
            updated_at=UTC_TIMESTAMP()
        """,
        (normalized,),
    )


def active_side_block(side: str) -> dict | None:
    normalized = normalize_side(side)
    return fetch_one(
        """
        SELECT side, blocked_until, created_by, updated_at,
               TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), blocked_until) AS remaining_seconds
        FROM ai_admin_side_blocks
        WHERE side=%s AND blocked_until > UTC_TIMESTAMP()
        LIMIT 1
        """,
        (normalized,),
    )


def side_block_statuses() -> list[dict]:
    existing = {
        str(row.get("side") or ""): row
        for row in fetch_all(
            """
            SELECT side, blocked_until, created_by, updated_at,
                   blocked_until > UTC_TIMESTAMP() AS active,
                   GREATEST(0, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), blocked_until)) AS remaining_seconds
            FROM ai_admin_side_blocks
            WHERE side IN ('long', 'short')
            """
        )
    }
    rows = []
    for side in ("long", "short"):
        row = existing.get(side) or {"side": side, "active": 0, "remaining_seconds": 0}
        rows.append(
            {
                **row,
                "side": side,
                "active": bool(row.get("active")),
                "remaining_seconds": int(row.get("remaining_seconds") or 0),
            }
        )
    return rows
