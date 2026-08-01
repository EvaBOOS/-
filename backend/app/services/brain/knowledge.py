"""VideoGen Brain — structured editing/script rules injected into LLM prompts."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "brain"


class BrainService:
    """Load domain JSON rule packs and render a compact prompt block."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self._cache: Dict[str, Dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "BRAIN_ENABLED", True))

    def reload(self) -> None:
        self._cache.clear()

    def list_domains(self) -> List[Dict[str, Any]]:
        """Return domain metadata for admin listing."""
        out: List[Dict[str, Any]] = []
        if not self.data_dir.is_dir():
            return out
        for path in sorted(self.data_dir.glob("*.json")):
            pack = self._load_domain(path.stem)
            if not pack:
                continue
            rules = pack.get("rules") if isinstance(pack.get("rules"), list) else []
            out.append(
                {
                    "domain": pack.get("domain") or path.stem,
                    "version": pack.get("version", 1),
                    "rules_count": len(rules),
                    "file": path.name,
                }
            )
        return out

    def get_rules(
        self,
        domains: List[str],
        max_rules: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        limit = max_rules if max_rules is not None else int(
            getattr(settings, "BRAIN_MAX_RULES", 12) or 12
        )
        limit = max(1, min(limit, 40))
        collected: List[Dict[str, str]] = []
        per_domain = max(1, limit // max(len(domains), 1))

        for domain in domains:
            pack = self._load_domain(domain.strip().lower())
            if not pack:
                continue
            rules = pack.get("rules") if isinstance(pack.get("rules"), list) else []
            for rule in rules[:per_domain]:
                if not isinstance(rule, dict):
                    continue
                text = str(rule.get("text") or "").strip()
                if not text:
                    continue
                collected.append(
                    {
                        "domain": str(pack.get("domain") or domain),
                        "id": str(rule.get("id") or ""),
                        "text": text,
                    }
                )
                if len(collected) >= limit:
                    return collected
        return collected

    def get_prompt_block(
        self,
        domains: List[str],
        max_rules: Optional[int] = None,
    ) -> str:
        """Compact bullet list for system prompts. Empty if disabled/missing."""
        if not self.enabled:
            return ""
        rules = self.get_rules(domains, max_rules=max_rules)
        if not rules:
            return ""
        lines = ["VideoGen Brain rules (follow when relevant; silent/factual guards still win):"]
        for rule in rules:
            prefix = rule["domain"]
            lines.append(f"- [{prefix}] {rule['text']}")
        return "\n".join(lines)

    def _load_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        key = (domain or "").strip().lower()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        path = self.data_dir / f"{key}.json"
        if not path.is_file():
            logger.warning("Brain domain file missing: %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Brain domain load failed (%s): %s", path, exc)
            return None
        if not isinstance(data, dict):
            return None
        self._cache[key] = data
        return data


_brain: Optional[BrainService] = None


def get_brain() -> BrainService:
    global _brain
    if _brain is None:
        _brain = BrainService()
    return _brain
