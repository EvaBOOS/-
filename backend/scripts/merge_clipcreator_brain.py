"""Merge ClipCreator-style extracted brain JSON into app/data/brain packs.

Usage:
  python scripts/merge_clipcreator_brain.py path/to/cc-brain-extract.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO_BACKEND = Path(__file__).resolve().parents[1]
BRAIN_DIR = REPO_BACKEND / "app" / "data" / "brain"
DEFAULT_EXTRACT = Path("cc-brain-extract.json")
SOURCE_TAG = "clipcreator"
MIN_LEN = 30
MAX_LEN = 220
DEDUP_PREFIX = 55


def norm(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[«»\"']", "", t)
    return t


def resolve_extract(path: Path | None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    candidates = [
        Path.cwd() / DEFAULT_EXTRACT.name,
        Path(__file__).resolve().parent / DEFAULT_EXTRACT.name,
        Path(tempfile.gettempdir()) / DEFAULT_EXTRACT.name,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"Extract JSON not found. Pass a path or place {DEFAULT_EXTRACT.name} "
        f"in CWD, scripts/, or the system temp dir."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "extract",
        nargs="?",
        type=Path,
        default=None,
        help="Path to extracted rules JSON",
    )
    args = parser.parse_args(argv)

    extract_path = resolve_extract(args.extract)
    extract = json.loads(extract_path.read_text(encoding="utf-8"))

    existing_norm: set[str] = set()
    packs: dict = {}
    for f in BRAIN_DIR.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        packs[data["domain"]] = data
        for r in data.get("rules") or []:
            existing_norm.add(norm(str(r.get("text") or "")))

    counters: dict[str, int] = {}
    for domain, data in packs.items():
        prefix = {"hooks": "h", "editing": "e", "storytelling": "s", "platforms": "p"}.get(
            domain, "x"
        )
        nums = []
        for r in data["rules"]:
            m = re.match(rf"{prefix}(\d+)$", str(r.get("id") or ""), re.I)
            if m:
                nums.append(int(m.group(1)))
        counters[domain] = max(nums) if nums else 0

    added = {d: 0 for d in packs}
    skipped = 0
    for r in extract.get("rules") or []:
        domain = r["domain"]
        if domain not in packs:
            continue
        text = r["text"].strip()
        if len(text) < MIN_LEN or len(text) > MAX_LEN:
            skipped += 1
            continue
        n = norm(text)
        if n in existing_norm or any(n[:DEDUP_PREFIX] == e[:DEDUP_PREFIX] for e in existing_norm):
            skipped += 1
            continue
        low = text.lower()
        if any(x in low for x in ["clipcreator", "premiere pro", "capcut", "наш сервис"]):
            skipped += 1
            continue
        counters[domain] += 1
        prefix = {"hooks": "h", "editing": "e", "storytelling": "s", "platforms": "p"}[domain]
        rid = f"{prefix}{counters[domain]:02d}"
        tags = list(dict.fromkeys(list(r.get("tags") or []) + [SOURCE_TAG]))[:7]
        packs[domain]["rules"].append({"id": rid, "text": text, "tags": tags})
        packs[domain]["version"] = int(packs[domain].get("version") or 1) + 1
        existing_norm.add(n)
        added[domain] += 1

    for domain, data in packs.items():
        path = BRAIN_DIR / f"{domain}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(domain, "total", len(data["rules"]), "added", added[domain], "v", data["version"])
    print("skipped", skipped)
    print("extract", extract_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
