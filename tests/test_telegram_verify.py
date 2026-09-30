import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from app.telegram.verify import InitDataValidationError, verify_init_data

BOT_TOKEN = "123456:TEST-BOT-TOKEN"


def build_init_data(fields: dict, bot_token: str = BOT_TOKEN) -> str:
    """Constructs a validly-signed initData string using the exact same
    algorithm the server expects, so tests exercise the real verification
    path rather than a mock."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    all_fields = dict(fields)
    all_fields["hash"] = computed_hash
    return urllib.parse.urlencode(all_fields)


def test_valid_init_data_is_accepted():
    user = {"id": 279058397, "first_name": "Ada", "username": "ada_l", "is_premium": True}
    init_data = build_init_data({"auth_date": str(int(time.time())), "query_id": "AAH123", "user": json.dumps(user)})

    result = verify_init_data(init_data, BOT_TOKEN)

    assert result.user is not None
    assert result.user.id == 279058397
    assert result.user.username == "ada_l"
    assert result.user.is_premium is True
    assert result.query_id == "AAH123"


def test_tampered_field_rejected():
    init_data = build_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 1})})
    tampered = init_data.replace("id%22%3A+1", "id%22%3A+999999")  # attempt to change user id post-signing

    with pytest.raises(InitDataValidationError, match="signature"):
        verify_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_rejected():
    init_data = build_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 1})})
    with pytest.raises(InitDataValidationError, match="signature"):
        verify_init_data(init_data, "999999:WRONG-TOKEN")


def test_missing_hash_rejected():
    with pytest.raises(InitDataValidationError, match="hash"):
        verify_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN)


def test_expired_auth_date_rejected():
    old_auth_date = int(time.time()) - 100_000
    init_data = build_init_data({"auth_date": str(old_auth_date)})
    with pytest.raises(InitDataValidationError, match="expired"):
        verify_init_data(init_data, BOT_TOKEN, max_age_seconds=86400)


def test_future_auth_date_rejected():
    future_auth_date = int(time.time()) + 100_000
    init_data = build_init_data({"auth_date": str(future_auth_date)})
    with pytest.raises(InitDataValidationError, match="future"):
        verify_init_data(init_data, BOT_TOKEN)


def test_no_max_age_skips_expiry_check():
    old_auth_date = int(time.time()) - 1_000_000
    init_data = build_init_data({"auth_date": str(old_auth_date)})
    # Should not raise when max_age_seconds is None.
    result = verify_init_data(init_data, BOT_TOKEN, max_age_seconds=None)
    assert result.auth_date == old_auth_date


def test_empty_init_data_rejected():
    with pytest.raises(InitDataValidationError, match="empty"):
        verify_init_data("", BOT_TOKEN)


def test_missing_bot_token_rejected():
    init_data = build_init_data({"auth_date": str(int(time.time()))})
    with pytest.raises(InitDataValidationError, match="bot token"):
        verify_init_data(init_data, "")


def test_malformed_user_field_rejected():
    init_data = build_init_data({"auth_date": str(int(time.time())), "user": "not-json"})
    with pytest.raises(InitDataValidationError, match="malformed"):
        verify_init_data(init_data, BOT_TOKEN)
