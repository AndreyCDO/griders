"""Telegram support bot for Griders."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import sys
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("griders_support_bot")

MOSCOW_TZ = timezone(timedelta(hours=3), name="Europe/Moscow")
DEFAULT_ADMIN_ID = 6244691896


@dataclass(frozen=True)
class Config:
    token: str
    username: str
    db_path: Path
    admin_ids: set[int]
    poll_timeout: int


def load_config() -> Config:
    token = os.getenv("SUPPORT_BOT_TOKEN", "").strip()
    if not token:
        token = os.getenv("TELEGRAM_SUPPORT_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SUPPORT_BOT_TOKEN is required")

    admin_ids = {
        int(item.strip())
        for item in os.getenv("SUPPORT_ADMIN_IDS", str(DEFAULT_ADMIN_ID)).split(",")
        if item.strip().isdigit()
    }
    return Config(
        token=token,
        username=os.getenv("SUPPORT_BOT_USERNAME", "griders_support_bot").strip().lstrip("@"),
        db_path=Path(os.getenv("SUPPORT_DB_PATH", "support_bot.sqlite3")),
        admin_ids=admin_ids or {DEFAULT_ADMIN_ID},
        poll_timeout=int(os.getenv("SUPPORT_POLL_TIMEOUT", "25")),
    )


class SupportBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.api_url = f"https://api.telegram.org/bot{config.token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=15.0))

    async def run(self) -> None:
        init_db(self.config.db_path)
        await self.api("deleteWebhook", {"drop_pending_updates": False})
        me = await self.api("getMe")
        logger.info("Started @%s (%s)", me.get("username"), me.get("id"))
        await self.poll_loop()

    async def poll_loop(self) -> None:
        offset = 0
        while True:
            try:
                updates = await self.api(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": self.config.poll_timeout,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    await self.handle_update(update)
            except Exception:
                logger.exception("Polling failed")
                await asyncio.sleep(3)

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self.handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = int(sender.get("id") or 0)
        chat_id = int(chat.get("id") or 0)
        if not user_id or not chat_id:
            return
        if self.is_admin(user_id):
            await self.handle_admin_message(message)
        else:
            await self.handle_user_message(message)

    async def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        sender = callback.get("from") or {}
        admin_id = int(sender.get("id") or 0)
        if not self.is_admin(admin_id):
            await self.answer_callback(callback_id, "Недоступно")
            return
        data = str(callback.get("data") or "")
        if data == "dialogs":
            await self.answer_callback(callback_id)
            await self.send_contacts(int(sender["id"]), admin_id)
            return
        if data.startswith("close:"):
            try:
                user_id = int(data.split(":", 1)[1])
            except ValueError:
                await self.answer_callback(callback_id)
                return
            user = get_user(self.config.db_path, user_id)
            if not user:
                await self.answer_callback(callback_id, "Р”РёР°Р»РѕРі РЅРµ РЅР°Р№РґРµРЅ")
                return
            close_dialog(self.config.db_path, user_id)
            clear_active_dialog_for_user(self.config.db_path, user_id)
            await self.answer_callback(callback_id, "Диалог закрыт")
            await self.send_message(
                int(sender["id"]),
                f"Диалог закрыт: {profile_line(user)}\n\n"
                "Он исчез из активных. Если пользователь напишет снова, бот создаст новое обращение.",
                [[{"text": "Последние диалоги", "callback_data": "dialogs"}]],
            )
            return
        if data.startswith("select:"):
            try:
                user_id = int(data.split(":", 1)[1])
            except ValueError:
                await self.answer_callback(callback_id)
                return
            user = get_user(self.config.db_path, user_id)
            if not user:
                await self.answer_callback(callback_id, "Диалог не найден")
                return
            if not row_is_open(user):
                await self.answer_callback(callback_id, "Диалог уже закрыт")
                return
            set_active_dialog(self.config.db_path, admin_id, user_id)
            await self.answer_callback(callback_id, f"Выбран диалог: {display_name(user)}")
            await self.send_message(
                int(sender["id"]),
                f"Активный диалог: {profile_line(user)}\n\n"
                "Теперь можно писать сообщения без reply. "
                "Чтобы ответить другому пользователю, нажмите кнопку под его сообщением или ответьте reply на уведомление.",
            )
            return
        await self.answer_callback(callback_id)

    async def handle_user_message(self, message: dict[str, Any]) -> None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        chat_id = int(chat.get("id") or 0)
        user_id = int(sender.get("id") or 0)
        command, _ = command_name_and_args(text)

        if command == "start":
            await self.send_message(
                chat_id,
                "Здравствуйте! Это поддержка Griders.\n\n"
                "Напишите ваш вопрос одним или несколькими сообщениями. "
                "Администратор получит его здесь и ответит вам в этом же боте.",
            )
            return
        if command == "id":
            await self.send_message(chat_id, f"Ваш Telegram ID: <code>{user_id}</code>")
            return

        is_new_dialog = not is_dialog_open(self.config.db_path, user_id)
        upsert_user(self.config.db_path, sender, chat_id)
        user = get_user(self.config.db_path, user_id)
        if not user:
            return
        await self.notify_admins(message, user, is_new_dialog=is_new_dialog)
        await self.send_message(chat_id, "Спасибо, сообщение передано в поддержку Griders.")

    async def notify_admins(self, message: dict[str, Any], user: sqlite3.Row) -> None:
        text = message_text(message)
        for admin_id in self.config.admin_ids:
            try:
                if text:
                    sent = await self.send_message(
                        admin_id,
                        admin_notification_text(user, text),
                        [[{"text": "Выбрать диалог", "callback_data": f"select:{int(user['telegram_user_id'])}"}]],
                    )
                    map_admin_message(
                        self.config.db_path,
                        int(sent["message_id"]),
                        admin_id,
                        int(user["telegram_user_id"]),
                        int(user["chat_id"]),
                    )
                else:
                    header = await self.send_message(
                        admin_id,
                        admin_notification_text(user, "Пользователь прислал вложение."),
                        [[{"text": "Выбрать диалог", "callback_data": f"select:{int(user['telegram_user_id'])}"}]],
                    )
                    copied = await self.copy_message(admin_id, int(user["chat_id"]), int(message["message_id"]))
                    for admin_message_id in [int(header["message_id"]), int(copied["message_id"])]:
                        map_admin_message(
                            self.config.db_path,
                            admin_message_id,
                            admin_id,
                            int(user["telegram_user_id"]),
                            int(user["chat_id"]),
                        )
            except Exception as exc:
                logger.warning("Admin notification failed for %s: %s", admin_id, exc)

    async def notify_admins(self, message: dict[str, Any], user: sqlite3.Row) -> None:
        text = message_text(message)
        has_attachment = message_has_attachment(message)
        for admin_id in self.config.admin_ids:
            try:
                header = await self.send_message(
                    admin_id,
                    admin_notification_text(user, text or attachment_summary(message)),
                    dialog_keyboard(user, include_dialogs=True),
                )
                map_admin_message(
                    self.config.db_path,
                    int(header["message_id"]),
                    admin_id,
                    int(user["telegram_user_id"]),
                    int(user["chat_id"]),
                )
                if has_attachment:
                    copied = await self.copy_message(admin_id, int(user["chat_id"]), int(message["message_id"]))
                    map_admin_message(
                        self.config.db_path,
                        int(copied["message_id"]),
                        admin_id,
                        int(user["telegram_user_id"]),
                        int(user["chat_id"]),
                    )
            except Exception as exc:
                logger.warning("Admin notification failed for %s: %s", admin_id, safe_error(exc))

    async def notify_admins(self, message: dict[str, Any], user: sqlite3.Row, is_new_dialog: bool = False) -> None:
        text = message_text(message)
        has_attachment = message_has_attachment(message)
        for admin_id in self.config.admin_ids:
            try:
                header = await self.send_message(
                    admin_id,
                    admin_notification_text(user, text or attachment_summary(message), is_new_dialog=is_new_dialog),
                    dialog_keyboard(user, include_dialogs=True, include_close=True),
                )
                map_admin_message(
                    self.config.db_path,
                    int(header["message_id"]),
                    admin_id,
                    int(user["telegram_user_id"]),
                    int(user["chat_id"]),
                )
                if has_attachment:
                    copied = await self.copy_message(admin_id, int(user["chat_id"]), int(message["message_id"]))
                    map_admin_message(
                        self.config.db_path,
                        int(copied["message_id"]),
                        admin_id,
                        int(user["telegram_user_id"]),
                        int(user["chat_id"]),
                    )
            except Exception as exc:
                logger.warning("Admin notification failed for %s: %s", admin_id, safe_error(exc))

    async def handle_admin_message(self, message: dict[str, Any]) -> None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        admin_id = int(sender.get("id") or 0)
        chat_id = int(chat.get("id") or 0)
        command, args = command_name_and_args(text)

        if command in {"start", "help", "admin"}:
            await self.send_admin_help(chat_id)
            return
        if command == "id":
            await self.send_message(chat_id, f"Ваш Telegram ID: <code>{admin_id}</code>")
            return
        if command == "contacts":
            await self.send_contacts(chat_id, admin_id)
            return
        if command == "chat":
            await self.select_chat(chat_id, admin_id, args)
            return
        if command == "reply":
            await self.reply_by_command(chat_id, admin_id, args)
            return

        route = self.route_from_reply(message, admin_id)
        if not route:
            active_user_id = get_active_dialog(self.config.db_path, admin_id)
            route = get_route_for_user(self.config.db_path, active_user_id) if active_user_id else None
        if not route:
            await self.send_message(
                chat_id,
                "Не выбран пользователь для ответа.\n\n"
                "Ответьте reply на уведомление от пользователя, нажмите «Выбрать диалог» под его сообщением "
                "или используйте /reply USER_ID текст.",
            )
            return

        await self.deliver_admin_reply(message, route)
        await self.send_message(chat_id, f"Отправлено пользователю: {profile_line(route)}")

    def route_from_reply(self, message: dict[str, Any], admin_id: int) -> sqlite3.Row | None:
        reply = message.get("reply_to_message") or {}
        reply_message_id = int(reply.get("message_id") or 0)
        if not reply_message_id:
            return None
        return get_route_for_admin_message(self.config.db_path, admin_id, reply_message_id)

    async def reply_by_command(self, chat_id: int, admin_id: int, args: str) -> None:
        user_id_raw, _, text = args.partition(" ")
        if not user_id_raw.isdigit() or not text.strip():
            await self.send_message(chat_id, "Формат: /reply USER_ID текст ответа")
            return
        route = get_route_for_user(self.config.db_path, int(user_id_raw))
        if not route:
            await self.send_message(chat_id, "Пользователь не найден. Проверьте USER_ID в /contacts.")
            return
        await self.send_message(int(route["chat_id"]), f"<b>Поддержка Griders:</b>\n\n{html.escape(text.strip())}")
        set_active_dialog(self.config.db_path, admin_id, int(route["telegram_user_id"]))
        await self.send_message(chat_id, f"Отправлено пользователю: {profile_line(route)}")

    async def deliver_admin_reply(self, message: dict[str, Any], route: sqlite3.Row) -> None:
        text = str(message.get("text") or "").strip()
        if text:
            await self.send_message(int(route["chat_id"]), f"<b>Поддержка Griders:</b>\n\n{html.escape(text)}")
            return
        caption = str(message.get("caption") or "").strip()
        if caption:
            await self.send_message(int(route["chat_id"]), "<b>Поддержка Griders:</b>")
        await self.copy_message(int(route["chat_id"]), int(message["chat"]["id"]), int(message["message_id"]))

    async def select_chat(self, chat_id: int, admin_id: int, args: str) -> None:
        user_id_raw = args.strip()
        if not user_id_raw.isdigit():
            await self.send_message(chat_id, "Формат: /chat USER_ID")
            return
        user = get_user(self.config.db_path, int(user_id_raw))
        if not user:
            await self.send_message(chat_id, "Диалог не найден. Используйте /contacts.")
            return
        if not row_is_open(user):
            await self.send_message(chat_id, "Этот диалог закрыт. Новое сообщение пользователя снова откроет обращение.")
            return
        set_active_dialog(self.config.db_path, admin_id, int(user["telegram_user_id"]))
        await self.send_message(chat_id, f"Активный диалог: {profile_line(user)}")

    async def send_contacts(self, chat_id: int) -> None:
        rows = recent_users(self.config.db_path)
        if not rows:
            await self.send_message(chat_id, "Пока нет обращений в поддержку.")
            return
        lines = ["Последние диалоги:"]
        for row in rows:
            lines.append(f"{profile_line(row)}\n/chat {int(row['telegram_user_id'])}")
        await self.send_message(chat_id, "\n\n".join(lines))

    async def send_contacts(self, chat_id: int, admin_id: int) -> None:
        rows = recent_users(self.config.db_path)
        if not rows:
            await self.send_message(chat_id, "Пока нет обращений в поддержку.")
            return
        active_user_id = get_active_dialog(self.config.db_path, admin_id)
        lines = ["Последние диалоги. Нажмите кнопку, чтобы переключиться:"]
        for row in rows:
            marker = " активный" if active_user_id == int(row["telegram_user_id"]) else ""
            lines.append(f"{profile_line(row)}{html.escape(marker)}\n/chat {int(row['telegram_user_id'])}")
        await self.send_message(chat_id, "\n\n".join(lines), dialogs_keyboard(rows))

    async def send_admin_help(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "Вы администратор поддержки Griders.\n\n"
            "Как отвечать:\n"
            "1. Ответьте reply на сообщение с обращением пользователя.\n"
            "2. Или нажмите «Выбрать диалог» и пишите следующие сообщения без reply.\n"
            "3. Или используйте /reply USER_ID текст.\n\n"
            "Команды:\n"
            "/contacts - последние диалоги\n"
            "/chat USER_ID - выбрать активный диалог\n"
            "/reply USER_ID текст - отправить ответ\n"
            "/id - показать ваш Telegram ID",
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: list[list[dict[str, str]]] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self.api("sendMessage", payload)

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int) -> Any:
        return await self.api("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})

    async def answer_callback(self, callback_id: str, text: str = "") -> Any:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return await self.api("answerCallbackQuery", payload)

    async def api(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        response = await self.client.post(f"{self.api_url}/{method}", json=payload or {})
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram HTTP {response.status_code} for {method}: {response.text[:500]}")
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error for {method}: {data}")
        return data.get("result")

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.config.admin_ids


def db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path(".") else None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path) -> None:
    with closing(db(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS support_users (
                telegram_user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                closed_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_message_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message_routes (
                admin_message_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (admin_message_id, admin_id)
            );

            CREATE TABLE IF NOT EXISTS admin_state (
                admin_id INTEGER PRIMARY KEY,
                active_user_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "support_users", "status", "TEXT NOT NULL DEFAULT 'open'")
        ensure_column(conn, "support_users", "closed_at", "TEXT")
        conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upsert_user(path: Path, sender: dict[str, Any], chat_id: int) -> None:
    now = now_iso()
    with closing(db(path)) as conn:
        conn.execute(
            """
            INSERT INTO support_users(telegram_user_id, chat_id, username, first_name, last_name, first_seen_at, last_message_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                status='open',
                closed_at=NULL,
                last_message_at=excluded.last_message_at
            """,
            (
                int(sender["id"]),
                chat_id,
                sender.get("username") or "",
                sender.get("first_name") or "",
                sender.get("last_name") or "",
                now,
                now,
            ),
        )
        conn.commit()


def is_dialog_open(path: Path, telegram_user_id: int) -> bool:
    user = get_user(path, telegram_user_id)
    return row_is_open(user) if user else False


def row_is_open(row: sqlite3.Row | None) -> bool:
    if not row:
        return False
    try:
        return str(row["status"] or "open") == "open"
    except (IndexError, KeyError):
        return True


def close_dialog(path: Path, telegram_user_id: int) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            "UPDATE support_users SET status='closed', closed_at=? WHERE telegram_user_id=?",
            (now_iso(), telegram_user_id),
        )
        conn.commit()


def clear_active_dialog_for_user(path: Path, telegram_user_id: int) -> None:
    with closing(db(path)) as conn:
        conn.execute("DELETE FROM admin_state WHERE active_user_id=?", (telegram_user_id,))
        conn.commit()


def map_admin_message(path: Path, admin_message_id: int, admin_id: int, telegram_user_id: int, chat_id: int) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO message_routes(admin_message_id, admin_id, telegram_user_id, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (admin_message_id, admin_id, telegram_user_id, chat_id, now_iso()),
        )
        conn.commit()


def get_user(path: Path, telegram_user_id: int) -> sqlite3.Row | None:
    with closing(db(path)) as conn:
        return conn.execute("SELECT * FROM support_users WHERE telegram_user_id=?", (telegram_user_id,)).fetchone()


def recent_users(path: Path, limit: int = 10) -> list[sqlite3.Row]:
    with closing(db(path)) as conn:
        return list(conn.execute("SELECT * FROM support_users WHERE status='open' ORDER BY last_message_at DESC LIMIT ?", (limit,)))


def get_route_for_admin_message(path: Path, admin_id: int, admin_message_id: int) -> sqlite3.Row | None:
    with closing(db(path)) as conn:
        return conn.execute(
            """
            SELECT u.*, r.chat_id
            FROM message_routes r
            JOIN support_users u ON u.telegram_user_id=r.telegram_user_id
            WHERE r.admin_id=? AND r.admin_message_id=? AND u.status='open'
            """,
            (admin_id, admin_message_id),
        ).fetchone()


def get_route_for_user(path: Path, telegram_user_id: int) -> sqlite3.Row | None:
    user = get_user(path, telegram_user_id)
    return user if row_is_open(user) else None


def set_active_dialog(path: Path, admin_id: int, telegram_user_id: int) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            """
            INSERT INTO admin_state(admin_id, active_user_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                active_user_id=excluded.active_user_id,
                updated_at=excluded.updated_at
            """,
            (admin_id, telegram_user_id, now_iso()),
        )
        conn.commit()


def get_active_dialog(path: Path, admin_id: int) -> int | None:
    with closing(db(path)) as conn:
        row = conn.execute("SELECT active_user_id FROM admin_state WHERE admin_id=?", (admin_id,)).fetchone()
    return int(row["active_user_id"]) if row else None


def now_iso() -> str:
    return datetime.now(MOSCOW_TZ).isoformat(timespec="seconds")


def command_name_and_args(text: str) -> tuple[str, str]:
    first, _, rest = text.strip().partition(" ")
    if not first.startswith("/"):
        return "", text.strip()
    command = first[1:].split("@", 1)[0].lower()
    return command, rest.strip()


def message_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    caption = str(message.get("caption") or "").strip()
    return text or caption


def message_has_attachment(message: dict[str, Any]) -> bool:
    media_keys = {
        "animation",
        "audio",
        "contact",
        "dice",
        "document",
        "game",
        "invoice",
        "location",
        "photo",
        "poll",
        "sticker",
        "story",
        "venue",
        "video",
        "video_note",
        "voice",
    }
    return any(key in message for key in media_keys)


def attachment_summary(message: dict[str, Any]) -> str:
    labels = [
        ("photo", "Пользователь прислал изображение."),
        ("video", "Пользователь прислал видео."),
        ("document", "Пользователь прислал файл."),
        ("voice", "Пользователь прислал голосовое сообщение."),
        ("audio", "Пользователь прислал аудио."),
        ("sticker", "Пользователь прислал стикер."),
        ("animation", "Пользователь прислал GIF/анимацию."),
        ("video_note", "Пользователь прислал видеосообщение."),
        ("contact", "Пользователь прислал контакт."),
        ("location", "Пользователь прислал геолокацию."),
        ("venue", "Пользователь прислал место."),
        ("poll", "Пользователь прислал опрос."),
    ]
    for key, label in labels:
        if key in message:
            return label
    return "Пользователь прислал вложение."


def dialog_keyboard(
    user: sqlite3.Row,
    include_dialogs: bool = False,
    include_close: bool = False,
) -> list[list[dict[str, str]]]:
    keyboard = [[{"text": f"Ответить: {button_label(user)}", "callback_data": f"select:{int(user['telegram_user_id'])}"}]]
    if include_close:
        keyboard.append([{"text": "Закрыть диалог", "callback_data": f"close:{int(user['telegram_user_id'])}"}])
    if include_dialogs:
        keyboard.append([{"text": "Последние диалоги", "callback_data": "dialogs"}])
    return keyboard


def dialogs_keyboard(rows: list[sqlite3.Row]) -> list[list[dict[str, str]]]:
    keyboard = []
    for row in rows[:10]:
        user_id = int(row["telegram_user_id"])
        keyboard.append(
            [
                {"text": button_label(row), "callback_data": f"select:{user_id}"},
                {"text": "Закрыть", "callback_data": f"close:{user_id}"},
            ]
        )
    return keyboard


def button_label(row: sqlite3.Row) -> str:
    label = display_name(row)
    user_id = int(row["telegram_user_id"])
    if len(label) > 34:
        label = f"{label[:31]}..."
    return f"{label} | {user_id}"


def display_name(row: sqlite3.Row) -> str:
    username = str(row["username"] or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    full_name = " ".join(
        part
        for part in [str(row["first_name"] or "").strip(), str(row["last_name"] or "").strip()]
        if part
    )
    return full_name or str(row["telegram_user_id"])


def profile_line(row: sqlite3.Row) -> str:
    username = str(row["username"] or "").strip().lstrip("@")
    username_part = f" @{html.escape(username)}" if username else ""
    name = html.escape(display_name(row), quote=False)
    return f"<b>{name}</b>{username_part} | ID <code>{int(row['telegram_user_id'])}</code>"


def safe_error(exc: Exception) -> str:
    text = str(exc)
    token = os.getenv("SUPPORT_BOT_TOKEN", "").strip()
    if token:
        text = text.replace(token, "***")
    return text[:500]


def admin_notification_text(user: sqlite3.Row, text: str, is_new_dialog: bool = True) -> str:
    title = "Новое обращение в поддержку Griders" if is_new_dialog else "Сообщение в открытом диалоге Griders"
    return (
        f"{title}\n\n"
        f"Пользователь: {profile_line(user)}\n"
        f"Chat ID: <code>{int(user['chat_id'])}</code>\n\n"
        f"Сообщение:\n{html.escape(text)}\n\n"
        "Ответьте reply на это сообщение, чтобы написать пользователю."
    )


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = SupportBot(load_config())
    if "--check" in sys.argv:
        init_db(bot.config.db_path)
        me = await bot.api("getMe")
        print(f"OK @{me.get('username')} id={me.get('id')}")
        await bot.client.aclose()
        return
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
