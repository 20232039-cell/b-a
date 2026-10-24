"""브랜드마다 무엇을 어디서 얻었나 — 데이터 저장소 data/reports/brand_collection.md 를 만든다(비공개 — 이 저장소는 공개라 결과를 여기 두지 않는다).

차례(docs/PIPELINE.md 2절)는 같고, 매장마다 어느 칸에서 멈추는지가 다르다. 그 결과를 판매중 옷 기준으로 센다.
    python scripts/brand_collection_report.py
"""
from __future__ import annotations

import csv
import glob
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import platforms as P

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "reports" / "brand_collection.md"
APPAREL = {"tops", "bottoms", "outer", "dress", "skirt", "suiting"}
# 사이즈 출처 → PIPELINE.md 2절 차례
SIZE_STEP = [("html", "① HTML"), ("browser", "②' 브라우저"), ("sizeguide", "③ 사이즈가이드"), ("cremafit", "③ 크리마"),
             ("ocr", "④ 그림 OCR"), ("llm", "④' 그림 LLM"), ("manual", "⑤ 수기"), ("sibling", "⑥ 형제")]
STEP_NAME = dict(SIZE_STEP)


def platform(slug: str) -> str:
    if slug in P.SHOPIFY:
        return "Shopify"
    if slug in P.SIXSHOP:
        return "식스샵"
    if slug in P.PAGES:
        return "개별 해석기"
    return "카페24"


def pct(a: int, b: int) -> str:
    return f"{round(100 * a / b)}%" if b else "-"


def main() -> None:
    seed = {r["slug"]: r for r in csv.DictReader(open(DATA / "brands_seed.csv", encoding="utf-8-sig"))}
    closed = {r["브랜드"] for r in csv.DictReader(open(DATA / "dead" / "store_closed.csv", encoding="utf-8-sig"))}
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    desc_len: dict[str, int] = {}
    for f in glob.glob(str(DATA / "crawl" / "*.jsonl")):
        if Path(f).name.startswith("_"):
            continue
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            desc_len[d.get("source_url") or ""] = len((d.get("description") or "").strip())

    st: dict[str, Counter] = defaultdict(Counter)
    for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")):
        if r.get("status") != "ON_SALE" or r.get("category_code") not in APPAREL:
            continue
        b, u = r["brand_slug"], r["source_url"]
        c = st[b]
        c["n"] += 1
        c["size:" + ((sizes.get(u) or {}).get("source") or "none")] += 1
        t = tags.get(u) or {}
        if t.get("mat"):
            c["mat"] += 1
        if desc_len.get(u, 0) >= 50:
            c["desc_html"] += 1
        elif "ocr" in (t.get("text_sources") or []):
            c["desc_ocr"] += 1

    rows = []
    for b, c in st.items():
        n = c["n"]
        got = [(k, c["size:" + k]) for k, _ in SIZE_STEP if c["size:" + k]]
        main_step = STEP_NAME[max(got, key=lambda x: x[1])[0]] if got else "없음"
        mix = " · ".join(f"{STEP_NAME[k]} {pct(v, n)}" for k, v in sorted(got, key=lambda x: -x[1]))
        state = "닫힘" if b in closed else "보류" if b in P.NEW_HOLD else "앱" if b in seed else "씨앗 밖"
        rows.append((b, platform(b), state, n, main_step, mix, pct(c["size:none"], n),
                     pct(c["desc_html"], n), pct(c["desc_ocr"], n), pct(c["mat"], n)))
    rows.sort(key=lambda x: (x[1], x[0]))

    by_main = Counter(r[4] for r in rows)
    by_plat = Counter(r[1] for r in rows)
    lines = [
        "# 브랜드별 수집 방법",
        "",
        f"`scripts/brand_collection_report.py` 가 만든다({date.today().isoformat()}, 판매중 옷 기준). 손으로 고치지 않는다.",
        "차례는 b-a docs/PIPELINE.md 2절 — 앞 칸에서 얻으면 뒤 칸은 안 본다. 이 표는 매장마다 어느 칸에서 얻었는지다.",
        "",
        "- **틀** — 카페24 · Shopify · 식스샵은 틀 수집기, 개별 해석기는 `scripts/stores/<slug>.py`.",
        "- **사이즈 주 출처** — 가장 많은 상품이 얻은 칸. 비율은 판매중 옷 중 그 칸에서 얻은 몫.",
        "- **설명 HTML / 그림** — 상세 글이 50자 이상인 몫 / 글이 없어 상세 그림 글자(OCR)를 쓴 몫.",
        "",
        "## 요약",
        "",
        "| 틀 | 브랜드 |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in by_plat.most_common()],
        "",
        "| 사이즈 주 출처 | 브랜드 |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in by_main.most_common()],
        "",
        "## 브랜드",
        "",
        "| 브랜드 | 틀 | 상태 | 옷 | 사이즈 주 출처 | 사이즈 출처 비율 | 사이즈 없음 | 설명 HTML | 설명 그림 | 혼용률 |",
        "|---|---|---|---:|---|---|---:|---:|---:|---:|",
        *[f"| {' | '.join(str(x) for x in r)} |" for r in rows],
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT} — 브랜드 {len(rows)}")


if __name__ == "__main__":
    main()
