"""아직 안 채워진 칸을 상품 단위로 모은다 — 사이즈·소재·색상·품목.

한 상품이 여러 칸을 비웠으면 한 줄에 모아 적는다. 사람이 링크를 하나만 열어도
빠진 것이 다 보이게 하려는 것이다(사람 요청 2026-09-05).

「채울 수 있음 / 못 채움」을 함께 적는다 — 매장이 애초에 안 적은 것을 열어 보는 건
헛수고다. 판단 근거는 매번 다시 계산한다(size_gap_report.py 와 같은 잣대).

  → data/_missing.csv           전부
  → data/_missing_existing.csv  기존 브랜드만
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
NEW = {"siyazu", "noirer", "mardi-mercredi", "margesherwood", "nick-nicole", "sinoon", "grove",
       "vunque", "loeuvre", "xlim", "stand-oil", "osoi", "koominseong", "nonnod", "miseki-seoul",
       "far-from-what", "aoiro", "junne", "haiq", "espionage", "roaringrad"}

# 낱말만 보면 안 된다 — 「어깨의 절개 라인」·「여유 있는 밑위」처럼 설명 문장에도 나온다.
# 옆에 숫자가 붙어야 실측이다(2026-09-05: 그 탓에 22벌을 「읽어야 함」으로 잘못 세었다).
_LAB = (r"총장|총 ?기장|어깨|가슴|밑단|허리|밑위|암홀|허벅지|엉덩이|화장|소매|"
        r"shoulder|chest|sleeve|waist|thigh|hem|rise|bust|length")
SIZE_HINT = re.compile(rf"(?:{_LAB})[^0-9가-힣A-Za-z]{{0,12}}\d{{1,3}}(?:[.,]\d)?"
                       rf"|\d{{1,3}}(?:[.,]\d)?\s*(?:cm|CM)?[^0-9가-힣A-Za-z]{{0,4}}(?:{_LAB})", re.I)
# 「소재」라는 낱말만으로는 안 된다 — 「소재, 컬러, 그래픽을 직접 개발」 같은 문장이 걸린다.
# 실제 섬유 이름이나 혼용률(%)이 있어야 한다.
MAT_HINT = re.compile(r"코튼|cotton|폴리에스터|polyester|나일론|nylon|린넨|linen|리넨|레이온|rayon|"
                      r"비스코스|viscose|아크릴|acrylic|스판덱스|spandex|캐시미어|cashmere|실크|silk|"
                      r"가죽|leather|텐셀|tencel|리오셀|lyocell|모달|modal|알파카|alpaca|모헤어|mohair|"
                      r"트위드|tweed|벨벳|velvet|코듀로이|corduroy|스웨이드|suede|양모|울\s*\d|wool\s*\d|"
                      r"써지컬|스테인리스|stainless|브라스|brass|황동|실버\s*925|sterling|진주|pearl|"
                      r"원석|큐빅|zirconia|금도금|gold\s*plat|"
                      r"(?:혼용률|composition)|\d{1,3}\s*%", re.I)
COLOR_HINT = re.compile(r"(?:^|[\n\r•▪|·\-\s])(?:colou?r|색상|컬러)\s*[:：]", re.I)
# 「S 36.5」처럼 이름과 숫자만 있는 줄 — 도식 위에 찍힌 치수라 라벨을 복원할 수 없다
SKIP_IMG = re.compile(r"shipping|delivery|notice|issue|banner|event|coupon|logo|icon|배송|공지", re.I)


def readable_images(d: dict, ocr_rec: dict | None) -> int:
    """실제로 읽을 수 있는 상세 그림 수.

    주소만 세면 안 된다 — badblood 13벌은 53×16px 아이콘 하나뿐인데 「그림 있음」으로
    세어 재판독 대상이 됐다(2026-09-05). OCR 을 이미 돌린 적이 있으면 그때 실제로 읽은
    장수가 답이다(너무 작거나 못 받은 그림은 그 목록에서 빠져 있다).
    """
    if ocr_rec is not None:
        return len(ocr_rec.get("images") or [])
    return len([u for u in (d.get("detail_images") or []) if not SKIP_IMG.search(u)])
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
    runs = cur = 0
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
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
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

    out = []
    for path in sorted(DATA.glob("crawl/*.jsonl")):
        slug = path.stem
        if slug.startswith("_"):
            continue
        for no, d in load_latest(path).items():
            r = by_key.get((slug, str(no)))
            if not r or r["status"] != "ON_SALE":
                continue
            ocr_rec = ocr.get((slug, no))
            t = (ocr_rec or {}).get("ocr_text") or ""
            body = " ".join([d.get("description") or "", d.get("detail_text") or "", t,
                             " ".join(f"{k} {v}" for k, v in (d.get("spec") or {}).items())])
            opts = " / ".join(str(o) for o in (d.get("options") or []))
            gap, why = [], []
            if r["category_code"] in GARMENTS and not (sizes.get(r["source_url"]) or {}).get("sizes"):
                gap.append("사이즈")
                if SIZE_HINT.search(body) or d.get("size_table"):
                    why.append("사이즈: 원문에 있음 — 읽어야 함")
                elif draw_runs(t) >= 2:
                    why.append("사이즈: 도식형 — 못 채움")
                elif readable_images(d, ocr_rec):
                    # 그림이 있는데 읽은 글에 치수가 없다 — 아직 모른다(재판독으로 갈린다)
                    why.append("사이즈: 그림 재판독 대상")
                else:
                    why.append("사이즈: 매장이 안 적음 — 못 채움")
            if not ((tags.get(r["source_url"]) or {}).get("tags") or {}).get("material"):
                gap.append("소재")
                why.append("소재: 원문에 있음 — 읽어야 함" if MAT_HINT.search(body)
                           else "소재: 매장이 안 적음 — 못 채움")
            if not (r["representative_color"] or "").strip():
                gap.append("색상")
                why.append("색상: 원문에 있음 — 읽어야 함"
                           if (COLOR_HINT.search(body) or COLOR_HINT.search(opts))
                           else "색상: 매장이 안 적음 — 못 채움")
            if not (r["subtype"] or "").strip():
                gap.append("품목")
                why.append("품목: 이름으로 못 가림")
            if not gap:
                continue
            out.append({
                "브랜드": slug, "빠진 칸": "·".join(gap), "칸 수": len(gap),
                "채울 수 있나": ("예" if any("읽어야 함" in w for w in why)
                          else ("아직 모름" if any("재판독" in w for w in why) else "아니오")),
                "왜": " | ".join(why),
                "분류": r["category_code"], "품목": r["subtype"],
                "상품명": (d.get("name") or "")[:70], "링크": r["source_url"], "상품번호": no,
            })

    keys = ["브랜드", "빠진 칸", "칸 수", "채울 수 있나", "왜", "분류", "품목", "상품명", "링크", "상품번호"]
    for name, data in (("_missing.csv", out),
                       ("_missing_existing.csv", [x for x in out if x["브랜드"] not in NEW])):
        data = sorted(data, key=lambda x: (-x["칸 수"], x["채울 수 있나"], x["브랜드"], x["상품명"]))
        p = DATA / name
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(data)
        c = collections.Counter(x["빠진 칸"] for x in data)
        can = sum(1 for x in data if x["채울 수 있나"] == "예")
        print(f"{len(data)}벌 → {p}   (채울 수 있음 {can} · 못 채움 {len(data) - can})")
        for k, v in c.most_common(8):
            print(f"   {k:16} {v}")


if __name__ == "__main__":
    main()
