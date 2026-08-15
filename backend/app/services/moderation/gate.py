"""
LoudCut moderation gates.

G0 account → G1 input text/URL → G2 media hashes/frames → G3 transcript → G4 output
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

RULES = json.loads((Path(__file__).parent / "moderation_rules.json").read_text(encoding="utf-8"))
CATS = {c["code"]: c for c in RULES["categories"]}
TH = RULES["thresholds"]


class Action(str, Enum):
    ALLOW = "allow"
    FLAG = "flag"
    HOLD = "hold"
    BLOCK = "block"
    BAN = "ban"


ORDER = [Action.ALLOW, Action.FLAG, Action.HOLD, Action.BLOCK, Action.BAN]


@dataclass
class Verdict:
    action: Action = Action.ALLOW
    categories: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    stage: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.action in (Action.HOLD, Action.BLOCK, Action.BAN)

    def merge(self, other: "Verdict") -> "Verdict":
        strongest = max(self.action, other.action, key=ORDER.index)
        return Verdict(
            action=strongest,
            categories=sorted(set(self.categories) | set(other.categories)),
            scores={**self.scores, **other.scores},
            reason=other.reason if strongest == other.action else self.reason,
            stage=other.stage if strongest == other.action else self.stage,
            evidence={**self.evidence, **other.evidence},
        )


def _action_for(code: str) -> Action:
    return Action(CATS[code]["action"])


# Dictionary hits except CSAE/TERR go to HOLD so the LLM can tell
# "instruction" from "educational / harm-reduction".

PATTERNS: dict[str, list[str]] = {
    "CSAE": [
        r"(несовершеннолетн\w*|школьниц\w*|детск\w+\s+порн\w*|\bcsam\b|\bcp\b)",
        r"\b(child|children|kid|kids|minor|underage).{0,48}(porn|nude|nsfw|sex)",
    ],
    "DRUG": [
        r"\bзакладк\w*",
        r"\bмефедрон\w*",
        r"\bсоль\s+скорост\w*",
        r"\bкак\s+сделать\s+(меф|спайс|соль)\b",
        r"\bкупить\s+(меф|гашиш|амфетамин|кокаин)\b",
    ],
    "FRAUD": [
        r"гарантированн\w+\s+доход",
        r"заработок\s+от\s+\d+\s*(?:000|к|k)\b",
        r"схема\s+без\s+вложений",
        r"пирамид\w+",
        r"t\.me/\w+.{0,80}(заработ|доход|вложен)",
    ],
    "TERR": [],
    "MALW": [
        r"как\s+взломать\b",
        r"обойти\s+защит\w*",
        r"изготов\w+\s+(бомб|взрывчат|оружи)",
    ],
    "ILLEG": [
        r"поддельн\w+\s+(паспорт|документ|права)",
        r"\bобнал\w*",
    ],
    "IMPER": [
        r"от\s+лица\s+(путин|зеленск|трамп|медведев|мишустин)",
        r"голосом\s+(путин|знаменитост|селебрити)",
        r"от\s+имени\s+(сбер\w*|банка|фнс|мвд|госдум|росреестр)",
        r"озвучь\s+голосом\s+\w+",
    ],
    "SEX": [
        r"\bonlyfans\b.{0,40}(слив|nude|nsfw)",
    ],
}


def normalize(text: str) -> str:
    """Collapse obfuscation: latin-as-cyrillic, separators, stretched letters."""
    table = str.maketrans("aAeEoOpPcCxXyYkKMmTHB", "аАеЕоОрРсСхХуУкКМмТНВ")
    t = text.translate(table).lower()
    t = re.sub(r"[.\-_*·•|]+", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return re.sub(r"\s+", " ", t).strip()


def prefilter(text: str) -> Verdict:
    norm = normalize(text)
    hits: list[str] = []
    for code, pats in PATTERNS.items():
        if any(re.search(p, norm, flags=re.IGNORECASE) for p in pats):
            hits.append(code)
    if not hits:
        return Verdict(stage="prefilter")
    if "CSAE" in hits:
        return Verdict(Action.BAN, ["CSAE"], reason="словарный префильтр CSAE", stage="prefilter")
    if "TERR" in hits:
        return Verdict(Action.BLOCK, ["TERR"] + [h for h in hits if h != "TERR"],
                       reason="словарный префильтр TERR", stage="prefilter")
    return Verdict(
        Action.HOLD,
        hits,
        reason="словарный префильтр",
        stage="prefilter",
    )


@dataclass
class AccountSignals:
    account_age_hours: float
    email_verified: bool
    tasks_last_hour: int
    tasks_last_day: int
    distinct_accounts_same_fingerprint: int
    strikes_90d: int
    is_paying: bool
    disposable_email: bool = False


def check_account(s: AccountSignals) -> Verdict:
    v = Verdict(stage="G0")
    if s.strikes_90d >= RULES["strikes"]["ban_threshold"]:
        return Verdict(Action.BAN, ["SPAM"], reason="исчерпан лимит нарушений", stage="G0")
    if s.disposable_email and not s.is_paying:
        v = v.merge(Verdict(Action.HOLD, ["SPAM"], reason="одноразовая почта", stage="G0"))
    if not s.email_verified and s.tasks_last_day > 2:
        v = v.merge(Verdict(Action.HOLD, reason="почта не подтверждена", stage="G0"))
    limit_hour = 30 if s.is_paying else 6
    if s.tasks_last_hour > limit_hour:
        v = v.merge(Verdict(Action.HOLD, ["SPAM"], reason="превышен темп задач", stage="G0"))
    if s.distinct_accounts_same_fingerprint >= 3 and not s.is_paying:
        v = v.merge(Verdict(Action.HOLD, ["SPAM"], reason="мультиаккаунт с одного устройства", stage="G0"))
    if s.account_age_hours < 1 and s.tasks_last_hour > 2:
        v = v.merge(Verdict(Action.FLAG, reason="свежий аккаунт, высокий темп", stage="G0"))
    return v


async def check_text(text: str, *, stage: str, context: str = "") -> Verdict:
    if not text or not text.strip():
        return Verdict(stage=stage)

    v = prefilter(text)
    v.stage = stage
    if v.action == Action.BAN or (v.action == Action.BLOCK and "TERR" in v.categories):
        return v
    # Never send CSAE-flagged material to an LLM.
    if "CSAE" in v.categories:
        return v

    try:
        mod = await provider_text_moderation(text)
    except Exception as exc:
        logger.warning("text moderation provider failed: %s", exc)
        mod = {}

    for code, score in mod.items():
        v.scores[code] = score
        if code == "CSAE" and score >= TH["text_model_hold"]:
            return Verdict(Action.BAN, ["CSAE"], {**v.scores, code: score},
                           reason=f"CSAE={score:.2f}", stage=stage)
        if score >= TH["text_model_block"]:
            v = v.merge(Verdict(_action_for(code), [code], reason=f"{code}={score:.2f}", stage=stage))
        elif score >= TH["text_model_hold"]:
            v = v.merge(Verdict(Action.HOLD, [code], reason=f"{code}={score:.2f} (пограничный)", stage=stage))

    if v.action == Action.HOLD and "CSAE" not in v.categories:
        try:
            judged = await provider_llm_judge(text, categories=v.categories, context=context)
        except Exception as exc:
            logger.warning("LLM judge failed: %s", exc)
            judged = {"intent": "unclear"}
        intent = judged.get("intent")
        if intent == "educational":
            v = Verdict(Action.FLAG, v.categories, v.scores,
                        reason="LLM: просветительский контекст", stage=stage)
        elif intent == "violating" and v.categories:
            worst = max(v.categories, key=lambda c: ORDER.index(_action_for(c)))
            v = Verdict(_action_for(worst), v.categories, v.scores,
                        reason="LLM: нарушающий замысел", stage=stage)
    return v


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def check_media(
    video_path: str | Path,
    frames: Iterable[str | Path],
    *,
    known_bad: bool = False,
    duration_sec: float = 0.0,
    has_source_url: bool = False,
    movie_seconds: float = 5400.0,
) -> Verdict:
    v = Verdict(stage="G2")
    digest = file_sha256(video_path)
    v.evidence["sha256"] = digest

    if known_bad:
        return Verdict(Action.BAN, ["CSAE"], reason="совпадение с базой запрещённого",
                       stage="G2", evidence={"sha256": digest})

    frame_list = list(frames)
    try:
        if provider_csam_scan(frame_list):
            return Verdict(Action.BAN, ["CSAE"], reason="сработал CSAM-скан",
                           stage="G2", evidence={"sha256": digest})
    except Exception as exc:
        logger.warning("CSAM scan adapter failed: %s", exc)

    flagged_block = 0
    flagged_hold = 0
    for f in frame_list:
        try:
            score = provider_nsfw_score(f)
        except Exception:
            score = 0.0
        v.scores[str(f)] = score
        if score >= TH["nsfw_frame_block"]:
            flagged_block += 1
        elif score >= TH["nsfw_frame_hold"]:
            flagged_hold += 1
    if flagged_block >= TH["min_frames_flagged_to_trigger"]:
        v = v.merge(Verdict(Action.BLOCK, ["SEX"], reason=f"NSFW на {flagged_block} кадрах", stage="G2"))
    elif flagged_hold >= TH["min_frames_flagged_to_trigger"]:
        v = v.merge(Verdict(Action.HOLD, ["SEX"], reason=f"пограничный NSFW на {flagged_hold} кадрах", stage="G2"))

    if has_source_url and duration_sec >= movie_seconds:
        v = v.merge(Verdict(
            Action.HOLD, ["COPY"],
            reason=f"длинный исходник ({int(duration_sec)}с) с чужой ссылки",
            stage="G2",
        ))
    return v


def check_source_link(url: str, *, user_confirmed_rights: bool) -> Verdict:
    if not user_confirmed_rights:
        return Verdict(Action.HOLD, ["COPY"],
                       reason="не подтверждены права на исходное видео",
                       stage="G1", evidence={"url": url})
    return Verdict(stage="G1", evidence={"url": url})


async def check_output(script: str, *, avatar_source: str = "library", consent_id: str | None = None) -> Verdict:
    v = await check_text(script, stage="G4", context="итоговый сценарий")
    if avatar_source == "uploaded_face" and not consent_id:
        v = v.merge(Verdict(Action.HOLD, ["IMPER"],
                            reason="лицо третьего лица без подтверждения согласия", stage="G4"))
    return v


def audit_log(job_id: str, v: Verdict, user_id: str) -> dict[str, Any]:
    return {
        "ts": int(time.time()),
        "job_id": job_id,
        "user_id": user_id,
        "action": v.action.value,
        "categories": v.categories,
        "stage": v.stage,
        "reason": v.reason,
        "scores": v.scores,
        "evidence": v.evidence,
    }


# OpenAI omni-moderation → LoudCut codes
_OPENAI_MAP = {
    "sexual/minors": "CSAE",
    "sexual": "SEX",
    "hate": "HATE",
    "hate/threatening": "HATE",
    "harassment": "HATE",
    "harassment/threatening": "HATE",
    "illicit": "ILLEG",
    "illicit/violent": "TERR",
    "violence": "TERR",
    "self-harm/instructions": "MALW",
    "self-harm/intent": "MALW",
}


async def provider_text_moderation(text: str) -> dict[str, float]:
    """OpenAI omni-moderation-latest. Fail-open if no key."""
    from app.core.config import settings

    key = (settings.OPENAI_API_KEY or "").strip()
    if not key or not text.strip():
        return {}

    import httpx

    payload = {"model": "omni-moderation-latest", "input": text[:12000]}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/moderations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code != 200:
        logger.warning("OpenAI moderation HTTP %s", response.status_code)
        return {}

    results = (response.json().get("results") or [{}])[0]
    scores_in = results.get("category_scores") or {}
    out: dict[str, float] = {}
    for src, dst in _OPENAI_MAP.items():
        score = float(scores_in.get(src) or 0.0)
        if score > out.get(dst, 0.0):
            out[dst] = score
    return out


async def provider_llm_judge(text: str, *, categories: list[str], context: str) -> dict[str, Any]:
    from app.services.ai.openai_service import OpenAIService

    svc = OpenAIService()
    raw = await svc._chat_completion(
        system_prompt=(
            "You are a content-moderation judge for a short-form video editor. "
            "Reply with JSON only: {\"intent\": \"educational\"|\"violating\"|\"unclear\", \"why\": \"...\"}. "
            "educational = news, harm-reduction, documentary, medical warning. "
            "violating = instruction, sale, recruitment, scam, sexual exploitation."
        ),
        user_prompt=(
            f"Context: {context}\nFlagged categories: {', '.join(categories) or 'none'}\n\n"
            f"Text:\n{text[:4000]}"
        ),
        temperature=0.0,
        max_tokens=180,
    )
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"intent": "unclear"}
    intent = str(data.get("intent") or "unclear")
    if intent not in {"educational", "violating", "unclear"}:
        intent = "unclear"
    return {"intent": intent, "why": str(data.get("why") or "")[:400]}


_nudenet = None


def provider_nsfw_score(frame_path: str | Path) -> float:
    """Local NudeNet if installed. Never send frames to an LLM."""
    global _nudenet
    try:
        if _nudenet is None:
            from nudenet import NudeDetector  # type: ignore

            _nudenet = NudeDetector()
        detections = _nudenet.detect(str(frame_path)) or []
        scores = [float(d.get("score") or 0.0) for d in detections if d.get("class") not in {"FACE_FEMALE", "FACE_MALE"}]
        return max(scores) if scores else 0.0
    except Exception:
        return 0.0


def provider_csam_scan(frames: Iterable[str | Path]) -> bool:
    """PhotoDNA / Cloudflare CSAM Scanning Tool only. Never an LLM. Stub until access is granted."""
    return False


def provider_known_bad_hash(sha256: str) -> bool:
    return False
