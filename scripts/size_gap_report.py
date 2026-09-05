"""사이즈가 안 채워진 옷을 「못 채움」과 「볼 것」으로 가른다.

왜 나누나: 사람이 상품을 하나씩 열어 보며 고쳐 주는데, 매장이 애초에 실측을 안 적은 옷이
목록에 섞여 있으면 열어 봐야 헛수고다(사람 지시 2026-09-05: 「사이즈 없는 상품, dnsr 도식화
상품은 아예 못 채우는 걸로 따로 표시해 두고 내가 열어보고 알려줄 수 있는 것만 남겨라」).

판단 근거는 매번 다시 계산한다 — 손으로 적어 둔 목록은 재수집하면 썩는다.

  못 채움 · 매장이 안 적음   원문(설명·상세글·spec·그림에서 읽은 글) 어디에도 치수 낱말이 없다
  못 채움 · 도식형          그림 위에 「S 36.5 / M 39」 덩어리만 있고 라벨은 화살표로만 표시된다
  볼 것                    원문에 치수가 분명히 있는데 표로 못 만든 것

  → data/size_unavailable.csv   못 채움
  → data/_issues_existing_brands.csv  볼 것
"""
from __future__ import annotations

import collections
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

GARMENTS = {"tops", "outer", "bottoms", "dress", "skirt", "suiting"}
# 새로 넣은 브랜드는 아직 수집이 끝나지 않아 이 표에서 뺀다.
NEW = {"siyazu", "noirer", "mardi-mercredi", "margesherwood", "nick-nicole", "sinoon", "grove",
       "vunque", "loeuvre", "xlim", "stand-oil", "osoi", "koominseong", "nonnod", "miseki-seoul",
       "far-from-what", "aoiro", "junne", "haiq", "espionage", "roaringrad"}

HINT = re.compile(r"총장|총 ?기장|어깨|가슴|밑단|허리|밑위|암홀|허벅지|엉덩이|화장|"
                  r"shoulder|chest|sleeve|waist|thigh|\bhem\b|\brise\b|\bbust\b", re.I)
# 「S 36.5」처럼 사이즈 이름과 숫자만 있는 줄 — 도식 위에 찍힌 치수
DRAW = re.compile(r"^\s*(XS|S|M|L|XL|XXL|2XL|3XL|F|FREE|OS|\d{2,3})\s*(\d{1,3}(?:\.\d)?)\s*(?:cm)?\s*$", re.I)


def load_latest(path: Path) -> dict:
    latest: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        latest[d["product_no"]] = d
    return latest


def draw_runs(text: str) -> int:
    """도식 위 숫자 덩어리 수 — 이름+숫자만 있는 줄이 연달아 둘 이상이면 한 덩어리."""
    runs, cur = 0, 0
    for ln in text.splitlines():
        if DRAW.match(ln):
            cur += 1
        else:
            if cur >= 2:
                runs += 1
            cur = 0
    return runs + (1 if cur >= 2 else 0)


def main() -> None:
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((DATA / "products_full.csv").open(encoding="utf-8-sig")))
    by_key = {(r["brand_slug"], r["product_no"]): r for r in rows}

    ocr: dict = {}
    for f in glob.glob(str(DATA / "crawl" / "ocr" / "*.jsonl")):
        slug = os.path.basename(f)[:-6]
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            ocr[(slug, d["product_no"])] = d

    # 브랜드가 사이즈를 어디서 얻는지 — 「그림에서 글자가 안 나온다」는 말은
    # 그림을 보는 브랜드에만 뜻이 있다(사람 지적 2026-09-05: badblood 는 HTML 을 읽는다).
    src_of = collections.defaultdict(collections.Counter)
    for r in rows:
        e = sizes.get(r["source_url"]) or {}
        if e.get("sizes"):
            src_of[r["brand_slug"]][str(e.get("source", "?")).split("+")[0]] += 1

    gone, look = [], []
    for path in sorted(DATA.glob("crawl/*.jsonl")):
        slug = path.stem
        if slug.startswith("_") or slug in NEW:
            continue
        reads_html = src_of[slug].most_common(1)[0][0] == "html" if src_of[slug] else False
        for no, d in load_latest(path).items():
            r = by_key.get((slug, str(no)))
            if not r or r["category_code"] not in GARMENTS or r["status"] != "ON_SALE":
                continue
            if (sizes.get(r["source_url"]) or {}).get("sizes"):
                continue
            o = ocr.get((slug, no)) or {}
            t = o.get("ocr_text") or ""
            body = " ".join([d.get("description") or "", d.get("detail_text") or "", t,
                             " ".join(f"{k} {v}" for k, v in (d.get("spec") or {}).items())])
            row = {"브랜드": slug, "상품번호": no, "상품명": (d.get("name") or "")[:70],
                   "분류": r["category_code"], "링크": r["source_url"]}
            if HINT.search(body) or d.get("size_table"):
                row["왜"] = "원문에 치수가 있는데 표로 못 만듦"
                look.append(row)
            elif draw_runs(t) >= 2:
                row["왜"] = "도식형 — 그림 위 숫자만 있고 라벨이 화살표뿐"
                gone.append(row)
            else:
                row["왜"] = ("매장이 실측을 안 적음(설명글에 없음)" if reads_html
                             else "매장이 실측을 안 적음(글·그림 어디에도 없음)")
                gone.append(row)

    for name, data, keys in (("size_unavailable.csv", gone, ["왜", "브랜드", "상품명", "분류", "링크", "상품번호"]),
                             ("_issues_existing_brands.csv", look, ["왜", "브랜드", "상품명", "분류", "링크", "상품번호"])):
        data.sort(key=lambda x: (x["왜"], x["브랜드"], x["상품명"]))
        p = DATA / name
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(data)
        c = collections.Counter(x["왜"] for x in data)
        print(f"{len(data)}건 → {p}")
        for k, v in c.most_common():
            b = collections.Counter(x["브랜드"] for x in data if x["왜"] == k)
            print(f"   {k:36} {v:4}  {', '.join(f'{s} {n}' for s, n in b.most_common(5))}")


if __name__ == "__main__":
    main()
