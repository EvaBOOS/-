"""Anti-spam helpers: disposable mail, captcha, verification mail, input dedup."""
from __future__ import annotations

import hashlib
import logging
import smtplib
from email.message import EmailMessage
from datetime import timedelta
from typing import Optional

import httpx

from app.core.config import settings
from app.core.security import create_access_token, verify_token

logger = logging.getLogger(__name__)

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "throwaway.email", "yopmail.com", "trashmail.com", "getnada.com",
    "temp-mail.org", "sharklasers.com", "guerrillamailblock.com", "grr.la",
    "discard.email", "discardmail.com", "mailnesia.com", "maildrop.cc",
    "mintemail.com", "fakeinbox.com", "getairmail.com", "emailondeck.com",
    "tempail.com", "tempr.email", "tmpmail.org", "tmpmail.net",
    "inboxkitten.com", "moakt.com", "spamgourmet.com", "mytrashmail.com",
    "trashmailer.com", "trashemail.de", "wegwerfmail.de", "wegwerfmail.net",
    "einrot.com", "guerrillamail.info", "pokemail.net", "spam4.me",
    "bccto.me", "dispostable.com", "mailcatch.com", "mailnull.com",
    "spamherelots.com", "tempinbox.com", "thankyou2010.com", "anonymbox.com",
    "mailinater.com", "sogetthis.com", "spamhereplease.com", "safetymail.info",
    "reallymymail.com", "thisisnotmyrealemail.com", "mailmetrash.com",
    "meltmail.com", "mt2014.com", "mt2015.com", "trbvm.com",
    "yopmail.fr", "cool.fr.nf", "jetable.fr.nf", "nospam.ze.tc",
    "courriel.fr.nf", "moncourrier.fr.nf", "monemail.fr.nf", "monmail.fr.nf",
    "hidemail.de", "kmpsp.com", "nowmymail.com", "mailtemp.info",
    "dropmail.me", "mini-mail.net", "temp-mail.io", "tempmailo.com",
    "emailfake.com", "generator.email", "crazymailing.com", "fakemail.net",
    "mail-temp.com", "tempmail.dev", "burnermail.io", "guerrillamail.de",
}


def smtp_configured() -> bool:
    return bool((settings.SMTP_HOST or "").strip())


def captcha_configured() -> bool:
    return bool(
        (settings.YANDEX_SMARTCAPTCHA_SERVER_KEY or "").strip()
        and (settings.YANDEX_SMARTCAPTCHA_CLIENT_KEY or "").strip()
    )


def is_disposable_email(email: str) -> bool:
    domain = (email or "").strip().lower().rsplit("@", 1)[-1]
    return domain in DISPOSABLE_DOMAINS


def client_ip(request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    if request.client and request.client.host:
        return request.client.host
    return ""


async def verify_smartcaptcha(token: str, ip: str) -> bool:
    if not captcha_configured():
        return True
    token = (token or "").strip()
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                "https://smartcaptcha.yandexcloud.net/validate",
                data={
                    "secret": settings.YANDEX_SMARTCAPTCHA_SERVER_KEY,
                    "token": token,
                    "ip": ip or "0.0.0.0",
                },
            )
        data = response.json()
        return str(data.get("status") or "").lower() == "ok"
    except Exception as exc:
        logger.warning("SmartCaptcha validate failed: %s", exc)
        return False


def create_email_verify_token(user_id: int) -> str:
    return create_access_token(
        subject=user_id,
        expires_delta=timedelta(hours=48),
        additional_claims={"purpose": "email_verify"},
    )


def parse_email_verify_token(token: str) -> Optional[int]:
    payload = verify_token(token)
    if not payload or payload.get("purpose") != "email_verify":
        return None
    try:
        return int(payload.get("sub"))
    except (TypeError, ValueError):
        return None


def verification_url(token: str) -> str:
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/public/verify-email?token={token}"


def send_verification_email(to_email: str, token: str) -> bool:
    url = verification_url(token)
    subject = "LoudCut — подтвердите почту"
    body = (
        "Здравствуйте!\n\n"
        "Подтвердите email, чтобы запускать ролики в LoudCut:\n"
        f"{url}\n\n"
        "Ссылка действует 48 часов. Если это были не вы — просто проигнорируйте письмо.\n"
    )
    host = (settings.SMTP_HOST or "").strip()
    if not host:
        logger.info("SMTP unset; verification link for %s: %s", to_email, url)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or "noreply@loudcut.ru"
    msg["To"] = to_email
    msg.set_content(body)

    port = int(settings.SMTP_PORT or 587)
    user = (settings.SMTP_USER or "").strip()
    password = settings.SMTP_PASSWORD or ""
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception as exc:
        logger.warning("SMTP send failed: %s", exc)
        logger.info("verification link for %s: %s", to_email, url)
        return False


def input_fingerprint(*, text: str = "", file_sha: str = "") -> str:
    raw = f"{(text or '').strip().lower()}|{file_sha or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def duplicate_count(client_id: int, fingerprint: str) -> int:
    """How many times this client submitted the same input in 24h. 0 if Redis down."""
    if not fingerprint:
        return 0
    try:
        import redis

        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1.0)
        key = f"mod:dup:{client_id}:{fingerprint}"
        n = int(r.incr(key) or 1)
        if n == 1:
            r.expire(key, 24 * 3600)
        return n
    except Exception:
        return 0


def new_account_delay_seconds(client) -> int:
    """Queue delay for free self-serve accounts younger than 2 hours."""
    from datetime import datetime

    from app.models.client import AccountType

    delay = int(getattr(settings, "NEW_ACCOUNT_JOB_DELAY_SECONDS", 0) or 0)
    if delay <= 0:
        return 0
    if getattr(client, "account_type", None) != AccountType.INDIVIDUAL:
        return 0
    created = getattr(client, "created_at", None)
    if created and getattr(created, "tzinfo", None):
        created = created.replace(tzinfo=None)
    age_h = (datetime.utcnow() - (created or datetime.utcnow())).total_seconds() / 3600.0
    return delay if age_h < 2 else 0
