import uuid
from decimal import Decimal
from typing import Optional

import httpx

from app.core.config import settings

API_BASE = "https://api.yookassa.ru/v3"

# Fixed token packages — placeholder prices, edit here to change what's sold.
TOKEN_PACKAGES: dict[str, dict] = {
    "starter": {"label": "Старт", "tokens": 10, "price_rub": Decimal("990.00")},
    "pro": {"label": "Про", "tokens": 30, "price_rub": Decimal("2490.00")},
    "studio": {"label": "Студия", "tokens": 100, "price_rub": Decimal("6990.00")},
}


class YooKassaError(Exception):
    pass


def _auth() -> tuple[str, str]:
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        raise YooKassaError("YOOKASSA_SHOP_ID/YOOKASSA_SECRET_KEY not configured")
    return (settings.YOOKASSA_SHOP_ID, settings.YOOKASSA_SECRET_KEY)


async def create_payment(package_id: str, client_id: int) -> dict:
    """Create a ЮKassa payment for a token package. Returns
    {"id": <yookassa payment id>, "confirmation_url": <redirect-here URL>}."""
    package = TOKEN_PACKAGES.get(package_id)
    if not package:
        raise ValueError(f"Unknown package_id: {package_id}")

    return_url = f"{settings.PUBLIC_BASE_URL}/dashboard?payment=return"
    payload = {
        "amount": {"value": f"{package['price_rub']:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": f"VideoGen — пакет «{package['label']}» ({package['tokens']} токенов)",
        "metadata": {"client_id": str(client_id), "package_id": package_id},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{API_BASE}/payments",
            auth=_auth(),
            headers={"Idempotence-Key": str(uuid.uuid4())},
            json=payload,
        )
        if response.status_code not in (200, 201):
            raise YooKassaError(f"ЮKassa create payment error: {response.text}")
        data = response.json()

    confirmation_url = data.get("confirmation", {}).get("confirmation_url")
    if not confirmation_url:
        raise YooKassaError(f"ЮKassa response missing confirmation_url: {data}")

    return {"id": data["id"], "confirmation_url": confirmation_url}


async def fetch_payment_status(yookassa_payment_id: str) -> Optional[str]:
    """Authoritative status check via our own authenticated request — never
    trust a webhook body's status field directly, since ЮKassa doesn't sign
    notifications with an HMAC we can verify."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{API_BASE}/payments/{yookassa_payment_id}",
            auth=_auth(),
        )
        if response.status_code != 200:
            raise YooKassaError(f"ЮKassa fetch payment error: {response.text}")
        return response.json().get("status")
