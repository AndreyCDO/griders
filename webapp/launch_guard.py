"""Short cooldown guard for Cryptorg webhook launches."""

from __future__ import annotations

import time

from . import settings
from .db import execute, fetch_one


def pair_launch_cooldown_reason(user_id: int, connection_id: int | None, pair: str) -> str | None:
    """Return a user-facing reason when the pair is still in launch cooldown."""
    seconds = int(settings.PAIR_LAUNCH_COOLDOWN_SECONDS)
    if seconds <= 0:
        return None
    pair = pair.upper()
    connection_key = int(connection_id or 0)
    lock = fetch_one(
        """
        SELECT TIMESTAMPDIFF(SECOND, NOW(), locked_until) AS remaining_seconds
        FROM ai_pair_launch_locks
        WHERE user_id=%s AND connection_id=%s AND pair=%s AND locked_until > NOW()
        LIMIT 1
        """,
        (user_id, connection_key, pair),
    )
    if lock:
        remaining = max(1, int(lock.get("remaining_seconds") or seconds))
        return f"защита от повторного входа: по {pair} уже был запуск webhook менее минуты назад, осталось примерно {remaining} сек."

    recent = fetch_one(
        """
        SELECT id
        FROM ai_signals
        WHERE user_id=%s AND connection_id <=> %s AND pair=%s
          AND status='sent'
          AND created_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id, connection_id, pair, seconds),
    )
    if recent:
        return f"защита от повторного входа: по {pair} уже был webhook менее минуты назад"
    return None


def reserve_pair_launch(user_id: int, connection_id: int | None, pair: str, source_ref: str) -> str | None:
    """Reserve a pair launch slot. Returns a reason if the slot is already taken."""
    reason = pair_launch_cooldown_reason(user_id, connection_id, pair)
    if reason:
        return reason

    seconds = int(settings.PAIR_LAUNCH_COOLDOWN_SECONDS)
    if seconds <= 0:
        return None
    pair = pair.upper()
    connection_key = int(connection_id or 0)
    token = f"{source_ref}:{time.time_ns()}"
    execute(
        """
        INSERT INTO ai_pair_launch_locks (user_id, connection_id, pair, locked_until, source_ref)
        VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND), %s)
        ON DUPLICATE KEY UPDATE
            source_ref = IF(locked_until <= NOW(), VALUES(source_ref), source_ref),
            locked_until = IF(locked_until <= NOW(), VALUES(locked_until), locked_until),
            updated_at = IF(locked_until <= NOW(), CURRENT_TIMESTAMP, updated_at)
        """,
        (user_id, connection_key, pair, seconds, token),
    )
    row = fetch_one(
        """
        SELECT source_ref, TIMESTAMPDIFF(SECOND, NOW(), locked_until) AS remaining_seconds
        FROM ai_pair_launch_locks
        WHERE user_id=%s AND connection_id=%s AND pair=%s
        LIMIT 1
        """,
        (user_id, connection_key, pair),
    )
    if row and row.get("source_ref") == token:
        return None
    remaining = max(1, int((row or {}).get("remaining_seconds") or seconds))
    return f"защита от повторного входа: по {pair} уже был запуск webhook менее минуты назад, осталось примерно {remaining} сек."

def release_pair_launch(user_id: int, connection_id: int | None, pair: str) -> None:
    pair = pair.upper()
    connection_key = int(connection_id or 0)
    execute(
        "DELETE FROM ai_pair_launch_locks WHERE user_id=%s AND connection_id=%s AND pair=%s",
        (user_id, connection_key, pair),
    )


def strategy_side_launch_cooldown_reason(
    user_id: int,
    connection_id: int | None,
    strategy_code: str,
    side: str,
    seconds: int,
) -> str | None:
    """Return a reason when this connection already launched a strategy side recently."""
    seconds = int(seconds)
    if seconds <= 0:
        return None
    connection_key = int(connection_id or 0)
    strategy_code = strategy_code.strip()
    side = side.lower()
    lock = fetch_one(
        """
        SELECT TIMESTAMPDIFF(SECOND, NOW(), locked_until) AS remaining_seconds
        FROM ai_strategy_side_launch_locks
        WHERE user_id=%s AND connection_id=%s AND strategy_code=%s AND side=%s AND locked_until > NOW()
        LIMIT 1
        """,
        (user_id, connection_key, strategy_code, side),
    )
    if lock:
        remaining = max(1, int(lock.get("remaining_seconds") or seconds))
        return _side_cooldown_message(side, remaining)

    recent = fetch_one(
        """
        SELECT id
        FROM ai_signals
        WHERE user_id=%s AND connection_id <=> %s AND strategy_code=%s AND side=%s
          AND status='sent'
          AND created_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id, connection_id, strategy_code, side, seconds),
    )
    if recent:
        return _side_cooldown_message(side, seconds)
    return None


def reserve_strategy_side_launch(
    user_id: int,
    connection_id: int | None,
    strategy_code: str,
    side: str,
    seconds: int,
    source_ref: str,
) -> str | None:
    """Reserve a user+connection+strategy+side webhook slot."""
    reason = strategy_side_launch_cooldown_reason(user_id, connection_id, strategy_code, side, seconds)
    if reason:
        return reason

    seconds = int(seconds)
    if seconds <= 0:
        return None
    connection_key = int(connection_id or 0)
    strategy_code = strategy_code.strip()
    side = side.lower()
    token = f"{source_ref}:{time.time_ns()}"
    execute(
        """
        INSERT INTO ai_strategy_side_launch_locks (user_id, connection_id, strategy_code, side, locked_until, source_ref)
        VALUES (%s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND), %s)
        ON DUPLICATE KEY UPDATE
            source_ref = IF(locked_until <= NOW(), VALUES(source_ref), source_ref),
            locked_until = IF(locked_until <= NOW(), VALUES(locked_until), locked_until),
            updated_at = IF(locked_until <= NOW(), CURRENT_TIMESTAMP, updated_at)
        """,
        (user_id, connection_key, strategy_code, side, seconds, token),
    )
    row = fetch_one(
        """
        SELECT source_ref, TIMESTAMPDIFF(SECOND, NOW(), locked_until) AS remaining_seconds
        FROM ai_strategy_side_launch_locks
        WHERE user_id=%s AND connection_id=%s AND strategy_code=%s AND side=%s
        LIMIT 1
        """,
        (user_id, connection_key, strategy_code, side),
    )
    if row and row.get("source_ref") == token:
        return None
    remaining = max(1, int((row or {}).get("remaining_seconds") or seconds))
    return _side_cooldown_message(side, remaining)

def release_strategy_side_launch(user_id: int, connection_id: int | None, strategy_code: str, side: str) -> None:
    connection_key = int(connection_id or 0)
    execute(
        "DELETE FROM ai_strategy_side_launch_locks WHERE user_id=%s AND connection_id=%s AND strategy_code=%s AND side=%s",
        (user_id, connection_key, strategy_code.strip(), side.lower()),
    )


def _side_cooldown_message(side: str, remaining_seconds: int) -> str:
    side_label = "лонг" if side == "long" else "шорт"
    minutes = max(1, int((remaining_seconds + 59) / 60))
    return f"защита GRID DCA: по пользователю уже был webhook в {side_label} за последние 5 минут, осталось примерно {minutes} мин."
def _side_cooldown_message(side: str, remaining_seconds: int) -> str:
    side_label = "лонг" if side == "long" else "шорт"
    minutes = max(1, int((remaining_seconds + 59) / 60))
    return f"защита GRID DCA: по этому подключению уже был webhook в {side_label} за последние 5 минут, осталось примерно {minutes} мин."
