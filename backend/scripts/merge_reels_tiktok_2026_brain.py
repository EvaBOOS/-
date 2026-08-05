import json
import re
from pathlib import Path

extract = json.loads(
    Path(r"C:\Users\79144\AppData\Local\Temp\reels-tiktok-2026-brain.json").read_text(
        encoding="utf-8"
    )
)
brain = Path(r"c:\ГетХаб\-\backend\app\data\brain")


def norm(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[«»\"']", "", t)
    return t


existing_norm = set()
packs = {}
for f in brain.glob("*.json"):
    data = json.loads(f.read_text(encoding="utf-8"))
    packs[data["domain"]] = data
    for r in data.get("rules") or []:
        existing_norm.add(norm(str(r.get("text") or "")))

counters = {}
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
new_rules = []
for r in extract.get("rules") or []:
    domain = r["domain"]
    if domain not in packs:
        skipped += 1
        continue
    text = r["text"].strip()
    if len(text) < 30 or len(text) > 240:
        skipped += 1
        continue
    n = norm(text)
    if n in existing_norm or any(n[:50] == e[:50] for e in existing_norm):
        skipped += 1
        continue
    low = text.lower()
    if any(x in low for x in ["clipcreator", "premiere", "capcut"]):
        skipped += 1
        continue
    counters[domain] += 1
    prefix = {"hooks": "h", "editing": "e", "storytelling": "s", "platforms": "p"}[domain]
    rid = f"{prefix}{counters[domain]:02d}"
    tags = list(dict.fromkeys(list(r.get("tags") or []) + ["reels_vs_tiktok_2026"]))[:7]
    item = {"id": rid, "text": text, "tags": tags}
    packs[domain]["rules"].append(item)
    packs[domain]["version"] = int(packs[domain].get("version") or 1) + 1
    existing_norm.add(n)
    added[domain] += 1
    new_rules.append({"domain": domain, **item})

for domain, data in packs.items():
    (brain / f"{domain}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(domain, "total", len(data["rules"]), "added", added[domain], "v", data["version"])
print("skipped", skipped)
out = Path(r"C:\Users\79144\AppData\Local\Temp\reels-tiktok-2026-added.json")
out.write_text(json.dumps(new_rules, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", out)
