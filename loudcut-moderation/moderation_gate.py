"""
LoudCut — шлюз модерации.

Пять точек проверки в пайплайне:
  G0  аккаунт        — до постановки задачи (антиспам, лимиты)
  G1  входной текст  — промпт, сценарий, ссылка
  G2  входное медиа  — кадры и хеши загруженного файла
  G3  транскрипт     — сразу после Whisper (текст уже есть, проверка почти бесплатна)
  G4  выход          — перед выдачей: сценарий, озвучка, аватар, маркировка

Каждая проверка возвращает Verdict. Задача Celery останавливается на первом
блокирующем вердикте, токены возвращаются, событие пишется в аудит-лог.

Провайдеры подключаются через функции-адаптеры внизу файла — заменить заглушки
на реальные вызовы.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

RULES = json.loads((Path(__file__).parent / "moderation_rules.json").read_text("utf-8"))
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
        """Побеждает более строгое действие; категории и оценки объединяются."""
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


# --------------------------------------------------------------------------
# Слой 1: дешёвый префильтр по словарям. Ловит очевидное до платных вызовов.
# --------------------------------------------------------------------------

# Словари держать в отдельных файлах и пополнять по инцидентам.
# Здесь — только форма записи. Паттерны пишутся по нормализованному тексту.
PATTERNS: dict[str, list[str]] = {
    "DRUG": [r"\bзакладк\w*", r"\bмефедрон\w*", r"\bсоль\s+скорост\w*"],
    "FRAUD": [r"гарантированн\w+ доход", r"заработок от \d+ ?(?:000|к|k)\b", r"схема без вложений"],
    "TERR": [],   # список запрещённых организаций подгружать из файла
    "MALW": [r"как взломать\b", r"обойти защиту\b"],
}


def normalize(text: str) -> str:
    """Схлопывает обфускацию: латиница вместо кириллицы, разделители, повторы."""
    table = str.maketrans("aAeEoOpPcCxXyYkKMmTHB", "аАеЕоОрРсСхХуУкКМмТНВ")
    t = text.translate(table).lower()
    t = re.sub(r"[.\-_*·•|]+", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return re.sub(r"\s+", " ", t).strip()


def prefilter(text: str) -> Verdict:
    norm = normalize(text)
    hits: list[str] = []
    for code, pats in PATTERNS.items():
        if any(re.search(p, norm) for p in pats):
            hits.append(code)
    if not hits:
        return Verdict(stage="prefilter")
    worst = max(hits, key=lambda c: ORDER.index(_action_for(c)))
    return Verdict(
        action=_action_for(worst),
        categories=hits,
        reason="сработал словарный префильтр",
        stage="prefilter",
    )


# --------------------------------------------------------------------------
# G0 — аккаунт и антиспам
# --------------------------------------------------------------------------

@dataclass
class AccountSignals:
    account_age_hours: float
    email_verified: bool
    tasks_last_hour: int
    tasks_last_day: int
    distinct_accounts_same_fingerprint: int
    strikes_90d: int
    is_paying: bool


def check_account(s: AccountSignals) -> Verdict:
    v = Verdict(stage="G0")
    if s.strikes_90d >= RULES["strikes"]["ban_threshold"]:
        return Verdict(Action.BAN, ["SPAM"], reason="исчерпан лимит нарушений", stage="G0")
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


# --------------------------------------------------------------------------
# G1 / G3 — текст: промпт, сценарий, транскрипт
# --------------------------------------------------------------------------

def check_text(text: str, *, stage: str, context: str = "") -> Verdict:
    if not text or not text.strip():
        return Verdict(stage=stage)

    v = prefilter(text)
    v.stage = stage
    if v.action == Action.BAN:
        return v

    mod = provider_text_moderation(text)             # быстрый классификатор
    for code, score in mod.items():
        v.scores[code] = score
        if score >= TH["text_model_block"]:
            v = v.merge(Verdict(_action_for(code), [code], reason=f"{code}={score:.2f}", stage=stage))
        elif score >= TH["text_model_hold"]:
            v = v.merge(Verdict(Action.HOLD, [code], reason=f"{code}={score:.2f} (пограничный)", stage=stage))

    # спорные случаи отдаём LLM: она различает «инструкция» и «обзор/профилактика»
    if v.action == Action.HOLD:
        judged = provider_llm_judge(text, categories=v.categories, context=context)
        if judged.get("intent") == "educational":
            v = Verdict(Action.FLAG, v.categories, v.scores,
                        reason="LLM: просветительский контекст", stage=stage)
        elif judged.get("intent") == "violating":
            worst = max(v.categories, key=lambda c: ORDER.index(_action_for(c)))
            v = Verdict(_action_for(worst), v.categories, v.scores,
                        reason="LLM: нарушающий замысел", stage=stage)
    return v


# --------------------------------------------------------------------------
# G2 — медиа
# --------------------------------------------------------------------------

def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_media(video_path: str | Path, frames: Iterable[str | Path]) -> Verdict:
    """frames — кадры, снятые из видео (~4 на минуту, плюс первый и последний)."""
    v = Verdict(stage="G2")

    digest = file_sha256(video_path)
    if provider_known_bad_hash(digest):
        return Verdict(Action.BAN, ["CSAE"], reason="совпадение с базой запрещённого",
                       stage="G2", evidence={"sha256": digest})

    # CSAM-скан выполняет внешний специализированный сервис.
    # Результат — только флаг, материал не хранить и не передавать дальше.
    if provider_csam_scan(frames):
        return Verdict(Action.BAN, ["CSAE"], reason="сработал CSAM-скан",
                       stage="G2", evidence={"sha256": digest})

    flagged = 0
    for f in frames:
        score = provider_nsfw_score(f)
        v.scores[str(f)] = score
        if score >= TH["nsfw_frame_block"]:
            flagged += 1
    if flagged >= TH["min_frames_flagged_to_trigger"]:
        v = v.merge(Verdict(Action.BLOCK, ["SEX"], reason=f"NSFW на {flagged} кадрах", stage="G2"))
    v.evidence["sha256"] = digest
    return v


def check_source_link(url: str, *, user_confirmed_rights: bool) -> Verdict:
    """Ссылка на чужой ролик — зона авторских прав, а не морали."""
    if not user_confirmed_rights:
        return Verdict(Action.HOLD, ["COPY"],
                       reason="не подтверждены права на исходное видео",
                       stage="G1", evidence={"url": url})
    return Verdict(stage="G1", evidence={"url": url})


# --------------------------------------------------------------------------
# G4 — выход
# --------------------------------------------------------------------------

def check_output(script: str, *, avatar_source: str, consent_id: str | None) -> Verdict:
    """avatar_source: 'library' | 'user_face' | 'uploaded_face'"""
    v = check_text(script, stage="G4", context="итоговый сценарий")
    if avatar_source == "uploaded_face" and not consent_id:
        v = v.merge(Verdict(Action.HOLD, ["IMPER"],
                            reason="лицо третьего лица без подтверждения согласия", stage="G4"))
    return v


# --------------------------------------------------------------------------
# Точка входа для задачи Celery
# --------------------------------------------------------------------------

def moderate_job(job: dict[str, Any]) -> Verdict:
    v = check_account(job["account_signals"])
    if v.blocking:
        return v

    v = v.merge(check_text(job.get("prompt", ""), stage="G1", context="запрос пользователя"))
    if job.get("source_url"):
        v = v.merge(check_source_link(job["source_url"],
                                      user_confirmed_rights=job.get("rights_confirmed", False)))
    if v.blocking:
        return v

    if job.get("video_path"):
        v = v.merge(check_media(job["video_path"], job.get("frames", [])))
    if v.blocking:
        return v

    v = v.merge(check_text(job.get("transcript", ""), stage="G3", context="речь в ролике"))
    return v


def audit_log(job_id: str, v: Verdict, user_id: str) -> dict[str, Any]:
    """Писать в отдельную таблицу: разбор инцидентов и статистика ложных срабатываний."""
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


# --------------------------------------------------------------------------
# Адаптеры провайдеров — заменить заглушки
# --------------------------------------------------------------------------

def provider_text_moderation(text: str) -> dict[str, float]:
    """OpenAI omni-moderation-latest (бесплатный) → маппинг его категорий в коды LoudCut.
    Резерв — свой классификатор через AITunnel."""
    return {}


def provider_llm_judge(text: str, *, categories: list[str], context: str) -> dict[str, Any]:
    """LLM с жёсткой JSON-схемой: {"intent": "educational|violating|unclear", "why": "..."}"""
    return {"intent": "unclear"}


def provider_nsfw_score(frame_path: str | Path) -> float:
    """NudeNet локально или облачный сервис распознавания. 0.0–1.0."""
    return 0.0


def provider_csam_scan(frames: Iterable[str | Path]) -> bool:
    """Только специализированный сервис хеш-матчинга (PhotoDNA / CSAM Scanning Tool).
    Никогда не LLM и не собственная модель."""
    return False


def provider_known_bad_hash(sha256: str) -> bool:
    """Локальная база хешей ранее заблокированных файлов."""
    return False
