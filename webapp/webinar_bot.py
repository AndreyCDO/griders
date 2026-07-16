"""Telegram bot for Griders webinar registration."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("griders_webinar_bot")

MOSCOW_TZ = timezone(timedelta(hours=3), name="Europe/Moscow")
DEFAULT_WEBINAR_TITLE = "Griders.ru: как работает автотрейдинг через Cryptorg Ghost Bot"
DEFAULT_WEBINAR_START_AT = "2026-07-18 15:00"
DEFAULT_WEBINAR_URL = "https://telemost.yandex.ru/j/50722545037884"
REMINDERS = {
    "day": ("за сутки", timedelta(days=1)),
    "3h": ("за 3 часа", timedelta(hours=3)),
    "1h": ("за 1 час", timedelta(hours=1)),
}


@dataclass(frozen=True)
class Config:
    token: str
    username: str
    db_path: Path
    admin_ids: set[int]
    poll_timeout: int


def load_config() -> Config:
    token = os.getenv("WEBINAR_BOT_TOKEN", "").strip()
    if not token:
        token = os.getenv("TELEGRAM_WEBINAR_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WEBINAR_BOT_TOKEN is required")

    admin_ids = {
        int(item.strip())
        for item in os.getenv("WEBINAR_ADMIN_IDS", "").split(",")
        if item.strip().isdigit()
    }
    db_path = Path(os.getenv("WEBINAR_DB_PATH", "webinar_bot.sqlite3"))
    return Config(
        token=token,
        username=os.getenv("WEBINAR_BOT_USERNAME", "griders_webinar_bot").strip().lstrip("@"),
        db_path=db_path,
        admin_ids=admin_ids,
        poll_timeout=int(os.getenv("WEBINAR_POLL_TIMEOUT", "25")),
    )


class WebinarBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.api_url = f"https://api.telegram.org/bot{config.token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=15.0))

    async def run(self) -> None:
        init_db(self.config.db_path)
        seed_settings(self.config.db_path)
        me = await self.api("getMe")
        logger.info("Started @%s (%s)", me.get("username"), me.get("id"))
        await asyncio.gather(self.poll_loop(), self.reminder_loop())

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

    async def reminder_loop(self) -> None:
        while True:
            try:
                await self.send_due_reminders()
            except Exception:
                logger.exception("Reminder loop failed")
            await asyncio.sleep(30)

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self.handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = int(chat.get("id") or 0)
        user_id = int(sender.get("id") or 0)
        if not chat_id or not user_id:
            return
        command, args = command_name_and_args(text)

        if command == "start":
            await self.send_start(chat_id, sender)
        elif command == "id":
            await self.send_message(chat_id, f"Ваш Telegram ID: <code>{user_id}</code>")
        elif command == "help":
            await self.send_help(chat_id, user_id)
        elif command == "cancel":
            await self.cancel_registration(chat_id, user_id)
        elif command == "stats" and self.is_admin(user_id):
            await self.send_stats(chat_id)
        elif command == "export" and self.is_admin(user_id):
            await self.send_export(chat_id)
        elif command == "preview" and self.is_admin(user_id):
            await self.send_start(chat_id, sender, show_admin_menu=False)
        elif command == "test_reminder" and self.is_admin(user_id):
            await self.send_test_reminder(chat_id, args)
        elif command == "test_reminder_day" and self.is_admin(user_id):
            await self.send_test_reminder(chat_id, "day")
        elif command == "test_reminder_3h" and self.is_admin(user_id):
            await self.send_test_reminder(chat_id, "3h")
        elif command == "test_reminder_1h" and self.is_admin(user_id):
            await self.send_test_reminder(chat_id, "1h")
        elif command == "broadcast" and self.is_admin(user_id):
            await self.broadcast(chat_id, args)
        elif command == "set_webinar" and self.is_admin(user_id):
            await self.set_webinar(chat_id, args)
        elif command == "set_recording" and self.is_admin(user_id):
            await self.set_recording(chat_id, args)
        elif command == "admin" and self.is_admin(user_id):
            await self.send_admin_help(chat_id)
        else:
            await self.send_start(chat_id, sender)

    async def handle_callback(self, callback: dict[str, Any]) -> None:
        data = str(callback.get("data") or "")
        callback_id = str(callback.get("id") or "")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        sender = callback.get("from") or {}
        chat_id = int(chat.get("id") or 0)
        user_id = int(sender.get("id") or 0)
        if not chat_id or not user_id:
            return
        if data == "register":
            upsert_participant(self.config.db_path, sender, chat_id)
            await self.answer_callback(callback_id, "Вы зарегистрированы")
            await self.send_registered(chat_id)
        elif data == "cancel":
            set_participant_status(self.config.db_path, user_id, "cancelled")
            await self.answer_callback(callback_id, "Регистрация отменена")
            await self.send_cancelled(chat_id)
        elif data == "details":
            await self.answer_callback(callback_id)
            await self.send_registered(chat_id)
        else:
            await self.answer_callback(callback_id)

    async def send_start(self, chat_id: int, sender: dict[str, Any], show_admin_menu: bool = True) -> None:
        user_id = int(sender.get("id") or 0)
        if show_admin_menu and self.is_admin(user_id):
            await self.send_admin_help(chat_id)
            return
        webinar = get_webinar(self.config.db_path)
        registered = is_registered(self.config.db_path, user_id)
        text = start_text(webinar, registered)
        keyboard = start_keyboard(webinar, registered)
        await self.send_message(chat_id, text, keyboard)

    async def send_registered(self, chat_id: int) -> None:
        webinar = get_webinar(self.config.db_path)
        await self.send_message(chat_id, registered_text(webinar), registered_keyboard(webinar))

    async def send_cancelled(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "Регистрация отменена.\n\nЕсли передумаете, нажмите кнопку ниже — я снова добавлю вас в список участников.",
            [[{"text": "Зарегистрироваться снова", "callback_data": "register"}]],
        )

    async def send_help(self, chat_id: int, user_id: int) -> None:
        if self.is_admin(user_id):
            await self.send_admin_help(chat_id)
            return
        await self.send_message(
            chat_id,
            "Я регистрирую на ознакомительный вебинар по Griders.ru и пришлю напоминания перед началом.\n\n"
            "Команды:\n"
            "/start — регистрация\n"
            "/id — показать ваш Telegram ID\n"
            "/cancel — отменить регистрацию",
        )

    async def send_admin_help(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "Вы администратор вебинарного бота Griders.\n\n"
            "Доступные команды:\n\n"
            "/start — показать это админское меню\n"
            "/admin — показать это админское меню\n"
            "/stats — показать количество всех контактов, регистраций и отмен\n"
            "/export — выгрузить список участников: один Telegram-ник на строку\n"
            "/preview — посмотреть стартовый экран как обычный участник\n"
            "/test_reminder_day — отправить себе тест напоминания за сутки\n"
            "/test_reminder_3h — отправить себе тест напоминания за 3 часа\n"
            "/test_reminder_1h — отправить себе тест напоминания за 1 час\n"
            "/broadcast текст — отправить текст всем зарегистрированным участникам\n"
            "/set_webinar 2026-07-18 15:00 | ссылка — изменить дату, время и ссылку вебинара\n"
            "/set_recording ссылка — добавить ссылку на запись после вебинара\n"
            "/id — показать ваш Telegram ID",
        )

    async def send_stats(self, chat_id: int) -> None:
        with db(self.config.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM participants WHERE status='registered'").fetchone()[0]
            cancelled = conn.execute("SELECT COUNT(*) FROM participants WHERE status='cancelled'").fetchone()[0]
        await self.send_message(
            chat_id,
            f"Статистика @{self.config.username}\n\n"
            f"Всего касаний: {total}\n"
            f"Зарегистрированы: {active}\n"
            f"Отменили регистрацию: {cancelled}",
        )

    async def send_export(self, chat_id: int) -> None:
        with db(self.config.db_path) as conn:
            rows = conn.execute(
                """
                SELECT telegram_user_id, username, first_name, last_name
                FROM participants
                WHERE status='registered'
                ORDER BY registered_at DESC
                """
            ).fetchall()
        names = [participant_export_name(row) for row in rows]
        content = "\n".join(names) if names else "Пока нет зарегистрированных участников."
        await self.send_message(chat_id, f"<pre>{html.escape(content)}</pre>")

    async def send_test_reminder(self, chat_id: int, args: str) -> None:
        reminder_key = args.strip().lower() or "1h"
        if reminder_key not in REMINDERS:
            await self.send_message(chat_id, "Используйте: /test_reminder_day, /test_reminder_3h или /test_reminder_1h")
            return
        await self.send_message(chat_id, reminder_text(get_webinar(self.config.db_path), reminder_key), webinar_keyboard(get_webinar(self.config.db_path)))

    async def broadcast(self, chat_id: int, args: str) -> None:
        message = args.strip()
        if not message:
            await self.send_message(chat_id, "Напишите так: /broadcast текст сообщения")
            return
        rows = active_participants(self.config.db_path)
        sent = 0
        for row in rows:
            try:
                await self.send_message(int(row["chat_id"]), message)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning("Broadcast failed for %s: %s", row["telegram_user_id"], exc)
        await self.send_message(chat_id, f"Рассылка отправлена: {sent} из {len(rows)}")

    async def set_webinar(self, chat_id: int, args: str) -> None:
        payload = args.strip()
        if not payload or "|" not in payload:
            await self.send_message(chat_id, "Формат: /set_webinar 2026-07-18 15:00 | https://...")
            return
        date_part, url = [item.strip() for item in payload.split("|", 1)]
        try:
            parse_local_datetime(date_part)
        except ValueError:
            await self.send_message(chat_id, "Не понял дату. Формат: 2026-07-18 15:00")
            return
        set_setting(self.config.db_path, "webinar_start_at", date_part)
        set_setting(self.config.db_path, "webinar_url", url)
        set_setting(self.config.db_path, "recording_url", "")
        clear_reminders(self.config.db_path)
        await self.send_message(chat_id, "Вебинар обновлён. Ссылка на старую запись и отметки напоминаний очищены.")

    async def set_recording(self, chat_id: int, args: str) -> None:
        url = args.strip()
        if not url:
            await self.send_message(chat_id, "Формат: /set_recording https://...")
            return
        set_setting(self.config.db_path, "recording_url", url)
        webinar = get_webinar(self.config.db_path)
        rows = active_participants(self.config.db_path)
        sent = 0
        for row in rows:
            try:
                await self.send_message(int(row["chat_id"]), recording_available_text(webinar), recording_keyboard(webinar))
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.warning("Recording delivery failed for %s: %s", row["telegram_user_id"], exc)
        await self.send_message(chat_id, f"Ссылка на запись сохранена и отправлена: {sent} из {len(rows)}\n{html.escape(url)}")

    async def cancel_registration(self, chat_id: int, user_id: int) -> None:
        set_participant_status(self.config.db_path, user_id, "cancelled")
        await self.send_cancelled(chat_id)

    async def send_due_reminders(self) -> None:
        webinar = get_webinar(self.config.db_path)
        start_at = parse_local_datetime(webinar["start_at"])
        now = datetime.now(MOSCOW_TZ)
        if now >= start_at:
            return
        for key, (_, offset) in REMINDERS.items():
            due_at = start_at - offset
            if now < due_at:
                continue
            rows = participants_due_for_reminder(self.config.db_path, key)
            for row in rows:
                try:
                    await self.send_message(int(row["chat_id"]), reminder_text(webinar, key), webinar_keyboard(webinar))
                    mark_reminder_sent(self.config.db_path, int(row["telegram_user_id"]), key)
                    await asyncio.sleep(0.05)
                except Exception as exc:
                    logger.warning("Reminder %s failed for %s: %s", key, row["telegram_user_id"], exc)

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

    async def answer_callback(self, callback_id: str, text: str = "") -> Any:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return await self.api("answerCallbackQuery", payload)

    async def api(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        response = await self.client.post(f"{self.api_url}/{method}", json=payload or {})
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error for {method}: {data}")
        return data.get("result")

    def is_admin(self, user_id: int) -> bool:
        return bool(self.config.admin_ids and user_id in self.config.admin_ids)


def db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path(".") else None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path) -> None:
    with closing(db(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
                telegram_user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                status TEXT NOT NULL DEFAULT 'registered',
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminder_log (
                telegram_user_id INTEGER NOT NULL,
                reminder_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (telegram_user_id, reminder_key)
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()


def seed_settings(path: Path) -> None:
    defaults = {
        "webinar_title": os.getenv("WEBINAR_TITLE", DEFAULT_WEBINAR_TITLE),
        "webinar_start_at": os.getenv("WEBINAR_START_AT", DEFAULT_WEBINAR_START_AT),
        "webinar_url": os.getenv("WEBINAR_URL", DEFAULT_WEBINAR_URL),
        "recording_url": os.getenv("WEBINAR_RECORDING_URL", ""),
    }
    with closing(db(path)) as conn:
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO bot_settings(key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def set_setting(path: Path, key: str, value: str) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            "INSERT INTO bot_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_webinar(path: Path) -> dict[str, str]:
    with closing(db(path)) as conn:
        rows = conn.execute("SELECT key, value FROM bot_settings").fetchall()
    values = {str(row["key"]): str(row["value"]) for row in rows}
    return {
        "title": values.get("webinar_title") or DEFAULT_WEBINAR_TITLE,
        "start_at": values.get("webinar_start_at") or DEFAULT_WEBINAR_START_AT,
        "url": values.get("webinar_url") or DEFAULT_WEBINAR_URL,
        "recording_url": values.get("recording_url") or "",
    }


def upsert_participant(path: Path, sender: dict[str, Any], chat_id: int) -> None:
    now = datetime.now(MOSCOW_TZ).isoformat(timespec="seconds")
    user_id = int(sender["id"])
    with closing(db(path)) as conn:
        conn.execute(
            """
            INSERT INTO participants(telegram_user_id, chat_id, username, first_name, last_name, status, registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'registered', ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                status='registered',
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                chat_id,
                sender.get("username") or "",
                sender.get("first_name") or "",
                sender.get("last_name") or "",
                now,
                now,
            ),
        )
        conn.commit()


def set_participant_status(path: Path, telegram_user_id: int, status: str) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            "UPDATE participants SET status=?, updated_at=? WHERE telegram_user_id=?",
            (status, datetime.now(MOSCOW_TZ).isoformat(timespec="seconds"), telegram_user_id),
        )
        conn.commit()


def is_registered(path: Path, telegram_user_id: int) -> bool:
    with closing(db(path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM participants WHERE telegram_user_id=? AND status='registered'",
            (telegram_user_id,),
        ).fetchone()
    return bool(row)


def active_participants(path: Path) -> list[sqlite3.Row]:
    with closing(db(path)) as conn:
        return list(conn.execute("SELECT * FROM participants WHERE status='registered' ORDER BY registered_at"))


def participant_export_name(row: sqlite3.Row) -> str:
    username = str(row["username"] or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    full_name = " ".join(
        part
        for part in [str(row["first_name"] or "").strip(), str(row["last_name"] or "").strip()]
        if part
    )
    if full_name:
        return full_name
    return str(row["telegram_user_id"])


def participants_due_for_reminder(path: Path, reminder_key: str) -> list[sqlite3.Row]:
    with closing(db(path)) as conn:
        return list(
            conn.execute(
                """
                SELECT p.*
                FROM participants p
                LEFT JOIN reminder_log r
                  ON r.telegram_user_id=p.telegram_user_id AND r.reminder_key=?
                WHERE p.status='registered' AND r.telegram_user_id IS NULL
                ORDER BY p.registered_at
                """,
                (reminder_key,),
            )
        )


def mark_reminder_sent(path: Path, telegram_user_id: int, reminder_key: str) -> None:
    with closing(db(path)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO reminder_log(telegram_user_id, reminder_key, sent_at) VALUES (?, ?, ?)",
            (telegram_user_id, reminder_key, datetime.now(MOSCOW_TZ).isoformat(timespec="seconds")),
        )
        conn.commit()


def clear_reminders(path: Path) -> None:
    with closing(db(path)) as conn:
        conn.execute("DELETE FROM reminder_log")
        conn.commit()


def parse_local_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=MOSCOW_TZ)


def command_name_and_args(text: str) -> tuple[str, str]:
    first, _, rest = text.strip().partition(" ")
    if not first.startswith("/"):
        return "", text.strip()
    command = first[1:].split("@", 1)[0].lower()
    return command, rest.strip()


def format_webinar_time(value: str) -> str:
    dt = parse_local_datetime(value)
    return dt.strftime("%d.%m.%Y в %H:%M по Москве")


def webinar_has_passed(webinar: dict[str, str]) -> bool:
    return datetime.now(MOSCOW_TZ) >= parse_local_datetime(webinar["start_at"])


def webinar_keyboard(webinar: dict[str, str]) -> list[list[dict[str, str]]]:
    return [[{"text": "Открыть вебинар", "url": webinar["url"]}]]


def recording_keyboard(webinar: dict[str, str]) -> list[list[dict[str, str]]]:
    return [[{"text": "Смотреть запись вебинара", "url": webinar["recording_url"]}]]


def start_keyboard(webinar: dict[str, str], registered: bool) -> list[list[dict[str, str]]]:
    if webinar_has_passed(webinar):
        if webinar.get("recording_url"):
            return recording_keyboard(webinar)
        if registered:
            return [[{"text": "Открыть Griders.ru", "url": "https://griders.ru"}]]
        return [
            [{"text": "Получить запись, когда будет готова", "callback_data": "register"}],
            [{"text": "Открыть Griders.ru", "url": "https://griders.ru"}],
        ]
    if registered:
        return registered_keyboard(webinar)
    return [
        [{"text": "Зарегистрироваться на вебинар", "callback_data": "register"}],
        [{"text": "Открыть Griders.ru", "url": "https://griders.ru"}],
    ]


def registered_keyboard(webinar: dict[str, str]) -> list[list[dict[str, str]]]:
    if webinar_has_passed(webinar):
        if webinar.get("recording_url"):
            return recording_keyboard(webinar)
        return [[{"text": "Открыть Griders.ru", "url": "https://griders.ru"}]]
    return [
        [{"text": "Открыть ссылку вебинара", "url": webinar["url"]}],
        [{"text": "Отменить регистрацию", "callback_data": "cancel"}],
    ]


def start_text(webinar: dict[str, str], registered: bool) -> str:
    if webinar_has_passed(webinar):
        if webinar.get("recording_url"):
            return recording_available_text(webinar)
        return recording_pending_text()
    if registered:
        return (
            "Вы уже зарегистрированы на вебинар по <b>Griders.ru</b>.\n\n"
            f"Дата и время: <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>\n"
            f"Ссылка: {html.escape(webinar['url'])}\n\n"
            "Я напомню вам перед началом."
        )
    return (
        "Привет!\n\n"
        f"В субботу, <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>, "
        "проведём ознакомительный вебинар по <b>Griders.ru</b>.\n\n"
        "Griders — это сервис для автотрейдинга через <b>Cryptorg Ghost Bot</b>: "
        "он анализирует сигналы TradingView, фильтрует рынок по объёму, волатильности и рискам, "
        "рассчитывает GRID DCA-сетку и помогает запускать сделки по понятным правилам.\n\n"
        "На вебинаре покажем:\n\n"
        "• как устроен Griders\n"
        "• как подключается Cryptorg Ghost Bot\n"
        "• как работает стратегия GRID DCA\n"
        "• какие есть лимиты, тарифы и режимы расчёта ордера\n"
        "• как контролировать сделки, баланс и риски в кабинете\n"
        "• с чего начать без лишней путаницы\n\n"
        "Участие бесплатное. Зарегистрируйтесь, и бот напомнит о вебинаре перед началом."
    )


def registered_text(webinar: dict[str, str]) -> str:
    if webinar_has_passed(webinar):
        if webinar.get("recording_url"):
            return recording_available_text(webinar)
        return recording_pending_text()
    return (
        "Отлично, вы зарегистрированы на вебинар:\n\n"
        f"<b>{html.escape(webinar['title'])}</b>\n"
        f"Дата и время: <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>\n"
        "Формат: онлайн-встреча в Yandex Telemost\n\n"
        f"Ссылка для участия:\n{html.escape(webinar['url'])}\n\n"
        "Я напомню вам о вебинаре за сутки, за 3 часа и за 1 час до начала."
    )


def recording_pending_text() -> str:
    return (
        "К сожалению, вебинар уже прошёл.\n\n"
        "Скоро будет ссылка на запись — мы обязательно вам её отправим."
    )


def recording_available_text(webinar: dict[str, str]) -> str:
    return (
        "К сожалению, вебинар уже прошёл.\n\n"
        "Но вы можете посмотреть вебинар в записи:\n"
        f"{html.escape(webinar['recording_url'])}"
    )


def reminder_text(webinar: dict[str, str], reminder_key: str) -> str:
    if reminder_key == "day":
        return (
            "Напоминаю: завтра пройдёт ознакомительный вебинар по <b>Griders.ru</b>.\n\n"
            f"Начало: <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>\n\n"
            "Разберём, как работает сервис, как подключается Cryptorg Ghost Bot, "
            "как устроена GRID DCA-стратегия и как контролировать риски.\n\n"
            f"Ссылка на вебинар:\n{html.escape(webinar['url'])}"
        )
    if reminder_key == "3h":
        return (
            "До вебинара по <b>Griders.ru</b> осталось 3 часа.\n\n"
            f"Начинаем: <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>\n"
            "Подготовьте вопросы — на встрече можно будет разобрать практические моменты.\n\n"
            f"Ссылка:\n{html.escape(webinar['url'])}"
        )
    return (
        "Через час начинаем вебинар по <b>Griders.ru</b>.\n\n"
        f"Время старта: <b>{html.escape(format_webinar_time(webinar['start_at']))}</b>\n"
        "Подключайтесь чуть заранее, чтобы спокойно войти в комнату.\n\n"
        f"Ссылка:\n{html.escape(webinar['url'])}"
    )


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = WebinarBot(load_config())
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
