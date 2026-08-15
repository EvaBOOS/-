"""Apply moderation verdicts to generations, accounts, and the hold queue."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.client import AccountType, Client
from app.models.generation import GenerationStatus, VideoGeneration
from app.models.moderation import BlockedFileHash, ModerationEvent
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.services.antispam import is_disposable_email, smtp_configured
from app.services.moderation.gate import (
    RULES,
    Action,
    AccountSignals,
    Verdict,
    check_account,
    check_media,
    check_output,
    check_source_link,
    check_text,
    file_sha256,
)

logger = logging.getLogger(__name__)

USER_MESSAGES = {
    Action.HOLD: "Ролик на проверке, обычно до 30 минут.",
    Action.BLOCK: "Задача отклонена модерацией. Токен не списан.",
    Action.BAN: "Аккаунт заблокирован за нарушение правил.",
}

CATEGORY_MESSAGES = {
    "TERR": "Контент нарушает правила: запрещённые темы.",
    "DRUG": "Контент нарушает правила: запрещённые вещества.",
    "FRAUD": "Контент нарушает правила: мошенничество или схемы заработка.",
    "MALW": "Контент нарушает правила: вредоносные инструкции.",
    "ILLEG": "Контент нарушает правила: незаконная деятельность.",
    "SEX": "Сексуальный контент в этом сервисе запрещён.",
    "COPY": "Нужна проверка прав на исходное видео.",
    "IMPER": "Ролик с чужим лицом, голосом или от имени организации отправлен на проверку.",
    "HATE": "Ролик отправлен на проверку.",
    "SPAM": "Слишком много однотипных задач.",
    "CSAE": "Аккаунт заблокирован.",
}

KIND_BY_MODE = {
    "avatar": "avatar",
    "viral_edit": "viral",
    "ai_clips": "clips",
}


class ModerationHalt(Exception):
    """Pipeline stopped after a blocking verdict was already persisted."""

    def __init__(self, verdict: Verdict):
        super().__init__(verdict.reason or verdict.action.value)
        self.verdict = verdict


def enabled() -> bool:
    return bool(getattr(settings, "MODERATION_ENABLED", True))


def skipped(generation: Optional[VideoGeneration]) -> bool:
    if not generation:
        return False
    meta = generation.api_responses or {}
    return bool(meta.get("moderation_override"))


def user_message(v: Verdict) -> str:
    if "CSAE" in v.categories:
        return CATEGORY_MESSAGES["CSAE"]
    for code in v.categories:
        if code in CATEGORY_MESSAGES:
            return CATEGORY_MESSAGES[code]
    return USER_MESSAGES.get(v.action, USER_MESSAGES[Action.BLOCK])


def _wipe_file(path: Optional[str]) -> None:
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _wipe_dir(path: Optional[str]) -> None:
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


async def gather_account_signals(
    db: AsyncSession,
    client: Client,
    *,
    fingerprint: str = "",
) -> AccountSignals:
    now = datetime.utcnow()
    created = client.created_at.replace(tzinfo=None) if client.created_at and client.created_at.tzinfo else (client.created_at or now)
    age_hours = max(0.0, (now - created).total_seconds() / 3600.0)

    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    window = now - timedelta(days=int(RULES["strikes"]["window_days"]))

    hour_q = await db.execute(
        select(func.count(VideoGeneration.id)).where(
            VideoGeneration.client_id == client.id,
            VideoGeneration.created_at >= hour_ago,
        )
    )
    day_q = await db.execute(
        select(func.count(VideoGeneration.id)).where(
            VideoGeneration.client_id == client.id,
            VideoGeneration.created_at >= day_ago,
        )
    )
    strike_q = await db.execute(
        select(func.count(ModerationEvent.id)).where(
            ModerationEvent.client_id == client.id,
            ModerationEvent.action.in_(["block", "ban"]),
            ModerationEvent.created_at >= window,
        )
    )

    pay_q = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.client_id == client.id,
            Payment.status == PaymentStatus.SUCCEEDED,
        )
    )
    is_paying = (
        client.account_type != AccountType.INDIVIDUAL
        or int(pay_q.scalar() or 0) > 0
    )

    email = ""
    verified = True
    if client.user_id:
        u = await db.execute(select(User).where(User.id == client.user_id))
        user = u.scalar_one_or_none()
        if user:
            email = (user.email or "").lower()
            verified = bool(getattr(user, "email_verified", True))
    if not smtp_configured():
        verified = True

    same_fp = 1
    if fingerprint:
        same_fp = _fingerprint_accounts(client.id, fingerprint)

    stored_strikes = int(getattr(client, "moderation_strikes", 0) or 0)
    strikes = max(int(strike_q.scalar() or 0), stored_strikes)

    return AccountSignals(
        account_age_hours=age_hours,
        email_verified=verified,
        tasks_last_hour=int(hour_q.scalar() or 0),
        tasks_last_day=int(day_q.scalar() or 0),
        distinct_accounts_same_fingerprint=same_fp,
        strikes_90d=strikes,
        is_paying=is_paying,
        disposable_email=is_disposable_email(email),
    )


def _fingerprint_accounts(client_id: int, fingerprint: str) -> int:
    try:
        import redis

        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1.0)
        key = f"mod:fp:{fingerprint[:80]}"
        r.sadd(key, str(client_id))
        r.expire(key, 90 * 24 * 3600)
        return int(r.scard(key) or 1)
    except Exception:
        return 1


async def hash_is_blocked(db: AsyncSession, digest: str) -> bool:
    row = await db.execute(
        select(BlockedFileHash.id).where(BlockedFileHash.sha256 == digest)
    )
    return row.scalar() is not None


async def remember_bad_hash(db: AsyncSession, digest: str, category: str = "CSAE") -> None:
    if not digest:
        return
    exists = await hash_is_blocked(db, digest)
    if exists:
        return
    db.add(BlockedFileHash(sha256=digest, category=category))


def _client_status_for(v: Verdict) -> GenerationStatus:
    if v.action == Action.HOLD:
        return GenerationStatus.MODERATION_HOLD
    return GenerationStatus.BLOCKED


async def persist_event(
    db: AsyncSession,
    *,
    generation: Optional[VideoGeneration],
    client: Client,
    verdict: Verdict,
) -> ModerationEvent:
    evidence = dict(verdict.evidence or {})
    if "CSAE" in verdict.categories:
        evidence = {"sha256": evidence.get("sha256")} if evidence.get("sha256") else {}
    event = ModerationEvent(
        generation_id=generation.id if generation else None,
        client_id=client.id,
        user_id=client.user_id,
        action=verdict.action.value,
        categories=list(verdict.categories),
        stage=verdict.stage,
        reason=verdict.reason if "CSAE" not in verdict.categories else "critical",
        scores={k: v for k, v in (verdict.scores or {}).items() if not str(k).endswith((".jpg", ".png"))},
        evidence=evidence,
    )
    db.add(event)
    return event


async def apply_account_penalty(db: AsyncSession, client: Client, verdict: Verdict) -> None:
    critical = set(RULES["strikes"]["critical_categories_bypass_threshold"])
    if verdict.action == Action.BAN or (critical & set(verdict.categories)):
        client.is_active = False
        client.moderation_frozen_at = datetime.utcnow()
        user = await db.execute(select(User).where(User.id == client.user_id))
        u = user.scalar_one_or_none()
        if u:
            u.is_active = False
        return
    if verdict.action == Action.BLOCK:
        client.moderation_strikes = int(client.moderation_strikes or 0) + 1
        client.last_strike_at = datetime.utcnow()
        if client.moderation_strikes >= int(RULES["strikes"]["ban_threshold"]):
            client.is_active = False
            client.moderation_frozen_at = datetime.utcnow()


async def enforce(
    db: AsyncSession,
    generation: VideoGeneration,
    client: Client,
    verdict: Verdict,
    *,
    extra_wipe: Optional[list[str]] = None,
) -> None:
    """Persist verdict. Raise ModerationHalt on hold/block/ban."""
    if not enabled() or skipped(generation):
        return
    if verdict.action in (Action.ALLOW, Action.FLAG):
        generation.api_responses = generation.api_responses or {}
        if verdict.action == Action.FLAG or verdict.categories:
            generation.api_responses["moderation"] = {
                "action": verdict.action.value,
                "categories": verdict.categories,
                "stage": verdict.stage,
                "reason": verdict.reason,
            }
            await persist_event(db, generation=generation, client=client, verdict=verdict)
            await db.commit()
        return

    digest = (verdict.evidence or {}).get("sha256")
    if "CSAE" in verdict.categories and digest:
        await remember_bad_hash(db, str(digest), "CSAE")

    if "CSAE" in verdict.categories:
        _wipe_file(generation.source_video_path)
        _wipe_file(generation.audio_path)
        _wipe_file(generation.avatar_video_path)
        _wipe_file(generation.final_video_path)
        for p in extra_wipe or []:
            _wipe_file(p)
        generation.source_video_path = None
        generation.audio_path = None
        generation.original_text = "[removed]"
        generation.generated_script = None
        generation.api_responses = {"moderation": {"action": "ban", "categories": ["CSAE"], "stage": verdict.stage}}

    await persist_event(db, generation=generation, client=client, verdict=verdict)
    await apply_account_penalty(db, client, verdict)

    generation.status = _client_status_for(verdict)
    generation.error_message = user_message(verdict)
    generation.api_responses = generation.api_responses or {}
    if "CSAE" not in verdict.categories:
        generation.api_responses["moderation"] = {
            "action": verdict.action.value,
            "categories": verdict.categories,
            "stage": verdict.stage,
            "reason": verdict.reason,
        }
    await db.commit()
    raise ModerationHalt(verdict)


async def preflight(
    db: AsyncSession,
    client: Client,
    *,
    text: str = "",
    source_url: str = "",
    rights_confirmed: bool = True,
    fingerprint: str = "",
    context: str = "запрос пользователя",
) -> Verdict:
    """G0 + G1 before enqueue. Raises HTTPException on G0 hold/ban and G1 block/ban."""
    if not enabled():
        return Verdict(stage="G0")

    signals = await gather_account_signals(db, client, fingerprint=fingerprint)
    if smtp_configured() and not signals.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Подтвердите почту — ссылка в письме. Повторная отправка в профиле.",
        )
    v = check_account(signals)
    if v.action == Action.BAN:
        await persist_event(db, generation=None, client=client, verdict=v)
        await apply_account_penalty(db, client, v)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=user_message(v))
    if v.blocking:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=user_message(v))

    text_v = await check_text(text, stage="G1", context=context)
    v = v.merge(text_v)
    if source_url:
        v = v.merge(check_source_link(source_url, user_confirmed_rights=rights_confirmed))

    from app.services.antispam import duplicate_count, input_fingerprint

    dup_n = duplicate_count(client.id, input_fingerprint(text=text, file_sha=""))
    if dup_n >= 5:
        v = v.merge(Verdict(Action.HOLD, ["SPAM"], reason="повторяющийся вход", stage="G0"))
    elif dup_n >= 3:
        v = v.merge(Verdict(Action.FLAG, ["SPAM"], reason="повторяющийся вход", stage="G0"))

    if v.action == Action.BAN:
        await persist_event(db, generation=None, client=client, verdict=v)
        await apply_account_penalty(db, client, v)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=user_message(v))
    if v.action == Action.BLOCK:
        await persist_event(db, generation=None, client=client, verdict=v)
        await apply_account_penalty(db, client, v)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=user_message(v))
    return v


async def apply_preflight_to_generation(
    db: AsyncSession,
    generation: VideoGeneration,
    client: Client,
    verdict: Verdict,
) -> bool:
    """Attach G1 FLAG/HOLD to a new generation. Returns True if the job may be enqueued."""
    generation.api_responses = generation.api_responses or {}
    if verdict.action == Action.HOLD:
        await persist_event(db, generation=generation, client=client, verdict=verdict)
        generation.status = GenerationStatus.MODERATION_HOLD
        generation.error_message = user_message(verdict)
        generation.api_responses["moderation"] = {
            "action": verdict.action.value,
            "categories": verdict.categories,
            "stage": verdict.stage,
            "reason": verdict.reason,
        }
        await db.commit()
        return False
    if verdict.action == Action.FLAG or verdict.categories:
        generation.api_responses["moderation"] = {
            "action": verdict.action.value,
            "categories": verdict.categories,
            "stage": verdict.stage,
            "reason": verdict.reason,
        }
    return True


async def run_g2(
    db: AsyncSession,
    generation: VideoGeneration,
    client: Client,
    video_path: str,
) -> None:
    if not enabled() or skipped(generation) or not video_path or not os.path.isfile(video_path):
        return

    from app.services.video.ffmpeg_service import FFmpegService

    ffmpeg = FFmpegService()
    duration = 0.0
    try:
        duration = float(ffmpeg.get_video_duration(video_path) or 0)
    except Exception:
        duration = 0.0

    work = os.path.join(os.path.dirname(video_path) or ".", f"_mod_{generation.id}")
    frames: list[str] = []
    try:
        frames = ffmpeg.extract_moderation_frames(
            video_path,
            work,
            per_minute=int(RULES["thresholds"]["frames_sampled_per_minute"]),
        )
    except Exception as exc:
        logger.warning("moderation frame extract failed: %s", exc)

    digest = file_sha256(video_path)
    known = await hash_is_blocked(db, digest)
    has_url = bool((generation.api_responses or {}).get("source_url"))
    movie_seconds = float(getattr(settings, "MODERATION_MOVIE_SECONDS", 5400) or 5400)

    try:
        v = await check_media(
            video_path,
            frames,
            known_bad=known,
            duration_sec=duration,
            has_source_url=has_url,
            movie_seconds=movie_seconds,
        )
        generation.api_responses = generation.api_responses or {}
        generation.api_responses["source_sha256"] = digest
        generation.api_responses["source_duration_sec"] = duration
        await enforce(db, generation, client, v, extra_wipe=frames + [video_path])
    finally:
        _wipe_dir(work)


async def run_g3(
    db: AsyncSession,
    generation: VideoGeneration,
    client: Client,
    transcript: str,
) -> None:
    if not enabled() or skipped(generation) or not (transcript or "").strip():
        return
    v = await check_text(transcript, stage="G3", context="речь в ролике")
    await enforce(db, generation, client, v)


async def run_g4(
    db: AsyncSession,
    generation: VideoGeneration,
    client: Client,
    script: str,
    *,
    avatar_source: str = "library",
    consent_id: Optional[str] = None,
) -> None:
    if not enabled() or skipped(generation):
        return
    v = await check_output(script or "", avatar_source=avatar_source, consent_id=consent_id)
    await enforce(db, generation, client, v)


def public_moderation_payload(generation: VideoGeneration) -> dict:
    meta = (generation.api_responses or {}).get("moderation") or {}
    cats = meta.get("categories") or []
    if "CSAE" in cats:
        return {"action": "ban", "message": CATEGORY_MESSAGES["CSAE"]}
    return {
        "action": meta.get("action"),
        "categories": cats,
        "stage": meta.get("stage"),
        "message": generation.error_message,
        "appealed": bool(meta.get("appealed")),
    }
