CREATE DATABASE IF NOT EXISTS aicryptorg CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'aicryptorg'@'localhost' IDENTIFIED BY 'change-this-password';
GRANT ALL PRIVILEGES ON aicryptorg.* TO 'aicryptorg'@'localhost';
FLUSH PRIVILEGES;

-- The app creates these prefixed tables automatically on first start:
-- ai_users (role: admin/user, plan: free/premium), ai_user_connections, ai_user_strategy_settings, ai_signals,
-- ai_risk_pause_overrides, ai_password_resets, ai_email_verifications, ai_market_shock_events,
-- ai_strategy_pauses, ai_strategy_pause_overrides, ai_tp_cleanup_events,
-- ai_pair_launch_locks, ai_strategy_side_launch_locks, ai_tradingview_events,
-- ai_user_admin_stats, ai_admin_closed_pnl_rows, ai_user_trade_daily_stats,
-- ai_site_trade_counter, ai_site_trade_deals, ai_site_trade_daily_stats
