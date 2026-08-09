import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
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

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/apply", response_model=ClientApplicationResponse, status_code=status.HTTP_201_CREATED)
async def submit_application(
    application_data: ClientApplicationCreate,
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
    data = application_data.model_dump(exclude={"accepted_terms"})
    application = ClientApplication(**data, terms_accepted_at=datetime.utcnow())
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    register_data: PublicRegisterRequest,
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
    )
    client.terms_accepted_at = datetime.utcnow()
    client.marketing_opt_in = register_data.marketing_opt_in
    await db.commit()
    await db.refresh(client)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=client.user_id,
        expires_delta=access_token_expires,
        additional_claims={"role": "client"},
    )
    return Token(access_token=access_token)


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
