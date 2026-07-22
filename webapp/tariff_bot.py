"""Telegram bot integration for paid tariff checks."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from typing import Any

import httpx

from . import settings
from .db import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

ACTIVE_MEMBER_STATUSES = {"creator", "administrator", "member"}
FREE_DOWNGRADE_CONFIRMATIONS = 3


def normalize_telegram_username(value: str | None) -> str:
    username = str(value or "").strip().lstrip("@").lower()
    allowed = []
    for ch in username:
        if ch.isalnum() or ch == "_":
            allowed.append(ch)
    return "".join(allowed)[:80]


def telegram_verify_url(user_id: int) -> str:
    bot_username = normalize_telegram_username(settings.TELEGRAM_TARIFF_BOT_USERNAME)
    if not bot_username:
        return ""
    return f"https://t.me/{bot_username}?start={_verify_payload(user_id)}"


async def handle_tariff_bot_update(update: dict[str, Any]) -> dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    channel_message = update.get("channel_post") or update.get("edited_channel_post") or {}
    membership = update.get("my_chat_member") or update.get("chat_member") or {}
    channel_chat = channel_message.get("chat") or membership.get("chat") or {}
    if channel_chat and channel_chat.get("type") == "channel":
        plan = _infer_plan_from_title(channel_chat.get("title") or "")
        if plan:
            _upsert_tariff_channel(plan, str(channel_chat.get("id")), channel_chat.get("title") or "")
            return {"ok": True, "captured_channel": plan}
        return {"ok": True, "ignored": "unknown_channel"}

    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = str(message.get("text") or "").strip()
    telegram_user_id = int(sender.get("id") or 0)
    chat_id = int(chat.get("id") or telegram_user_id or 0)
    telegram_username = normalize_telegram_username(sender.get("username"))
    if not telegram_user_id or not chat_id:
        return {"ok": True, "ignored": "no_user"}

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        linked = await _handle_start(chat_id, telegram_user_id, telegram_username, payload)
        if not linked:
            linked = await _handle_username_link(chat_id, telegram_user_id, telegram_username)
        return {"ok": True, "linked": linked}

    user = fetch_one("SELECT * FROM ai_users WHERE telegram_user_id=%s", (telegram_user_id,))
    if user:
        await sync_user_tariff(user, notify_chat_id=chat_id)
    elif await _handle_username_link(chat_id, telegram_user_id, telegram_username):
        pass
    else:
        await _send_message(
            chat_id,
            "Откройте профиль Griders, укажите Telegram username и нажмите ссылку подтверждения Telegram.",
        )
    return {"ok": True}


async def sync_all_tariffs() -> dict[str, int]:
    rows = fetch_all("SELECT * FROM ai_users WHERE role='user' AND telegram_user_id IS NOT NULL")
    changed = 0
    checked = 0
    for row in rows:
        try:
            result = await sync_user_tariff(row)
            checked += 1
            changed += 1 if result.get("changed") else 0
        except Exception as exc:
            logger.warning("tariff sync failed for user %s: %s", row.get("id"), exc)
    return {"checked": checked, "changed": changed}


async def tariff_sync_loop() -> None:
    await asyncio.sleep(600)
    while True:
        try:
            if settings.TELEGRAM_TARIFF_BOT_TOKEN:
                await sync_all_tariffs()
        except Exception:
            logger.exception("tariff sync loop failed")
        await asyncio.sleep(max(86400, int(settings.TELEGRAM_TARIFF_SYNC_INTERVAL_SECONDS)))


async def sync_user_tariff(user: dict[str, Any], notify_chat_id: int | None = None) -> dict[str, Any]:
    if not settings.TELEGRAM_TARIFF_BOT_TOKEN:
        return {"ok": False, "reason": "tariff bot token is not configured"}
    if str(user.get("role") or "user") == "admin":
        return {"ok": True, "plan": user.get("plan") or "premium", "admin": True}
    telegram_user_id = int(user.get("telegram_user_id") or 0)
    if not telegram_user_id:
        return {"ok": False, "reason": "telegram account is not verified"}

    current_plan = str(user.get("plan") or "free")
    if current_plan in {"free_plus", "start_plus", "premium_plus"}:
        execute("UPDATE ai_users SET telegram_last_checked_at=NOW() WHERE id=%s", (int(user["id"]),))
        return {"ok": True, "plan": current_plan, "changed": False, "manual_plus_plan": True}
    plan = await _subscription_plan(telegram_user_id, current_plan)
    if plan is None:
        execute("UPDATE ai_users SET telegram_last_checked_at=NOW() WHERE id=%s", (int(user["id"]),))
        if notify_chat_id:
            await _send_message(notify_chat_id, "Не удалось надёжно проверить тариф. Текущий тариф Griders пока не изменён.")
        return {"ok": False, "reason": "tariff check is temporarily unavailable"}
    if plan == "free" and current_plan in {"start", "premium"}:
        free_checks = int(user.get("tariff_free_checks") or 0) + 1
        if free_checks < FREE_DOWNGRADE_CONFIRMATIONS:
            execute(
                "UPDATE ai_users SET tariff_free_checks=%s, telegram_last_checked_at=NOW() WHERE id=%s",
                (free_checks, int(user["id"])),
            )
            return {
                "ok": True,
                "plan": current_plan,
                "changed": False,
                "pending_free_checks": free_checks,
            }
    free_started_sql = ", free_plan_started_at=NOW()" if plan == "free" and current_plan != "free" else ""
    execute(
        f"UPDATE ai_users SET plan=%s, tariff_free_checks=0, telegram_last_checked_at=NOW(){free_started_sql} WHERE id=%s",
        (plan, int(user["id"])),
    )
    if notify_chat_id:
        await _send_message(notify_chat_id, _plan_message(plan))
    return {"ok": True, "plan": plan, "changed": plan != current_plan}


async def _handle_start(chat_id: int, telegram_user_id: int, telegram_username: str, payload: str) -> bool:
    user_id = _parse_verify_payload(payload)
    if not user_id:
        await _send_message(chat_id, "Откройте профиль Griders и нажмите ссылку подтверждения Telegram.")
        return False

    user = fetch_one("SELECT * FROM ai_users WHERE id=%s", (user_id,))
    if not user:
        await _send_message(chat_id, "Аккаунт Griders не найден. Попробуйте создать новую ссылку в профиле.")
        return False
    if not telegram_username:
        await _send_message(chat_id, "У вашего Telegram аккаунта должен быть username. Укажите его в Telegram и повторите подтверждение.")
        return False

    profile_username = normalize_telegram_username(user.get("telegram_username"))
    if profile_username and profile_username != telegram_username:
        await _send_message(
            chat_id,
            f"В профиле Griders указан Telegram @{profile_username}, а вы написали с @{telegram_username}. Исправьте ник в профиле и повторите.",
        )
        return False

    duplicate_username = fetch_one(
        "SELECT id FROM ai_users WHERE telegram_username=%s AND id<>%s LIMIT 1",
        (telegram_username, user_id),
    )
    if duplicate_username:
        await _send_message(chat_id, "Этот Telegram username уже указан в другом аккаунте Griders.")
        return False

    duplicate_user_id = fetch_one(
        "SELECT id FROM ai_users WHERE telegram_user_id=%s AND id<>%s LIMIT 1",
        (telegram_user_id, user_id),
    )
    if duplicate_user_id:
        await _send_message(chat_id, "Этот Telegram аккаунт уже привязан к другому аккаунту Griders.")
        return False

    execute(
        """
        UPDATE ai_users
        SET telegram_username=%s, telegram_user_id=%s, telegram_verified_at=NOW(), telegram_last_checked_at=NULL
        WHERE id=%s
        """,
        (telegram_username, telegram_user_id, user_id),
    )
    refreshed = fetch_one("SELECT * FROM ai_users WHERE id=%s", (user_id,)) or user
    await _send_message(chat_id, "Telegram аккаунт подтверждён. Проверяю подписку на тарифные каналы.")
    await sync_user_tariff(refreshed, notify_chat_id=chat_id)
    return True


async def _handle_username_link(chat_id: int, telegram_user_id: int, telegram_username: str) -> bool:
    if not telegram_user_id or not telegram_username:
        return False
    rows = fetch_all(
        """
        SELECT *
        FROM ai_users
        WHERE telegram_username=%s
          AND (telegram_user_id IS NULL OR telegram_user_id=%s)
        ORDER BY id
        LIMIT 2
        """,
        (telegram_username, telegram_user_id),
    )
    if len(rows) != 1:
        return False
    user = rows[0]
    duplicate_user_id = fetch_one(
        "SELECT id FROM ai_users WHERE telegram_user_id=%s AND id<>%s LIMIT 1",
        (telegram_user_id, int(user["id"])),
    )
    if duplicate_user_id:
        await _send_message(chat_id, "Этот Telegram аккаунт уже привязан к другому аккаунту Griders.")
        return False
    execute(
        """
        UPDATE ai_users
        SET telegram_user_id=%s, telegram_verified_at=NOW(), telegram_last_checked_at=NULL
        WHERE id=%s
        """,
        (telegram_user_id, int(user["id"])),
    )
    refreshed = fetch_one("SELECT * FROM ai_users WHERE id=%s", (int(user["id"]),)) or user
    await _send_message(chat_id, "Telegram аккаунт подтверждён по username из профиля Griders. Проверяю подписку на тарифные каналы.")
    await sync_user_tariff(refreshed, notify_chat_id=chat_id)
    return True


async def _subscription_plan(telegram_user_id: int, current_plan: str = "free") -> str | None:
    premium_channel = _tariff_channel_id("premium") or settings.TELEGRAM_TARIFF_PREMIUM_CHANNEL_ID.strip()
    start_channel = _tariff_channel_id("start") or settings.TELEGRAM_TARIFF_START_CHANNEL_ID.strip()
    if not premium_channel and not start_channel:
        return None
    premium_member = await _is_channel_member(premium_channel, telegram_user_id) if premium_channel else False
    start_member = await _is_channel_member(start_channel, telegram_user_id) if start_channel else False
    if premium_member is True:
        return "premium"
    if start_member is True and (premium_member is False or current_plan != "premium"):
        return "start"
    if premium_member is None or start_member is None:
        return None
    return "free"


def _tariff_channel_id(plan: str) -> str:
    row = fetch_one("SELECT chat_id FROM ai_tariff_channels WHERE plan=%s", (plan,))
    return str((row or {}).get("chat_id") or "").strip()


def _upsert_tariff_channel(plan: str, chat_id: str, title: str) -> None:
    execute(
        """
        INSERT INTO ai_tariff_channels (plan, chat_id, title)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE chat_id=VALUES(chat_id), title=VALUES(title)
        """,
        (plan, chat_id, title[:190]),
    )


def _infer_plan_from_title(title: str) -> str | None:
    normalized = title.lower().replace("_", " ").replace("-", " ")
    if "premium" in normalized or "премиум" in normalized:
        return "premium"
    if "start" in normalized or "старт" in normalized:
        return "start"
    return None


async def _is_channel_member(channel_id: str, telegram_user_id: int) -> bool | None:
    try:
        data = await _telegram_api("getChatMember", {"chat_id": channel_id, "user_id": telegram_user_id})
    except Exception as exc:
        logger.warning("getChatMember failed for channel %s user %s: %r", channel_id, telegram_user_id, exc)
        return None
    member = data.get("result") or {}
    status = str(member.get("status") or "")
    if status in ACTIVE_MEMBER_STATUSES:
        return True
    return status == "restricted" and bool(member.get("is_member"))


async def _send_message(chat_id: int, text: str) -> None:
    if not settings.TELEGRAM_TARIFF_BOT_TOKEN:
        return
    try:
        await _telegram_api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    except Exception as exc:
        logger.warning("sendMessage failed for chat %s: %s", chat_id, exc)


async def _telegram_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_TARIFF_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API error: {response.status_code}")
    return data


def _verify_payload(user_id: int) -> str:
    signature = hmac.new(settings.APP_SECRET.encode(), str(int(user_id)).encode(), hashlib.sha256).digest()
    short = base64.urlsafe_b64encode(signature[:12]).decode().rstrip("=")
    return f"verify_{int(user_id)}_{short}"


def _parse_verify_payload(payload: str) -> int | None:
    parts = payload.split("_")
    if len(parts) != 3 or parts[0] != "verify":
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    expected = _verify_payload(user_id)
    return user_id if hmac.compare_digest(payload, expected) else None


def _plan_message(plan: str) -> str:
    if plan == "premium":
        return "Подписка найдена. Ваш тариф Griders: Премиум."
    if plan == "start":
        return "Подписка найдена. Ваш тариф Griders: Старт."
    return "Активная подписка на тарифные каналы не найдена. Ваш тариф Griders: Бесплатный."
