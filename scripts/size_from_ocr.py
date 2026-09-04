"""OCR 글에서 사이즈 표를 뽑아 정식 라벨로 정리한다 → data/product_sizes.json

왜: 사이즈 표가 이미지로만 있는 매장(9999archive·rssc·dnsr·frizmworks·easy-no-easy·blayer 등)은 crawl 의
size_table 이 비어 있다(전체 54%). OCR 글 1,039건에 「총장 66 어깨 64」가 이미 읽혀 있어 표만 뽑으면 채워진다.

표는 두 모양이다(2026-09-03 표본):
  A. 라벨 한 줄씩:  「Shoulder (어깨) 39cm 42cm 50cm」 → crawl 의 SIZE_RX 와 같은 방식
  B. 행렬:          「(00) 총장 허리 엉덩이 앞밑위 허벅지 밑단」 다음 줄부터 「001 101 355 52 31 31 255」
     — 헤더 줄에 정식 라벨이 둘 이상이면 헤더로 보고, 다음 줄들에서 사이즈 이름 + 숫자 N개(N=라벨 수)를 읽는다.
OCR 이 소수점을 떨어뜨린다(35.5 → 355, 77.5 → 775). 세 자리가 라벨 범위(size_labels.json _ranges_cm)를 넘으면 /10.

결과는 crawl 의 {라벨: [값…]}(HTML 표) 와 OCR 표를 한 모양으로 합친 것:
  product_sizes.json: source_url → {"brand_slug", "source": "html"|"ocr", "size_names": [...]|null,
                                    "sizes": {정식라벨: [값…]}}  (값 순서 = size_names 순서)
사용: py scripts/size_from_ocr.py [--report]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"
OCR = CRAWL / "ocr"
OUT = DATA / "product_sizes.json"

LABELS = json.loads((DATA / "size_labels.json").read_text(encoding="utf-8"))
RANGES = LABELS["_ranges_cm"]
ALIAS: dict[str, str] = {}
for canon, als in LABELS.items():
    if canon.startswith("_"):
        continue
    for a in [canon] + als:
        ALIAS[re.sub(r"\s+", "", a).lower()] = canon
ALIAS_SORTED = sorted(ALIAS, key=len, reverse=True)
LABEL_RX = re.compile("|".join(re.escape(a) for a in ALIAS_SORTED), re.I)
NUM = r"\d{1,3}(?:[.,]\d)?"
# 행렬 표의 칸에는 다섯 자리까지 받는다 — OCR 이 「104.0cm」를 「10400」으로 흘려 쓴다(easy-no-easy).
# 머리에 정식 라벨이 둘 이상 있고 칸 수가 정확히 맞을 때만 쓰이는 자리라, 값 대신 가격이 끼어들 여지가 없다.
NUM_CELL = r"\d{1,5}(?:[.,]\d)?"
# 사이즈 이름은 느슨하게 — OCR 이 「002」를 「OOM」으로 읽는다(kirsh). 헤더(정식 라벨 ≥2)와 숫자 개수 일치가 지킨다
SIZE_NAME = r"(?:xxs|xs|s|m|l|xl|xxl|2xl|3xl|free|f|one\s*size|os|[A-Za-z0-9]{1,4})"


def canon_label(s: str) -> str | None:
    key = re.sub(r"[\s()（）:：]", "", s).lower()
    if key in ALIAS:
        return ALIAS[key]
    m = LABEL_RX.search(s)
    return ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower()) if m else None


def fix_value(label: str, raw: str, girth: bool = False) -> float | None:
    v = float(raw.replace(",", "."))
    if girth:
        v = v / 2   # 「가슴둘레 111」은 둘레 — 국내 표기 기준(단면)으로 맞춘다(rough-side, 2026-09-04)
    lo, hi = RANGES.get(label, (3, 200))
    # 소수점이 떨어진 숫자(355 → 35.5, 3500 → 35.0, 10400 → 104.0) — 범위에 들어올 때까지 10 으로 나눈다.
    # 「3225」처럼 어느 자리로도 안 맞는 것은 범위 밖으로 버려진다.
    if raw.isdigit():
        for _ in range(3):
            if v <= hi:
                break
            v = v / 10
    return v if lo <= v <= hi else None


def parse_matrix(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """헤더 줄(정식 라벨 ≥2) + 사이즈 행들. 가장 많은 행을 얻는 헤더를 고른다."""
    best = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if not re.search(r"size|사이즈|\(00\)|\(07\)|cm", low) and len(LABEL_RX.findall(ln)) < 2:
            continue
        # 헤더에서 라벨을 순서대로
        labels = []
        for m in LABEL_RX.finditer(re.sub(r"[|ㅣ]", " ", ln)):
            c = ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower())
            if c and c not in labels:
                labels.append(c)
        if len(labels) < 2:
            continue
        names, cols = [], {c: [] for c in labels}
        for row in lines[i + 1:i + 12]:
            # 세로선(|)은 칸 구분(frizmworks). 콜론·세미콜론은 OCR 이 세로선이나 점을 잘못 읽은 것이다
            # — 「M 49 55 58: 60」(dnsr, 2026-09-04) 처럼 한 글자 때문에 표 한 장을 통째로 버리고 있었다.
            r = re.sub(r"[|ㅣ:;=_]", " ", row).strip()
            # OCR 이 cm 을 em·cem·c¢m·om 으로 흘려 쓴다(easy-no-easy) — 숫자 뒤에 붙은 것만 지운다
            r = re.sub(r"(?<=\d)\s*(?:cm|cem|c[^\w\s]m|em|om|¢m|crn)\b", " ", r, flags=re.I)
            r = re.sub(r"\s+", " ", r)
            # 사이즈 이름: 「1」「M」뿐 아니라 「1 SIZE」「1 SIZE [9]」(easy-no-easy) 도 한 칸이다.
            # 숫자 뒤에 남는 부스러기(「Th (cm)」 — kirsh)는 버린다.
            m = re.match(rf"^\(?({SIZE_NAME})\)?(?:\s*size)?(?:\s*[\[(][^\])]{{0,8}}[\])])?\s+"
                         rf"((?:{NUM_CELL}\s*){{{len(labels)},{len(labels)+1}}})\s*(?:\D{{0,10}})?$", r, re.I)
            if m:
                nums = re.findall(NUM_CELL, m.group(2))[:len(labels)]
                nm = m.group(1)
            else:
                # 빈 칸을 「-」로 두는 표(민소매의 SLEEVE — dnsr) 는 칸 수가 맞을 때만 받는다
                tok = r.split()
                nm, cells = (tok[0], tok[1:]) if tok else ("", [])
                if len(cells) != len(labels) or not re.fullmatch(SIZE_NAME, nm, re.I) \
                        or not all(re.fullmatch(rf"{NUM_CELL}|[-–—]", c) for c in cells) \
                        or not any(re.fullmatch(NUM_CELL, c) for c in cells):
                    if names:      # 표가 끝났다
                        break
                    continue
                nums = cells
            if len(nums) < len(labels):
                continue
            nm = nm.upper()
            if re.fullmatch(r"[0O]{2}[0-9OM]", nm):
                nm = nm.replace("O", "0").replace("M", "2")   # 「OOM」= 002 (kirsh 표기)
            names.append(nm)
            for c, raw in zip(labels, nums):
                cols[c].append(None if raw in ("-", "–", "—") else fix_value(c, raw))
        if names and (best is None or len(names) > len(best[0])):
            best = (names, cols)
    if not best:
        return None
    names, cols = best
    sizes = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    return names, sizes


def parse_rows(text: str) -> dict[str, list[float]]:
    """A형 — 라벨 뒤에 숫자 묶음. crawl SIZE_RX 와 같은 발상."""
    out: dict[str, list[float]] = {}
    t = re.sub(r"[ \t]+", " ", text)
    for m in re.finditer(rf"({LABEL_RX.pattern})\s*(?:\([^)]{{0,24}}\))?\s*[:：]?\s*((?:{NUM}\s*(?:cm)?\s*[/,|]?\s*){{1,8}})", t, re.I):
        c = canon_label(m.group(1))
        if not c or c in out:
            continue
        vals = [fix_value(c, x) for x in re.findall(NUM, m.group(2))]
        vals = [v for v in vals if v is not None]
        if vals:
            out[c] = vals[:8]
    return out


def from_ocr(text: str) -> tuple[list[str] | None, dict[str, list[float]]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    mat = parse_matrix(lines)
    if mat and len(mat[1]) >= 2:
        return mat[0], {k: [v for v in vs] for k, vs in mat[1].items()}
    rows = parse_rows(text)
    return None, rows


def sweep_all(st: dict) -> bool:
    """표 전체가 못 쓰는 경우 — 라벨이 한둘뿐인데 값이 일곱 개 넘게 붙어 있다(매장 공용 안내표거나
    한 라벨이 표를 통째로 쓸어담은 것). kamien 「총장 [41, 37.5, 31, 39, 51, 109]」이 그 예다."""
    return len(st) <= 2 and any(isinstance(v, list) and len(v) > 6 for v in st.values())


def bad_label(xs: list) -> bool:
    """사이즈 값은 한 방향으로만 간다(S→M→L). 오르락내리락하면 그 라벨이 두 칸을 섞어 읽은 것이다 —
    glowny 「length [26, 30, 27, 31]」(앞기장·뒤기장), insilence 「밑단단면 [22.7, 5.0, 23.2, 5.0]」
    (22.75 가 쪼개짐). 표 전체가 아니라 그 라벨만 버린다 — 나머지 라벨은 멀쩡하다(2026-09-04).
    """
    nums = [x for x in xs if isinstance(x, (int, float))]
    if len(nums) < 4:
        return False
    return not (all(a <= b for a, b in zip(nums, nums[1:])) or all(a >= b for a, b in zip(nums, nums[1:])))


def normalize_html(st: dict, brand: str = "", girth_keys: set | None = None) -> dict[str, list[float]]:
    if sweep_all(st):
        return {}
    out: dict[str, list[float]] = {}
    for k, vals in st.items():
        c = canon_label(k)
        if not c or c in out:
            continue
        if bad_label(vals if isinstance(vals, list) else []):
            continue
        girth = "둘레" in k or "circum" in k.lower() or (girth_keys is not None and (brand, c) in girth_keys)
        vs = [v for v in (fix_value(c, str(x), girth) for x in vals) if v is not None]
        if vs:
            out[c] = vs
    return out


# 라벨별 「둘레로 보이는」 문턱 — 이 위로 브랜드 중앙값이 오면 그 브랜드는 둘레로 재는 것이다.
# 라벨에 「둘레」라고 써 주는 매장만 반으로 나누고 있었더니, 영어 라벨을 쓰는 moif 는 chest 132·137·142 가
# 범위 밖이라 통째로 버려졌다(2026-09-04 사람 지적: 「단면 쓰는 곳도 전체 쓰는 곳도 있다」).
GIRTH_MIN = {"가슴": 75, "허리": 55, "엉덩이": 70, "밑단": 60, "허벅지": 45, "어깨": 75}


def brand_girth(crawl_dir) -> set[tuple[str, str]]:
    """브랜드×라벨 중 둘레로 재는 것을 값 분포로 가려낸다."""
    import statistics
    vals = defaultdict(list)
    for p in sorted(crawl_dir.glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            st = d.get("size_table")
            if not isinstance(st, dict):
                continue
            for k, xs in st.items():
                c = canon_label(k)
                if not c or c not in GIRTH_MIN or "둘레" in k:
                    continue
                for x in xs:
                    if isinstance(x, (int, float)):
                        vals[(d["brand_slug"], c)].append(float(x))
    out = set()
    for key, xs in vals.items():
        if len(xs) >= 20 and statistics.median(xs) >= GIRTH_MIN[key[1]]:
            out.add(key)
    return out


def shop_wide_tables(crawl_dir, rows: dict) -> set[tuple[str, str]]:
    """매장 공용 안내표를 가려낸다 — 키링·베레모·청바지·티셔츠가 전부 같은 값을 갖는 것
    (wkndrs·espionage·noice, 가슴 [22,23,…,32], 753벌).
    「같은 표가 여러 벌」만으로는 안 된다 — 색만 다른 같은 옷이 스무 벌인 브랜드가 흔해서 dunst·insilence 까지
    잘렸다(2026-09-04 1차 시도, 1,456벌 손실). 품목 서넛에 걸쳐 같은 표가 나올 때만 공용표로 본다."""
    seen = defaultdict(lambda: [0, set()])
    for p in sorted(crawl_dir.glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            st = d.get("size_table")
            if not (isinstance(st, dict) and st):
                continue
            r = rows.get((d["brand_slug"], str(d["product_no"])))
            key = (d["brand_slug"], json.dumps(st, sort_keys=True, ensure_ascii=False))
            seen[key][0] += 1
            if r and r.get("category"):
                seen[key][1].add(r["category"])
    return {k for k, (n, cats) in seen.items() if n >= 10 and len(cats) >= 3}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    rows = {(r["brand_slug"], r["product_no"]): r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    ocr: dict[tuple, str] = {}
    for p in OCR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                d = json.loads(l)
                ocr[(d["brand_slug"], str(d["product_no"]))] = d.get("ocr_text") or ""
    girth_keys = brand_girth(CRAWL)
    shared = shop_wide_tables(CRAWL, rows)
    if girth_keys:
        print("둘레로 재는 브랜드×라벨:", sorted(f"{b}/{l}" for b, l in girth_keys))
    if shared:
        print("매장 공용 표로 판단해 버림:", sorted({b for b, _ in shared}))
    out: dict[str, dict] = {}
    src = Counter()
    per_brand_html, per_brand_ocr, per_brand_tot = Counter(), Counter(), Counter()
    for p in sorted(CRAWL.glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            k = (d["brand_slug"], str(d["product_no"]))
            r = rows.get(k)
            if not r:
                continue
            per_brand_tot[k[0]] += 1
            st = d.get("size_table")
            sizes, names, source = {}, None, None
            if isinstance(st, dict) and st and (k[0], json.dumps(st, sort_keys=True, ensure_ascii=False)) not in shared:
                sizes = normalize_html(st, k[0], girth_keys)
                source = "html"
            if len(sizes) < 2 and ocr.get(k):
                names2, sizes2 = from_ocr(ocr[k])
                if len(sizes2) > len(sizes):
                    sizes, names, source = sizes2, names2, "ocr"
            if not sizes:
                continue
            # 사이즈 개수가 라벨마다 다르면(OCR 누락) 가장 짧은 길이로 맞춘다
            n = min(len(v) for v in sizes.values())
            sizes = {c: v[:n] for c, v in sizes.items()}
            if names:
                names = names[:n]
            out[r["source_url"]] = {"brand_slug": k[0], "source": source, "size_names": names, "sizes": sizes}
            src[source] += 1
            (per_brand_html if source == "html" else per_brand_ocr)[k[0]] += 1
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"사이즈 있는 상품 {len(out)} / {len(rows)} ({len(out)/len(rows):.0%}) — html {src['html']} · ocr {src['ocr']} → {OUT}")
    lab = Counter(c for e in out.values() for c in e["sizes"])
    print("정식 라벨 분포:", dict(lab.most_common()))
    if args.report:
        print("\n브랜드별 (전체 / html / ocr):")
        for b in sorted(per_brand_tot, key=lambda b: -(per_brand_ocr[b])):
            print(f"  {b:20s} {per_brand_tot[b]:5d} {per_brand_html[b]:5d} {per_brand_ocr[b]:5d}")


if __name__ == "__main__":
    main()
