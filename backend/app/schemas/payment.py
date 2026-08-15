from decimal import Decimal

from pydantic import BaseModel


class PaymentPackageOut(BaseModel):
    id: str
    label: str
    tokens: int
    price_rub: Decimal


class PaymentCreateRequest(BaseModel):
    package_id: str


class PaymentCreateResponse(BaseModel):
    confirmation_url: str
