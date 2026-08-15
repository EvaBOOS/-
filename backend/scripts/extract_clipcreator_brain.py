"""Batch-extract VideoGen Brain rules from ClipCreator article HTML via AITUNNEL."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ART_DIR = Path(os.environ.get("CC_ART_DIR", r"C:\Users\79144\AppData\Local\Temp\cc-articles"))
BRAIN = Path(r"c:\ГетХаб\-\backend\app\data\brain")
OUT = Path(os.environ.get("CC_OUT", r"C:\Users\79144\AppData\Local\Temp\cc-brain-extract.json"))

# load .env without printing secrets
for env_path in [
    Path(r"c:\ГетХаб\-\backend\.env"),
    Path(r"c:\ГетХаб\-\docker\.env"),
]:
    if not env_path.is_file():
        continue
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = (os.environ.get("AITUNNEL_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
BASE = (os.environ.get("AITUNNEL_BASE_URL") or "https://api.openai.com/v1/").rstrip("/")
MODEL = os.environ.get("AITUNNEL_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
if not API_KEY:
    print("NO_API_KEY", file=sys.stderr)
    sys.exit(1)


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    html = re.sub(r"(?is)<nav[^>]*>.*?</nav>", " ", html)
    html = re.sub(r"(?is)<footer[^>]*>.*?</footer>", " ", html)
    html = re.sub(r"(?is)<[^>]+>", "\n", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&quot;", '"', html)
    html = re.sub(r"&#x27;|&#39;", "'", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    return html.strip()


def chat(system: str, user: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 2200,
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


SYSTEM = """Ты извлекаешь экспертные правила для VideoGen Brain (монтаж short-form, хуки, сценарии, платформы).
Верни ONLY JSON:
{"rules":[{"domain":"hooks|editing|storytelling|platforms","text":"правило на русском своими словами","tags":["reels","tiktok","shorts","captions",...]}]}
Правила:
- только actionable принципы, не маркетинг сервиса ClipCreator
- не копируй длинные цитаты дословно
- 4–10 правил на пачку статей
- domain строго один из четырёх
- коротко (1 предложение)
"""


def extract_batch(items: list[tuple[str, str]]) -> list[dict]:
    parts = []
    for slug, text in items:
        parts.append(f"### {slug}\n{text[:4500]}")
    user = "Статьи:\n\n" + "\n\n".join(parts)
    content = chat(SYSTEM, user)
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    data = json.loads(content)
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        return []
    out = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        domain = str(r.get("domain") or "").strip().lower()
        text = str(r.get("text") or "").strip()
        if domain not in {"hooks", "editing", "storytelling", "platforms"} or not text:
            continue
        tags = r.get("tags") if isinstance(r.get("tags"), list) else []
        out.append(
            {
                "domain": domain,
                "text": text,
                "tags": [str(t).strip().lower() for t in tags if t][:6],
            }
        )
    return out


def main() -> None:
    files = sorted(ART_DIR.glob("*.html"))
    print(f"articles={len(files)} model={MODEL}")
    pairs: list[tuple[str, str]] = []
    for f in files:
        text = html_to_text(f.read_text(encoding="utf-8", errors="ignore"))
        # drop boilerplate tails
        text = text[:12000]
        if len(text) < 800:
            continue
        pairs.append((f.stem, text))
    print(f"usable={len(pairs)}")

    all_rules: list[dict] = []
    batch_size = 4
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i : i + batch_size]
        print(f"batch {i // batch_size + 1}/{(len(pairs) + batch_size - 1) // batch_size}: {[b[0] for b in batch]}")
        try:
            got = extract_batch(batch)
            print(f"  -> {len(got)} rules")
            all_rules.extend(got)
        except Exception as exc:
            print(f"  FAIL {type(exc).__name__}: {exc}")
        time.sleep(1.2)

    # dedupe by normalized text
    seen = set()
    uniq = []
    for r in all_rules:
        key = re.sub(r"\s+", " ", r["text"].lower()).strip()
        if key in seen or len(key) < 25:
            continue
        seen.add(key)
        uniq.append(r)

    OUT.write_text(json.dumps({"rules": uniq}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(uniq)} -> {OUT}")


if __name__ == "__main__":
    main()
