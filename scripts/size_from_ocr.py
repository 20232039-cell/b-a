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
BROWSER = CRAWL / "browser"
OUT = DATA / "product_sizes.json"

NON_APPAREL_CODES = {"shoes", "bags", "accessories", "headwear", "jewelry", "lifestyle", "pet"}
# 옷에만 있는 실측 항목 — 잡화 행에 이게 있으면 표를 잘못 물어 온 것이다
GARMENT_ONLY = {"어깨", "가슴", "밑위", "허벅지", "암홀", "화장", "소매길이"}
GARMENT_LABELS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim", "Skirts", "Dresses"}
LABELS = json.loads((DATA / "size_labels.json").read_text(encoding="utf-8"))
RANGES = LABELS["_ranges_cm"]
ALIAS: dict[str, str] = {}
for canon, als in LABELS.items():
    if canon.startswith("_"):
        continue
    for a in [canon] + als:
        ALIAS[re.sub(r"\s+", "", a).lower()] = canon
ALIAS_SORTED = sorted(ALIAS, key=len, reverse=True)
# 라벨 사이에 낀 공백을 넘어가며 찾는다 — OCR 이 「총기장」을 「총 기장」으로, 「밑위」를
# 「밑 위」로 띄어 쓴다. 별칭 키는 공백을 지운 꼴이라 원문과 안 맞았고, 그 한 칸 때문에
# 머리줄이 라벨 둘을 못 채워 표가 통째로 버려졌다(frizmworks, 2026-09-05).
LABEL_RX = re.compile("|".join(r"\s*".join(re.escape(c) for c in a) for a in ALIAS_SORTED), re.I)
# 두 낱말 별칭을 뺀 판. ALIAS 의 키는 이미 공백을 지운 꼴이라(「sleeve length」→
# 「sleevelength」) 키만 봐서는 두 낱말인지 알 수 없어, 원본 별칭에서 골라 만든다.
_ONE_WORD = {re.sub(r"\s+", "", a).lower()
             for canon, als in LABELS.items() if not canon.startswith("_")
             for a in [canon] + als if " " not in a.strip()}
LABEL_RX_1W = re.compile("|".join(r"\s*".join(re.escape(c) for c in a)
                                  for a in ALIAS_SORTED if a in _ONE_WORD), re.I)
NUM = r"\d{1,3}(?:[.,]\d)?"
# 행렬 표의 칸에는 다섯 자리까지 받는다 — OCR 이 「104.0cm」를 「10400」으로 흘려 쓴다(easy-no-easy).
# 머리에 정식 라벨이 둘 이상 있고 칸 수가 정확히 맞을 때만 쓰이는 자리라, 값 대신 가격이 끼어들 여지가 없다.
# 소수점 아래 두 자리까지 받는다. 한 자리만 받던 시절 kirsh 의 「33.75 28.25」 같은
# 1/4cm 표기가 칸 검사에서 통째로 떨어져 나가, 표가 멀쩡한데도 상품이 미수집으로 남았다
# (2026-09-05 실측: 이 한 줄 때문에 23건이 걸려 있었다).
NUM_CELL = r"\d{1,5}(?:[.,]\d{1,2})?"
# 사이즈 이름은 느슨하게 — OCR 이 「002」를 「OOM」으로 읽는다(kirsh). 헤더(정식 라벨 ≥2)와 숫자 개수 일치가 지킨다
SIZE_NAME = r"(?:xxs|xs|s|m|l|xl|xxl|2xl|3xl|free|f|one\s*size|os|[A-Za-z0-9]{1,4})"
# 세트 상품은 사이즈 자리에 옷 이름이 들어간다 — kirsh 「볼레로 튜브탑 세트」는 표가 둘이고
# 줄 머리가 「Tube Top」·「Bolero」다(사람이 화면으로 보여 줌, 2026-09-05). 네 글자 제한에
# 걸려 통째로 버려지고 있었다. 머리줄에 정식 라벨이 둘 이상이고 칸 수가 정확히 맞을 때만
# 쓰이는 자리라, 이름을 낱말 두 개까지 늘려도 잡음이 들어올 여지가 없다.
SET_NAME = r"(?:[A-Za-z가-힣][A-Za-z가-힣.\-]{0,11}(?:\s+[A-Za-z가-힣][A-Za-z가-힣.\-]{0,11})?)"


def canon_label(s: str) -> str | None:
    key = re.sub(r"[\s()（）:：]", "", s).lower()
    if key in ALIAS:
        return ALIAS[key]
    m = LABEL_RX.search(s)
    return ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower()) if m else None


RANGE_RX = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*[~\-–—]\s*(\d+(?:[.,]\d+)?)\s*$")


def _row_name_ok(nm: str) -> bool:
    """값 줄 머리의 사이즈 이름인가. 「M」·「00F」 같은 짧은 표기와, 세트 상품의 옷 이름
    (「Tube Top」·「Bolero」)을 함께 받는다."""
    return bool(nm) and (re.fullmatch(SIZE_NAME, nm, re.I) or re.fullmatch(SET_NAME, nm, re.I))


def fix_value(label: str, raw: str, girth: bool = False) -> float | None:
    raw = str(raw).strip()
    # 「33~40」 — 밴딩 허리처럼 늘어나는 칸. 브라우저가 받아온 표에 있고, 그대로 float()
    # 하면 병합이 통째로 죽는다(2026-09-04). 한 칸에 한 수만 담는 구조라 가운뎃값을 쓴다 —
    # 작은 쪽만 쓰면 「허리 33」이 되어 실제보다 훨씬 작게 보이고, 큰 쪽만 쓰면 그 반대다.
    m = RANGE_RX.match(raw)
    if m:
        a, b = (float(x.replace(",", ".")) for x in m.groups())
        raw = f"{(a + b) / 2:.1f}"
    try:
        v = float(raw.replace(",", "."))
    except ValueError:
        return None
    if girth:
        v = v / 2   # 「가슴둘레 111」은 둘레 — 국내 표기 기준(단면)으로 맞춘다(rough-side, 2026-09-04)
    lo, hi = RANGES.get(label, (3, 200))
    # 소수점이 떨어진 숫자(355 → 35.5, 3500 → 35.0, 10400 → 104.0) — 범위에 들어올 때까지 10 으로 나눈다.
    # 「3225」처럼 어느 자리로도 안 맞는 것은 범위 밖으로 버려진다.
    # 세 자리 이상일 때만 되살린다. 두 자리 수에는 떨어질 소수점이 없는데도 이 규칙이
    # 돌아서, 상한을 아슬아슬하게 넘긴 값이 그럴듯한 쓰레기로 바뀌었다 —
    # 소매길이 상한이 80 이라 「M 67 62 80 / L 68.5 63.5 81 / XL 70 65 82」가
    # [80, 8.1, 8.2] 가 됐다(frizmworks, 2026-09-05). 범위를 넘으면 버리는 게 맞다.
    if raw.isdigit() and len(raw) >= 3:
        for _ in range(3):
            if v <= hi:
                break
            v = v / 10
    return round(v, 1) if lo <= v <= hi else None


# 머리를 괄호 안에 몰아 적고 값은 슬래시로 나열하는 표 — 표가 아니라 문장이다.
#     SIZE ( LENGTH / WAIST / CROTCH / HIP / THIGH / HEM)
#     S l 108cm / 29cm / 22cm / 40cm / 22cm / 15cm
# badblood 가 이 꼴이라 「원본에 실측이 없다」로 세고 있었다(사람이 화면으로 짚어 줌,
# 2026-09-05). 사이즈 이름과 값 사이의 「l」은 세로선을 OCR·폰트가 흘려 쓴 것이다.
_PAREN_HEAD = re.compile(r"(?:size|사이즈)\s*[\(（]\s*([^)）]{6,140})[\)）]", re.I)


def parse_paren_slash(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    best = None
    for m in _PAREN_HEAD.finditer(text or ""):
        labels = [canon_label(w) for w in re.split(r"[/,·|]", m.group(1))]
        # 같은 라벨이 두 번 나오면(「Front rise / Back rise」는 둘 다 밑위) 뒤엣것을
        # 「모르는 칸」으로 둔다 — 통째로 거부하면 자리가 멀쩡한 표를 버리게 된다.
        seen_lab: set[str] = set()
        for i, c in enumerate(labels):
            if c and c in seen_lab:
                labels[i] = None
            elif c:
                seen_lab.add(c)
        known = [c for c in labels if c]
        if len(known) < 2:
            continue
        n = len(labels)
        # 밴딩 허리는 「34~44」처럼 범위로 적는다(9999archive) — 칸 하나로 받아
        # fix_value 가 가운뎃값을 쓰게 한다. 숫자만 긁으면 34 와 44 가 두 칸이 된다.
        num = r"\d{1,4}(?:[.,]\d{1,2})?"
        cell = rf"{num}(?:\s*[~\-–]\s*{num})?\s*(?:cm)?"
        # 보이지 않는 글자(\ufeff·\u200b)가 여러 개 붙기도 한다 — 하나만 허용했더니
        # badblood 「… 밑단) \ufeff\ufeff S | 61 cm / …」를 놓쳤다(2026-09-05).
        row_rx = re.compile(rf"[\ufeff\u200b\s]*({SIZE_NAME})\s*[l|I:\-–]?\s*"
                            rf"((?:{cell}\s*/\s*){{{n - 1}}}{cell})", re.I)
        names, cols = [], {c: [] for c in known}
        pos = m.end()
        while len(names) < 8:
            r = row_rx.match(text, pos)
            if not r:
                break
            nums = [re.sub(r"\s*cm\s*$", "", x.strip(), flags=re.I)
                    for x in re.split(r"\s*/\s*", r.group(2))][:n]
            if len(nums) < n:
                break
            names.append(r.group(1).upper())
            for c, raw in zip(labels, nums):
                if c:
                    v = fix_value(c, raw)
                    cols[c].append(v)
            pos = r.end()
        cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
        if names and len(cols) >= 2 and (best is None or len(cols) > len(best[1])):
            best = (names, cols)
    return best


def _row_cells(row: str) -> list[str]:
    """값 줄을 칸으로 나눈다(맨 앞 사이즈 이름은 뺀다)."""
    r = re.sub(r"[|ㅣ:;=_]", " ", row).strip()
    r = re.sub(r"(?<=\d)\s*(?:cm|cem|c[^\w\s]m|em|om|¢m|crn)\b", " ", r, flags=re.I)
    # 한 칸에 두 수를 붙여 적는 표 — dnsr 의 레이어드 소매는 「35/19」(겉/안)다. 앞엣것이 값이다.
    # 붙여 쓴 것만 접는다 — 「66 / 34~44」처럼 띄어 쓴 슬래시는 칸 구분이라 접으면 표가 무너진다.
    r = re.sub(r"(?<!\S)(\d[\d.,]*)/(\d[\d.,]*)(?!\S)", r"\1", r)
    tok = re.sub(r"\s+", " ", r).split()
    return tok[1:] if len(tok) >= 2 else []


def pick_labels(head_line: str, greedy: list[str], next_lines: list[str]) -> list[str]:
    """머리줄을 어떻게 끊을지 값 줄의 칸 수로 고른다.

    「SIZE GUIDE (CM) SHOULDER CHEST SLEEVE LENGTH」에서 SLEEVE LENGTH 가 한 별칭으로
    붙어 별개 열인 LENGTH(총장)를 삼킨다(dnsr). 쪼갠 안을 늘 쓰면 「소매 기장」이
    소매+기장으로 갈려 다른 매장이 무너지므로(2026-09-05 실측 440 라벨 손실),
    값 줄의 칸 수와 맞아떨어지는 쪽만 쓴다 — 칸 수는 표가 스스로 말해 주는 답이다."""
    alt: list[str] = []
    for m in LABEL_RX_1W.finditer(re.sub(r"[|ㅣ]", " ", head_line)):
        c = ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower())
        if c and c not in alt:
            alt.append(c)
    if len(alt) <= len(greedy):
        return greedy
    for row in next_lines:
        cells = _row_cells(row)
        if len(cells) < 2 or not all(re.fullmatch(rf"{NUM_CELL}|[-–—]", c) for c in cells):
            continue
        if len(cells) == len(alt) != len(greedy):
            return alt
        return greedy
    return greedy


def parse_slots(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """머리줄의 칸 「자리」로 값을 맞춘다 — 라벨 하나가 깨져도 나머지가 산다.

    parse_matrix 는 알아본 라벨 수만큼만 값을 가져간다. 그런데 OCR 이 라벨 하나를
    통째로 흘려 쓰면(kirsh 「(cm) Be 허리 엉덩이 앞밑위 허벅지 밑단」 — Be 는 총장)
    라벨 5 · 값 6 이 되어 값이 한 칸씩 밀리고, 밀린 값이 범위를 벗어나 표 전체가 버려진다.

    그래서 머리줄을 「아는 라벨 + 모르는 칸」의 자리 목록으로 읽고, 값 개수가 자리
    개수와 같을 때만 자리대로 짝지어 아는 라벨만 취한다. 모르는 칸의 값은 버린다 —
    무엇인지 모르는 수를 아무 라벨에나 붙이는 것보다 비우는 편이 낫다.
    """
    UNIT = re.compile(r"^[(\[]?\s*(?:cm|size|사이즈|단위|inch|in)\s*[)\]]?$", re.I)
    best = None
    for i, ln in enumerate(lines):
        head = re.sub(r"[|ㅣ]", " ", ln).strip()
        toks = [t for t in head.split() if t]
        while toks and UNIT.match(toks[0]):
            toks.pop(0)
        if len(toks) < 3:
            continue
        # 라벨 뒤에 붙은 기호를 떼고 본다 — 「총장(4) 어깨(8) 가슴(6)」처럼 그림의 번호를
        # 달아 두는 표가 있다(mardi-mercredi 아동복). 별칭 표를 그대로 찾으면 다 놓친다.
        slots = [canon_label(t) for t in toks]
        known = [c for c in slots if c]
        if len(known) < 2 or len(set(known)) != len(known):
            continue
        names, cols = [], {c: [] for c in known}
        for row in lines[i + 1:i + 12]:
            r = re.sub(r"[|ㅣ:;=_]", " ", row).strip()
            r = re.sub(r"(?<=\d)\s*(?:cm|cem|em|om|crn)\b", " ", r, flags=re.I)
            tok = re.sub(r"\s+", " ", r).split()
            # 머리줄 첫 칸이 사이즈 이름 칸의 제목일 때가 있다(「Unit(cm) 연령 신장 총장 …」)
            # — 그때는 값 줄의 칸 수가 머리줄과 같다.
            if len(tok) == len(slots) + 1:
                nm, cells = tok[0], tok[1:]
                cslots = slots
            elif len(tok) == len(slots):
                nm, cells = tok[0], tok[1:]
                cslots = slots[1:]
            else:
                if names:
                    break
                continue
            # 숫자인지는 「아는 라벨」 자리만 본다 — 모르는 칸의 값은 어차피 버리는데
            # 거기에 「5-6Y」·「100-110」(연령·신장)이 들어 있어 표 전체가 떨어졌다.
            pairs = list(zip(cslots, cells))
            named = [c for sl, c in pairs if sl]
            # 사이즈 이름에 괄호가 붙는 표가 있다 — 「M(110)」·「J2(140)」(아동복 호칭)
            nm_core = re.sub(r"\([^)]*\)$", "", nm) or nm
            if not re.fullmatch(SIZE_NAME, nm_core, re.I) or not named or not all(
                    re.fullmatch(rf"{NUM_CELL}|[-–—]", c) for c in named) or not any(
                    re.fullmatch(NUM_CELL, c) for c in named):
                if names:
                    break
                continue
            names.append(nm.upper())
            for c, raw in pairs:
                if c:
                    cols[c].append(None if raw in ("-", "–", "—") else fix_value(c, raw))
        if names and (best is None or len(names) > len(best[0])):
            best = (names, cols)
    if not best:
        return None
    names, cols = best
    sizes = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    return (names, sizes) if sizes else None


def parse_matrix(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """헤더 줄(정식 라벨 ≥2) + 사이즈 행들. 가장 많은 행을 얻는 헤더를 고른다."""
    best = None
    best_score = (0, 0)
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
        labels = pick_labels(ln, labels, lines[i + 1:i + 6])
        names, cols = [], {c: [] for c in labels}
        for row in lines[i + 1:i + 12]:
            # 세로선(|)은 칸 구분(frizmworks). 콜론·세미콜론은 OCR 이 세로선이나 점을 잘못 읽은 것이다
            # — 「M 49 55 58: 60」(dnsr, 2026-09-04) 처럼 한 글자 때문에 표 한 장을 통째로 버리고 있었다.
            r = re.sub(r"[|ㅣ:;=_]", " ", row).strip()
            # OCR 이 cm 을 em·cem·c¢m·om 으로 흘려 쓴다(easy-no-easy) — 숫자 뒤에 붙은 것만 지운다
            r = re.sub(r"(?<=\d)\s*(?:cm|cem|c[^\w\s]m|em|om|¢m|crn)\b", " ", r, flags=re.I)
            r = re.sub(r"(?<!\S)(\d[\d.,]*)/(\d[\d.,]*)(?!\S)", r"\1", r)   # 「35/19」= 겉/안
            # 숫자 뒤에 한 글자만 붙은 것은 OCR 이 흘려 쓴 단위다 — diafvine 은 표가 그림이라
            # 「39.5cm」이 「3950n」으로 온다(소수점은 떨어지고 cm 은 0n·07·0 이 된다).
            # 글자만 떼면 기존 소수점 복원이 3950 → 395 → 39.5 로 되살린다(2026-09-05).
            r = re.sub(r"(?<=\d)[A-Za-z가-힣](?=\s|$)", "", r)
            r = re.sub(r"\s+", " ", r)
            # 사이즈 이름: 「1」「M」뿐 아니라 「1 SIZE」「1 SIZE [9]」(easy-no-easy) 도 한 칸이다.
            # 숫자 뒤에 남는 부스러기(「Th (cm)」 — kirsh)는 버린다.
            # SET_NAME 을 여기 넣었다가 되돌렸다(2026-09-05) — 줄 머리를 느슨하게 받으면
            # 엉뚱한 머리줄이 점수에서 이겨 값이 세로로 긁힌다(the-coldest-moment
            # 「어깨[37,39] 가슴[40,42]」가 「소매길이[37,40,42,58]」로 뭉갰다, 892 라벨 손실).
            # 세트 상품 한 건을 얻자고 치를 값이 아니다. 아래 「-」 칸 갈래에서만 받는다.
            m = re.match(rf"^\(?({SIZE_NAME})\)?(?:\s*size)?(?:\s*[\[(][^\])]{{0,8}}[\])])?\s+"
                         rf"((?:{NUM_CELL}\s*){{{len(labels)},{len(labels)+1}}})\s*(?:\D{{0,10}})?$", r, re.I)
            if m:
                nums = re.findall(NUM_CELL, m.group(2))[:len(labels)]
                nm = m.group(1)
            else:
                # 빈 칸을 「-」로 두는 표(민소매의 SLEEVE — dnsr) 는 칸 수가 맞을 때만 받는다
                tok = r.split()
                nm, cells = (tok[0], tok[1:]) if tok else ("", [])
                if len(cells) != len(labels) or not _row_name_ok(nm) \
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
        # 라벨 수를 먼저 본다. 행 수만 보면 설명 문장이 진짜 머리줄을 이긴다 —
        # kirsh 「ㆍ 암홀, 밑단 뒷부분 밴딩」(라벨 2)이 「(cm) 총장 어깨 가슴」(라벨 3)을
        # 눌러서 {'밑단': [41, 43]} 한 칸만 남았다(2026-09-04, 100건이 이 꼴이었다).
        # 표의 머리줄은 라벨이 많고, 문장은 어쩌다 두 개가 걸린다.
        score = (len(labels), len(names))
        if names and (best is None or score > best_score):
            best_score = score
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


ROW_BREAK = re.compile(
    r"(?i)(?<![\w.])((?:\d\s*)?size\s*[\[(][^\])]{1,8}[\])]|(?:\d\s*)?size\s*\d?"
    r"|xxs|xs|s|m|l|xl|xxl|2xl|3xl|free|one\s*size)\s+(?=\d{1,3}(?:[.,]\d)?\s*(?:cm|em|om)?\s+\d)")


def resegment(text: str) -> list[str]:
    """OCR 이 표를 줄바꿈 없이 한 줄로 뭉쳐 놓으면 parse_matrix 가 「헤더 다음 줄」을 못 찾는다 —
    easy-no-easy 45벌이 「SIZE 종장 어깨너비 가슴단면 1SIZE[M] 73.5cm 61cm …」 한 줄이었다.
    사이즈 이름 뒤에 숫자가 오는 자리마다 줄을 끊어 표 모양을 되살린다(2026-09-04)."""
    t = re.sub(r"[ \t]+", " ", text)
    return [ln.strip() for ln in ROW_BREAK.sub(r"\n\1 ", t).splitlines() if ln.strip()]


def parse_flat(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    """헤더와 값이 한 줄에 이어 붙은 정방향 표. kamien 은 설명글에 이렇게 들어 있어 크롤러의
    SIZE_RX 가 「SLEEVE 뒤의 숫자 전부」로 잘못 잘랐다(2026-09-04):
        Size SIZE LENGTH SHOULDER CHEST SLEEVE 2 74 61 68 64 3 76 63 72 65
    라벨 수(k)를 센 뒤 남은 낱말을 k+1 개씩 끊는다 — 첫 칸이 사이즈 이름, 나머지가 값이다.
    「OS 36-48 40 33 42 54 111」처럼 값에 범위가 섞여도 칸 수로 맞으니 받는다."""
    best = None
    for m in re.finditer(r"(?i)\bsizes?\b", text):
        tok = re.sub(r"[|ㅣ:;=]", " ", text[m.end():m.end() + 500]).split()
        NUMISH = rf"{NUM_CELL}|{NUM_CELL}-{NUM_CELL}"
        cols_raw: list[str | None] = []
        i = 0
        while i < len(tok) and not re.fullmatch(NUMISH, tok[i]):
            if re.fullmatch(r"(?i)sizes?", tok[i]):
                i += 1
                continue
            c = canon_label(tok[i])
            # 못 알아본 낱말 뒤에 바로 숫자가 오면 그건 라벨이 아니라 첫 행의 사이즈 이름이다
            # (kamien 「… LENGTH OS 36-48 40 …」의 OS).
            if not c and i + 1 < len(tok) and re.fullmatch(NUMISH, tok[i + 1]):
                break
            cols_raw.append(c)
            i += 1
            if len(cols_raw) > 12:
                break
        # 첫 라벨 앞에 붙은 낱말은 칸이 아니다 — 「SIZE CM 총장 …」의 CM 이 가짜 칸이 되어
        # 값이 한 칸씩 밀렸다(9999archive, 2026-09-04).
        while cols_raw and cols_raw[0] is None:
            cols_raw.pop(0)
        # 같은 라벨이 두 칸일 때는 앞 칸만 쓴다 — 「(f)RISE (b)RISE」는 둘 다 밑위로 읽힌다
        first: set[str] = set()
        for j, c in enumerate(cols_raw):
            if c and c in first:
                cols_raw[j] = None
            elif c:
                first.add(c)
        labels = [c for c in cols_raw if c]
        k = len(cols_raw)
        if len({c for c in labels}) < 2 or k < 2:
            continue
        names: list[str] = []
        cols: dict[str, list[float]] = {}
        while i + k < len(tok) + 1 and len(names) < 10:
            chunk = tok[i:i + k + 1]
            if len(chunk) < k + 1:
                break
            nm, vals = chunk[0], chunk[1:]
            if not re.fullmatch(SIZE_NAME, nm, re.I):
                break
            if not all(re.fullmatch(rf"{NUM_CELL}|{NUM_CELL}-{NUM_CELL}|\({NUM_CELL},{NUM_CELL}\)|[-–—]", v)
                       for v in vals):
                break
            names.append(nm.upper())
            for c, raw in zip(cols_raw, vals):
                if not c:
                    continue
                # 「36-48」(조절되는 허리)과 「(74,78)」(9999archive 가 두 값을 괄호로 묶는다)은 앞 값을 쓴다
                if re.fullmatch(rf"\({NUM_CELL},{NUM_CELL}\)", raw):
                    raw = raw[1:].split(",")[0]
                elif re.fullmatch(rf"{NUM_CELL}-{NUM_CELL}", raw):
                    raw = raw.split("-")[0]
                v = None if raw in ("-", "–", "—") else fix_value(c, raw)
                cols.setdefault(c, []).append(v)
            i += k + 1
        sizes = {c: v for c, v in cols.items() if any(x is not None for x in v)}
        if names and len(sizes) >= 2 and (best is None or len(names) * len(sizes) > len(best[0]) * len(best[1])):
            best = (names, sizes)
    return best


def drop_inches(st: dict[str, list]) -> dict[str, list]:
    """cm 과 inch 를 나란히 적은 표 — 「가슴 [59.0, 23.2]」는 두 사이즈가 아니라 한 값의 두 단위다
    (59.0 / 23.2 = 2.54). far-from-what 51개 표가 전부 이 꼴이었다(2026-09-04)."""
    def num(x):
        try:
            return float(str(x).replace(",", "."))
        except (TypeError, ValueError):
            return None

    out = {}
    for k, v in st.items():
        if k == "_names" or not isinstance(v, list) or len(v) < 2:
            out[k] = v
            continue
        # 브라우저가 거둔 표는 값이 문자열이다(「61」) — 숫자로 못 읽는 칸은 그냥 남긴다
        ns = [num(x) for x in v]
        inch = {j for i, a in enumerate(ns) for j, b in enumerate(ns)
                if i != j and a and b and abs(a / b - 2.54) < 0.04}
        out[k] = [x for j, x in enumerate(v) if j not in inch] if inch else v
    return out


def from_ocr(text: str) -> tuple[list[str] | None, dict[str, list[float]]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for cand in (lines, resegment(text)):
        mat = parse_matrix(cand)
        if mat and len(mat[1]) >= 2:
            return mat[0], clean_ocr({k: list(vs) for k, vs in mat[1].items()})
    # 설명글에만 걸어 두었던 정방향 표 읽기를 OCR 글에도 건다 — 「SIZE CM 총장 허리 … 1 114 40 …」
    # 이 꼴이 OCR 글에 그대로 나오는데 243벌이 그래서 빠져 있었다(2026-09-04).
    flat = parse_flat(text)
    if flat and len(flat[1]) >= 2:
        return flat[0], clean_ocr(flat[1])
    par = parse_paren_slash(text)
    if par and len(par[1]) >= 2:
        return par[0], clean_ocr(par[1])
    # 라벨 하나가 깨져 칸이 밀린 표 — 자리로 맞춘다(parse_slots 주석)
    for cand in (lines, resegment(text)):
        slot = parse_slots(cand)
        if slot and len(slot[1]) >= 2:
            return slot[0], clean_ocr({k: list(vs) for k, vs in slot[1].items()})
    return None, clean_ocr(parse_rows(text))


def clean_ocr(st: dict[str, list]) -> dict[str, list]:
    """HTML 표에만 걸어 두었던 값 검사를 OCR 표에도 건다 — OCR 은 오히려 더 자주 두 칸을 섞어 읽는다.
    「가슴 [60, 62.5, 65, 61.5]」(diafvine)처럼 오르락내리락하는 라벨만 버린다(2026-09-04)."""
    if sweep_all(st):
        return {}
    return {k: v for k, v in st.items() if not bad_label(v)}


def sweep_all(st: dict) -> bool:
    """표 전체가 못 쓰는 경우 — 라벨이 한둘뿐인데 값이 일곱 개 넘게 붙어 있다(매장 공용 안내표거나
    한 라벨이 표를 통째로 쓸어담은 것). kamien 「총장 [41, 37.5, 31, 39, 51, 109]」이 그 예다."""
    # 「_names」는 라벨이 아니라 사이즈 이름 열이다 — 세면 안 된다(라벨 하나짜리 표가
    # 「라벨 둘」로 보여 문턱을 빠져나가거나, 이름 여덟 개가 값으로 세어진다).
    lab = {k: v for k, v in st.items() if k != "_names"}
    return len(lab) <= 2 and any(isinstance(v, list) and len(v) > 6 for v in lab.values())


def bad_label(xs: list) -> bool:
    """사이즈 값은 한 방향으로만 간다(S→M→L). 오르락내리락하면 그 라벨이 두 칸을 섞어 읽은 것이다 —
    glowny 「length [26, 30, 27, 31]」(앞기장·뒤기장), insilence 「밑단단면 [22.7, 5.0, 23.2, 5.0]」
    (22.75 가 쪼개짐). 표 전체가 아니라 그 라벨만 버린다 — 나머지 라벨은 멀쩡하다(2026-09-04).
    """
    nums = [x for x in xs if isinstance(x, (int, float))]
    # 같은 옷의 사이즈끼리는 몇 cm 차이지 배로 벌어지지 않는다. 라벨 하나가 서로 다른
    # 두 항목을 섞어 읽으면 그때만 크게 벌어진다 — 「총장 [108, 37]」(far-from-what),
    # 「밑단 [10.5, 32]」(9999archive), 「총장 [40, 56, 95]」(dnsr).
    # 실측 분포로 문턱을 정했다(2026-09-05, 라벨×상품 53,899개):
    #   1.2배 이내 97.1% · 1.4배 이내 99.6% · 1.6배 이내 99.81%.
    #   1.6배를 넘는 100개는 표본이 전부 깨진 값이었고, 1.4~1.6 구간은
    #   「소매길이 [15,16,17,19,20,21,22]」(사이즈 일곱 벌짜리 반팔)처럼 멀쩡했다.
    if len(nums) < 4:
        return False
    return not (all(a <= b for a, b in zip(nums, nums[1:])) or all(a >= b for a, b in zip(nums, nums[1:])))


def brand_label_median(crawl_dir) -> dict[tuple[str, str], float]:
    """브랜드×라벨의 값 중앙값. 한 라벨 안에 엉뚱한 값이 섞였을 때 어느 쪽이 잡값인지 가른다.
    「총장 [102, 28]」(tonywack)에서 102 가 그 브랜드의 바지 총장 무리에 속한다."""
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
            for k, vs in st.items():
                if k == "_names":
                    continue
                c = canon_label(k)
                if not c or not isinstance(vs, list):
                    continue
                for x in vs:
                    try:
                        f = float(str(x).replace(",", "."))
                    except Exception:
                        continue
                    lo, hi = RANGES.get(c, (0, 999))
                    if lo <= f <= hi:
                        vals[(d["brand_slug"], c)].append(f)
    return {k: statistics.median(v) for k, v in vals.items() if len(v) >= 12}


def drop_strays(brand: str, c: str, vs: list[float], med: dict) -> list[float]:
    """한 라벨 안에서 무리를 벗어난 값만 뺀다 — 라벨을 통째로 버리면 멀쩡한 값까지 잃는다.
    같은 옷의 사이즈끼리는 몇 cm 차이지 배로 벌어지지 않는다(실측 2026-09-05: 라벨×상품
    53,899개 중 1.2배 이내 97.1% · 1.6배 이내 99.81%). 1.6배를 넘게 벌어졌을 때만,
    그 브랜드 그 라벨의 중앙값에 가까운 쪽을 남긴다."""
    # OCR 표는 빈 칸을 None 으로 둔다 — 숫자만 놓고 본다
    pos = [v for v in vs if isinstance(v, (int, float)) and v > 0]
    if len(pos) < 2 or max(pos) / min(pos) <= 1.6:
        return vs
    m = med.get((brand, c))
    if m is None:
        return vs
    keep = [v for v in vs if not isinstance(v, (int, float)) or m / 1.6 <= v <= m * 1.6]
    return keep if any(isinstance(v, (int, float)) for v in keep) else vs


def normalize_html(st: dict, brand: str = "", girth_keys: set | None = None,
                   med: dict | None = None) -> dict[str, list[float]]:
    if sweep_all(st):
        return {}
    st = drop_inches(st)
    out: dict[str, list[float]] = {}
    for k, vals in st.items():
        c = canon_label(k)
        if not c or c in out:
            continue
        if bad_label(vals if isinstance(vals, list) else []):
            continue
        girth = "둘레" in k or "circum" in k.lower() or (girth_keys is not None and (brand, c) in girth_keys)
        vs = [v for v in (fix_value(c, str(x), girth) for x in vals) if v is not None]
        vs = drop_strays(brand, c, vs, med or {})
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


# 색만 다른 같은 옷 — 소재·사이즈·디테일이 같다(사람 확인 2026-09-04). 한쪽에만 표가 있으면 물려준다.
_COLOR = re.compile(
    r"[\s_\-\(\[/]*(black|white|ivory|beige|navy|blue|grey|gray|charcoal|khaki|olive|brown|cream|pink|red|"
    r"green|melange|sand|stone|mocha|camel|burgundy|purple|yellow|orange|silver|gold|light|dark|deep|washed|"
    r"블랙|화이트|아이보리|베이지|네이비|블루|그레이|차콜|카키|올리브|브라운|크림|핑크|레드|그린|멜란지|샌드|모카|카멜|버건디|퍼플)"
    r"[\s_\-\)\]/]*", re.I)


def color_base(name: str) -> str:
    """상품 이름에서 색 이름과 대괄호를 걷어낸 알맹이 — 같은 옷의 다른 색을 한 묶음으로 묶는 열쇠."""
    n = re.sub(r"\[[^\]]*\]", "", name or "")
    # 시즌 표기는 알맹이가 아니다 — 같은 옷을 한쪽만 「25FW 박스 플리츠 팬츠 차콜」로,
    # 다른 쪽은 「박스 플리츠 팬츠 블랙」으로 올린다(rough-side). 그 접두사 하나 때문에
    # 형제로 안 묶여 사이즈를 물려받지 못했다(사람이 링크로 짚어 줌, 2026-09-05).
    n = re.sub(r"(?<![0-9A-Za-z])\d{2}\s*(?:fw|ss|su|aw|ps|s/s|f/w)(?![0-9A-Za-z])", " ", n, flags=re.I)
    n = _COLOR.sub(" ", n)
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z가-힣]+", " ", n)).strip().lower()


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
    # 브라우저로 거둔 것 — 자바스크립트가 그리는 표는 여기밖에 없다(diafvine).
    brw: dict[str, dict] = {}
    if BROWSER.exists():
        for p2 in BROWSER.glob("*.jsonl"):
            for l in p2.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    d2 = json.loads(l)
                    if d2.get("source_url"):
                        brw[d2["source_url"]] = d2
    if brw:
        print(f"브라우저 기록 {len(brw)}건")
    girth_keys = brand_girth(CRAWL)
    label_med = brand_label_median(CRAWL)
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
                sizes = normalize_html(st, k[0], girth_keys, label_med)
                source = "html"
                # 크롤러가 표의 사이즈 이름 열을 「_names」로 함께 넘긴다(2026-09-05).
                hn = st.get("_names")
                if isinstance(hn, list) and hn:
                    names = [str(x) for x in hn]
            # 크롤러의 SIZE_RX 가 「라벨 뒤 숫자 전부」로 잘못 자른 표는 설명글에서 다시 읽는다 —
            # kamien 은 표가 전부 설명글에 정방향으로 들어 있는데 59개가 그렇게 버려졌다(2026-09-04).
            if len(sizes) < 2:
                body = "\n".join(t for t in (d.get("description") or "", d.get("detail_text") or "") if t)
                flat = parse_flat(body)
                if flat and len(clean_ocr(flat[1])) > len(sizes):
                    sizes, names, source = clean_ocr(flat[1]), flat[0], "html"
                # 괄호 머리 + 슬래시 나열(badblood) — 표가 아니라 문장이라 위 갈래로는 안 잡힌다
                if len(sizes) < 2:
                    par = parse_paren_slash(body)
                    if par and len(clean_ocr(par[1])) > len(sizes):
                        sizes, names, source = clean_ocr(par[1]), par[0], "html"
            # 브라우저가 본 표·설명글 — 서버 HTML 에 없던 것이 여기 있다
            b = brw.get(r["source_url"])
            if len(sizes) < 2 and b:
                raw = b.get("size_table_raw") or {}
                if raw:
                    cand = normalize_html(raw, k[0], girth_keys, label_med)
                    if len(cand) > len(sizes):
                        sizes, names, source = cand, None, "browser"
                if len(sizes) < 2:
                    flat2 = parse_flat(b.get("description") or "")
                    if flat2 and len(clean_ocr(flat2[1])) > len(sizes):
                        sizes, names, source = clean_ocr(flat2[1]), flat2[0], "browser"
                if len(sizes) < 2:
                    n2, s2 = from_ocr(b.get("description") or "")
                    if len(s2) > len(sizes):
                        sizes, names, source = s2, n2, "browser"
            if len(sizes) < 2 and ocr.get(k):
                names2, sizes2 = from_ocr(ocr[k])
                if len(sizes2) > len(sizes):
                    sizes, names, source = sizes2, names2, "ocr"
            if not sizes:
                continue
            # HTML 표에는 사이즈 이름 열이 없다(크롤러가 라벨 칸만 읽었다 — 2026-09-05 에
            # 「_names」로 함께 넘기게 고쳤지만 이미 받아 둔 기록에는 없다). 같은 상품의 OCR
            # 글에서 표를 다시 읽어 이름만 빌려 온다. 다른 표의 이름을 붙이면 안 되니,
            # 사이즈 개수가 같고 겹치는 라벨의 값이 1cm 안에서 맞을 때만 쓴다.
            if not names and source in ("html", "browser") and ocr.get(k):
                n2, s2 = from_ocr(ocr[k])
                want = max((len(v) for v in sizes.values() if isinstance(v, list)), default=0)
                if n2 and len(n2) == want >= 2:
                    same = [c for c in set(sizes) & set(s2)
                            if all(abs(a - b) <= 1 for a, b in zip(sizes[c], s2[c])
                                   if isinstance(a, (int, float)) and isinstance(b, (int, float)))]
                    if same:
                        names = n2
            # 어디서 왔든(HTML·브라우저·OCR) 무리를 벗어난 값은 여기서 한 번에 뺀다 —
            # normalize_html 안에만 두었더니 OCR 로 읽은 「밑단 [10.5, 32]」가 그대로 남았다.
            sizes = {c: v for c, v in ((c, drop_strays(k[0], c, v, label_med))
                                       for c, v in sizes.items()) if v}
            if not sizes:
                continue
            # 잡화에 옷 실측 표를 붙이지 않는다 — 매장 공용 안내표가 모자·양말·백팩에까지
            # 「가슴 허리」를 물려 주고 있었다(thebarnnet 33 등 96건, 2026-09-05 검사).
            # 가방에 어깨너비가 있을 리 없고, 있다면 그건 남의 옷 표다.
            if r.get("category_code") in NON_APPAREL_CODES and set(sizes) & GARMENT_ONLY:
                continue
            # 사이즈 개수가 라벨마다 다르면(OCR 누락) 가장 짧은 길이로 맞춘다
            n = min(len(v) for v in sizes.values())
            sizes = {c: v[:n] for c, v in sizes.items()}
            if names:
                names = names[:n]
            out[r["source_url"]] = {"brand_slug": k[0], "source": source, "size_names": names, "sizes": sizes}
            src[source] += 1
            (per_brand_html if source in ("html", "browser") else per_brand_ocr)[k[0]] += 1
    # 색만 다른 형제에게서 사이즈를 물려받는다 — 같은 옷이라 실측이 같다. 어디서 왔는지 남긴다.
    by_base = defaultdict(list)
    for k, r in rows.items():
        by_base[(k[0], color_base(r["name"]))].append(r)
    lent = 0
    for key, sibs in by_base.items():
        if len(sibs) < 2 or not key[1]:
            continue
        donor = next((s for s in sibs if s["source_url"] in out), None)
        if not donor:
            continue
        base_entry = out[donor["source_url"]]
        for r in sibs:
            if r["source_url"] in out or r.get("category") not in GARMENT_LABELS:
                continue
            out[r["source_url"]] = {"brand_slug": r["brand_slug"], "source": "sibling",
                                    "sibling_of": donor["source_url"],
                                    "size_names": base_entry["size_names"], "sizes": base_entry["sizes"]}
            lent += 1
    if lent:
        print(f"색만 다른 형제에게서 물려받은 사이즈 {lent}벌")
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
