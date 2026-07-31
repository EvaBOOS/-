"""
Smoke checks for Phase B/C (API-level, no long YouTube downloads).

Usage (API already running on :8000):
  .\\.venv\\Scripts\\python.exe scripts\\smoke_phase_bc.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")
API = f"{BASE}/api/v1"
EMAIL = os.environ.get("SMOKE_EMAIL", "client@example.com")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "client12345")


def _ok(name: str, detail: str = "") -> None:
    print(f"PASS  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str) -> None:
    print(f"FAIL  {name} — {detail}")
    raise SystemExit(1)


def main() -> None:
    results: list[str] = []
    with httpx.Client(timeout=30.0) as client:
        # 1) health
        r = client.get(f"{BASE}/health")
        if r.status_code != 200:
            _fail("health", f"status={r.status_code} body={r.text[:200]}")
        _ok("health", r.json() if r.headers.get("content-type", "").startswith("application/json") else "200")

        # 2) login
        r = client.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code != 200:
            _fail("login", f"status={r.status_code} body={r.text[:300]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            _fail("login", f"no access_token in {data}")
        _ok("login", EMAIL)
        headers = {"Authorization": f"Bearer {token}"}

        # 3) credits + watermark policy
        r = client.get(f"{API}/client/credits", headers=headers)
        if r.status_code != 200:
            _fail("credits", r.text[:300])
        credits = r.json()
        plan = credits.get("subscription_plan")
        wp = credits.get("watermark_policy") or {}
        if not wp.get("watermark"):
            _fail("watermark_policy", f"missing in response: {credits}")
        _ok(
            "credits/watermark_policy",
            f"plan={plan} clean={wp.get('clean_export')} credits={credits.get('credits_remaining')}",
        )

        # 4) fonts catalog
        r = client.get(f"{API}/client/assets/fonts", headers=headers)
        if r.status_code != 200:
            _fail("fonts", r.text[:300])
        fonts = (r.json().get("fonts") or [])
        ready = [f for f in fonts if f.get("ready") is not False]
        if len(ready) < 1:
            _fail("fonts", "empty ready fonts")
        _ok("fonts", f"{len(ready)} ready")

        # 5) SSRF block on source_url
        r = client.post(
            f"{API}/client/viral-edit",
            headers=headers,
            data={
                "source_url": "http://127.0.0.1/secret.mp4",
                "language": "ru",
                "style": "beast",
                "format": "9:16",
            },
        )
        if r.status_code == 400 and ("внутренн" in r.text.lower() or "запрещ" in r.text.lower() or "внутрен" in r.text):
            _ok("ssrf_block", "localhost rejected")
        elif r.status_code == 400:
            _ok("ssrf_block", f"400: {r.json().get('detail')}")
        else:
            _fail("ssrf_block", f"expected 400, got {r.status_code}: {r.text[:200]}")

        # 6) require file or url
        r = client.post(
            f"{API}/client/viral-edit",
            headers=headers,
            data={"language": "ru", "style": "beast"},
        )
        if r.status_code != 400:
            _fail("viral_requires_source", f"expected 400, got {r.status_code}")
        _ok("viral_requires_source", str(r.json().get("detail"))[:80])

        r = client.post(
            f"{API}/client/clips",
            headers=headers,
            data={"language": "ru", "max_clips": "3"},
        )
        if r.status_code != 400:
            _fail("clips_requires_source", f"expected 400, got {r.status_code}")
        _ok("clips_requires_source", str(r.json().get("detail"))[:80])

        # 7) optional local viral if sample exists (short file, real pipeline)
        sample = os.environ.get(
            "SMOKE_VIDEO",
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "media",
                "generated",
                "qa_suite",
                "source_ru.mp4",
            ),
        )
        sample = os.path.abspath(sample)
        if os.path.isfile(sample) and os.environ.get("SMOKE_RUN_PIPELINE") == "1":
            with open(sample, "rb") as f:
                r = client.post(
                    f"{API}/client/viral-edit",
                    headers=headers,
                    data={
                        "language": "ru",
                        "style": "beast",
                        "format": "9:16",
                        "font_id": "",
                    },
                    files={"file": ("source_ru.mp4", f, "video/mp4")},
                    timeout=60.0,
                )
            if r.status_code not in (200, 201):
                _fail("viral_enqueue", f"{r.status_code} {r.text[:300]}")
            gen = r.json()
            _ok("viral_enqueue", f"id={gen.get('id')} status={gen.get('status')}")
            results.append(f"Watch generation {gen.get('id')} in UI / Мои видео")
        else:
            _ok(
                "viral_enqueue_skipped",
                "set SMOKE_RUN_PIPELINE=1 to enqueue real beast job on source_ru.mp4",
            )

    print("\nAll API smoke checks passed.")
    for line in results:
        print(f"→ {line}")
    print(
        "\nUI checklist:\n"
        "1) http://127.0.0.1:8000/dashboard — client@example.com / client12345\n"
        "2) Профиль — тариф + строка «Экспорт» (watermark policy)\n"
        "3) Вирусный монтаж — вставь публичную ссылку ИЛИ файл, стиль Beast\n"
        "4) После ready — Basic: VideoGen-знак; Premium: без платформенного\n"
        "5) AI-клипы — шрифт «из профиля» + файл/ссылка\n"
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print("FAIL  connect — API not running on", BASE)
        print("Start:  backend\\scripts\\run_api.ps1")
        sys.exit(1)
