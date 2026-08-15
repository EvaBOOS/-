from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.client import AccountType, Client, ClientBranding, PLAN_LIMITS, SubscriptionPlan
from app.models.user import User, UserRole


async def provision_client(
    db: AsyncSession,
    *,
    user_email: str,
    user_password: str,
    user_full_name: Optional[str],
    company_name: Optional[str],
    account_type: AccountType,
    discount_percent: int,
    subscription_plan: SubscriptionPlan,
    elevenlabs_voice_id: Optional[str] = None,
    heygen_avatar_id: Optional[str] = None,
    custom_voice_clone_id: Optional[str] = None,
    initial_credits: Optional[int] = None,
    email_verified: bool = True,
) -> Client:
    """Create the User+Client+ClientBranding trio shared by admin-provisioned
    B2B accounts (direct create, application approval) and self-serve public
    registration. Raises HTTPException(400) if the email is already taken.

    `initial_credits`, when given, overrides the subscription plan's monthly
    limit — used by self-serve signup to grant a small free-trial balance
    instead of a full B2B plan allowance.
    """
    result = await db.execute(select(User).where(User.email == user_email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )

    user = User(
        email=user_email,
        hashed_password=get_password_hash(user_password),
        full_name=user_full_name,
        role=UserRole.CLIENT,
        email_verified=email_verified,
    )
    db.add(user)
    await db.flush()

    credits = initial_credits if initial_credits is not None else PLAN_LIMITS.get(subscription_plan, 15)
    client = Client(
        user_id=user.id,
        company_name=company_name,
        account_type=account_type,
        discount_percent=discount_percent,
        subscription_plan=subscription_plan,
        credits_remaining=credits,
        elevenlabs_voice_id=elevenlabs_voice_id,
        heygen_avatar_id=heygen_avatar_id,
        custom_voice_clone_id=custom_voice_clone_id,
    )
    db.add(client)
    await db.flush()

    branding = ClientBranding(client_id=client.id)
    db.add(branding)

    await db.commit()
    await db.refresh(client)
    return client
