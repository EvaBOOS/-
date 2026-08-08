"""Client-scoped token-purchase endpoints (checkout initiation only — the
ЮKassa webhook that actually credits tokens lives in public.py, since
ЮKassa calls it unauthenticated)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_client, get_db
from app.models.client import Client
from app.models.payment import Payment
from app.schemas.payment import PaymentCreateRequest, PaymentCreateResponse, PaymentPackageOut
from app.services.payments import yookassa_service
from app.services.payments.yookassa_service import TOKEN_PACKAGES

router = APIRouter()


@router.get("/payments/packages", response_model=list[PaymentPackageOut])
async def list_packages(
    client: Client = Depends(get_current_client)
):
    return [
        PaymentPackageOut(id=package_id, **package)
        for package_id, package in TOKEN_PACKAGES.items()
    ]


@router.post("/payments/create", response_model=PaymentCreateResponse)
async def create_payment(
    request: PaymentCreateRequest,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db)
):
    package = TOKEN_PACKAGES.get(request.package_id)
    if not package:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown package_id")

    try:
        yk_payment = await yookassa_service.create_payment(request.package_id, client.id)
    except yookassa_service.YooKassaError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    payment = Payment(
        client_id=client.id,
        yookassa_payment_id=yk_payment["id"],
        package_id=request.package_id,
        tokens=package["tokens"],
        amount_rub=package["price_rub"],
    )
    db.add(payment)
    await db.commit()

    return PaymentCreateResponse(confirmation_url=yk_payment["confirmation_url"])
