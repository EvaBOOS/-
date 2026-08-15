import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.core.security import create_access_token
from app.models.client import AccountType, Client, ClientApplication, SubscriptionPlan
from app.models.payment import Payment, PaymentStatus
from app.schemas.client import (
    ClientApplicationCreate,
    ClientApplicationResponse,
    PublicRegisterRequest,
)
from app.schemas.user import Token
from app.services.client_provisioning import provision_client
from app.services.payments import yookassa_service
from fastapi.responses import RedirectResponse
from app.services.antispam import (
    captcha_configured,
    client_ip,
    create_email_verify_token,
    is_disposable_email,
    parse_email_verify_token,
    send_verification_email,
    smtp_configured,
    verify_smartcaptcha,
)
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/apply", response_model=ClientApplicationResponse, status_code=status.HTTP_201_CREATED)
async def submit_application(
    application_data: ClientApplicationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Public, unauthenticated endpoint — a company or blogger requests
    access to the B2B/campaign cabinet. An admin reviews it later via
    GET/POST /admin/applications."""
    if not application_data.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо принять условия обработки персональных данных",
        )
    if is_disposable_email(application_data.email):
        raise HTTPException(status_code=400, detail="Используйте постоянный email, не одноразовый")
    if captcha_configured():
        ok = await verify_smartcaptcha(application_data.captcha_token or "", client_ip(request))
        if not ok:
            raise HTTPException(status_code=400, detail="Подтвердите, что вы не робот")
    data = application_data.model_dump(exclude={"accepted_terms", "captcha_token"})
    application = ClientApplication(**data, terms_accepted_at=datetime.utcnow())
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    register_data: PublicRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Self-serve signup — instantly provisions a Client (no admin review)
    with a small free-trial token balance, and logs the new account in
    immediately so the frontend can go straight to the dashboard."""
    if not register_data.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо принять пользовательское соглашение и политику обработки персональных данных",
        )
    if is_disposable_email(register_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Используйте постоянный email, не одноразовый ящик",
        )
    if captcha_configured():
        ok = await verify_smartcaptcha(register_data.captcha_token or "", client_ip(request))
        if not ok:
            raise HTTPException(status_code=400, detail="Подтвердите, что вы не робот")

    must_verify = smtp_configured()
    client = await provision_client(
        db,
        user_email=register_data.email,
        user_password=register_data.password,
        user_full_name=register_data.full_name,
        company_name=None,
        account_type=AccountType.INDIVIDUAL,
        discount_percent=0,
        subscription_plan=SubscriptionPlan.BASIC,
        initial_credits=settings.SELF_SERVE_FREE_CREDITS,
        email_verified=not must_verify,
    )
    client.terms_accepted_at = datetime.utcnow()
    client.marketing_opt_in = register_data.marketing_opt_in
    await db.commit()
    await db.refresh(client)

    if must_verify:
        token = create_email_verify_token(client.user_id)
        send_verification_email(register_data.email, token)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=client.user_id,
        expires_delta=access_token_expires,
        additional_claims={"role": "client"},
    )
    return Token(access_token=access_token, email_verified=not must_verify)


@router.get("/antispam")
async def antispam_public_config():
    """Frontend bootstrap: whether captcha / email verification are live."""
    return {
        "captcha_enabled": captcha_configured(),
        "site_key": (settings.YANDEX_SMARTCAPTCHA_CLIENT_KEY or "") if captcha_configured() else "",
        "email_verification_enabled": smtp_configured(),
    }


@router.get("/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    user_id = parse_email_verify_token(token)
    dest = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/dashboard"
    if not user_id:
        return RedirectResponse(f"{dest}?verified=invalid", status_code=302)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return RedirectResponse(f"{dest}?verified=invalid", status_code=302)
    user.email_verified = True
    await db.commit()
    return RedirectResponse(f"{dest}?verified=ok", status_code=302)


@router.post("/payments/webhook")
async def yookassa_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """ЮKassa HTTP notification. Always answers 200 (even on unknown/foreign
    payment ids) so ЮKassa doesn't retry-storm us — but never credits tokens
    based on the notification body alone: the payment status is always
    re-verified via our own authenticated call to the ЮKassa API first."""
    body = await request.json()
    payment_id = (body.get("object") or {}).get("id")
    if not payment_id:
        logger.warning("ЮKassa webhook without object.id: %s", body)
        return {"status": "ok"}

    result = await db.execute(select(Payment).where(Payment.yookassa_payment_id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.warning("ЮKassa webhook for unknown payment_id=%s", payment_id)
        return {"status": "ok"}

    if payment.status == PaymentStatus.SUCCEEDED:
        return {"status": "ok"}

    try:
        verified_status = await yookassa_service.fetch_payment_status(payment_id)
    except yookassa_service.YooKassaError:
        logger.exception("Failed to verify ЮKassa payment_id=%s", payment_id)
        return {"status": "ok"}

    if verified_status != "succeeded":
        return {"status": "ok"}

    result = await db.execute(select(Client).where(Client.id == payment.client_id))
    client = result.scalar_one_or_none()
    if not client:
        logger.warning("ЮKassa payment_id=%s has no matching client_id=%s", payment_id, payment.client_id)
        return {"status": "ok"}

    client.credits_remaining += payment.tokens
    payment.status = PaymentStatus.SUCCEEDED
    await db.commit()

    return {"status": "ok"}


class RightsComplaintIn(BaseModel):
    email: str = Field(..., max_length=255)
    message: str = Field(..., min_length=10, max_length=4000)
    source_url: Optional[str] = Field(None, max_length=1000)
    generation_id: Optional[int] = None


@router.post("/rights-complaint", status_code=status.HTTP_201_CREATED)
async def submit_rights_complaint(
    body: RightsComplaintIn,
    db: AsyncSession = Depends(get_db),
):
    """Copyright / rights-holder notice. Logged for the takedown procedure."""
    from app.models.moderation import RightsComplaint

    email = (body.email or "").strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    row = RightsComplaint(
        email=email[:255],
        source_url=(body.source_url or "").strip()[:1000] or None,
        generation_id=body.generation_id,
        message=body.message.strip()[:4000],
        status="pending",
    )
    db.add(row)
    await db.commit()
    return {"message": "Обращение принято. Ответим в течение 7 рабочих дней."}
