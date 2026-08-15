"""Enqueue beast viral + poll until watermark lands in api_responses."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
SAMPLE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "media", "generated", "qa_suite", "source_ru.mp4")
)


def main() -> None:
    with httpx.Client(timeout=60.0) as client:
        token = client.post(
            f"{BASE}/auth/login",
            json={"email": "client@example.com", "password": "client12345"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        with open(SAMPLE, "rb") as f:
            resp = client.post(
                f"{BASE}/client/viral-edit",
                headers=headers,
                data={"language": "ru", "style": "beast", "format": "9:16"},
                files={"file": ("source_ru.mp4", f, "video/mp4")},
            )
        print("enqueue", resp.status_code)
        resp.raise_for_status()
        gid = resp.json()["id"]
        print("id", gid)

    for i in range(30):
        con = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "videogen.db"))
        row = con.execute(
            "select status, progress_percent, api_responses, error_message "
            "from video_generations where id=?",
            (gid,),
        ).fetchone()
        con.close()
        ar = json.loads(row[2] or "{}")
        print(
            f"{i * 12:3d}s status={row[0]} progress={row[1]} "
            f"wm={ar.get('watermark')} virality={bool(ar.get('virality'))} "
            f"keys={len(ar)} err={row[3]}"
        )
        if str(row[0]).lower() in {"completed", "failed"}:
            break
        time.sleep(12)


if __name__ == "__main__":
    main()
