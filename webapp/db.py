"""Small MySQL data layer for the MVP."""

import time
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from pymysql.err import OperationalError
from pymysql.cursors import DictCursor

from . import settings


@contextmanager
def get_conn() -> Iterator[pymysql.Connection]:
    conn = pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        charset=settings.DB_CHARSET,
        cursorclass=DictCursor,
        autocommit=False,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_TRANSIENT_MYSQL_ERRORS = {2003, 2006, 2013}


def _is_transient_mysql_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and bool(exc.args) and int(exc.args[0]) in _TRANSIENT_MYSQL_ERRORS


def _with_retry(fn):
    for attempt in range(3):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_transient_mysql_error(exc) or attempt >= 2:
                raise
            time.sleep(0.2 * (attempt + 1))


def fetch_one(sql: str, params: tuple | dict = ()) -> dict | None:
    def run() -> dict | None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    return _with_retry(run)


def fetch_all(sql: str, params: tuple | dict = ()) -> list[dict]:
    def run() -> list[dict]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

    return _with_retry(run)


def execute(sql: str, params: tuple | dict = ()) -> int:
    def run() -> int:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return int(cur.lastrowid or cur.rowcount)

    return _with_retry(run)

def init_db() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS ai_users (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(190) NOT NULL UNIQUE,
            nickname VARCHAR(80) NOT NULL DEFAULT '',
            telegram_username VARCHAR(80) NOT NULL DEFAULT '',
            telegram_user_id BIGINT UNSIGNED NULL,
            telegram_verified_at TIMESTAMP NULL,
            telegram_last_checked_at TIMESTAMP NULL,
            role ENUM('admin','user') NOT NULL DEFAULT 'user',
            plan ENUM('free','start','premium') NOT NULL DEFAULT 'free',
            password_hash VARCHAR(255) NOT NULL,
            twofa_method ENUM('none','pin','totp') NOT NULL DEFAULT 'none',
            twofa_pin_hash VARCHAR(255) NULL,
            twofa_totp_secret_encrypted TEXT NULL,
            twofa_totp_pending_secret_encrypted TEXT NULL,
            twofa_enabled_at TIMESTAMP NULL,
            timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow',
            personal_data_consent_at TIMESTAMP NULL,
            terms_accepted_at TIMESTAMP NULL,
            email_verified_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_user_connections (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            label VARCHAR(120) NOT NULL DEFAULT 'Main account',
            strategy_code VARCHAR(80) NOT NULL DEFAULT 'grid_dca_v2',
            bybit_api_key VARCHAR(190) NOT NULL DEFAULT '',
            bybit_api_secret_encrypted TEXT NULL,
            webhook_url_encrypted TEXT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            last_balance DECIMAL(18, 8) NULL,
            last_error TEXT NULL,
            last_checked_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_ai_connections_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_user_strategy_settings (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 0,
            auto_trade TINYINT(1) NOT NULL DEFAULT 0,
            risk_pct DECIMAL(6, 3) NOT NULL DEFAULT 2.000,
            min_order_volume DECIMAL(18, 4) NOT NULL DEFAULT 6.0000,
            first_order_mode ENUM('manual','deposit_pct') NOT NULL DEFAULT 'manual',
            leverage INT NOT NULL DEFAULT 10,
            max_active_deals INT NOT NULL DEFAULT 2,
            max_long_deals INT NOT NULL DEFAULT 1,
            max_short_deals INT NOT NULL DEFAULT 1,
            watchlist TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_user_connection (user_id, connection_id),
            CONSTRAINT fk_ai_strategy_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_signals (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL,
            side ENUM('long','short','wait') NOT NULL DEFAULT 'wait',
            status ENUM('new','sent','skipped','failed') NOT NULL DEFAULT 'new',
            confidence DECIMAL(5, 4) NOT NULL DEFAULT 0.0000,
            order_volume DECIMAL(18, 4) NULL,
            leverage INT NULL,
            reasons JSON NULL,
            payload JSON NULL,
            response JSON NULL,
            error_message TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP NULL,
            INDEX idx_user_created (user_id, created_at),
            CONSTRAINT fk_ai_signals_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_risk_pause_overrides (
            user_id BIGINT UNSIGNED PRIMARY KEY,
            override_until TIMESTAMP NOT NULL,
            reason TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_ai_risk_override_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_password_resets (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            token_hash CHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            request_ip VARCHAR(80) NULL,
            user_agent VARCHAR(255) NULL,
            INDEX idx_password_reset_user (user_id, created_at),
            INDEX idx_password_reset_expires (expires_at),
            CONSTRAINT fk_ai_password_resets_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_email_verifications (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            token_hash CHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            request_ip VARCHAR(80) NULL,
            user_agent VARCHAR(255) NULL,
            INDEX idx_email_verify_user (user_id, created_at),
            INDEX idx_email_verify_expires (expires_at),
            CONSTRAINT fk_ai_email_verify_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_market_shock_events (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source VARCHAR(40) NOT NULL,
            source_message_id VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL,
            side ENUM('long','short') NOT NULL,
            move_pct DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
            shock_type VARCHAR(20) NOT NULL DEFAULT '',
            raw_text TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP NULL,
            UNIQUE KEY uniq_market_shock_source (source, source_message_id),
            INDEX idx_market_shock_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_market_shock_pair_lists (
            pair VARCHAR(40) PRIMARY KEY,
            list_type ENUM('white','black') NOT NULL,
            reason TEXT NULL,
            metrics JSON NULL,
            source_event_id BIGINT UNSIGNED NULL,
            checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_market_shock_pair_list_type (list_type, checked_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_strategy_pauses (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL DEFAULT '*',
            reason TEXT NULL,
            source VARCHAR(80) NOT NULL DEFAULT '',
            source_ref VARCHAR(190) NOT NULL DEFAULT '',
            starts_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ends_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_strategy_pause_lookup (user_id, connection_id, strategy_code, pair, ends_at),
            UNIQUE KEY uniq_strategy_pause_source (user_id, connection_id, strategy_code, pair, source, source_ref),
            CONSTRAINT fk_ai_strategy_pauses_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_strategy_pause_overrides (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL DEFAULT '*',
            override_until TIMESTAMP NOT NULL,
            reason TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_strategy_pause_override (user_id, connection_id, strategy_code, pair),
            INDEX idx_strategy_pause_override_lookup (user_id, connection_id, strategy_code, pair, override_until),
            CONSTRAINT fk_ai_strategy_pause_overrides_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_tp_cleanup_events (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL,
            side ENUM('long','short') NOT NULL,
            source_ref VARCHAR(190) NOT NULL,
            payload JSON NULL,
            response JSON NULL,
            status ENUM('sent','failed') NOT NULL DEFAULT 'sent',
            error_message TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_tp_cleanup_source (user_id, connection_id, source_ref),
            INDEX idx_tp_cleanup_lookup (user_id, connection_id, pair, created_at),
            CONSTRAINT fk_ai_tp_cleanup_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_pair_launch_locks (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
            pair VARCHAR(40) NOT NULL,
            locked_until TIMESTAMP NOT NULL,
            source_ref VARCHAR(190) NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_pair_launch_lock (user_id, connection_id, pair),
            INDEX idx_pair_launch_until (locked_until),
            CONSTRAINT fk_ai_pair_launch_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_strategy_side_launch_locks (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
            strategy_code VARCHAR(80) NOT NULL,
            side ENUM('long','short') NOT NULL,
            locked_until TIMESTAMP NOT NULL,
            source_ref VARCHAR(190) NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_strategy_side_launch_lock (user_id, connection_id, strategy_code, side),
            INDEX idx_strategy_side_launch_user (user_id),
            INDEX idx_strategy_side_launch_until (locked_until),
            CONSTRAINT fk_ai_strategy_side_launch_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_tradingview_events (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source VARCHAR(40) NOT NULL DEFAULT 'tradingview',
            source_message_id VARCHAR(190) NOT NULL,
            strategy_code VARCHAR(80) NOT NULL,
            pair VARCHAR(40) NOT NULL,
            side ENUM('long','short','wait') NOT NULL DEFAULT 'wait',
            confidence DECIMAL(5, 4) NOT NULL DEFAULT 0.0000,
            raw_payload JSON NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP NULL,
            UNIQUE KEY uniq_tradingview_source (source, source_message_id),
            INDEX idx_tradingview_strategy_created (strategy_code, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_user_admin_stats (
            user_id BIGINT UNSIGNED PRIMARY KEY,
            cumulative_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0.00000000,
            closed_trades_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            closed_entry_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            connection_status ENUM('active','ready','missing') NOT NULL DEFAULT 'missing',
            pnl_calculated_at TIMESTAMP NULL,
            status_checked_at TIMESTAMP NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_ai_admin_stats_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_admin_closed_pnl_rows (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NOT NULL,
            closed_ref VARCHAR(190) NOT NULL,
            symbol VARCHAR(40) NOT NULL DEFAULT '',
            side VARCHAR(10) NOT NULL DEFAULT '',
            closed_at TIMESTAMP NOT NULL,
            closed_pnl DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            entry_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_admin_closed_pnl (user_id, connection_id, closed_ref),
            INDEX idx_admin_closed_pnl_user_date (user_id, closed_at),
            INDEX idx_admin_closed_pnl_connection_date (connection_id, closed_at),
            CONSTRAINT fk_ai_admin_closed_pnl_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE,
            CONSTRAINT fk_ai_admin_closed_pnl_connection FOREIGN KEY (connection_id) REFERENCES ai_user_connections(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_user_trade_daily_stats (
            user_id BIGINT UNSIGNED NOT NULL,
            stat_date DATE NOT NULL,
            closed_trades_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            closed_pnl DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            entry_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, stat_date),
            CONSTRAINT fk_ai_user_trade_daily_user FOREIGN KEY (user_id) REFERENCES ai_users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "DROP TABLE IF EXISTS ai_site_trade_stats",
        """
        CREATE TABLE IF NOT EXISTS ai_site_trade_counter (
            counter_key VARCHAR(40) PRIMARY KEY,
            counted_from DATE NOT NULL,
            deals_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            traded_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_site_trade_deals (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            signal_id BIGINT UNSIGNED NULL,
            user_id BIGINT UNSIGNED NOT NULL,
            connection_id BIGINT UNSIGNED NULL,
            strategy_code VARCHAR(80) NOT NULL DEFAULT '',
            pair VARCHAR(40) NOT NULL,
            side ENUM('long','short') NOT NULL,
            sent_at TIMESTAMP NOT NULL,
            payload JSON NULL,
            expected_profits JSON NULL,
            planned_volumes JSON NULL,
            full_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            active_safety_orders INT NOT NULL DEFAULT 0,
            status ENUM('open','closed') NOT NULL DEFAULT 'open',
            matched_safety_orders INT NULL,
            credited_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            closed_pnl DECIMAL(24, 8) NULL,
            api_entry_value DECIMAL(24, 8) NULL,
            closed_ref VARCHAR(190) NULL,
            closed_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_site_trade_signal (signal_id),
            UNIQUE KEY uniq_site_trade_closed_ref (closed_ref),
            INDEX idx_site_trade_open (status, user_id, connection_id, pair, side, sent_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_site_trade_daily_stats (
            stat_date DATE PRIMARY KEY,
            sent_deals_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            closed_deals_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
            traded_volume DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000,
            calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ai_tariff_channels (
            plan ENUM('start','premium') PRIMARY KEY,
            chat_id VARCHAR(80) NOT NULL,
            title VARCHAR(190) NOT NULL DEFAULT '',
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
            cur.execute(
                """
                INSERT IGNORE INTO ai_site_trade_counter
                    (counter_key, counted_from, deals_count, traded_volume)
                VALUES ('site_totals_v2', '2026-06-08', 0, 0)
                """
            )
            _ensure_column(cur, "ai_users", "nickname", "VARCHAR(80) NOT NULL DEFAULT ''")
            _ensure_column(cur, "ai_users", "telegram_username", "VARCHAR(80) NOT NULL DEFAULT ''")
            _ensure_column(cur, "ai_users", "telegram_user_id", "BIGINT UNSIGNED NULL")
            _ensure_column(cur, "ai_users", "telegram_verified_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_users", "telegram_last_checked_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_users", "tariff_free_checks", "INT NOT NULL DEFAULT 0")
            _ensure_column(cur, "ai_users", "role", "ENUM('admin','user') NOT NULL DEFAULT 'user'")
            _ensure_column(cur, "ai_users", "plan", "ENUM('free','start','premium') NOT NULL DEFAULT 'free'")
            cur.execute("ALTER TABLE ai_users MODIFY plan ENUM('free','start','premium') NOT NULL DEFAULT 'free'")
            _ensure_column(cur, "ai_users", "twofa_method", "ENUM('none','pin','totp') NOT NULL DEFAULT 'none'")
            _ensure_column(cur, "ai_users", "twofa_pin_hash", "VARCHAR(255) NULL")
            _ensure_column(cur, "ai_users", "twofa_totp_secret_encrypted", "TEXT NULL")
            _ensure_column(cur, "ai_users", "twofa_totp_pending_secret_encrypted", "TEXT NULL")
            _ensure_column(cur, "ai_users", "twofa_enabled_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_users", "timezone", "VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow'")
            _ensure_column(cur, "ai_users", "personal_data_consent_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_users", "terms_accepted_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_users", "email_verified_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_user_admin_stats", "closed_trades_count", "BIGINT UNSIGNED NOT NULL DEFAULT 0")
            _ensure_column(cur, "ai_user_admin_stats", "closed_entry_volume", "DECIMAL(24, 8) NOT NULL DEFAULT 0.00000000")
            _ensure_column(cur, "ai_user_connections", "last_admin_closed_sync_at", "TIMESTAMP NULL")
            _ensure_column(cur, "ai_site_trade_deals", "active_safety_orders", "INT NOT NULL DEFAULT 0")
            _ensure_column(cur, "ai_site_trade_deals", "closed_pnl", "DECIMAL(24, 8) NULL")
            _ensure_column(cur, "ai_site_trade_deals", "api_entry_value", "DECIMAL(24, 8) NULL")
            _ensure_column(cur, "ai_user_connections", "strategy_code", "VARCHAR(80) NOT NULL DEFAULT 'grid_dca_v2'")
            _ensure_column(cur, "ai_user_strategy_settings", "connection_id", "BIGINT UNSIGNED NULL")
            _ensure_column(cur, "ai_user_strategy_settings", "max_active_deals", "INT NOT NULL DEFAULT 2")
            _ensure_column(cur, "ai_user_strategy_settings", "max_long_deals", "INT NOT NULL DEFAULT 1")
            _ensure_column(cur, "ai_user_strategy_settings", "max_short_deals", "INT NOT NULL DEFAULT 1")
            _ensure_column(cur, "ai_user_strategy_settings", "first_order_mode", "ENUM('manual','deposit_pct') NOT NULL DEFAULT 'manual'")
            _ensure_column(cur, "ai_signals", "connection_id", "BIGINT UNSIGNED NULL")
            _ensure_column(cur, "ai_strategy_side_launch_locks", "connection_id", "BIGINT UNSIGNED NOT NULL DEFAULT 0")
            _ensure_index(
                cur,
                "ai_strategy_side_launch_locks",
                "idx_strategy_side_launch_user",
                "KEY idx_strategy_side_launch_user (user_id)",
            )
            _ensure_index_columns(
                cur,
                "ai_strategy_side_launch_locks",
                "uniq_strategy_side_launch_lock",
                "UNIQUE KEY uniq_strategy_side_launch_lock (user_id, connection_id, strategy_code, side)",
                ["user_id", "connection_id", "strategy_code", "side"],
                unique=True,
            )
            _ensure_index(
                cur,
                "ai_strategy_side_launch_locks",
                "idx_strategy_side_launch_until",
                "KEY idx_strategy_side_launch_until (locked_until)",
            )
            cur.execute("ALTER TABLE ai_user_strategy_settings MODIFY watchlist TEXT NOT NULL")
            cur.execute("UPDATE ai_user_strategy_settings SET strategy_code='grid_dca_v2' WHERE strategy_code='liquid_scalp_v1'")
            cur.execute("UPDATE ai_user_connections SET strategy_code='grid_dca_v2' WHERE strategy_code='liquid_scalp_v1'")
            _ensure_index(cur, "ai_user_strategy_settings", "idx_strategy_user", "KEY idx_strategy_user (user_id)")
            _drop_index_if_exists(cur, "ai_user_strategy_settings", "uniq_user_strategy")
            cur.execute(
                """
                UPDATE ai_user_strategy_settings s
                JOIN (
                    SELECT user_id, MIN(id) AS connection_id
                    FROM ai_user_connections
                    GROUP BY user_id
                ) c ON c.user_id = s.user_id
                SET s.connection_id = c.connection_id
                WHERE s.connection_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM ai_user_strategy_settings existing
                    WHERE existing.user_id = s.user_id
                      AND existing.connection_id = c.connection_id
                  )
                """
            )
            cur.execute(
                """
                DELETE s FROM ai_user_strategy_settings s
                JOIN (
                    SELECT user_id, MIN(id) AS connection_id
                    FROM ai_user_connections
                    GROUP BY user_id
                ) c ON c.user_id = s.user_id
                JOIN ai_user_strategy_settings existing
                  ON existing.user_id = s.user_id
                 AND existing.connection_id = c.connection_id
                WHERE s.connection_id IS NULL
                """
            )
            cur.execute(
                """
                DELETE FROM ai_user_strategy_settings
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT MAX(id) AS id
                        FROM ai_user_strategy_settings
                        WHERE connection_id IS NOT NULL
                        GROUP BY user_id, connection_id
                    ) keep_rows
                )
                AND connection_id IS NOT NULL
                """
            )
            _ensure_index(cur, "ai_user_strategy_settings", "uniq_user_connection", "UNIQUE KEY uniq_user_connection (user_id, connection_id)")
            _ensure_index(cur, "ai_users", "idx_ai_users_telegram_username", "KEY idx_ai_users_telegram_username (telegram_username)")
            _ensure_index(cur, "ai_users", "uniq_ai_users_telegram_user_id", "UNIQUE KEY uniq_ai_users_telegram_user_id (telegram_user_id)")
            cur.execute(
                """
                UPDATE ai_user_connections c
                JOIN ai_user_strategy_settings s ON s.connection_id = c.id
                SET c.strategy_code = s.strategy_code
                """
            )
            cur.execute("ALTER TABLE ai_user_strategy_settings ALTER risk_pct SET DEFAULT 2.000")
            cur.execute("ALTER TABLE ai_user_strategy_settings ALTER max_active_deals SET DEFAULT 2")
            cur.execute("ALTER TABLE ai_user_strategy_settings ALTER max_long_deals SET DEFAULT 1")
            cur.execute("ALTER TABLE ai_user_strategy_settings ALTER max_short_deals SET DEFAULT 1")
            cur.execute("UPDATE ai_user_strategy_settings SET leverage=10 WHERE leverage<>10")
            cur.execute(
                """
                UPDATE ai_user_strategy_settings
                SET risk_pct = 2.000
                WHERE risk_pct = 1.000
                """
            )
            cur.execute(
                """
                UPDATE ai_user_strategy_settings
                SET max_active_deals = 2, max_long_deals = 1, max_short_deals = 1
                WHERE max_active_deals = 0 AND max_long_deals = 0 AND max_short_deals = 0
                """
            )
            cur.execute(
                """
                UPDATE ai_users
                SET role='admin', plan='premium'
                WHERE email=%s
                """,
                (settings.ADMIN_EMAIL,),
            )


def _ensure_column(cur: Any, table: str, column: str, definition: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
        """,
        (table, column),
    )
    row = cur.fetchone()
    if row and int(row["cnt"]) == 0:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _drop_index_if_exists(cur: Any, table: str, index_name: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        """,
        (table, index_name),
    )
    row = cur.fetchone()
    if row and int(row["cnt"]) > 0:
        cur.execute(f"ALTER TABLE {table} DROP INDEX {index_name}")


def _ensure_index(cur: Any, table: str, index_name: str, definition: str) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        """,
        (table, index_name),
    )
    row = cur.fetchone()
    if row and int(row["cnt"]) == 0:
        cur.execute(f"ALTER TABLE {table} ADD {definition}")


def _ensure_index_columns(
    cur: Any,
    table: str,
    index_name: str,
    definition: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    cur.execute(
        """
        SELECT COLUMN_NAME, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        ORDER BY SEQ_IN_INDEX
        """,
        (table, index_name),
    )
    rows = list(cur.fetchall())
    current_columns = [str(row["COLUMN_NAME"]) for row in rows]
    is_unique = bool(rows) and int(rows[0]["NON_UNIQUE"]) == 0
    if current_columns == columns and (not unique or is_unique):
        return
    if rows:
        cur.execute(f"ALTER TABLE {table} DROP INDEX {index_name}")
    cur.execute(f"ALTER TABLE {table} ADD {definition}")
