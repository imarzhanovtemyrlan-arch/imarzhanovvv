from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.schemas.api import TelegramAuthRequest, TelegramAuthResponse, TelegramUserSchema
from app.telegram.verify import InitDataValidationError, verify_init_data

router = APIRouter(prefix="/api/auth")


@router.post("/telegram", response_model=TelegramAuthResponse)
async def verify_telegram_auth(payload: TelegramAuthRequest) -> TelegramAuthResponse:
    """Verify initData sent by the Mini App frontend.

    This is the ONLY place a Telegram user identity should be trusted from -
    the frontend must never decode/trust initData itself (spec section 12).
    """
    try:
        verified = verify_init_data(payload.init_data, settings.telegram_bot_token)
    except InitDataValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user_schema = None
    if verified.user is not None:
        user_schema = TelegramUserSchema(
            id=verified.user.id,
            first_name=verified.user.first_name,
            last_name=verified.user.last_name,
            username=verified.user.username,
            language_code=verified.user.language_code,
            is_premium=verified.user.is_premium,
        )

    return TelegramAuthResponse(user=user_schema, auth_date=verified.auth_date)
