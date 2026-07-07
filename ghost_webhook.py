"""Cryptorg Ghost Bot webhook client."""

from typing import Any

import httpx

import config


class GhostWebhookError(Exception):
    pass


def normalize_webhook_url(url: str | None) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def failure_message(result: dict | None) -> str | None:
    if not result or result.get("ok"):
        return None
    error_type = str(result.get("error_type") or result.get("error") or "")
    error_text = str(result.get("error") or error_type or "").strip()
    if error_type in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"} or "timeout" in error_text.lower():
        return f"Cryptorg не ответил вовремя: {error_type or error_text}"
    if result.get("exception") and error_text:
        return f"Ошибка соединения с Cryptorg: {error_text}"
    body = result.get("response")
    status_code = result.get("status_code")
    if isinstance(body, dict):
        code = body.get("code")
        message = body.get("message") or body.get("msg") or body.get("error") or body.get("reason")
        if message and code is not None:
            return f"Cryptorg отклонил webhook: {message} (code {code})"
        if message:
            return f"Cryptorg отклонил webhook: {message}"
        if code is not None:
            return f"Cryptorg отклонил webhook: code {code}"
    if isinstance(body, str) and body.strip():
        text = body.strip()
        if len(text) > 240:
            text = f"{text[:237]}..."
        if status_code:
            return f"Cryptorg webhook вернул HTTP {status_code}: {text}"
        return f"Cryptorg webhook вернул ошибку: {text}"
    if status_code:
        return f"Cryptorg webhook вернул HTTP {status_code}"
    return "Cryptorg webhook не выполнил команду"


def _normalize_strategy(strategy: str) -> str:
    direction = strategy.lower()
    if direction not in {"long", "short"}:
        raise ValueError("deal side must be long or short")
    return direction


def _normalize_symbol(pair: str) -> str:
    return pair.upper().replace("-", "").replace("/", "").replace("_", "")


def _base_params(strategy: str, pairs: str | list[str], bot_id: int | str | None = None) -> dict:
    pair_list = [pairs] if isinstance(pairs, str) else pairs
    params = {
        "strategy": _normalize_strategy(strategy),
        "pairs": [_normalize_symbol(pair) for pair in pair_list if pair],
    }
    if bot_id is not None:
        params["botId"] = bot_id
    return params


def build_open_payload(
    pair: str,
    strategy: str,
    order_volume: str | None = None,
    leverage: int | None = None,
    margin_type: str | None = None,
    order_type: str | None = None,
    cycles: int | None = None,
    dca_enabled: bool | None = None,
    dca_max: int | None = None,
    dca_active: int | None = None,
    dca_volume: str | None = None,
    dca_percent: str | None = None,
    dca_multiplier_volume: str | None = None,
    dca_multiplier_price: str | None = None,
    close_enabled: bool = True,
    close_value: str | None = None,
    stop_enabled: bool | None = None,
    stop_value: str | None = None,
    stop_delay: int | None = None,
) -> dict:
    payload = {
        "action": "open",
        "params": {
            **_base_params(strategy, pair),
            "open": {
                "orderVolume": order_volume or config.CRYPTORG_GHOST_DEFAULT_ORDER_VOLUME,
                "leverage": leverage or config.CRYPTORG_GHOST_DEFAULT_LEVERAGE,
                "marginType": margin_type or config.CRYPTORG_GHOST_DEFAULT_MARGIN_TYPE,
                "orderType": order_type or config.CRYPTORG_GHOST_DEFAULT_ORDER_TYPE,
                "cycles": cycles or config.CRYPTORG_GHOST_DEFAULT_CYCLES,
            },
            "dca": {
                "enabled": config.CRYPTORG_GHOST_DCA_ENABLED if dca_enabled is None else dca_enabled,
                "max": dca_max or config.CRYPTORG_GHOST_DCA_MAX,
                "active": dca_active or config.CRYPTORG_GHOST_DCA_ACTIVE,
                "volume": dca_volume or config.CRYPTORG_GHOST_DCA_VOLUME,
                "percent": dca_percent or config.CRYPTORG_GHOST_DCA_PERCENT,
                "multiplierVolume": dca_multiplier_volume or config.CRYPTORG_GHOST_DCA_MULTIPLIER_VOLUME,
                "multiplierPrice": dca_multiplier_price or config.CRYPTORG_GHOST_DCA_MULTIPLIER_PRICE,
            },
            "close": {
                "enabled": close_enabled,
                "event": "percentage",
                "value": close_value or config.CRYPTORG_GHOST_DEFAULT_TP_PERCENT,
            },
        },
    }

    use_stop = config.CRYPTORG_GHOST_DEFAULT_STOP_ENABLED if stop_enabled is None else stop_enabled
    if use_stop:
        payload["params"]["stop"] = {
            "enabled": True,
            "event": "percentage",
            "value": stop_value or config.CRYPTORG_GHOST_DEFAULT_STOP_PERCENT,
        }
        if stop_delay is not None and int(stop_delay) > 0:
            payload["params"]["stop"]["delay"] = stop_delay
    else:
        payload["params"]["stop"] = {"enabled": False}

    return payload


def build_modify_payload(
    pair: str,
    strategy: str,
    dca_enabled: bool | None = None,
    dca_max: int | None = None,
    dca_active: int | None = None,
    dca_volume: str | None = None,
    dca_percent: str | None = None,
    dca_multiplier_volume: str | None = None,
    dca_multiplier_price: str | None = None,
    close_enabled: bool | None = None,
    close_value: str | None = None,
    stop_enabled: bool | None = None,
    stop_value: str | None = None,
    stop_delay: int | None = None,
    bot_id: int | str | None = None,
) -> dict:
    params = _base_params(strategy, pair, bot_id=bot_id)
    if dca_enabled is not None:
        params["dca"] = {
            "enabled": dca_enabled,
            "max": dca_max or config.CRYPTORG_GHOST_DCA_MAX,
            "active": dca_active or config.CRYPTORG_GHOST_DCA_ACTIVE,
            "volume": dca_volume or config.CRYPTORG_GHOST_DCA_VOLUME,
            "percent": dca_percent or config.CRYPTORG_GHOST_DCA_PERCENT,
            "multiplierVolume": dca_multiplier_volume or config.CRYPTORG_GHOST_DCA_MULTIPLIER_VOLUME,
            "multiplierPrice": dca_multiplier_price or config.CRYPTORG_GHOST_DCA_MULTIPLIER_PRICE,
        }
    if close_enabled is not None:
        params["close"] = {
            "enabled": close_enabled,
            "event": "percentage",
            "value": close_value or config.CRYPTORG_GHOST_DEFAULT_TP_PERCENT,
        }
    if stop_enabled is not None:
        params["stop"] = {"enabled": False}
        if stop_enabled:
            params["stop"] = {
                "enabled": True,
                "event": "percentage",
                "value": stop_value or config.CRYPTORG_GHOST_DEFAULT_STOP_PERCENT,
            }
            if stop_delay is not None and int(stop_delay) > 0:
                params["stop"]["delay"] = stop_delay
    return {"action": "update", "params": params}


def build_average_payload(
    pair: str,
    strategy: str,
    amount: str,
    bot_id: int | str | None = None,
) -> dict:
    params = _base_params(strategy, pair, bot_id=bot_id)
    params["amount"] = amount
    return {"action": "average", "params": params}


def build_close_payload(
    pair: str,
    strategy: str,
    close_position: bool = True,
    bot_id: int | str | None = None,
) -> dict:
    params = _base_params(strategy, pair, bot_id=bot_id)
    params["closePosition"] = close_position
    return {"action": "close", "params": params}


async def send_payload(
    payload: dict,
    webhook_url: str | None = None,
    confirm: bool = False,
) -> dict:
    url = normalize_webhook_url(webhook_url or config.CRYPTORG_GHOST_WEBHOOK_URL)
    if not url:
        raise GhostWebhookError("Cryptorg webhook URL is not configured")

    if not confirm:
        return {
            "sent": False,
            "dry_run": True,
            "webhook_configured": True,
            "payload": payload,
            "note": "Pass confirm=true to send the command to Cryptorg.",
        }

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        response = await client.post(url, json=payload)

    text = response.text
    try:
        body: Any = response.json()
    except ValueError:
        body = text

    cryptorg_ok = True
    if isinstance(body, dict) and "code" in body:
        cryptorg_ok = str(body.get("code")) == "0"

    return {
        "sent": True,
        "status_code": response.status_code,
        "ok": 200 <= response.status_code < 300 and cryptorg_ok,
        "response": body,
        "payload": payload,
    }


async def open_deal(
    pair: str,
    strategy: str,
    confirm: bool = False,
    **kwargs,
) -> dict:
    payload = build_open_payload(pair=pair, strategy=strategy, **kwargs)
    return await send_payload(payload, confirm=confirm)


async def modify_deal(
    pair: str,
    strategy: str,
    confirm: bool = False,
    **kwargs,
) -> dict:
    payload = build_modify_payload(pair=pair, strategy=strategy, **kwargs)
    return await send_payload(payload, confirm=confirm)


async def average_deal(
    pair: str,
    strategy: str,
    amount: str,
    confirm: bool = False,
    **kwargs,
) -> dict:
    payload = build_average_payload(pair=pair, strategy=strategy, amount=amount, **kwargs)
    return await send_payload(payload, confirm=confirm)


async def close_deal(
    pair: str,
    strategy: str,
    confirm: bool = False,
    **kwargs,
) -> dict:
    payload = build_close_payload(pair=pair, strategy=strategy, **kwargs)
    return await send_payload(payload, confirm=confirm)
