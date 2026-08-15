from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric, Enum as SQLEnum
from sqlalchemy.sql import func
from app.db.session import Base
import enum


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class Payment(Base):
    """A ЮKassa token-purchase attempt. Created PENDING when the checkout is
    initiated; flipped to SUCCEEDED only after the webhook handler
    independently re-verifies the status via the ЮKassa API (see
    yookassa_service.fetch_payment_status) — never trusted from the webhook
    body alone."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    yookassa_payment_id = Column(String(64), unique=True, index=True, nullable=False)
    package_id = Column(String(50), nullable=False)
    tokens = Column(Integer, nullable=False)
    amount_rub = Column(Numeric(10, 2), nullable=False)
    status = Column(SQLEnum(PaymentStatus, native_enum=False), default=PaymentStatus.PENDING, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Payment {self.yookassa_payment_id} ({self.status})>"
