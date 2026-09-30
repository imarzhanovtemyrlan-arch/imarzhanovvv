"""
Server-side verification of Telegram Mini App `initData`.

Implements the exact algorithm documented at
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app :

    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)          [raw bytes]
    data_check_string = "\n".join(sorted("key=value" for all fields except "hash"))
    computed_hash = HMAC_SHA256(key=secret_key, msg=data_check_string) [hex]
    valid iff computed_hash == received "hash" field (timing-safe compare)

`auth_date` is additionally checked against `max_age_seconds` to reject
replayed/stale init data.

The frontend (app/services/telegram.ts) never trusts `initData` itself -
it is only ever sent here, raw, for verification (spec section 12: "Не
доверять frontend данным пользователя без проверки на backend").
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl


class InitDataValidationError(Exception):
    """Raised when initData is missing, malformed, expired, or fails signature check."""


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None
    is_premium: bool = False


@dataclass(frozen=True)
class VerifiedInitData:
    user: Optional[TelegramUser]
    auth_date: int
    query_id: Optional[str]
    raw_fields: dict[str, str]


def _compute_hash(data_check_string: str, bot_token: str) -> str:
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: Optional[float] = 86400,
) -> VerifiedInitData:
    """Verify raw `initData` and return the parsed, trusted contents.

    Raises InitDataValidationError on any failure - callers should treat
    that as "reject the request", never fall back to trusting the payload.
    """
    if not init_data:
        raise InitDataValidationError("empty init data")
    if not bot_token:
        raise InitDataValidationError("bot token not configured on the server")

    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataValidationError("missing 'hash' field")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    computed_hash = _compute_hash(data_check_string, bot_token)

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataValidationError("signature verification failed")

    auth_date_raw = fields.get("auth_date")
    if auth_date_raw is None:
        raise InitDataValidationError("missing 'auth_date' field")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataValidationError("malformed 'auth_date' field") from exc

    if max_age_seconds is not None:
        age = time.time() - auth_date
        if age > max_age_seconds:
            raise InitDataValidationError(f"init data expired ({age:.0f}s old)")
        if age < -60:  # tolerate small clock skew, reject anything further in the future
            raise InitDataValidationError("'auth_date' is in the future")

    user: Optional[TelegramUser] = None
    if "user" in fields:
        try:
            user_data = json.loads(fields["user"])
            user = TelegramUser(
                id=user_data["id"],
                first_name=user_data.get("first_name"),
                last_name=user_data.get("last_name"),
                username=user_data.get("username"),
                language_code=user_data.get("language_code"),
                is_premium=bool(user_data.get("is_premium", False)),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise InitDataValidationError(f"malformed 'user' field: {exc}") from exc

    return VerifiedInitData(
        user=user,
        auth_date=auth_date,
        query_id=fields.get("query_id"),
        raw_fields=fields,
    )
