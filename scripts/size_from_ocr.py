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
import collections
import json
import re
import statistics
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"
OCR = CRAWL / "ocr"
BROWSER = CRAWL / "browser"
OUT = DATA / "product_sizes.json"
MANUAL = DATA / "manual_sizes.csv"   # 사람이 그림을 보고 옮겨 적은 값 — 무엇보다 앞선다
# 세트 상품의 부위 이름. **이 두 말만 쓴다** — 앱이 이 이름으로 옷장 카테고리와 짝을 맞춰
# 핏 비교할 표를 고른다. 「바지」·「팬츠」가 섞이면 짝을 못 찾는다(앱 세션 2026-09-24).
# size_parts 가 있는 상품의 기본 size 는 상의로 읽힌다 — size_parts 에는 하의만 담는다.
SIZE_PARTS = ("상의", "하의")

NON_APPAREL_CODES = {"shoes", "bags", "accessories", "headwear", "jewelry", "lifestyle", "pet"}
# 옷에만 있는 실측 항목 — 잡화 행에 이게 있으면 표를 잘못 물어 온 것이다
GARMENT_ONLY = {"어깨", "가슴", "밑위", "뒤밑위", "허벅지", "암홀", "화장", "소매길이"}
# 우리 치수 축(총장·허리 …)이 뜻을 갖지 않는 갈래 — 이 갈래는 표를 통째로 안 붙인다
NO_SIZE_AXIS_CODES = {"bags", "headwear", "shoes"}
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
# 「M/40」처럼 글자와 숫자를 빗금으로 잇는 사이즈 이름이 흔한데 네 글자 제한에 걸려
# 값 줄이 통째로 안 잡혔다 — 머리줄은 멀쩡히 읽히는데 아래 행을 하나도 못 받아
# 표가 버려진다(2026-09-15, 창고에서 197벌). 숫자끼리의 빗금(「35/19」= 겉/안)은
# 값 줄을 다듬는 자리에서 이미 앞쪽만 남기고 접히므로 여기 걸릴 일이 없다.
# OCR 이 사이즈 이름의 첫 글자를 기호로 흘리기도 한다 — 「S/38」이 「$/38」로 온다.
# 빗금 뒤에 숫자가 오는 자리는 사이즈 이름 말고 올 것이 없어 받아도 안전하다.
# 「size 뒤에 숫자」와 「밑줄로 이은 이름」도 사이즈 이름이다. 네 글자 제한에 걸려 값 줄을
# 통째로 못 받아 머리줄이 멀쩡한데 표째 버려졌다(창고에서 40벌: Size1 28 · OO_FREE·01_S 12).
#     - 가슴단면 소매 종장 어깨  /  Size1 58 55 54 62.5
#     총장 어깨 가슴 소매장 소매단 밑단  /  OO_FREE 60 36 36 61 12 39
# 둘 다 치수 이름과 겹칠 여지가 없다 — 「size+숫자」는 사이즈 말고 올 것이 없고, 밑줄로
# 이은 꼴은 매장이 제 호수 체계를 앞에 붙인 것이다. 이 자리는 머리줄에 정식 라벨이 둘
# 이상이고 칸 수가 맞을 때만 쓰이므로 잡음이 들어올 여지도 없다.
SIZE_NAME = (r"(?:[A-Za-z$§₩]{1,3}/\d{1,3}|size\s*\d{1,2}|[A-Za-z0-9]{1,4}_[A-Za-z0-9]{1,5}"
             r"|xxs|xs|s|m|l|xl|xxl|2xl|3xl|free|f|one\s*size|os|[A-Za-z0-9]{1,4})")
# 세트 상품은 사이즈 자리에 옷 이름이 들어간다 — kirsh 「볼레로 튜브탑 세트」는 표가 둘이고
# 줄 머리가 「Tube Top」·「Bolero」다(사람이 화면으로 보여 줌, 2026-09-05). 네 글자 제한에
# 걸려 통째로 버려지고 있었다. 머리줄에 정식 라벨이 둘 이상이고 칸 수가 정확히 맞을 때만
# 쓰이는 자리라, 이름을 낱말 두 개까지 늘려도 잡음이 들어올 여지가 없다.
SET_NAME = r"(?:[A-Za-z가-힣][A-Za-z가-힣.\-]{0,11}(?:\s+[A-Za-z가-힣][A-Za-z가-힣.\-]{0,11})?)"


# 앞에 붙으면 「다른 옷의 치수」라는 뜻인 말. 「나시총장」은 한 상품에 든 나시의 총장이라
# 겉옷의 총장이 아니다 — 그냥 총장으로 받으면 같은 이름이 두 번 나와 표가 밀린다
# (2026-09-05 kirsh 「총장 어째 가슴 화장 나시총장 나시어깨 나시가슴」).
OTHER_GARMENT = re.compile(r"(?:나시|이너|아우터|inner|outer|반팔|긴팔|상의|하의)\s*$", re.I)


_BAD_LINES: Counter = Counter()
# (원본 칸 이름, 정식 라벨) → [범위에 든 값, 범위 밖이라 버린 값]. 끝에 한 번 찍는다.
_COL_DROP: dict[tuple[str, str], list[int]] = {}


def iter_jsonl(p):
    """줄 하나가 깨졌다고 판 전체를 버리지 않는다 — 그 줄만 건너뛰고 몇 줄인지 남긴다.

    2026-09-13: 새 매장 13판이 전부 생성물 재생성에서 죽었다. 창고 어딘가에 중간에 끊긴
    줄 하나가 있었고(JSONDecodeError: Unterminated string), 그 한 줄 때문에 CSV·사이즈·
    태그가 통째로 안 만들어졌다 — 매장 221곳치 수집이 앱까지 못 갔다. 크롤러는 같은 자리를
    이미 try 로 감싸고 있었는데 여기만 맨몸이었다.
    조용히 삼키지는 않는다. 끝에 몇 줄을 건너뛰었는지 찍어서, 진짜 깨진 창고면 눈에 띄게 한다.
    """
    for l in p.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            yield json.loads(l)
        except json.JSONDecodeError:
            _BAD_LINES[p.name] += 1


# 같은 낱말을 수백만 번 다시 본다 — 치수표의 낱말은 「총장」·「가슴」·숫자 몇 가지가
# 끝없이 되풀이되는데, 그때마다 정규식을 네댓 번씩 다시 돌렸다. py-spy 로 40번 찍어
# 보니 이 함수 안에 있던 것이 **17/40** 이었다(2026-09-20). 값은 글자에만 달렸고
# 돌려주는 것이 문자열이나 None 이라 담아 둬도 남이 못 고친다.
@lru_cache(maxsize=1 << 18)
def canon_label(s: str) -> str | None:
    key = re.sub(r"[\s()（）:：]", "", s).lower()
    # 「화장」이 든 라벨은 무조건 화장이다. 뒷목 중심에서 소매끝까지를 가리키는 한 낱말이고,
    # 다른 치수 이름에는 이 말이 안 들어간다. 그런데 라벨을 찾는 자리는 왼쪽부터 보므로
    # 「소매화장」에서 앞의 「소매」가 먼저 걸려 소매길이가 됐다 — 20cm 넘게 다른 치수다.
    # 전수로 재니 소매화장 81 · 소매길이화장 11 · 어깨화장 1 (2026-09-15, rough-side
    # 「소매화장 86/88/90」이 소매길이로 서 있었다). 화장품·화장실은 뺀다.
    if "화장" in key:
        return None if re.search(r"화장[품실대]", key) else "화장"
    if key in ALIAS:
        return ALIAS[key]
    m = LABEL_RX.search(s)
    if not m:
        return None
    if OTHER_GARMENT.search(s[:m.start()]):
        return None
    g = re.sub(r"\s+", "", m.group(0))
    # 한 글자 별칭(「품」=가슴, 「힙」=엉덩이)은 홀로 설 때만 받는다 — 그냥 두면
    # 「제품색상은」·「상품」·「품목」이 전부 가슴이 된다(2026-09-05: 그 탓에 diafvine
    # 두 벌이 안내 문장을 값 줄로 오해해 표를 통째로 잃었다).
    if len(g) <= 1:
        near = (s[max(0, m.start() - 1):m.start()] + s[m.end():m.end() + 1])
        if re.search(r"[가-힣A-Za-z]", near):
            return None
    return ALIAS.get(g.lower())


RANGE_RX = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*[~\-–—]\s*(\d+(?:[.,]\d+)?)\s*$")


# 표의 머리말이지 사이즈 이름이 아닌 말. 「52 SIZE 61 36.2」에서 앞의 52 를 놓치면
# 「SIZE」가 사이즈 이름이 되어 앱 화면에 「SIZE」라는 사이즈가 섰다(2026-09-06).
# SIZE_NAME 이 네 글자 영숫자를 받아 그냥 통과한다. 읽기 단계에서 줄을 버리면 멀쩡한
# 치수까지 함께 날아가므로(frizmworks 총장 73·어깨 58…), 이름만 비운다.
NOT_A_SIZE = re.compile(r"(?i)^(?:sizes?|사이즈|cm|inch|in|item|model|note)$")


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


# 라벨을 빗금으로 나란히 적고 그 아래 값도 빗금으로 잇는 꼴. 표가 아니라 **글**이라
# 머리줄을 찾는 갈래가 전부 헛돈다:
#
#     ∥ Size Guide ∥ 총장 / 어깨 / 가슴 / 밑단 / 소매길이 / 소매통 / 소매단
#     S 61 / 55.9 / 64.8 /53.3 / 52.7/20.3/13.3   M 61.6 /57.2 / 67.3 / 54 / 53.3/ 20.6 /13.7
#
# 이 글은 **이미 창고에 있다** — 매장이 상세 설명에 적어 둔 것을 그대로 받아 왔는데
# 읽는 갈래가 없었을 뿐이다(2026-09-17 전수 287벌). 사람이 상품 페이지에서 짚어 줬다.
_SLASH_ROW = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*/\s*(?=\d)")
_SLASH_CUT = re.compile(r"/\s*[^\s/,]{1,16}\s+$")


def parse_slash_table(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    """빗금으로 이은 라벨 줄 + 빗금으로 이은 값 줄."""
    best = None
    for m in re.finditer(r"(?:[^\s/,]{1,16}\s*/\s*){2,}[^\s/,]{1,16}", text or ""):
        # 라벨 줄의 **뒤토막**만 잡혔으면 칸 수를 못 믿는다. 라벨에 빈칸이 있으면
        # (「Front rise」) 빈칸 없는 토막만 이어지는 이 눈이 앞을 잘라 먹는다:
        #     Size (Length / Waist / Front rise / Back rise / Thigh / Hem)
        #     M: 111 / 30inch / 36 / 40 / 34 / 28
        # 「rise / Thigh / Hem)」 세 칸으로 보면 36·40·34 가 밑위·허벅지·밑단이 되는데
        # 실제로는 밑위·뒤밑위·허벅지다(2026-09-17 실측: 이 꼴 2벌이 그렇게 틀렸다).
        # 바로 앞이 「/ 낱말 」이면 잘린 것이므로 통째로 버린다.
        if _SLASH_CUT.search(text[max(0, m.start() - 24):m.start()]):
            continue
        toks = [x.strip() for x in m.group(0).split("/")]
        labels = [canon_label(x) for x in toks]
        if sum(1 for c in labels if c) < 3:
            continue
        if len({c for c in labels if c}) != sum(1 for c in labels if c):
            continue                      # 같은 라벨이 두 번이면 자리를 못 믿는다
        n = len(labels)
        names, cols, seen_rows = [], {c: [] for c in labels if c}, set()
        tail = text[m.end():m.end() + 60 * n + 260]
        # 「이름 값/값/값…」이 이어지는 만큼 받는다
        # 이름 뒤에 「S- 59 / …」처럼 잇는 글자가 온다(tibaeg). 이름 칸을 느슨하게
        # 두면 눈이 「F-59」를 이름 5 + 값 9 로 쪼개 첫 값을 망가뜨리므로, 값 줄은
        # **숫자 한가운데서 시작할 수 없게** 막는다(2026-09-17 실측: tibaeg 1664).
        for rm in re.finditer(rf"(?:([A-Za-z0-9가-힣]{{1,6}})\s*[-:.)]?\s*)?(?<![\d.,])"
                              rf"((?:\d{{1,3}}(?:[.,]\d)?\s*/\s*){{{n-1}}}\d{{1,3}}(?:[.,]\d)?)", tail):
            vals = [x.strip() for x in re.split(r"\s*/\s*", rm.group(2))]
            if len(vals) != n:
                continue
            nm = (rm.group(1) or "").strip()
            if nm and not re.fullmatch(SIZE_NAME, nm, re.I):
                nm = ""
            # 같은 값 줄이 두 번 오면 접는다 — 크롤러가 description 과 detail_text 를
            # 따로 담는데 매장에 따라 둘이 같은 글이라, 이어 붙이면 표가 두 벌이 된다.
            if tuple(vals) in seen_rows:
                continue
            seen_rows.add(tuple(vals))
            names.append(nm.upper() or str(len(names) + 1))
            for c, v in zip(labels, vals):
                if c:
                    cols[c].append(fix_value(c, v))
        cols = {c: v for c, v in cols.items()
                if v and any(isinstance(x, (int, float)) for x in v)}
        if len(cols) < 3:
            continue
        sc = (len(names), len(cols))
        if best is None or sc > best[0]:
            best = (sc, (names, cols))
    return best[1] if best else None


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


_PAIR = re.compile(r"([가-힣A-Za-z][가-힣A-Za-z0-9]{0,9})\s*[:\-]?\s*"
                   r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:cm|CM|센티)?(?![\d.])")


def _label_value_pairs(s: str) -> list[tuple[str, str, int]]:
    """한 줄에서 「라벨 값」 짝을 차례대로 뽑는다. 라벨 뒤에 바로 수가 없으면 표 줄이 아니다."""
    out: list[tuple[str, str, int]] = []
    for m in LABEL_RX.finditer(s):
        lab = canon_label(m.group(0))
        if not lab:
            continue
        mv = re.match(r"\s*[:=]?\s*(\d{1,3}(?:[.,]\d)?)\s*(?:cm|CM)?", s[m.end():])
        if not mv:
            return []
        out.append((lab, mv.group(1), m.start()))
    return out


def parse_pair_rows(lines: list[str]) -> tuple[list[str] | None, dict[str, list[float]]] | None:
    """**줄 하나가 사이즈 하나**이고, 그 줄 안에 「라벨 값」이 되풀이되는 표.

        M
        총장 74.5 어깨 55.5 가슴 64 소매 22.5
        L
        총장 76.5 어깨 57.5 가슴 66 소매 23.5

    사람이 상품 페이지의 「INFORMATION」을 눌러 보고 알려 준 꼴이다(2026-09-18). 그 창은
    `/product/sizeguide.html?product_no=N` 로 그냥 받아지는데, 여태 우리는 그 창에서
    **그림만** 찾고 있었다 — 글로 적힌 매장은 통째로 「없음」으로 세었다. 못 채운 옷이
    20벌 넘는 매장 47곳을 찔러 보니 네 곳이 이 꼴이다(합 500벌 남짓).

    parse_named_pairs 와는 **눕는 방향이 반대**다. 거기서는 한 줄이 한 라벨이고 짝의 이름이
    사이즈였는데, 여기서는 한 줄이 한 사이즈고 짝의 이름이 라벨이다.

    잘못 걸리지 않도록: 줄마다 짝이 둘 이상 · 한 줄 안에 같은 라벨이 두 번 나오지 않을 것 ·
    **라벨 차례가 줄마다 똑같을 것** · 라벨 바로 뒤에 수가 붙을 것. 마지막 조건이 같은 창에
    함께 실린 매장 공용 환산표(「가슴 (inches) 22 23 24 …」)를 자동으로 걸러 낸다 —
    거기서는 라벨 뒤에 수가 아니라 괄호가 온다.
    """
    rows: list[tuple[tuple[str, ...], list[str], str | None]] = []
    prev = ""
    for ln in lines:
        # 모델 몸 치수 줄은 받지 않는다 — 꼴이 옷 표와 똑같아서 그냥 두면 그대로 들어온다:
        #     * Model size
        #     - INGA  Height 175cm Bust 78cm Waist 62cm Hip 90cm
        #     - LISA  Height 171cm Bust 75cm Waist 60cm Hip 90cm
        # 이 두 줄이 「라벨 차례가 같은 두 줄」이라 표로 보이고, 정작 그 위의 진짜 표
        # (Total length 66 / Shoulder 68 / …)는 열로 서 있어 이 갈래가 못 읽는다. 그래서
        # 가슴 78·75(줄어든다)가 들어왔다(2026-09-18 실측 crank 4벌). **키가 적힌 줄**은
        # 옷 표일 수 없다 — drop_model_body 가 쓰는 잣대와 같다.
        if _MODEL_H.search(ln or ""):
            prev = ""
            continue
        ps = _label_value_pairs(ln or "")
        if len(ps) < 2:
            if (ln or "").strip():
                prev = ln.strip()
            continue
        labs = tuple(l for l, _, _ in ps)
        if len(set(labs)) != len(labs):
            prev = ln.strip()
            continue
        # 사이즈 이름은 라벨 앞에 붙기도 하고(「M 총장 74.5 …」) 윗줄에 혼자 서기도 한다.
        head = (ln[:ps[0][2]] or "").strip() or prev
        mh = re.fullmatch(r"[\[(]?\s*([A-Za-z0-9가-힣]{1,6})\s*[\])]?", head)
        rows.append((labs, [v for _, v, _ in ps], mh.group(1) if mh else None))
        prev = ""
    # 한 줄뿐이어도 짝이 셋 이상이면 표로 본다 — 사이즈가 하나인 옷(FREE)이 그 꼴이다
    # (2026-09-18: 「총장 49 어깨 35 가슴 39 소매 12」 한 줄짜리가 실제로 있다).
    if len(rows) < 2 and not (len(rows) == 1 and len(rows[0][0]) >= 3):
        return None
    key = Counter(r[0] for r in rows).most_common(1)[0][0]
    rows = [r for r in rows if r[0] == key]
    if len(rows) < 2 and not (len(rows) == 1 and len(key) >= 3):
        return None
    cols: dict[str, list[float]] = {}
    for i, lab in enumerate(key):
        if lab in cols:
            continue
        cols[lab] = [fix_value(lab, r[1][i]) for r in rows]
    cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    if len(cols) < 2:
        return None
    names = [r[2] for r in rows]
    return (names if all(names) and len(set(names)) == len(names) else None), cols


_GRID_NUM = re.compile(r"^\s*\d{1,3}(?:[.,]\d+)?(?:\s*[~\-]\s*\d{1,3}(?:[.,]\d+)?)?\s*(?:cm|CM)?\s*$")
_GRID_NAME = re.compile(r"^\s*(?:\(?\s*(?:cm|CM|inch|in)\s*\)?|[A-Za-z0-9가-힣]{1,8})\s*$")


def parse_grid_rows(lines: list[str]) -> tuple[list[str] | None, dict[str, list[float]]] | None:
    """「라벨 | 값 | 값 | 값」 꼴로 한 줄에 모아 둔 표.

    fetch_sizeguide.text_in 이 `<table>` 을 줄마다 모아 주면 사이즈가이드 창의 표가 이 꼴로
    온다(2026-09-21):

        S | M | L | XL
        A 기장 / 앞-뒤 | 65.5~70 | 67.5~72 | 69.5~74 | 71.5~76
        B 어깨단면 | 49 | 51 | 53 | 55
        C 팔기장 | 63.5 | 64 | 65 | 66

    parse_pair_rows 와 **눕는 방향이 반대**다 — 거기서는 한 줄이 한 사이즈인데 여기서는
    한 줄이 한 라벨이고 열이 사이즈다. 크롤러가 상품 페이지에서 읽어 오는 size_table 과
    같은 모양이라, 값만 뽑아 주면 뒤는 기존 길을 그대로 탄다.

    **인치 환산표를 안 먹는 것이 이 갈래의 목숨줄이다.** 같은 창에 카페24 공용 표가 함께
    실리는데 그것도 생김새가 똑같다(「가슴 | 22 | 23 | 24 …」 — 인치다). 막는 것은 둘이다:
    ① 부르는 쪽(fetch_sizeguide)이 매장 안에서 열 벌 넘게 똑같은 줄을 미리 지운다 —
       공용표는 글자까지 같아서 거기서 걸린다. ② 여기서는 canon_label 을 통과하는 줄만
       받는다 — 공용표의 KR·US·JP·UK 행은 치수 라벨이 아니라 그대로 떨어진다.
    그래도 남는 것이 있을 수 있으니 값의 개수가 줄마다 같기를 요구한다.

    사이즈 이름은 **바로 위의 숫자 아닌 줄**에서 빌린다(위 보기의 「S | M | L | XL」).
    개수가 안 맞으면 이름 없이 값만 돌려준다 — 없는 이름을 지어내지 않는다.
    """
    rows: list[tuple[str, list[str]]] = []
    head: list[str] | None = None
    for ln in lines:
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < 3:
            continue
        if all(_GRID_NUM.match(c) for c in cells[1:]) and canon_label(cells[0]):
            rows.append((canon_label(cells[0]), cells[1:]))
            continue
        # 값이 하나도 없는 줄은 사이즈 이름 줄 후보다 — 마지막 것을 쓴다.
        if not rows and all(_GRID_NAME.match(c) and not _GRID_NUM.match(c) for c in cells):
            head = cells
    if len(rows) < 2:
        return None
    width = Counter(len(v) for _, v in rows).most_common(1)[0][0]
    rows = [r for r in rows if len(r[1]) == width]
    if len(rows) < 2 or width < 2:
        return None
    cols: dict[str, list[float]] = {}
    for lab, vals in rows:
        if lab in cols:
            continue
        cols[lab] = [fix_value(lab, v) for v in vals]
    cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    if len(cols) < 2:
        return None
    names = head if head and len(head) == width and len(set(head)) == width else None
    return names, cols


def _stack_label(s: str) -> str | None:
    """세로로 쌓인 표의 라벨 줄 — 앞의 번호(「1.어깨」·「A 기장」)는 떼고 본다."""
    if _GRID_NUM.match(s):
        return None
    return canon_label(re.sub(r"^\s*(?:\d{1,2}|[A-Za-z])\s*[.)]\s*", "", s))


def parse_stack_rows(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """표의 칸이 한 줄에 하나씩 흩어져 온 사이즈가이드 글.

    브라우저가 `<table>` 을 칸마다 줄을 바꿔 읽어 오는 매장이 있다. 눕는 방향이 둘이다
    (2026-09-24 창고 전수 — 결손 옷 가운데 실측이 글로 있는데 못 읽은 것):

      ㉠ 이름 먼저, 라벨마다 값 (lmood 31벌)       ㉡ 라벨 먼저, 사이즈마다 값 (legacy 20벌)
         44 / 46 / 48                                 사이즈 / 1.어깨 / 2.가슴 / 3.소매 / 4.총길이
         총장 / 59.5 / 61 / 62.5                      95 / 52 / 60 / 61 / 111.5
         어깨너비 / 52.5 / 54.5 / 56.5                100 / 53.5 / 62.5 / 62 / 113

    **칸 수가 딱 맞을 때만 받는다.** 이름 줄 수 = 라벨마다의 값 수(㉠), 라벨 수 = 사이즈마다의
    값 수(㉡)가 끝까지 맞아야 하고, 표가 끝난 다음 줄이 또 숫자면(= 칸을 잘못 셌다) 버린다.
    라벨은 canon_label 을 통과하는 줄만 — 카페24 공용 환산표(KR·US·JP)는 여기서 떨어진다.
    """
    # 「라벨 | 값 | 값」 줄은 parse_grid_rows 몫이다 — 여기서 라벨 줄로 잘못 잡으면 옆 칸의
    # 값이 사이즈 이름이 된다(2026-09-24 haiq·amomento). 칸마다 줄이 바뀐 글만 본다.
    L = [x.strip() for x in lines if x.strip() and "|" not in x]
    n = len(L)
    num = lambda s: bool(_GRID_NUM.match(s))
    for j in range(n):
        if not _stack_label(L[j]):
            continue
        # ㉠ 라벨 뒤 숫자 줄 수 = 사이즈 수. 바로 앞의 같은 수만큼이 사이즈 이름이다.
        w = 0
        while j + 1 + w < n and num(L[j + 1 + w]):
            w += 1
        if 2 <= w <= 8 and j >= w:
            names = L[j - w:j]
            before = L[j - w - 1] if j - w - 1 >= 0 else ""
            # 이름 자리 바로 앞이 라벨이면 그 「이름」은 앞 라벨의 값이다 — slowacid 가
            # Length 값 103·105·107… 을 사이즈 이름으로 받았다(2026-09-24).
            if (all(_row_name_ok(x) and not _stack_label(x) for x in names) and len(set(names)) == w
                    and not num(before) and not _stack_label(before)):
                cols: dict[str, list[float]] = {}
                p = j
                while p + w < n and _stack_label(L[p]) and all(num(x) for x in L[p + 1:p + 1 + w]):
                    lab = _stack_label(L[p])
                    cols.setdefault(lab, [fix_value(lab, x) for x in L[p + 1:p + 1 + w]])
                    p += 1 + w
                cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
                if len(cols) >= 2 and not (p < n and num(L[p])):
                    return names, cols
        # ㉡ 라벨이 k 줄 잇달아 서고, 그 뒤로 「이름 + 값 k 개」가 되풀이된다.
        k = 0
        while j + k < n and _stack_label(L[j + k]):
            k += 1
        if k >= 2:
            labs = [_stack_label(x) for x in L[j:j + k]]
            if len(set(labs)) == k:
                rows: list[tuple[str, list[str]]] = []
                p = j + k
                while (p + k < n and _row_name_ok(L[p]) and not _stack_label(L[p])
                       and all(num(x) for x in L[p + 1:p + 1 + k])):
                    rows.append((L[p], L[p + 1:p + 1 + k]))
                    p += 1 + k
                if (len(rows) >= 2 and len({r[0] for r in rows}) == len(rows)
                        and not (p < n and num(L[p]))):
                    cols = {lab: [fix_value(lab, r[1][c]) for r in rows] for c, lab in enumerate(labs)}
                    cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
                    if len(cols) >= 2:
                        return [r[0] for r in rows], cols
    return None


def parse_named_pairs(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """한 줄 안에서 「이름 값 이름 값」이 되풀이되는 표.

    레이어드 옷은 한 상품에 옷이 둘이라 표가 이렇게 눕는다(2026-09-05 the-coldest-moment,
    사람이 화면으로 짚어 줌):

        Shoulder (어깨) 반팔 39cm 긴팔 39cm
        Chest (가슴)   반팔 49cm 긴팔 43cm

    「반팔·긴팔」이 사이즈 이름 자리다. 잘못 걸리지 않도록 이름 묶음이 여러 줄에서
    똑같아야 하고(「가슴 단면 52 밑단 50」 같은 줄은 이름이 줄마다 달라 걸리지 않는다),
    이름이 그 자체로 치수 라벨이면 버린다.
    """
    rows: list[tuple[str, tuple[str, ...], list[str]]] = []
    for ln in lines:
        m = LABEL_RX.search(ln)
        if not m:
            continue
        lab = canon_label(m.group(0))
        if not lab:
            continue
        rest = ln[m.end():]
        rest = re.sub(r"^\s*\([^)]*\)", "", rest)          # 「(뒷면 중심선 기준)」
        pairs = _PAIR.findall(rest)
        if len(pairs) < 2:
            continue
        names = tuple(n.upper() for n, _ in pairs)
        if len(set(names)) != len(names):
            continue
        if any(canon_label(n) for n, _ in pairs):
            continue
        rows.append((lab, names, [v for _, v in pairs]))
    if len(rows) < 2:
        return None
    key = Counter(r[1] for r in rows).most_common(1)[0][0]
    rows = [r for r in rows if r[1] == key]
    if len(rows) < 2:
        return None
    cols: dict[str, list[float]] = {}
    for lab, _, vals in rows:
        if lab in cols:
            continue
        cols[lab] = [fix_value(lab, v) for v in vals]
    cols = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    return (list(key), cols) if len(cols) >= 2 else None


def _row_cells(row: str) -> list[str]:
    """값 줄을 칸으로 나눈다(맨 앞 사이즈 이름은 뺀다)."""
    # 숫자 사이에 낀 콜론은 소수점이다 — siyazu 「One 58 64.5 72:3 25:5」의 72:3 은 72.3 이지
    # 두 칸이 아니다. 칸 구분으로 보면 네 칸짜리 표가 여섯 칸이 되어 통째로 버려지고,
    # 「36:5」는 36 으로 읽혀 값이 조용히 틀어졌다(2026-09-05). 뒤에 빈칸이 오면 구분이다.
    row = re.sub(r"(?<=\d):(?=\d)", ".", row)
    r = re.sub(r"[|ㅣ:;=]", " ", row).strip()
    r = re.sub(r"(?<!\S)_+(?!\S)", "-", r)
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


# 두 낱말짜리 라벨은 머리줄을 「자리」로 셀 때 두 칸으로 세어진다. siyazu 는 영문 머리줄을
# 쓴다 — 「(cm) Length Neck Chest Sleeve Length Sleeve Width」는 값이 다섯인데 토막은
# 일곱이고, 게다가 Sleeve 와 Length 가 홀로 서서 소매길이·총장으로 두 번 잡혀 자리 세기가
# 통째로 깨졌다(2026-09-06). 자리를 세기 전에 아는 두 낱말 별칭을 한 토막으로 붙인다.
_MULTIWORD = sorted({a for canon, als in LABELS.items() if not canon.startswith("_")
                     for a in [canon] + als if " " in a.strip()}, key=len, reverse=True)
_MULTIWORD_RX = re.compile("|".join(r"\s+".join(re.escape(w) for w in a.split())
                                    for a in _MULTIWORD), re.I) if _MULTIWORD else None


def join_multiword(line: str) -> str:
    if not _MULTIWORD_RX:
        return line
    return _MULTIWORD_RX.sub(lambda m: re.sub(r"\s+", "", m.group(0)), line)


# 표 머리줄의 칸 번호 표식. 매장이 도식에 ①②③ 을 달고 머리줄에도 같은 번호를 붙이는데,
# OCR 은 그 동그라미를 「0)」·「@」·「(3)」·「6)」·「©」 따위로 흘려 쓴다. 자리를 세는
# 갈래(parse_slots)는 토막 수로 값과 짝을 짓는 터라, 이 부스러기 하나하나가 가짜 칸이
# 되어 머리줄 15칸 대 값 8칸이 된다 — 표가 멀쩡한데 통째로 버려진다
# (ronron 「Size 0) 머깨 @ 가슴 (3) 밑단 _ @) 팔길이 6) 팔통 @ 암홀」, 창고에서 661벌).
#
# 그런데 이것을 읽기 첫 판에 넣으면 **이미 잘 읽던 표가 움직인다**. 머리줄에서 칸 하나가
# 사라지면 값 줄과의 칸 수 관계가 바뀌어, 사이즈 이름 칸을 기준으로 한 칸씩 밀린 자리에
# 값이 적힌다(2026-09-16 실측: acover 「(cm) 총장 어깨너비 가슴단면 소매길이 / FREE 51 24 34」
# 이 총장 51·어깨 24 대신 어깨 51·소매길이 34 가 됐다 — 없는 치수보다 틀린 치수가 나쁘다).
# 그래서 **아무 갈래도 표를 못 얻었을 때만** 표식을 떼고 한 번 더 읽는다. 이미 읽히는
# 표는 손도 대지 않으므로 이 고침이 무엇을 망가뜨릴 여지가 없다.
#
# 맨숫자(「3」)는 떼지 않는다. 아동복 표의 연령·신장 칸이 그 꼴이라 함께 떼면 자리가 밀린다.
_MARK_TOK = re.compile(r"^(?![0-9]+$)(?:[_.·•*=]|[(\[（]?[0-9①-⑳⓪@©®]{1,2}[)\]）]?)$")
# 같은 되물음에서 단위 칸의 오독도 함께 뗀다 — OCR 이 「cm」을 em·cem·crn 으로 흘려 쓰고
# (「(em)」), 「사이즈」와 붙여 쓴 것(「Sizecm」·「SIZE(CM)」)도 정식 단위 칸으로 안 잡힌다.
# 창고 머리줄 916개에 (em) 44 · Sizecm 40 · SIZE(CM) 24. 이것도 첫 판에 넣으면 잘 읽던
# 표가 밀리므로(위 주석) 되물음 안에서만 뗀다.
_FAKE_UNIT = re.compile(r"^(?:[(\[]?(?:em|cem|crn)[)\]]?|(?:size|사이즈|단위)\s*[(\[]?(?:cm|em|cem|crn)[)\]]?)$", re.I)


def strip_header_markers(text: str) -> str:
    """머리줄처럼 보이는 줄에서만 칸 번호 표식을 뺀다.

    값 줄은 건드리지 않는다 — 값 줄의 「-」는 「이 칸은 비었다」는 뜻이라 빼면 자리가 밀린다.
    """
    out, hit = [], False
    for ln in text.splitlines():
        labs = {ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower()) for m in LABEL_RX.finditer(ln)}
        labs.discard(None)
        if len(labs) >= 2:
            toks = [t for t in ln.split()
                    if t and not _MARK_TOK.match(t) and not _FAKE_UNIT.match(t)]
            if len(toks) != len(ln.split()):
                hit = True
            ln = " ".join(toks)
        out.append(ln)
    return "\n".join(out) if hit else text


def parse_slots(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """머리줄의 칸 「자리」로 값을 맞춘다 — 라벨 하나가 깨져도 나머지가 산다.

    parse_matrix 는 알아본 라벨 수만큼만 값을 가져간다. 그런데 OCR 이 라벨 하나를
    통째로 흘려 쓰면(kirsh 「(cm) Be 허리 엉덩이 앞밑위 허벅지 밑단」 — Be 는 총장)
    라벨 5 · 값 6 이 되어 값이 한 칸씩 밀리고, 밀린 값이 범위를 벗어나 표 전체가 버려진다.

    그래서 머리줄을 「아는 라벨 + 모르는 칸」의 자리 목록으로 읽고, 값 개수가 자리
    개수와 같을 때만 자리대로 짝지어 아는 라벨만 취한다. 모르는 칸의 값은 버린다 —
    무엇인지 모르는 수를 아무 라벨에나 붙이는 것보다 비우는 편이 낫다.
    """
    UNIT = _UNIT_CELL
    best = None
    for i, ln in enumerate(lines):
        head = join_multiword(re.sub(r"[|ㅣ]", " ", ln)).strip()
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
            # 숫자 사이에 낀 콜론은 소수점이다 — siyazu 「One 58 64.5 72:3 25:5」의 72:3 은 72.3 이지
            # 두 칸이 아니다. 칸 구분으로 보면 네 칸짜리 표가 여섯 칸이 되어 통째로 버려지고,
            # 「36:5」는 36 으로 읽혀 값이 조용히 틀어졌다(2026-09-05). 뒤에 빈칸이 오면 구분이다.
            row = re.sub(r"(?<=\d):(?=\d)", ".", row)
            # 「_」는 지우지 않는다 — **빈 칸 표시**다. 9999archive 는 래글런이라 어깨 칸을
            # 비우고 「2 63 60 _ 83」으로 적는데, 구분자로 보고 지우면 값이 셋만 남아
            # 라벨 넷에 한 칸씩 밀려 붙었다(총장 63 이 가슴이 되고 가슴 60 이 어깨가 됐다,
            # 2026-09-21 그림 대조). 「-」와 같은 자리에 두면 자리가 그대로 산다.
            r = re.sub(r"[|ㅣ:;=]", " ", row).strip()
            r = re.sub(r"(?<!\S)_+(?!\S)", "-", r)
            r = re.sub(r"(?<=\d)\s*(?:cm|cem|em|om|crn)\b", " ", r, flags=re.I)
            tok = re.sub(r"\s+", " ", r).split()
            # 머리줄 첫 칸이 사이즈 이름 칸의 제목일 때가 있다(「Unit(cm) 연령 신장 총장 …」)
            # — 그때는 값 줄의 칸 수가 머리줄과 같다.
            if len(tok) == len(slots) + 1:
                nm, cells = tok[0], tok[1:]
                cslots = slots
            elif len(tok) == len(slots) and slots[0] is None:
                # **머리줄 첫 칸이 아는 라벨이면 이름 칸 제목일 리 없다.** 그 갈래는 첫
                # 자리를 버리고 한 칸씩 당겨 짝짓는데, 머리줄이 깨져 토막만 하나 늘어난
                # 표에서 수가 우연히 맞아떨어지면 값이 통째로 한 칸 밀린다 — ronron 은
                # 동그라미 번호 ①②③이 「@O378S · OFS OFF 62」 같은 부스러기로 읽혀
                # 「어깨 ? 밑단 ? ? ? ? 총기장」 8토막이 되고, 값 줄도 「free × 56 47 71
                # 20 X 60」 8토막이라 수가 맞는다. 그 바람에 어깨가 버려지고 밑단이
                # 가슴 값 56 을 먹었다(진짜 밑단은 47, 2026-09-21 그림 대조).
                # 첫 칸이 못 알아본 칸일 때만 제목으로 본다.
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
    # OCR 이 같은 줄을 두 번 읽으면 「FREE FREE」·「ONE ONE」이 된다(235벌, 2026-09-05).
    # 이름과 값이 통째로 같은 줄만 접는다 — 값이 다르면 접지 않는다(그건 다른 문제다).
    # 이름이 같고 값이 어긋나지 않는 줄은 하나로 합친다. 한쪽이 칸 하나를 놓쳤을 뿐이라
    # 통째로 같지 않아 예전엔 못 접었다 — 「XS XS S M」의 허벅지가 [27, null, 28, 29] 였다
    # (2026-09-05). 값이 정말 다르면 접지 않는다. 그건 다른 문제라 감사기가 잡는다.
    def _val(i):
        return [v[i] if i < len(v) else None for v in cols.values()]

    keep: list[int] = []
    for i, nm in enumerate(names):
        for j in keep:
            if names[j] != nm:
                continue
            a, b = _val(j), _val(i)
            if all(x is None or y is None or x == y for x, y in zip(a, b)):
                for k, v in enumerate(cols.values()):
                    if j < len(v) and i < len(v) and v[j] is None:
                        v[j] = v[i]
                break
        else:
            keep.append(i)
    if len(keep) < len(names):
        names = [names[i] for i in keep]
        cols = {c: [v[i] for i in keep if i < len(v)] for c, v in cols.items()}
    sizes = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    return (names, sizes) if sizes else None


_UNIT_CELL = re.compile(r"^[(\[]?\s*(?:cm|size|사이즈|단위|inch|in)\s*[)\]]?$", re.I)


def parse_matrix(lines: list[str]) -> tuple[list[str], dict[str, list[float]]] | None:
    """헤더 줄(정식 라벨 ≥2) + 사이즈 행들. 가장 많은 행을 얻는 헤더를 고른다."""
    best = None
    best_score = (0, 0)
    for i, ln in enumerate(lines):
        low = ln.lower()
        if not re.search(r"size|사이즈|\(00\)|\(07\)|cm", low) and len(LABEL_RX.findall(ln)) < 2:
            continue
        # 헤더에서 라벨을 순서대로. 같은 정식 라벨로 떨어지는 머리말이 둘이면 뒤엣것을
        # 버리는데, 그게 무엇이었는지는 남겨 둔다 — 아래 「충돌」 검사가 쓴다.
        labels = []
        _raw_of: dict[str, set] = {}
        for m in LABEL_RX.finditer(re.sub(r"[|ㅣ]", " ", ln)):
            c = ALIAS.get(re.sub(r"\s+", "", m.group(0)).lower())
            if not c:
                continue
            _raw_of.setdefault(c, set()).add(re.sub(r"\s+", "", m.group(0)).lower())
            if c not in labels:
                labels.append(c)
        if len(labels) < 2:
            continue
        # 값 줄이 라벨로 시작하면 이 표는 「눕힌 표」다 — 머리줄로 잡으면 안 된다.
        # 도식의 화살표 라벨(「ARMHOLE CHEST」)을 머리줄로 삼고 진짜 값 줄
        # (「SHOULDER 53 56」)을 사이즈 이름으로 읽어 암홀 하나만 남았다
        # (2026-09-05 andersson-bell). 그런 표는 parse_rows 가 바로 읽는다.
        nxt = [x for x in lines[i + 1:i + 6] if x.strip()]
        lab_first = sum(1 for x in nxt if canon_label(x.split()[0]) if x.split())
        if lab_first >= 2:
            continue
        # 아는 라벨 사이에 모르는 칸이 끼어 있으면 이 갈래로 읽으면 안 된다. parse_matrix 는
        # 아는 라벨 수만큼만 값을 가져가므로 그 자리가 통째로 밀린다. siyazu 영문 머리줄
        # 「Length Neck Chest SleeveLength SleeveWidth」에서 Neck 을 빼고 세어 총장이 26
        # (진짜 53)이 됐다(2026-09-06). 자리로 맞추는 parse_slots 에 넘긴다.
        htok = [t for t in join_multiword(re.sub(r"[|ㅣ]", " ", ln)).split() if t]
        while htok and _UNIT_CELL.match(htok[0]):
            htok.pop(0)
        hslots = [canon_label(t) for t in htok]
        last = max((x for x, c in enumerate(hslots) if c), default=-1)
        # 서로 다른 머리말이 같은 정식 라벨로 접히면서 값 줄의 숫자 칸이 고유 라벨보다
        # 많으면, 열 하나가 조용히 사라진 것이다. 「가슴둘레 어깨 팔기장 총 기장 /
        # M 51 43 62 81」에서 팔기장이 안에 든 「기장」에 걸려 총장이 되고 진짜 총 기장이
        # 중복으로 버려져, 코트 총장이 62cm(실제 81)로 앱에 섰다(2026-09-13, 16벌).
        # 별칭을 고쳐 그 16벌은 막았지만, 같은 구조는 어휘를 아무리 고쳐도 또 생긴다.
        # 그래서 이 꼴이면 라벨을 세어 맞추지 말고 자리로 맞추는 갈래에도 물어본다 —
        # 아래 판정은 사이즈를 더 많이 얻는 쪽을 쓰므로, 잘 읽던 표가 죽지는 않는다.
        _clash = any(len(v) >= 2 for v in _raw_of.values()) and \
            max((len(re.findall(NUM_CELL, re.sub(r"[|ㅣ:;=_]", " ", x)))
                 for x in lines[i + 1:i + 6] if x.strip()), default=0) > len(labels)
        _slots_ok = False
        if _clash or any(c is None for c in hslots[:last]):
            # 자리로 맞추는 갈래에도 물어보고, 사이즈를 더 많이 얻는 쪽을 쓴다(비기면 자리 쪽).
            # 무조건 넘기면 mardi-mercredi 289벌·blayer 199벌처럼 잘 읽던 표가 죽고,
            # 안 물어보면 siyazu 처럼 값이 한 칸씩 밀린다.
            alt = parse_slots(lines[i:])
            if alt and len(alt[1]) >= 2:
                _slots_ok = True
                # 아래 판정은 `score = (라벨 수, 사이즈 수)` 로 잰다. 자리 후보를
                # (사이즈 수, 라벨 수)로 넣어 두면 **앞뒤가 뒤집혀** 견줄 수가 없다.
                # 사이즈가 하나뿐인 표에서 자리 후보 (1,5) 대 행렬 후보 (5,1) 이 되어
                # 행렬이 늘 이겼다 — 자리 갈래에 물어보나 마나였다.
                #   siyazu 2652  머리줄 「size(cm) sa 어깨 가슴 소매기장 소매통 암홀」
                #                값줄  「One 60.5 48 55 575 26.5 30」
                #                sa 를 못 알아봐 라벨 5·값 6 이 되어 어깨가 sa 의 값 60.5 를
                #                먹고 소매통이 57.5(가슴 48 보다 넓다)가 됐다.
                # 전수 118,844벌로 재니 표를 잃는 상품 0 · 얻는 상품 9 · 값이 바뀌는 상품 936,
                # 그 936벌에서 「소매통>가슴·암홀>가슴」 같은 말 안 되는 관계가 51벌 → 4벌로
                # 줄었다(2026-09-22).
                sc = (len(alt[1]), len(alt[0]))
                if sc >= best_score:
                    best, best_score = alt, sc
        if _clash and not _slots_ok:
            # 자리로도 못 맞추는 충돌 — 이 머리줄은 쓰지 않는다. 라벨 수만큼만 값을
            # 가져가면 사라진 열 뒤의 값이 앞 라벨에 붙는다(팔기장 62 가 총장이 된 그 꼴).
            # 값을 못 얻더라도 남의 이름으로 적지는 않는다.
            continue
        labels = pick_labels(ln, labels, lines[i + 1:i + 6])
        names, cols = [], {c: [] for c in labels}
        pending: list[tuple[int, list, list]] = []
        for row in lines[i + 1:i + 12]:
            # 세로선(|)은 칸 구분(frizmworks). 콜론·세미콜론은 OCR 이 세로선이나 점을 잘못 읽은 것이다
            # — 「M 49 55 58: 60」(dnsr, 2026-09-04) 처럼 한 글자 때문에 표 한 장을 통째로 버리고 있었다.
            # 숫자 사이에 낀 콜론은 소수점이다 — siyazu 「One 58 64.5 72:3 25:5」의 72:3 은 72.3 이지
            # 두 칸이 아니다. 칸 구분으로 보면 네 칸짜리 표가 여섯 칸이 되어 통째로 버려지고,
            # 「36:5」는 36 으로 읽혀 값이 조용히 틀어졌다(2026-09-05). 뒤에 빈칸이 오면 구분이다.
            row = re.sub(r"(?<=\d):(?=\d)", ".", row)
            # 「_」는 빈 칸 표시다 — 지우면 칸이 밀린다(parse_slots 의 같은 자리 주석 참고).
            r = re.sub(r"[|ㅣ:;=]", " ", row).strip()
            r = re.sub(r"(?<!\S)_+(?!\S)", "-", r)
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
                cells = re.findall(NUM_CELL, m.group(2))
                # 칸이 라벨보다 하나 많으면 남는 것이 앞일 수도 뒤일 수도 있다. 늘 앞에서부터
                # 세다가 noirer 「50 5126 105.4 41.9 …」(OCR 이 SIZE 를 5126 으로 읽었다)가
                # 통째로 한 칸씩 밀려 종장 51.3cm·엉덩이 41.9cm 가 되었다 — 같은 표의 위아래
                # 줄은 「48 SIZE 103.5 …」로 멀쩡했다(2026-09-05). 앞뒤 두 벌을 만들어 보고
                # 값이 제 라벨의 상식 범위에 더 많이 들어맞는 쪽을 쓴다.
                if len(cells) == len(labels) + 1:
                    head, tail = cells[:len(labels)], cells[1:]

                    def _fit(cs):
                        return sum(1 for c, v in zip(labels, cs) if fix_value(c, v) is not None)

                    def _near(cs):
                        """같은 표의 바로 윗줄과 얼마나 닮았나. 한 표 안의 이웃한 두 사이즈는
                        칸마다 몇 %밖에 안 다르다 — 한 칸 밀리면 그 닮음이 대번에 깨진다.
                        상식 범위만으로는 앞뒤를 못 가렸다(범위가 넓어 둘 다 통과한다)."""
                        prev = [cols[c][-1] if cols[c] else None for c in labels]
                        if not any(x is not None for x in prev):
                            return None
                        tot = n = 0.0
                        for c, v, pv in zip(labels, cs, prev):
                            fv = fix_value(c, v)
                            if fv is None or pv in (None, 0):
                                continue
                            tot += abs(fv - pv) / abs(pv)
                            n += 1
                        return tot / n if n else None

                    nh, nt = _near(head), _near(tail)
                    if nh is not None and nt is not None and abs(nh - nt) > 1e-9:
                        nums = head if nh < nt else tail
                    else:
                        nums = head if _fit(head) >= _fit(tail) else tail
                        # 윗줄이 없어 못 가린 줄(대개 표의 첫 줄)은 표를 다 만든 뒤 다시 본다
                        pending.append((len(names), head, tail))
                else:
                    nums = cells[:len(labels)]
                nm = m.group(1)
            else:
                # 빈 칸을 「-」로 두는 표(민소매의 SLEEVE — dnsr) 는 칸 수가 맞을 때만 받는다.
                # 칸 하나가 글자로 뭉개졌다고 줄을 통째로 버리지 않는다 — 그림 속 표를 읽으면
                # 한 칸쯤은 늘 뭉개진다(siyazu 「One 09.5 43 AQ 61 15 24」의 AQ, 「One 65
                # 56.5 a 22」의 a). 그 칸만 빈칸으로 두고 나머지 다섯 치수는 살린다.
                # 늘어나는 허리를 「34~36」으로 적은 칸도 받는다(siyazu 슬랙스) — fix_value 가
                # 가운뎃값으로 바꾼다. 안전장치는 셋이다: 칸 수가 라벨 수와 정확히 같을 것,
                # 뭉개진 칸이 넷에 하나 이하일 것, 줄 머리가 사이즈 이름일 것.
                tok = r.split()
                nm, cells = (tok[0], tok[1:]) if tok else ("", [])
                # 「S(1)」「M(2)」처럼 호수를 괄호로 덧붙인 이름(siyazu). 괄호를 떼고 본다.
                nm = re.sub(r"[\[(]\w{1,4}[\])]$", "", nm) or nm
                # 칸이 라벨보다 하나 많아도 받는다 — 머리줄의 라벨 하나가 OCR 로 깨지면
                # (siyazu 「소매기장 소매통 ors」의 ors 는 암홀이다) 라벨만 하나 모자란다.
                # 어느 쪽이 남는 칸인지는 위 갈래와 같은 잣대(윗줄과 닮은 쪽)로 고른다.
                def _try(cs):
                    if len(cs) != len(labels):
                        return None
                    ok = [bool(re.fullmatch(NUM_CELL, c) or RANGE_RX.match(c)) for c in cs]
                    blank = [bool(re.fullmatch(r"[-–—]", c)) for c in cs]
                    junk = sum(1 for g, b in zip(ok, blank) if not g and not b)
                    if not any(ok) or junk > max(1, len(cs) // 4):
                        return None
                    return [c if g or b else "-" for c, g, b in zip(cs, ok, blank)]

                def _dev(pc):
                    tot = k = 0.0
                    for c, v in zip(labels, pc):
                        fv = fix_value(c, v)
                        pv = cols[c][-1] if cols[c] else None
                        if fv is None or pv in (None, 0):
                            continue
                        tot += abs(fv - pv) / abs(pv); k += 1
                    return tot / k if k else 9e9

                # +1 칸은 **이미 한 줄을 읽은 뒤에만** 받는다. 첫 줄부터 허용했더니 진짜 표
                # 앞에 붙은 쓰레기 줄이 행으로 통과해 noirer 46벌에 가짜 행이 생겼다
                # (2026-09-05: 「51.3 · null · null · 48.9 …」가 108.0 짜리 표 앞에 붙었다).
                # 윗줄이 있으면 닮은 쪽을 고를 수 있으니 안전하다.
                if len(cells) == len(labels):
                    cand = [_try(cells)]
                elif names:
                    cand = [_try(cells[:len(labels)]), _try(cells[1:])]
                else:
                    cand = []
                picks = [x for x in cand if x]
                if not picks or not _row_name_ok(nm):
                    if names:      # 표가 끝났다
                        break
                    continue
                nums = picks[0] if len(picks) == 1 else min(picks, key=_dev)
            if len(nums) < len(labels):
                continue
            # 줄 머리가 치수 이름이면 이건 「눕힌 표」의 줄이다 — 사이즈 이름이 아니다.
            # 뭉개진 칸을 봐주기 시작하자 the-coldest-moment 의 「LENGTH 65.5 70」이 값 줄로
            # 통과해, 어깨·가슴까지 갖춘 두 사이즈 표가 총장 한 줄로 뭉개졌다(40벌, 2026-09-05).
            # 사이즈 이름과 치수 이름은 겹치지 않는다(S·M·L·1·2·FREE 대 HEM·RISE·LENGTH).
            if canon_label(nm):
                if names:
                    break
                continue
            nm = nm.upper()
            if re.fullmatch(r"[0O]{2}[0-9OMSFL]", nm):
                # 「OOM」= 00M, 「OOF」= 00F — 앞의 00 만 되돌린다. 이 매장은 001·002·003 과 00S·00M·00L·00F
                # 두 체계를 함께 쓴다(값줄 머리 OOF 251 · OOM 213 · 005 228 · 001 254 · OOL 11). 예전엔 M 을
                # 2 로 바꿔 00M 옷 200여 벌이 002 라는 딴 체계 이름을 달았다(2026-09-08).
                nm = nm[:2].replace("O", "0") + nm[2:]
            names.append(nm)
            for c, raw in zip(labels, nums):
                cols[c].append(None if raw in ("-", "–", "—") else fix_value(c, raw))
        # 두 번째 훑기 — 첫 줄은 견줄 윗줄이 없어 앞뒤를 못 가렸다. 표가 다 만들어진 뒤
        # 열의 가운뎃값과 견주면 가릴 수 있다(noirer 「럭스 울 와이드 슬랙스」의 첫 줄이
        # 밀려 총장 51.3cm 가 되어 있었다, 2026-09-05).
        for row_i, head, tail in pending:
            if len(names) < 2:
                break
            pick, pick_d = None, None
            for cand in (head, tail):
                tot = n = 0.0
                for c, v in zip(labels, cand):
                    others = [x for j, x in enumerate(cols[c]) if j != row_i and x is not None]
                    fv = fix_value(c, v)
                    if fv is None or not others:
                        continue
                    med = sorted(others)[len(others) // 2]
                    if med:
                        tot += abs(fv - med) / abs(med); n += 1
                d = tot / n if n else None
                if d is not None and (pick_d is None or d < pick_d):
                    pick, pick_d = cand, d
            if pick is not None:
                for c, raw in zip(labels, pick):
                    cols[c][row_i] = None if raw in ("-", "–", "—") else fix_value(c, raw)
        # 라벨 수를 먼저 본다. 행 수만 보면 설명 문장이 진짜 머리줄을 이긴다 —
        # kirsh 「ㆍ 암홀, 밑단 뒷부분 밴딩」(라벨 2)이 「(cm) 총장 어깨 가슴」(라벨 3)을
        # 눌러서 {'밑단': [41, 43]} 한 칸만 남았다(2026-09-04, 100건이 이 꼴이었다).
        # 표의 머리줄은 라벨이 많고, 문장은 어쩌다 두 개가 걸린다.
        # (2026-09-23 해 본 것 — 되돌렸다) 「줄마다 한 칸씩 남으면 머리줄에서 라벨이 빠진 것이니
        # 버린다」를 넣었다. etmon 「(cm) 어깨 가슴 소매길이 밑단 / FREE 51.5 36 41 12 59.5」는
        # 맨 앞 총장이 빠져 한 칸씩 밀렸기 때문이다. 그런데 OCR 전수(11.8만 벌)로 재니 **맞게 읽던
        # 표 112벌을 잃었다** — 남는 칸이 대개 **맨 뒤**다(aeae 「Shoulder Chest Sleeve Length」를
        # 이름 셋으로 읽어 끝의 총장만 못 받음, easy-no-easy 넷째 이름이 「AKZO!」로 뭉개짐).
        # 앞이 빠졌는지 뒤가 빠졌는지 가를 표지가 없어서, 한 벌을 고치자고 백 벌을 버리지 않는다.
        score = (len(labels), len(names))
        if names and (best is None or score > best_score):
            best_score = score
            best = (names, cols)
    if not best:
        return None
    names, cols = best
    # OCR 이 같은 줄을 두 번 읽으면 「FREE FREE」·「ONE ONE」이 된다(235벌, 2026-09-05).
    # 이름과 값이 통째로 같은 줄만 접는다 — 값이 다르면 접지 않는다(그건 다른 문제다).
    # 이름이 같고 값이 어긋나지 않는 줄은 하나로 합친다. 한쪽이 칸 하나를 놓쳤을 뿐이라
    # 통째로 같지 않아 예전엔 못 접었다 — 「XS XS S M」의 허벅지가 [27, null, 28, 29] 였다
    # (2026-09-05). 값이 정말 다르면 접지 않는다. 그건 다른 문제라 감사기가 잡는다.
    def _val(i):
        return [v[i] if i < len(v) else None for v in cols.values()]

    keep: list[int] = []
    for i, nm in enumerate(names):
        for j in keep:
            if names[j] != nm:
                continue
            a, b = _val(j), _val(i)
            if all(x is None or y is None or x == y for x, y in zip(a, b)):
                for k, v in enumerate(cols.values()):
                    if j < len(v) and i < len(v) and v[j] is None:
                        v[j] = v[i]
                break
        else:
            keep.append(i)
    if len(keep) < len(names):
        names = [names[i] for i in keep]
        cols = {c: [v[i] for i in keep if i < len(v)] for c, v in cols.items()}
    sizes = {c: v for c, v in cols.items() if any(x is not None for x in v)}
    return names, sizes


_TRANS_NUM = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?")


def parse_transposed(lines: list[str]) -> tuple[None, dict[str, list[float]]] | None:
    """라벨이 세로로 서고 값이 그 오른쪽에 눕는 표.

        총장   | 63  64   65  66
        어깨   | 485 50   515 53
        가슴   | 55  575  60  625
        소매길이 | 62  63   64  65

    사이즈 이름 줄이 없어 머리줄을 찾는 갈래가 전부 헛돈다(parse_label_run 은 라벨이
    **잇달아** 붙어야 머리줄로 삼는데 여기선 라벨마다 값이 끼어 있다). 2026-09-15 에
    창고를 훑다 144벌이 이 꼴로 빠져 있는 것을 찾았다 — 값도 라벨도 멀쩡한데 꼴만 낯설었다.

    잘못 걸리지 않도록 네 가지를 함께 요구한다: 라벨이 **줄 맨 앞**일 것 · 그 줄에 수가
    둘 이상일 것 · 그런 줄이 둘 이상이고 **수의 개수가 모두 같을** 것 · 라벨을 뺀 자리에
    글자가 거의 없을 것(안내 문장이 걸리지 않게). 사이즈 이름은 알 수 없으니 None 이다.
    """
    rows: list[tuple[str, list[str]]] = []
    for ln in lines:
        # 줄 전체를 먼저 본다(「총장 | 63 64 65 66」 — 세로선은 라벨과 값 사이의 구분일 뿐).
        # 그것이 안 되면 세로선으로 쪼갠 칸을 하나씩 본다 — 한 줄에 앞 라벨의 꼬리와 다음
        # 라벨이 함께 오는 표가 있다(「Length) | Hip 50cm 52cm」, siyazu).
        cands = [re.sub(r"[|ㅣ]", " ", ln or "")]
        if "|" in (ln or "") or "ㅣ" in (ln or ""):
            cands += re.split(r"[|ㅣ]", ln or "")
        for cand in cands:
            s_ = re.sub(r"[:：=]", " ", cand).strip()
            m = LABEL_RX.match(s_)
            if not m:
                # 도식에 A·B·C 를 달고 줄머리에도 그 글자를 붙이는 표가 있다
                # (「A (BODY LENGTH) 78.5 80 81.5」). 글자 하나를 떼고 다시 본다 —
                # 뗀 자리에 **아는 라벨**이 서야만 받으므로, 사이즈 이름 줄(「S 65 61…」)이
                # 잘못 걸릴 여지가 없다(2026-09-17 실측: 이 꼴이 한 매장에서만 300벌 넘는다).
                k = re.match(r"[(\[]?\s*[A-Za-z①-⑳]\s*[)\].·-]?\s+", s_)
                if k:
                    s2 = s_[k.end():].lstrip("([<")
                    m = LABEL_RX.match(s2)
                    if m:
                        s_ = s2
            if not m:
                continue
            lab = canon_label(m.group(0))
            if not lab:
                continue
            # 한 줄에 **같은 라벨이 두 번 이상** 서면 표의 한 줄이 아니라 모델 정보 카드다:
            #     = Model size info
            #     가슴둘레 | 79cm 가슴둘레 | 77cm
            #     허리둘레 | 57cm 허리둘레 | 62cm
            # 칸이 옆으로 늘어서 있어 이 갈래에는 「라벨 + 값 둘」인 멀쩡한 표로 보인다.
            # drop_model_body 는 **키가 적힌 줄**만 모델 정보로 보고 값이 하나일 때만 빼므로
            # 여기엔 안 듣는다(2026-09-18 실측: loeuvre 두 벌이 모델 가슴 79·77 · 허리 57·62 를
            # 옷 치수로 받았다 — 여러 상품에 같은 값이 그대로 붙어 있어 곧 눈에 띈다).
            # 머리말 자리로는 못 가른다 — OCR 이 머리말과 값을 열 줄 넘게 떼어 놓는다.
            # 진짜 표는 라벨을 한 번만 적고 값을 잇는다. 서로 **다른** 라벨이 한 줄에 겹치는
            # 표(「Length) | Hip 50cm 52cm」)는 세로선으로 쪼갠 칸이 따로 받는다.
            if sum(1 for m2 in LABEL_RX.finditer(cand)
                   if canon_label(m2.group(0)) == lab) > 1:
                continue
            rest = s_[m.end():]
            # 단위와 구분 기호를 걷어낸 뒤에도 글자가 남으면 표가 아니라 문장이다
            # 라벨 뒤 괄호 속 한두 자리 수는 도식 번호다(「어깨(6) 42 44 46 48 50」).
            # 값으로 세면 줄 전체가 한 칸씩 밀린다 — 실제로 그렇게 밀었다(2026-09-17 실측:
            # 한 매장 88벌에서 어깨 [6, 42, 44 …] 처럼 앞에 번호가 들어앉았다).
            rest = re.sub(r"^\s*[(\[]\s*\d{1,2}\s*[)\]]", " ", rest)
            # 인치로 적은 줄은 받지 않는다. 아래에서 단위 낱말을 걷어 내고 「글자가 남지
            # 않으면 표」로 보는데, 그 걷어 내기가 inch 까지 지워 인치 값이 cm 자리에
            # 들어앉는다(2026-09-17 loeuvre/706: 「가슴둘레 | 30.5 inch」가 가슴 30.5cm 로
            # 읽혔다 — 실제로는 77cm 다). 환산해서 넣을 수도 있지만 그 매장 표는 모델
            # 치수와 옷 치수가 한 줄에 나란히 서 있어 어느 쪽인지 가릴 수 없다.
            # 없는 치수보다 틀린 치수가 나쁘다.
            if re.search(r"\d\s*(?:inch(?:es)?|in\b|[”\"])", rest, re.I):
                continue
            bare = re.sub(r"[\d.,\s/~\-]|cm|CM|em|inch|in\b", "", rest)
            # 라벨 뒤에 꾸밈말이 한 마디 더 붙는 표가 있다(「C (CHEST WIDTH) 53 55.5 58」).
            # 어휘에 「chest width」를 통째로 넣는 길도 있지만, 그러면 라벨이 두 토막에서
            # 한 토막이 되어 **자리로 맞추는 갈래의 칸 수가 어긋난다** — 전수로 재니 한 매장
            # 세 벌이 소매·총장을 잃었다. 그래서 어휘는 그대로 두고 이 자리에서만 덜어 낸다.
            bare = re.sub(r"width|length|단면|둘레|길이|너비", "", bare, flags=re.I)
            if len(bare) > 2:
                continue
            vals = _TRANS_NUM.findall(rest)
            if len(vals) < 2:
                continue
            rows.append((lab, vals))
            break
    def _sane(lab, vals):
        """값이 모두 그 라벨의 정상 범위 안인가."""
        lo, hi = RANGES.get(lab, (0, 10 ** 6))
        for raw in vals:
            x = fix_value(lab, raw)
            if x is None:
                try:
                    x = float(str(raw).replace(",", "."))
                except ValueError:
                    return False
            if not (lo <= x <= hi):
                return False
        return True

    # 괄호를 반만 흘린 도식 번호 — 위에서 떼지 못한 나머지.
    # OCR 이 「가슴단면 (0) 53.5 56 58.5」의 닫는 괄호를 흘리면(「(0 53.5 …」) 앞의 괄호
    # 떼기가 듣지 않아 그 줄만 값이 하나 많아지고, 칸 수가 어긋난다는 이유로 줄째 버려진다
    # (2026-09-17 blr/326: 가슴·소매길이를 잃고 대신 parse_rows 의 가슴 82 이 들어앉았다).
    # 그래서 **칸 수가 가장 흔한 수보다 딱 하나 많고 · 첫 값만 그 라벨의 범위 밖이며 ·
    # 나머지가 모두 범위 안**일 때만 앞의 하나를 덜어 낸다. 세 조건을 다 요구하므로 값이
    # 정말 하나 더 있는 표(칸이 하나 더 많은 사이즈)는 건드리지 않는다.
    if rows:
        cnt0 = Counter(len(v) for _, v in rows)
        n0 = max(cnt0, key=lambda k: (cnt0[k], k))
        if cnt0[n0] >= 2 and n0 >= 2:
            rows = [(lab, v[1:]) if (len(v) == n0 + 1 and not _sane(lab, v[:1])
                                     and _sane(lab, v[1:])) else (lab, v)
                    for lab, v in rows]
    if len(rows) < 2 or len({lab for lab, _ in rows}) < 2:
        return None
    n = len(rows[0][1])
    if n < 2:
        return None
    if any(len(v) != n for _, v in rows):
        # 칸 수가 어긋나는 줄이 있다. 예전에는 표를 통째로 버렸는데, OCR 이 칸 하나를 글자로
        # 흘리면(「허리 | 385 39.75 Al 42.25 …」의 Al 은 41) 그 줄만 한 칸 모자라게 잡혀
        # 아홉 줄짜리 표가 두 줄 때문에 날아간다(2026-09-16, 한 매장 87벌).
        # 그래서 가장 흔한 칸 수를 기준 삼아 어긋난 줄만 뺀다 — 다만 **남는 줄의 값이 모두
        # 그 라벨의 정상 범위 안일 때만**. 이 조건이 없으면 OCR 이 라벨을 흩뿌려 놓은 난장판
        # (「가슴둘레 소매통」 같은 조각들)이 가짜 표로 엮여, 가슴 119·밑단 122 처럼 단면 자리에
        # 둘레 값이 들어앉는다(같은 날 실측: 한 매장 14벌이 그 꼴로 새로 생겼다).
        # 칸 수가 처음부터 다 같은 표는 이 자리에 오지 않으므로 잘 읽던 표는 그대로다.
        cnt = Counter(len(v) for _, v in rows)
        n = max(cnt, key=lambda k: (cnt[k], k))
        if n < 2 or cnt[n] < 2:
            return None
        rows = [(lab, v) for lab, v in rows if len(v) == n]
        if len({lab for lab, _ in rows}) < 2:
            return None
        rows = [(lab, v) for lab, v in rows if _sane(lab, v)]
        if len({lab for lab, _ in rows}) < 2:
            return None
    # OCR 이 같은 줄을 두 번 뱉기도 한다 — 한 번은 깨진 채로:
    #     엉덩이둘레 S77 903.3 98.4     ← 잡음
    #     엉덩이둘레 87.7 93.3 98.4     ← 멀쩡한 줄
    # 먼저 나온 것을 쓰면 잡음이 이긴다(2026-09-17 실측). 범위 밖 값이 든 쪽을 버린다.
    def _score(lab, vals):
        """어느 줄이 더 표다운가 — (값이 다 범위 안, 사이즈가 커지며 값도 커짐)."""
        nums = []
        for raw in vals:
            x = fix_value(lab, raw)
            if x is None:
                try:
                    x = float(str(raw).replace(",", "."))
                except ValueError:
                    return (0, 0)
            nums.append(x)
        mono = all(a <= b for a, b in zip(nums, nums[1:]))
        return (int(_sane(lab, vals)), int(mono))

    best: dict[str, list[str]] = {}
    for lab, vals in rows:
        if lab not in best or _score(lab, vals) > _score(lab, best[lab]):
            best[lab] = vals
    rows = list(best.items())
    out: dict[str, list[float]] = {}
    for lab, vals in rows:
        if lab in out:
            continue
        # OCR 이 소수점을 자주 흘린다 — 「어깨 485 50 515 53」은 48.5·50·51.5·53 이다.
        # 그대로 두면 오르내리는 값으로 보여 bad_label 이 줄째로 버린다(실제로 어깨·가슴이
        # 그래서 빠졌다). 이미 쓰고 있는 값 교정 규칙을 여기서도 태운다.
        fixed = [fix_value(lab, v) for v in vals]
        out[lab] = [v if v is not None else float(raw.replace(",", "."))
                    for v, raw in zip(fixed, vals)]
    return None, out


def parse_label_run(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    """줄 단위로 먼저 보고, 한 줄에 다 뭉친 글은 통째로 본다.

    통째로만 보면 도식 위 화살표 라벨(「SHOULDER … ARMHOLE CHEST LENGTH SLEEVE」)이
    줄을 건너뛰어 머리줄이 되고, 그 아래 「SHOULDER 53 56」이 값 줄로 읽힌다
    (2026-09-05 andersson-bell: 암홀 하나만 남고 나머지가 None 이 됐다).
    """
    for cand in ([ln for ln in (text or "").splitlines() if ln.strip()], [text or ""]):
        for one in cand:
            got = _label_run_one(one)
            if got:
                return got
    return None


def _label_run_one(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    """라벨이 줄줄이 붙어 있으면 그 자리를 기준으로 삼는다 — 「SIZE」라는 낱말이 없어도.

    parse_flat 은 「SIZE」 뒤부터 읽는데, 그 낱말과 표 사이에 안내 문장이 끼면 못 읽는다
    (2026-09-05 kirsh: 「SIZE 실측 사이즈는 … (cm) 총장 어째 가슴 화장 나시총장 …
    005 48 37 …」). 라벨이 잇달아 나오는 자리가 곧 머리줄이다.
    """
    tok = re.sub(r"[|ㅣ:;=]", " ", text or "").split()
    NUMISH = rf"{NUM_CELL}(?:cm)?|{NUM_CELL}-{NUM_CELL}"
    best = None
    i = 0
    while i < len(tok):
        if re.fullmatch(NUMISH, tok[i], re.I) or not canon_label(tok[i]):
            i += 1
            continue
        # 머리줄 — 숫자가 나올 때까지 낱말을 모은다
        cols: list[str | None] = []
        j = i
        while j < len(tok) and not re.fullmatch(NUMISH, tok[j], re.I) and len(cols) <= 12:
            cols.append(canon_label(tok[j]))
            j += 1
        # 끝의 모르는 칸을 잘라내면 안 된다 — 「나시총장 나시어깨 나시가슴」도 자리는 차지한다.
        # 잘랐더니 사이즈 이름 자리가 밀려 총장 값이 어깨로 들어갔다(2026-09-05 kirsh).
        seen: set[str] = set()
        for x, c in enumerate(cols):
            if c and c in seen:
                cols[x] = None
            elif c:
                seen.add(c)
        k = len(cols)
        if len(seen) < 3 or k < 3:
            i += 1
            continue
        # 값 줄 — 사이즈 이름 하나 + 값 k 개씩 끊는다
        names, out = [], {c: [] for c in cols if c}
        pos = i + k                            # 사이즈 이름 자리(머리줄 바로 뒤)
        sticky = 0                             # 첫 줄에서 정해진 「이름 뒤 군더더기 칸」 수
        while pos + k < len(tok) + 1 and len(names) < 8:
            nm = tok[pos]
            # 사이즈 이름이 두 토막인 표가 있다 — 「48 SIZE 103.5 39.4 …」(noirer 914벌 ·
            # easy-no-easy 22 · blayer 21 · vunque 12). 「SIZE」를 값으로 읽으려다 줄이
            # 통째로 깨져, 라벨을 못 알아본 칸(「어리단면」)과 겹치면 값이 한 칸씩 밀렸다.
            # 총장 103.5 가 버려지고 허리 39.4 가 총장이 됐다(2026-09-06). 이름 다음 토막이
            # 「SIZE」·「사이즈」면 이름의 일부로 보고 건너뛴다.
            if pos + 1 < len(tok) and re.fullmatch(r"(?i)sizes?|사이즈", tok[pos + 1]):
                skip = sticky = 1
            else:
                # 표는 네모나다 — 첫 줄이 「48 SIZE …」였으면 아랫줄도 같은 자리에 칸이 있다.
                # 판독기가 그 칸을 늘 글자로 읽어 주지는 않는다: noirer 50번 줄은 「SIZE」가
                # 「5126」으로 읽혀 51.3 이 총장이 됐다(2026-09-06). 첫 줄에서 본 대로 건너뛴다.
                skip = sticky
            cells = tok[pos + 1 + skip:pos + 1 + skip + k]
            if len(cells) < k or not all(re.fullmatch(NUMISH, c, re.I) for c in cells):
                break
            if re.fullmatch(NUMISH, nm, re.I) and not _row_name_ok(nm):
                break
            names.append(nm.upper())
            for c, raw in zip(cols, cells):
                if c:
                    out[c].append(fix_value(c, re.sub(r"(?i)cm$", "", raw)))
            pos += k + 1 + skip
        out = {c: v for c, v in out.items() if any(x is not None for x in v)}
        if names and len(out) >= 2 and (best is None or len(out) > len(best[1])):
            best = (names, out)
        i = j
    return best


def parse_rows(text: str) -> dict[str, list[float]]:
    """A형 — 라벨 뒤에 숫자 묶음. crawl SIZE_RX 와 같은 발상."""
    out: dict[str, list[float]] = {}
    t = re.sub(r"[ \t]+", " ", text)
    # 라벨이 괄호 안에 든 표가 있다 — the-coldest-moment 는 「Thigh(허벅지)」로 적는데
    # OCR 이 영문을 뭉개면 「111017(허벅지) 33.5cm …」가 되어 라벨 뒤에 닫는 괄호가 남는다.
    # 그 한 글자 때문에 밑위·허벅지·밑단 세 줄을 통째로 놓쳤다(2026-09-05).
    # 단위도 넓힌다 — 매장 오타·OCR 로 「105m」처럼 c 가 빠지면 그 뒤 값이 다 끊겼다.
    for m in re.finditer(rf"({LABEL_RX.pattern})\s*(?:\([^)]{{0,24}}\))?\s*[)\]}}】』]?\s*[:：]?\s*"
                         rf"((?:{NUM}\s*(?:c?m|em|om)?\s*[/,|]?\s*){{1,8}})", t, re.I):
        c = canon_label(m.group(1))
        if not c or c in out:
            continue
        raw = re.findall(NUM, m.group(2))
        # 「Chest] 047+2s0}」 — 뒤에 붙은 깨진 라벨을 값으로 집어 47cm 를 지어냈다(mardi 4벌,
        # 2026-09-05). 실측에 0 으로 시작하는 수는 없다. 이 갈래는 가장 느슨한 마지막 수단이라
        # 의심스러운 것은 받지 않는 편이 낫다 — 없는 치수보다 틀린 치수가 나쁘다.
        raw = [x for x in raw if not re.match(r"0\d", x)]
        vals = [fix_value(c, x) for x in raw]
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
    names, cols = _from_ocr(text)
    if not cols:
        # 하나도 못 얻었을 때만 — 머리줄의 칸 번호 표식을 떼고 한 번 더(strip_header_markers 주석)
        stripped = strip_header_markers(text)
        if stripped is not text:
            n2, c2 = _from_ocr(stripped)
            if c2:
                names, cols = n2, c2
    return names, drop_model_body(text, cols)


# 「LENGTH : 66CM LENGTH : 68CM LENGTH : 71CM」 — 항목 이름이 칸마다 되풀이되는 표(포스센스티브 28벌).
# 느슨한 갈래들이 이 꼴을 읽으면 칸이 밀린다: 첫 칸의 LENGTH 가 「LENGIA - O0OCM」로 깨진 표에서
# 2사이즈 총장 65 와 1사이즈 가슴 58 이 한 벌 값으로 섞였다(2026-09-24 OCR 전수). 이 꼴은 칸 경계를
# **항목 이름으로 셀 수 있다** — 깨진 이름도 한 칸이다. 값이 깨진 칸이 있는 줄은 그 항목만 버리고,
# 칸 수가 다른 줄도 버린다(어느 칸이 빠졌는지 모른다). 이 꼴이 보이면 다른 갈래로 넘기지 않는다.
_LV_CELL = re.compile(r"([A-Za-z][A-Za-z ]{1,18}?|[가-힣]{1,6})\s*[:\-=]\s*([0-9OoIl.,]{1,6})\s*cm", re.I)
_LV_WORDS = {"length": "총장", "total length": "총장", "chest": "가슴", "shoulder": "어깨", "sleeve length": "소매길이",
             "sleeve": "소매길이", "waist": "허리", "hip": "엉덩이", "thigh": "허벅지", "rise": "밑위", "hem": "밑단"}


# 모델 몸 치수의 말 — 「HEIGHT: 174cm HEIGHT: 184cm / WAIST : 58cm WAIST : 71cm」(crump 두 모델). 비슷한 말
# 맞추기가 HEIGHT 를 THIGH 로 읽어 허벅지 174 가 됐다(전후 전수). 이 말이 든 줄과 그 곁 줄은 모델 묶음이다.
_LV_BODY = re.compile(r"(?i)height|weight|bust|shoes?|model|키|몸무게|신장|체중|모델")


def _lv_label(raw: str) -> str | None:
    import difflib
    w = re.sub(r"\s+", " ", raw.strip().lower())
    if _LV_BODY.search(w):
        return None
    c = canon_label(w)
    if c:
        return c
    m = difflib.get_close_matches(w, list(_LV_WORDS), n=1, cutoff=0.8)
    return _LV_WORDS[m[0]] if m else None


def parse_label_colon_rows(text: str) -> tuple[bool, list[str] | None, dict[str, list[float]]]:
    """(이 꼴인가, 사이즈 이름, 칸). 이 꼴이 아니면 (False, None, {})."""
    lines = (text or "").splitlines()
    body_at = {i for i, ln in enumerate(lines) if _LV_BODY.search(ln)}
    rows = []
    for i, ln in enumerate(lines):
        cells = _LV_CELL.findall(ln)
        if len(cells) < 2:
            continue
        if {i - 1, i, i + 1} & body_at:
            continue                       # 모델 묶음(키·몸무게 줄과 그 곁)
        rows.append(cells)
    if len(rows) < 2:
        return False, None, {}             # 이 꼴이 아니다 — 다른 갈래(모델 치수 걷기가 있다)에 맡긴다
    n = collections.Counter(len(r) for r in rows).most_common(1)[0][0]
    out: dict[str, list[float]] = {}
    for cells in rows:
        if len(cells) != n:
            continue                       # 칸 수가 다르다 — 어느 칸이 빠졌는지 모른다
        labs = [_lv_label(a) for a, _ in cells]
        lab = collections.Counter(x for x in labs if x).most_common(1)
        if not lab or lab[0][1] * 2 < n or lab[0][0] in out:
            continue
        vals = []
        for _, v in cells:
            v = v.replace(",", ".")
            vals.append(float(v) if re.fullmatch(r"\d{1,3}(?:\.\d)?", v) else None)
        if any(x is None for x in vals):
            continue                       # 깨진 칸이 있다 — 이 항목은 통째로 버린다(칸을 밀지 않는다)
        out[lab[0][0]] = vals
    m = re.search(r"(?im)^.*?((?:\b\w{1,3}\s*size\b\s*){2,})", text or "")
    names = None
    if m:
        got = [g.upper() for g in re.findall(r"(?i)\b(\w{1,3})\s*size\b", m.group(1))]
        # 머리줄 이름도 OCR 이 틀린다 — 옵션이 1·2 인 바지의 머리가 「4 SIZE 9 SIZE」로 읽혔다(포스센스티브).
        # 이어지는 숫자(0·1·2)이거나 표준 이름일 때만 받는다. 아니면 비워 두고 옵션에서 채우게 한다.
        digits = all(g.isdigit() for g in got)
        seq = digits and [int(g) for g in got] == list(range(int(got[0]), int(got[0]) + len(got)))
        std = all(g in {"XS", "S", "M", "L", "XL", "XXL", "F", "FREE", "OS"} for g in got)
        if len(got) == n and (seq or std):
            names = got
    return True, names, out


# 「1사이즈 (단면)가슴-62CM 어깨-55CM 기장-64CM 소매장-63CM」 — 한 줄이 한 사이즈이고 줄 안에 「항목-값」이
# 이어지는 표(포스센스티브 설명글 8벌). 사이즈 이름이 제 줄에 따로 서고 값이 다음 줄에 오기도 한다.
# parse_label_colon_rows 는 줄마다 **같은 항목**이 되풀이되는 꼴이라 이 줄을 버리고, 그 꼴로 판정된 뒤에는
# 다른 갈래로 넘기지 않아 이 표가 통째로 빠졌다. 줄 첫머리의 사이즈 이름, 알아보는 항목 둘 이상,
# **모든 줄의 항목 차례가 같을 것**, 줄 둘 이상 — 이 넷이 다 맞을 때만 받는다.
_SR_HEAD = re.compile(r"^\s*(\d{1,3}|XXS|XS|S|M|L|XL|XXL|XXXL|FREE|F)\s*(?:사이즈|size)\b\s*(?:\(\s*(?:단면|cm)\s*\))?\s*(.*)$", re.I)
# 구분자 없이 「밑단 48cm」로 적힌 칸도 같은 줄에 섞인다(포스센스티브 버뮤다). 한글 항목만 구분자를 뺄 수 있다.
_SR_CELL = re.compile(r"([가-힣]{1,6}\s*(?:[:\-=：]\s*)?|[A-Za-z][A-Za-z ]{1,18}?\s*[:\-=：]\s*)(\d{1,3}(?:\.\d)?)\s*cm", re.I)


def parse_size_rows(text: str) -> tuple[list[str], dict[str, list[float]]] | None:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    rows = []                              # (이름, [(라벨, 값)])
    i = 0
    while i < len(lines):
        m = _SR_HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        rest = m.group(2)
        cells = _SR_CELL.findall(rest)
        if len(cells) < 2 and i + 1 < len(lines) and not _SR_HEAD.match(lines[i + 1]):
            nxt = lines[i + 1]
            if not _LV_BODY.search(nxt):
                cells = _SR_CELL.findall(nxt)
                if len(cells) >= 2:
                    i += 1
        i += 1
        if len(cells) < 2 or _LV_BODY.search(rest):
            continue
        labs = [_lv_label(re.sub(r"[:\-=：\s]+$", "", a)) for a, _ in cells]
        if any(x is None for x in labs) or len(set(labs)) != len(labs):
            continue                       # 모르는 항목이나 같은 항목이 둘 — 이 꼴이 아니다
        row = (m.group(1).upper(), list(zip(labs, (float(v) for _, v in cells))))
        if row not in rows:                # 설명글과 상세글에 같은 표가 두 번 온다 — 똑같은 줄은 접는다
            rows.append(row)
    if len(rows) < 2:
        return None
    order = [lab for lab, _ in rows[0][1]]
    if any([lab for lab, _ in r] != order for _, r in rows):
        return None                        # 줄마다 항목이 다르다 — 어느 칸이 어디인지 모른다
    names = [nm for nm, _ in rows]
    if len(set(names)) != len(names):
        return None                        # 같은 사이즈에 값이 둘 — 어느 쪽이 맞는지 모른다
    digits = all(n.isdigit() for n in names)
    seq = digits and [int(n) for n in names] == list(range(int(names[0]), int(names[0]) + len(names)))
    std = not digits
    cols = {lab: [r[j][1] for _, r in rows] for j, lab in enumerate(order)}
    return (names if seq or std else None), cols


def _from_ocr(text: str) -> tuple[list[str] | None, dict[str, list[float]]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    sr = parse_size_rows(text)
    if sr:
        clean = clean_ocr(sr[1])
        if len(clean) >= 2:
            return sr[0], clean
    is_lv, lv_names, lv = parse_label_colon_rows(text)
    if is_lv:
        clean = clean_ocr(lv) if lv else {}
        return (lv_names, clean) if len(clean) >= 2 else (None, {})
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
    pair = parse_named_pairs(lines)
    if pair and len(pair[1]) >= 2:
        return pair[0], clean_ocr(pair[1])
    # 라벨 하나가 깨져 칸이 밀린 표 — 자리로 맞춘다(parse_slots 주석)
    for cand in (lines, resegment(text)):
        slot = parse_slots(cand)
        if slot and len(slot[1]) >= 2:
            return slot[0], clean_ocr({k: list(vs) for k, vs in slot[1].items()})
    # 가장 느슨한 읽기 — 앞의 것이 다 실패했을 때만. 검사를 통과한 뒤에도 라벨이
    # 둘은 남아야 받는다(한 줄만 살아남는 것은 대개 잘못 읽은 것이다).
    run = parse_label_run(text)
    if run:
        clean = clean_ocr(run[1])
        if len(clean) >= 2:
            return run[0], clean
    # 라벨이 세로로 서는 표 — 「총장 | 63 64 65 66」(parse_transposed 주석).
    # **맨 끝**이다. 앞 차례에 두었더니 더 잘 읽던 갈래를 가로채, 얻은 645벌 옆에서
    # 251벌이 라벨을 잃었다(siyazu 107·blr 37·andersson-bell 33 — 총장·허리가 통째로
    # 빠졌다). 이 꼴은 다른 갈래가 하나도 못 읽을 때만 쓸모가 있다(2026-09-15 실측).
    rows_out = clean_ocr(parse_rows(text))
    trans = parse_transposed(lines)
    if trans:
        tclean = clean_ocr(trans[1])
        # 라벨을 더 많이 얻는 쪽을 쓴다. 비기면 원래 쓰던 갈래(parse_rows)를 남긴다 —
        # 무조건 새 갈래를 쓰게 했더니 siyazu 「Length) | Hip 50cm 52cm」처럼 한 줄에
        # 두 칸이 얹힌 표에서 엉덩이가 빠졌다(2026-09-15, 107벌).
        if len(tclean) >= 2 and len(tclean) > len(rows_out):
            return trans[0], tclean
    return None, rows_out


_MODEL_H = re.compile(r"(?:height|키)\s*\D{0,3}(\d{3})\s*cm", re.I)
_MODEL_PART = re.compile(r"\b(bust|waist|hip|가슴|허리|엉덩이)\s*\D{0,3}(\d{2,3}(?:\.\d)?)\s*cm", re.I)
_MODEL_KO = {"bust": "가슴", "waist": "허리", "hip": "엉덩이",
             "가슴": "가슴", "허리": "허리", "엉덩이": "엉덩이"}


def drop_model_body(text: str, st: dict[str, list]) -> dict[str, list]:
    """모델의 몸 치수를 옷 치수로 적은 것을 뺀다.

    상세 그림에는 사이즈표 아래에 모델 정보가 붙는다:

        ULYANA
        Height 173cm  Bust 80cm  Waist 62cm  Hip 89cm

    표에 허리 칸이 없는 옷인데 이 62 가 「허리」로 들어갔다(crank, 2026-09-15에 그림을
    눈으로 대조하다 찾았다 — 표는 Total length 32·Chest 39·Hem 37.5 뿐이었다).
    62 는 허리 범위 안이고 값도 하나뿐이라 어떤 검사에도 안 걸린다. 그림을 봐야 보인다.

    「키가 적힌 한 줄 안에 가슴/허리/엉덩이가 둘 이상」일 때만 그 줄을 모델 정보로 보고,
    그 라벨의 값이 **하나뿐이고** 그 줄의 수와 같으면 뺀다. 옷 단면은 몸 둘레의 절반이라
    이 값들은 애초에 두 배로 적혀 있다. 창고 전수에서 118벌이었다(가슴 54·허리 64).
    """
    body: dict[str, set] = {}
    for ln in (text or "").splitlines():
        if not _MODEL_H.search(ln):
            continue
        ps = _MODEL_PART.findall(ln)
        if len(ps) >= 2:
            for w, v in ps:
                body.setdefault(_MODEL_KO[w.lower()], set()).add(float(v))
    if not body:
        return st
    out = dict(st)
    for lab, nums in body.items():
        v = out.get(lab)
        if not v:
            continue
        real = [x for x in v if isinstance(x, (int, float))]
        if len(real) == 1 and real[0] in nums:
            del out[lab]
    return out


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
        for d in iter_jsonl(p):
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


FLOOR_10 = {"총장", "가슴", "어깨", "허리", "허벅지", "밑위", "뒤밑위", "엉덩이", "암홀", "화장"}


def sleeve_to_hwajang(sizes: dict[str, list]) -> dict[str, list]:
    """소매길이가 통째로 80cm 를 넘으면 그건 화장이다 — 라벨을 고쳐 단다.

    어깨선에서 소매끝까지는 사람 팔 길이를 못 넘는다(어른 60~65cm). 어깨가 내려온 옷은
    솔기가 아래로 내려가므로 그 자리에서 잰 소매는 오히려 **짧아진다**. 그러니 80cm 넘는
    「소매」는 뒷목 중심에서 잰 것, 즉 화장이다. 래글런은 어깨 솔기가 없어 매장이 그렇게
    잴 수밖에 없는데, 적기는 그냥 「소매」라고 적는다:

        horlisun  Sugarpine **Raglan** Sweatshirt  총장 66~70 · 가슴 60~66 · 소매 85.5~90.5
        heritagefloss 8070 PL BLOUSON            length 70~73 · chest 64~66.5 · sleeve 85~89
        phyps     H-Tech Wind Shield Track        sleeve 79
        1993studio 스몰 로고 윈드브레이커            총장 72.5 · 가슴 73 · 소매 83

    화장과 소매길이는 20cm 넘게 다른 치수라(size_labels.json 머리말), 화장을 소매길이 자리에
    두면 같은 옷끼리 대는 순간 엉뚱한 옷이 딸려 나온다.

    **값이 하나라도 80 아래면 손대지 않는다** — 섞인 표는 한 칸이 잘못 읽힌 것일 수 있고,
    그건 blank_lone_jump 이 볼 일이다. 이미 화장 줄이 있는 표도 두 줄이 겹치므로 둔다
    (전수 10만 벌에서 다섯 벌뿐).

    반대쪽(화장이 40 아래)은 **고치지 않는다**. 열어 보니 반팔 래글런이었다 —
    covernat 「우먼 스트라이프 하프 니트」 화장 28~29.5 · afterpray 링거 티셔츠 34.5~36.5.
    뒷목에서 잰 반팔은 실제로 그만큼이다. 검사 쪽 범위를 넓히는 게 맞다.
    """
    sl = sizes.get("소매길이")
    if not sl or "화장" in sizes:
        return sizes
    nums = [v for v in sl if isinstance(v, (int, float))]
    if not nums or min(nums) < 80:
        return sizes
    out = {("화장" if c == "소매길이" else c): v for c, v in sizes.items()}
    return out


def blank_lone_jump(sizes: dict[str, list]) -> dict[str, list]:
    """한 라벨만 유독 한 칸 크게 뛰면 그 칸을 비운다.

    같은 표 안의 사이즈는 칸마다 1~3cm 씩 고르게 커진다. 한 라벨이 **다른 라벨들의
    걸음**보다 여섯 배를 한 번에 뛴다면, 사이즈가 달라진 게 아니라 그 칸 하나가 틀린 것이다:

        어깨 [58, 61, 63, 65] · 가슴 [56, 60, 62, 64] · 총장 [58, 70, 72, 74]
                                                            ↑ 68 을 58 로 읽었다
        허리 [38,40,43] · 허벅지 [36,36,37.5] · 밑위 [35,36,37] · 엉덩이 [32.9, 56.5, 59.5]
                                                                        ↑ 52.9 를 32.9 로

    기준 걸음은 **다른 라벨들에서** 가져온다. 예전엔 그 라벨 제 걸음의 중앙값을 썼는데,
    값이 셋뿐이면 걸음이 둘이라 중앙값이 「튄 걸음과 멀쩡한 걸음의 한가운데」가 되어
    제가 저를 가려 줬다(alvinclo 엉덩이 [32.9,56.5,59.5] — median(23.6, 3.0)=13.3,
    여섯 배는 79.8 이라 아무것도 안 걸렸다, 2026-09-15).

    **라벨이 다 같이 뛰면 건드리지 않는다** — 그건 진짜 치수 차이다. S 만 여성 핏인
    유니섹스 표가 그렇고(사람 지적 2026-09-15: 「s만 동떨어진건 여성 사이즈여서 그런가봐」),
    상·하의가 한 표에 든 세트도 그렇다.

    뛰는 자리가 가운데면 앞뒤 어느 쪽이 틀렸는지 알 수 없어 둔다. 맨 앞·맨 뒤만 고친다.
    **값이 둘뿐이면 라벨째 버린다** — 맨 앞이기도 하고 맨 뒤이기도 해서 어느 쪽이 틀렸는지
    가릴 길이 없다. 브랜드 중앙값으로 갈라 봤더니 1993studio 총장 [53.8, 95.8] 은 맞히고
    [51.0, 93.5] 는 틀렸다(둘 다 중앙값 72.5 에서 21cm 언저리). 반반 맞히는 잣대는
    없는 것만 못하다 — 없는 치수보다 틀린 치수가 나쁘다.
    """
    idx = {c: [i for i, x in enumerate(v) if isinstance(x, (int, float))] for c, v in sizes.items()}
    usable = {c: [sizes[c][i] for i in ii] for c, ii in idx.items() if len(ii) >= 2}
    if len(usable) < 2:
        return sizes
    steps_of = {}
    for c, vs in usable.items():
        st = [round(vs[i + 1] - vs[i], 2) for i in range(len(vs) - 1)]
        if any(x < 0 for x in st):
            continue                      # 오르내리는 것은 bad_label 이 본다
        steps_of[c] = st
    if len(steps_of) < 2:
        return sizes
    # 기준 걸음 — **표 전체**의 걸음에서 가져온다. 튄 걸음 하나가 섞여도 중앙값은 안 흔들린다.
    # 제 걸음만 보면 값이 셋뿐일 때 중앙값이 「튄 걸음과 멀쩡한 걸음의 한가운데」가 되어
    # 제가 저를 가려 준다(alvinclo 엉덩이 [32.9,56.5,59.5] — median(23.6,3)=13.3).
    allpos = [x for st in steps_of.values() for x in st if x > 0]
    if len(allpos) < 3:
        return sizes
    ref = statistics.median(allpos)
    if ref <= 0:
        return sizes
    strong = {c: [i for i, x in enumerate(st) if x >= ref * 6 and x >= 8]
              for c, st in steps_of.items()}
    strong = {c: v for c, v in strong.items() if v}
    if len(strong) != 1:
        return sizes
    c, big = next(iter(strong.items()))
    if len(big) != 1:
        return sizes
    i = big[0]
    # **같은 자리에서 다른 라벨도 함께 뛰면** 그건 진짜 치수 차이다 — 건드리지 않는다.
    # S 만 여성 핏인 유니섹스 표가 그렇다(사람 지적 2026-09-15: 「s만 동떨어진건 여성
    # 사이즈여서 그런가봐」 — 어깨 36.5·총장 52 는 실제 여성 치수대였다). 함께 뛰는지는
    # 무르게 본다(기준의 세 배·5cm) — 같이 뛰는 라벨의 걸음이 늘 똑같이 크진 않다.
    for d, st in steps_of.items():
        if d == c or i >= len(st):
            continue
        if st[i] >= ref * 3 and st[i] >= 5:
            return sizes
    vs = usable[c]
    if len(vs) == 2:
        # 맨 앞이기도 하고 맨 뒤이기도 하다 — 어느 쪽이 틀렸는지 가릴 길이 없다.
        # 브랜드 중앙값으로 갈라 봤더니 1993studio 총장 [53.8,95.8] 은 맞히고
        # [51.0,93.5] 는 틀렸다(둘 다 중앙값 72.5 에서 21cm 언저리). 반반 맞히는 잣대는
        # 없느니만 못하다 — 없는 치수보다 틀린 치수가 나쁘다. 라벨째 버린다.
        out = {k: x for k, x in sizes.items() if k != c}
        return out or sizes
    if i == 0:
        pos_in_vs = 0
    elif i == len(vs) - 2:
        pos_in_vs = len(vs) - 1
    else:
        return sizes                      # 가운데 — 앞뒤 어느 쪽이 틀렸는지 모른다
    out = dict(sizes)
    v = list(sizes[c])
    v[idx[c][pos_in_vs]] = None
    out[c] = v
    return out


def drop_strays(brand: str, c: str, vs: list[float], med: dict) -> list[float]:
    """한 라벨 안에서 무리를 벗어난 값만 뺀다 — 라벨을 통째로 버리면 멀쩡한 값까지 잃는다.
    같은 옷의 사이즈끼리는 몇 cm 차이지 배로 벌어지지 않는다(실측 2026-09-05: 라벨×상품
    53,899개 중 1.2배 이내 97.1% · 1.6배 이내 99.81%). 1.6배를 넘게 벌어졌을 때만,
    그 브랜드 그 라벨의 중앙값에 가까운 쪽을 남긴다."""
    # 옷에서 10cm 안 되는 총장·가슴·어깨는 없다. 실측으로 확인했다(2026-09-05, 값 186,138개
    # 중 이 라벨들에서 10 미만은 0개). 그림 옆 번호를 값으로 집는 사고를 여기서 막는다 —
    # andersson-bell 사이즈가이드의 「SLEEVE (OUTER) 8」이 소매길이 8cm 가 됐다.
    # 소매길이·밑단·소매단은 뺀다 — 민소매 진동이나 소매부리는 실제로 작다.
    if c in FLOOR_10:
        vs = [v for v in vs if not isinstance(v, (int, float)) or v >= 10]
        if not vs:
            return []
    # OCR 표는 빈 칸을 None 으로 둔다 — 숫자만 놓고 본다
    pos = [v for v in vs if isinstance(v, (int, float)) and v > 0]
    if len(pos) < 2 or max(pos) / min(pos) <= 1.6:
        return vs
    m = med.get((brand, c))
    if m is None:
        return vs
    keep = [v for v in vs if not isinstance(v, (int, float)) or m / 1.6 <= v <= m * 1.6]
    return keep if any(isinstance(v, (int, float)) for v in keep) else vs


def column_count(sizes: dict[str, list], names: int = 0) -> int:
    """라벨마다 칸 수가 다를 때, 표를 몇 칸으로 볼지 고른다.

    예전에는 가장 짧은 라벨에 맞췄다. 그랬더니 「모델 착용 치수」한 줄이 표 전체를 한 칸으로
    끌어내렸다 — nick-nicole 의 「가슴 35·37 / 총장 51·52 / waist 58 / hip 87」이 앞칸만 남아
    M·L 두 사이즈짜리 옷이 앱에서 프리사이즈로 섰다(2026-09-12, 판매중 옷 309벌).
    앞칸만 남기는 건 없는 것보다 나쁘다 — M 의 가슴을 「이 옷의 유일한 치수」라고 적는 꼴이다.

    그래서 「남는 값이 가장 많은 칸 수」를 고른다: 칸 수 n 을 택하면 n 칸 이상인 라벨만 남으므로
    남는 값은 n × (n 칸 이상인 라벨 수)다. 이 값이 가장 큰 n 을 고른다. 비기면 작은 쪽이다 —
    「총장 75 / 소매 60·3」에서 뒤엣것을 사이즈로 믿고 총장을 버리면 안 된다(그 3 은 오차 안내다).
    값이 더 남을 때만 칸이 늘고, 가장 짧은 길이도 후보라 지금보다 값이 줄어드는 일은 없다.
      가슴2 총장2 허리1     → n=2 (값 4 > 3) · 허리는 뺀다
      총장3 어깨3 가슴3 밑단3 힙1 → n=3 (12 > 5)
      총장1 어깨1 가슴1 소매2   → n=1 (4 > 2) · 소매의 뒷칸은 오차 안내다(fabrega)

    다만 소수가 다수를 이기지는 못한다 — 남는 라벨이 빠지는 라벨보다 적으면 그 n 은 쓰지 않는다.
    noice 후드의 「가슴 27·28.5·30·32 / 허리 22·22.5·23·24 / 어깨 57 / 소매길이 60 / 총장 58.5」에서
    앞 둘은 매장 공용 치수표고 뒤 셋이 이 옷의 실측이다. 값 수만 따지면 공용표가 이겨
    옷의 총장·어깨·소매를 버리게 된다(2026-09-12 표본 확인). 가장 짧은 길이는 늘 이 조건을
    지나가니 막다른 곳은 없다.
    """
    lens = sorted({len(v) for v in sizes.values()})
    best, best_score = lens[0], -1
    for n in lens:
        keep = sum(1 for v in sizes.values() if len(v) >= n)
        if keep < len(sizes) - keep:
            continue
        score = n * keep
        # 비기면 먼저 본 작은 n 이 남는다 — 다만 매장이 스스로 적은 사이즈 이름 수와 맞는
        # 칸이 있으면 그쪽이다. 「shoulder 4 · sleeve 4 · sleevewidth 3 · length 4」에서
        # 점수가 12 대 12 로 비겨 세 칸이 남고, XS·S·M·L 네 사이즈 옷의 L 이 통째로 사라졌다
        # (pushbutton 4벌, 2026-09-13). 매장 표의 한 칸이 짧다고 나머지 칸을 버릴 이유가 없다.
        if score > best_score or (score == best_score and n == names):
            best, best_score = n, score
    return best


def _monotone(a: list[float]) -> bool:
    return all(x <= y for x, y in zip(a, a[1:])) or all(x >= y for x, y in zip(a, a[1:]))


def drop_lone_outlier(vs: list, thr: float = 0.15) -> list:
    """한 라벨에서 혼자 튀는 칸 하나를 비운다 — 판독기가 그 칸만 잘못 읽은 것이다.

    같은 옷의 사이즈가 커지면 치수도 커진다(줄어드는 표는 없다). 그래서 값이 순서대로면
    손대지 않는다. 순서가 깨졌을 때만, 그 칸 하나를 빼서 순서가 잡히고 그 값이 나머지의
    중앙값에서 15% 넘게 벗어날 때 비운다. 뺄 후보가 여럿이면 남는 값들이 가장 촘촘해지는
    쪽을 고른다 — 「소매길이 57 · 9 · 61」에서 9 를 빼야지 61 을 빼면 안 된다(2026-09-06).

    잘못 읽은 칸 68개를 비운다: noirer 35 · loeuvre 8 · easy-no-easy 5 · frizmworks 5.
    「총장 109 · 110 · 71.1」(dnsr 플레어 데님) · 「어깨 49.5 · 31 · 52.5」(noirer 가디건)
    같은 것들이다. drop_strays 의 1.6배 잣대로는 아슬아슬하게 살아남는다.
    값을 지어내지 않고 비우기만 한다 — 없는 치수보다 틀린 치수가 나쁘다.
    """
    idx = [i for i, v in enumerate(vs) if isinstance(v, (int, float)) and v > 0]
    if len(idx) < 3 or _monotone([vs[i] for i in idx]):
        return vs
    best = None
    for i in idx:
        others = [vs[j] for j in idx if j != i]
        if not _monotone(others):
            continue
        m = statistics.median(others)
        if m <= 0 or abs(vs[i] - m) / m < thr:
            continue
        spread = max(others) / min(others)
        if best is None or spread < best[0]:
            best = (spread, i)
    if best is None:
        return vs
    out = list(vs)
    out[best[1]] = None
    return out


_TOTAL_ALIASES = {"총기장", "총장", "총길이", "전체기장", "length", "totallength"}
_SLEEVE_IN_BODY = re.compile(r"(?:소매|팔)\s*기장")
_MODEL_AT = re.compile(r"(?i)\bmodel\b|모델")
_BODY_WORD = re.compile(r"(?i)(bust|chest|waist|hips?|가슴|허리|엉덩이|힙)\s*[:\-]?\s*(\d{2,3}(?:\.\d+)?)")
# 모델 구간이라는 표지 — 키. 「Model(M) : 185cm」처럼 키라는 말 없이 수만 적는 매장도 있다.
_MODEL_HEIGHT = re.compile(r"(?i)(?:height|hegit|신장|키)\s*[:\-]?\s*1[5-9]\d|\b1[5-9]\d(?:\.\d)?\s*cm")
_SIZE_WORD = re.compile(r"(?i)size|사이즈")
_HEIGHT_WORD = re.compile(r"(?i)(?:height|hegit|신장|키)\s*[:\-]?\s*1[5-9]\d(?:\.\d)?\s*(?:cm)?")
_BODY_KEY_EN = {"bust": "bust", "chest": "bust", "waist": "waist", "hip": "hip", "hips": "hip"}
_BODY_KEY = {**_BODY_KEY_EN, "가슴": "bust", "허리": "waist", "엉덩이": "hip", "힙": "hip"}


def clean_html_table(st: dict, body: str) -> dict:
    """크롤러가 저장해 둔 HTML 표에서, 옷 치수가 아닌 것이 섞인 세 꼴을 걷는다.

    아스트라가 가린 판으로 200벌을 옮겨 적어 준 것과 대 보다가 찾았다(2026-09-23).
    셋 다 수확된 표 자체에 흔적이 남아 있어서, 다시 걷지 않고 여기서 고칠 수 있다.

    ① 번호 목록 — 「1.허리 2.엉덩이 3.허벅지 … 6.총장」을 라벨 뒤 숫자로 읽어
       엉덩이 3 · 허벅지 4 · 밑위 5 · 밑단 6 · 총장 44(첫 사이즈 이름 44(28))가 됐다.
       값 하나짜리 칸들이 1씩 느는 작은 정수면 표가 아니다 — 통째로 버린다(legacy 60벌 전부).
    ② 「소매기장」의 「소매」가 잘려 맨 「기장」만 남은 것 — 총장으로 읽혀 **소매 값이 총장
       자리에 앉고 진짜 총기장은 밀려났다**(lememe 「소매기장 54 · 총기장 63」→ 총장 54).
       총장 이름이 따로 있는 표에서만 본다. 원문에 소매기장·팔기장이 있으면 소매로 옮기고,
       없으면 버린다 — 밑위기장·랩기장·끈기장·앞중심기장·프린지기장도 같은 꼴로 잘려 들어와
       있었다(창고 전수 2,219벌, 매장마다 한 벌씩 원문과 대 봤다).
    ③ 모델 몸 치수 — 「Model(M): 185cm / CHEST 34" / WAIST 28" / HIP 37"」이 표에
       waist 28 · hip 37 로 들어가 윗옷에 허리·엉덩이가 생겼다(rest-recreation · nick-nicole ·
       mooneed …). 원문의 모델 구간(아래 주석)에만 나오고 옷 표에는 안 나오는 부위·수면 뺀다.
       모델 구간을 못 찾으면, 한글 표에 끼어 있고 칸 수가 표보다 모자란 영문 몸 부위만 뺀다 —
       영문으로 표를 적는 매장(slowacid 「waist 39·41·43」)은 칸이 가득 차 있어 안 걸린다.
    """
    keys = [k for k in st if not str(k).startswith("_")]

    singles = sorted(v[0] for k in keys
                     if isinstance(st[k], list) and len(st[k]) == 1
                     for v in [st[k]]
                     if isinstance(v[0], (int, float)) and float(v[0]).is_integer() and v[0] <= 12)
    # 번호는 1~3 에서 시작해 넷 넘게 잇닿는다. 「암홀 10 · 소매단 11 · 소매통 12」 같은
    # 진짜 한 벌 표가 걸리지 않도록 둘 다 요구한다.
    best, start, run = 0, None, 1
    for a, b in zip(singles, singles[1:]):
        run = run + 1 if b == a + 1 else 1
        if run > best:
            best, start = run, b - run + 1
    if best >= 4 and start is not None and start <= 3:
        return {}

    out = dict(st)
    norm = {re.sub(r"[\s()（）:：]", "", str(k)).lower(): k for k in keys}
    if "기장" in norm and set(norm) & (_TOTAL_ALIASES - {"기장"}):
        raw = norm["기장"]
        v = out.pop(raw)
        if _SLEEVE_IN_BODY.search(body or "") and "소매기장" not in norm:
            out["소매기장"] = v

    width = max((len(v) for v in out.values() if isinstance(v, list)), default=0)
    # 모델 구간 — 「키 → 가슴·허리·엉덩이」가 바짝 붙어 나온다. 키가 없으면 모델 줄이 아니고,
    # 키 뒤라도 「사이즈」라는 말이 나오면 거기서 끊는다 — anotheroffice 는 「모델 착용
    # 사이즈 182cm / 02사이즈 … 사이즈 가이드 01SIZE 총길이 70/ 어깨 54/ 가슴 57.5」라
    # 넓게 잡은 창 안에 **진짜 옷 표**가 들어와, 옷 가슴이 모델 치수로 오인됐다(전수 408벌).
    body = body or ""
    spans: list[tuple[int, int]] = []
    heights = [h for m in _MODEL_AT.finditer(body)
               for h in [_MODEL_HEIGHT.search(body, m.start(), m.start() + 160)] if h]
    # 「키 172」처럼 키라는 말이 붙은 줄은 앞에 model 이 없어도 모델 줄이다 — 옷 표에는 키가 없다.
    # j-koo 는 같은 모델 줄을 「MODEL SIZE HEIGHT 172CM …」와 「구매해주세요. HEIGHT 172CM …」로
    # 두 번 싣는다. 뒤엣것을 구간 밖으로 세면 몸 치수가 옷 표에서도 나온 것처럼 보인다.
    heights += list(_HEIGHT_WORD.finditer(body))
    for h in heights:
        a, b = h.end(), min(len(body), h.end() + 80)
        cut = _SIZE_WORD.search(body, a, b)
        spans.append((a, cut.start() if cut else b))
    # 부위·수 쌍마다 「모델 구간 밖에서도 나오는가」를 센다. 모델 구간에만 나오는 쌍만 몸 치수다 —
    # not-your-rose 는 옷 표 「… Hem 105 / Hip 86」과 모델 「Hips 86」이 우연히 같은 수라, 구간 안에
    # 보였다는 것만으로 빼면 옷 치수가 지워진다.
    inside: dict[str, set] = {}
    outside: dict[str, set] = {}
    for m in _BODY_WORD.finditer(body):
        part, n = _BODY_KEY[m.group(1).lower()], float(m.group(2))
        (inside if any(a <= m.start() < b for a, b in spans) else outside).setdefault(part, set()).add(n)
    has_ko = any(re.search(r"[가-힣]", str(k)) for k in out)
    for k in [k for k in out if not str(k).startswith("_")]:
        name = re.sub(r"\s", "", str(k)).lower()
        kk = _BODY_KEY.get(name)
        v = out[k]
        if not kk or not isinstance(v, list):
            continue
        nums = [x for x in v if isinstance(x, (int, float))]
        if spans:
            if nums and all(x in inside.get(kk, ()) and x not in outside.get(kk, ()) for x in nums):
                del out[k]
        elif name in _BODY_KEY_EN and has_ko and len(v) < width:
            del out[k]
    return out


def normalize_html(st: dict, brand: str = "", girth_keys: set | None = None,
                   med: dict | None = None) -> dict[str, list[float]]:
    if sweep_all(st):
        return {}
    st = drop_inches(st)
    def _spec(k) -> int:
        """이름이 얼마나 또렷한가 — 작을수록 먼저. 「XX단면」이 맨 앞이다.

        매장이 「밑단단면 36.5」와 「밑단 8.5」를 나란히 적는다. 뒤엣것은 밑단 시보리 높이지
        옷의 밑단 너비가 아니다. 정식 라벨 그대로인 이름을 앞세웠더니 카디건 밑단이 8.5cm 가
        됐다(2026-09-12, 표본 다섯 벌에서 잡았다). 「단면」이라 적어 준 쪽이 우리가 쓰는
        치수(단면) 그 자체라 가장 또렷하다.
        """
        c = canon_label(k)
        n = re.sub(r"[\s()（）:：]", "", str(k)).lower()
        if n == f"{c}단면":
            return 0
        return 1 if n == c else 2

    def _n(k) -> int:
        v = st[k]
        return len(v) if isinstance(v, list) else 0

    # 한 표에 같은 정식 라벨로 떨어지는 칸이 둘 있을 때 누가 그 자리를 갖는가.
    # 예전에는 먼저 나온 칸이었다. 그래서 「소매통 24 / 소매단 20」에서 팔뚝 둘레가 소매단
    # 자리에 앉았다 — 다른 치수를 그 이름으로 적는 건 비워 두는 것보다 나쁘다(창고 전수 127벌).
    #
    # 고르는 잣대는 「그 표가 몇 칸짜리인가」다. 표의 칸 수는 column_count 와 같은 셈으로
    # 정한다(남는 값이 가장 많은 칸 수). 그 칸 수에 맞는 후보가 먼저고, 같으면 이름이 정식
    # 라벨 그대로인 쪽, 그래도 같으면 원문 차례다.
    #   length 3칸 · shoulder 3칸 · 총장 1칸 · 가슴단면 1칸  → 표는 3칸, length 가 이긴다
    #   허리 1 · 엉덩이 1 · 밑단 1 · 총장 1 · waist 2 · hip 2 → 표는 1칸, 한글 쪽이 이긴다
    #     (그 waist 57·59 는 사이즈가 아니라 권장 체촌이다 — 프리사이즈 치마였다)
    #   소매통 2 · 소매단 2                                  → 칸 수가 같으니 소매단이 이긴다
    # 값이 범위 밖이라 한 칸도 안 남으면 다음 후보로 넘어간다 — 그 넘어감을 없앴다가
    # 「기장 20 / length 57」에서 총장을 통째로 잃었다(2026-09-12, 표본에서 잡았다).
    # 표가 몇 칸인지 셀 때는 **정식 라벨 하나에 한 표**다. 한글 표와 영문 표를 나란히 싣는
    # 매장에서 같은 치수가 두 번 세어져, 「length 3칸 / 총장 1칸」인 표가 1칸으로 읽혔다.
    _by_canon: dict[str, list] = {}
    for k in st:
        if k == "_names" or not _n(k):
            continue
        c = canon_label(k)
        if c and _n(k) > len(_by_canon.get(c, [])):
            _by_canon[c] = st[k]
    _want = column_count(_by_canon, len(st.get("_names") or [])) if _by_canon else 1
    order = sorted(st, key=lambda k: (_n(k) != _want, -_n(k), _spec(k), list(st).index(k)))
    out: dict[str, list[float]] = {}
    for k in order:
        vals = st[k]
        c = canon_label(k)
        if not c or c in out:
            continue
        if bad_label(vals if isinstance(vals, list) else []):
            continue
        girth = "둘레" in k or "circum" in k.lower() or (girth_keys is not None and (brand, c) in girth_keys)
        _fv = [fix_value(c, str(x), girth) for x in vals]
        # 한 열의 값이 거의 다 상식 범위 밖이면, 값이 이상한 게 아니라 **별칭이 틀린** 것이다.
        # 멀쩡한 라벨은 버림률이 0~5%인데 「shoulder*1/2+arm → 어깨」는 82%였다 —
        # 그건 어깨가 아니라 화장(어깨→소매끝)이었고, 83~92cm 를 어깨라고 적고 있었다
        # (2026-09-13, 창고 전수에서 이 잣대 하나로 찾았다). 아침의 팔기장과 같은 집안이다.
        # 여기서 세어 두고 끝에 한 번 찍는다 — 새 매장이 들어오면 그때 눈에 띄라고.
        _tally = _COL_DROP.setdefault((re.sub(r"\s+", " ", str(k)).strip().lower(), c), [0, 0])
        for _x, _y in zip(vals, _fv):
            if isinstance(_x, (int, float)) or (isinstance(_x, str) and _x.strip()):
                _tally[1 if _y is None else 0] += 1
        vs = [v for v in _fv if v is not None]
        vs = drop_strays(brand, c, vs, med or {})
        if vs:
            out[c] = vs
    # 고르는 차례와 보여 주는 차례는 다르다 — 칸 차례는 원문 그대로 돌려준다.
    first = {}
    for k in st:
        c = canon_label(k)
        if c and c not in first:
            first[c] = list(st).index(k)
    return {c: out[c] for c in sorted(out, key=lambda c: first.get(c, 99))}


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
        for d in iter_jsonl(p):
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
        for d in iter_jsonl(p):
            st = d.get("size_table")
            if not (isinstance(st, dict) and st):
                continue
            r = rows.get((d["brand_slug"], str(d["product_no"])))
            key = (d["brand_slug"], json.dumps(st, sort_keys=True, ensure_ascii=False))
            seen[key][0] += 1
            if r and r.get("category"):
                seen[key][1].add(r["category"])
    return {k for k, (n, cats) in seen.items() if n >= 10 and len(cats) >= 3}


def shop_wide_browser(brw: dict, rows_by_url: dict) -> set[str]:
    """브라우저가 상품마다 같은 표를 담아 온 것을 가려낸다 — 같은 잣대(품목 셋 이상 × 열 벌 이상).

    브라우저는 탭을 눌러 표를 읽는데, 페이지가 아직 안 바뀌었거나 매장 공용 사이즈 안내를 잡으면
    상품이 달라도 같은 표가 나온다. 한 매장은 252벌이 전부 같은 표였고 또 한 곳은 72벌 중 42벌이
    같았다 — 반팔 가디건 총장이 122.5cm 로 적히는 꼴이다(2026-09-10). 지금은 그 두 매장 모두 서버
    HTML 표가 이겨서 값이 새어 나가진 않았지만, HTML 이 한 번 비면 곧바로 수백 벌이 같은 치수를 단다.
    돌려주는 것: 버려야 할 표의 source_url 집합."""
    seen = defaultdict(lambda: [[], set()])
    for u, d in brw.items():
        t = d.get("size_table_raw")
        if not (isinstance(t, dict) and t):
            continue
        r = rows_by_url.get(u)
        key = (d.get("brand_slug"), json.dumps(t, sort_keys=True, ensure_ascii=False))
        seen[key][0].append(u)
        if r and r.get("category"):
            seen[key][1].add(r["category"])
    bad: set[str] = set()
    for (_, _), (urls, cats) in seen.items():
        if len(urls) >= 10 and len(cats) >= 3:
            bad.update(urls)
    return bad


# 색만 다른 같은 옷 — 소재·사이즈·디테일이 같다(사람 확인 2026-09-04). 한쪽에만 표가 있으면 물려준다.
# 색 이름은 「꾸밈말 + 색」으로 온다 — 라이트 블루·빈티지 블루·워시드 인디고. 꾸밈말은
# 뒤에 색이 따라올 때만 걷어낸다(「빈티지 데님」의 빈티지는 색이 아니라 스타일이다).
# 영문은 낱말 경계를 지킨다 — 경계가 없으면 「Laye(red)」의 red 까지 지운다.
_MOD_KO = r"(?:라이트|다크|딥|페일|빈티지|워시드|멜란지|파스텔|소프트|미디엄)\s*"
_MOD_EN = r"(?:light|dark|deep|pale|vintage|washed|melange|pastel|soft|medium)\s*"
_COLOR_KO = ("블랙|화이트|아이보리|베이지|네이비|블루|그레이|차콜|챠콜|카키|올리브|브라운|크림|핑크|레드|"
             "그린|멜란지|샌드|모카|카멜|버건디|퍼플|와인|인디고|토프|스카이|오렌지|옐로우|실버|골드|"
             "민트|라벤더|살몬|라임|마젠타|로즈|토바코|초콜릿|초콜렛|마론|오닉스|에크루|세이지|"
             "피치|버터|오트밀|머스타드|코발트|터콰이즈|라떼|초코|탠")
_COLOR_EN = ("black|white|ivory|beige|navy|blue|grey|gray|charcoal|khaki|olive|brown|cream|pink|red|"
             "green|melange|sand|stone|mocha|camel|burgundy|purple|yellow|orange|silver|gold|wine|"
             "indigo|taupe|ecru|sage|salmon|lime|magenta|rose|onyx|tobacco|chocolate|mint|lavender|"
             "peach|butter|oatmeal|mustard|cobalt|turquoise|latte|apricot|graphite|violet|maroon")
# 색 이름 앞에 붙는 꾸밈머리. 아래 _COLOR 가 쓰므로 그보다 **먼저** 서야 한다
# (꼬리에 남은 말을 떼는 데에도 그대로 쓴다 — color_base 끝부분).
COLOR_HEAD = {
    "ash", "애쉬", "dusty", "더스티", "dust", "더스트", "smoke", "스모크", "mid", "미드",
    "sky", "forest", "off", "오프", "moss", "french", "프렌치", "greyish", "misty", "미스트",
    "warm", "mix", "wood", "blossom", "baby", "크로우", "sax", "slate", "drab", "드랩",
    "coral", "oat", "oak", "indi", "ocean", "dove", "midnight", "pure", "fog", "steel",
    "oyster", "soap", "royal", "로열", "milk", "jade", "powder", "bean",
}

# 낱말 경계는 꾸밈말 「앞」에 둔다 — 뒤에 두면 「LIGHTPINK」의 pink 가 t 뒤라서 막힌다.
#
# 영문은 색 낱말을 **붙여 쓰는** 꼴이 흔한데(skyblue · greybeige · offwhite) 한 낱말만
# 보면 앞뒤 경계에 걸려 하나도 안 떨어졌다. 그래서 같은 옷의 형제 가운데 하나만 묶음에서
# 빠졌다 — depound 「… hairband - brown/pink」는 cg=4006 인데 「- skyblue」만 없었다
# (2026-09-22 전수: 형제 둘 이상 22,184묶음 중 124묶음이 이 꼴).
# 고침 둘: ① 색 낱말이 이어 붙은 것을 한 덩이로 본다 `(?:색)+` (greybeige = grey+beige)
#          ② 꾸밈머리(sky · off · royal …)가 **붙어** 와도 받는다. COLOR_HEAD 는 색을 뗀
#             뒤 꼬리에만 쓰여서 skyblue 처럼 붙은 것은 거기까지 가지도 못했다.
# 「blackberry」·「greenhouse」는 여전히 안 걸린다 — berry·house 가 색이 아니라 `(?![a-z])` 가 막는다.
_HEAD_EN = "|".join(sorted((h for h in COLOR_HEAD if h.isascii() and h.isalpha()),
                           key=len, reverse=True))
_COLOR = re.compile(
    rf"[\s_\-\(\[/]*(?:(?:{_MOD_KO})?(?:{_COLOR_KO})"
    rf"|(?<![a-z])(?:{_MOD_EN})?(?:{_HEAD_EN})?(?:{_COLOR_EN})+(?![a-z]))[\s_\-\)\]/]*",
    re.I)

# 색 목록을 여기에 또 적어 두면 수집기와 어긋난다. 실제로 어긋났다 — 오늘 수집기에 넣은
# 머드·탠·브릭이 여기엔 없어서 frizmworks 「_ mud」와 「_ tan」이 다른 옷으로 보였고,
# 사람이 사이즈를 적어 준 한 벌이 형제에게 안 물려갔다(2026-09-05). 수집기 어휘를 가져다 쓴다.
try:
    import crawl_cafe24 as _cc
    _EXTRA_KO, _EXTRA_EN = [], []
    for _vals in _cc.COLOR_VOCAB.values():
        for _a in _vals:
            _a = _a.strip().lower()
            if not _a or len(_a) < 2:
                continue
            (_EXTRA_KO if re.fullmatch(r"[가-힣]+", _a) else
             _EXTRA_EN if re.fullmatch(r"[a-z][a-z ]*", _a) else []).append(_a)
    if _EXTRA_KO or _EXTRA_EN:
        _ko = "|".join(sorted(set(_COLOR_KO.split("|")) | set(_EXTRA_KO), key=len, reverse=True))
        _en = "|".join(re.escape(x) for x in sorted(set(_COLOR_EN.split("|")) | set(_EXTRA_EN),
                                                    key=len, reverse=True))
        _COLOR = re.compile(
            rf"[\s_\-\(\[/]*(?:(?:{_MOD_KO})?(?:{_ko})"
            rf"|(?<![a-z])(?:{_MOD_EN})?(?:{_en})(?![a-z]))[\s_\-\)\]/]*", re.I)
except Exception:
    pass

# 매장이 제품 코드를 이름에 적으면 그것이 같은 옷을 가리키는 가장 확실한 열쇠다 —
# diafvine 「DV.LOT 685」 네 벌은 색 이름이 서로 달라(Tiger Stripe Camo Grey ·
# Desert Camo · Military Olive Drab) 색만 지워서는 한 묶음이 안 된다(2026-09-05).
_STYLE_CODE = re.compile(r"(?:dv\.?lot|lot|style|art|no)\.?\s*#?\s*(\d{2,5})", re.I)


# 색 이름의 **앞부분** — 「더스트 핑크」의 더스트, 「애쉬그레이」의 애쉬. 뒤의 색은 이미
# 지우는데 앞부분을 몰라 이름이 달라지고, 같은 옷의 다른 색이 형제로 안 묶였다.
#
# 자동으로는 못 골랐다. 네 가지 잣대를 다 재 보고 버렸다(꼬리말 빈도 · 색 칸이 다른가 ·
# 치수표가 같은가 · 여러 색 앞에 붙는가). 까닭은 하나다 — 색이 늘 이름 끝에 오니
# **그 앞 낱말은 무엇이든** 여러 색 앞에 선다. 그래서 후보를 뽑아 점검표로 굽고
# 사람이 골랐다(2026-09-20, 50개 중 46개가 색 · 7개가 아님).
#
# 사람이 「아님」으로 고른 것도 적어 둔다 — 다음에 또 후보로 올라오지 않게:
#     every(라인 이름) · mini(크기) · restock(재입고) · dot(무늬) · classic · organ · squid



# 같은 이름을 네 자리에서 따로 묻는다(2287·2333·2346·2870). 정규식이 다섯 번 도는
# 함수라 이름 하나당 스무 번씩 헛돈다 — canon_label 과 같은 까닭으로 담아 둔다.
@lru_cache(maxsize=1 << 18)
def color_base(name: str) -> str:
    """상품 이름에서 색 이름과 대괄호를 걷어낸 알맹이 — 같은 옷의 다른 색을 한 묶음으로 묶는 열쇠."""
    n = re.sub(r"\[[^\]]*\]", "", name or "")
    # 시즌 표기는 알맹이가 아니다 — 같은 옷을 한쪽만 「25FW 박스 플리츠 팬츠 차콜」로,
    # 다른 쪽은 「박스 플리츠 팬츠 블랙」으로 올린다(rough-side). 그 접두사 하나 때문에
    # 형제로 안 묶여 사이즈를 물려받지 못했다(사람이 링크로 짚어 줌, 2026-09-05).
    n = re.sub(r"(?<![0-9A-Za-z])\d{2}\s*(?:fw|ss|su|aw|ps|s/s|f/w)(?![0-9A-Za-z])", " ", n, flags=re.I)
    m = _STYLE_CODE.search(name or "")
    if m:
        return f"#{m.group(1)}"          # 제품 코드가 있으면 그것이 알맹이다
    n = _COLOR.sub(" ", n)
    out = re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z가-힣]+", " ", n)).strip().lower()
    # 색을 지우고 **끝에 남은** 말이 색 이름의 앞부분이면 그것도 뗀다. 끝에서만 본다 —
    # 「스모크 그레이 후드」처럼 앞에 붙은 것은 이름의 일부라 안 건드린다.
    parts = out.split()
    while len(parts) > 1 and parts[-1] in COLOR_HEAD:
        parts.pop()
    return " ".join(parts)


def load_manual() -> dict[str, dict]:
    """data/manual_sizes.csv — 사람이 매장 그림을 보고 옮겨 적은 실측.

    왜 필요한가: 매장이 사이즈표를 자바스크립트 탭 안 그림으로만 두면(etce 「SIZE GUIDE」)
    서버 HTML 에도 상세 그림 목록에도 없다. 브라우저를 붙일 값어치가 없는 몇 벌은 사람이
    보고 적는 편이 빠르다(2026-09-05 사람이 etce 두 벌을 그렇게 넘겨 줬다).

    한 줄이 한 라벨이다:  링크, 사이즈이름, 항목, 값, 왜, 부위
      https://etce.kr/...963/, S·M·L, 총장, 102·104·106, 사이즈가이드 그림,

    「부위」는 세트(셋업)처럼 한 상품에 표가 둘일 때만 쓴다 — 비우거나 「상의」면 기본 표,
    「하의」면 size_parts 로 따로 나간다(앱 세션과 정한 모양 2026-09-24, SIZE_PARTS 주석).
    「품목」은 상품 품목(subtype)과 같은 말(팬츠 · 숏팬츠 · 스커트 · 탑 …). 하의 줄에 쓰면 하의 표의
    품목(앱이 긴바지·반바지 핏 기준을 가른다), 상의 줄에 쓰면 세트의 상의 표 품목이다(앱 세션 2026-09-24·25).
    """
    if not MANUAL.exists():
        return {}
    acc: dict[str, dict] = {}
    parts: dict[str, dict[str, dict]] = {}
    for r in csv.DictReader(MANUAL.open(encoding="utf-8-sig")):
        part = (r.get("부위") or "").strip()
        if part and part not in SIZE_PARTS:
            print(f"   manual_sizes.csv — 부위는 {'·'.join(SIZE_PARTS)} 만 쓴다: {part!r} ({r.get('링크')})")
            continue
        url = (r.get("링크") or "").strip()
        lab = canon_label((r.get("항목") or "").strip()) or (r.get("항목") or "").strip()
        vals = [v.strip() for v in re.split(r"[·|]", r.get("값") or "") if v.strip()]
        names = [v.strip() for v in re.split(r"[·|]", r.get("사이즈이름") or "") if v.strip()]
        if not url or not lab or not vals:
            continue
        try:
            nums = [float(v) for v in vals]
        except ValueError:
            print(f"   manual_sizes.csv — 숫자가 아닌 값 {vals} ({url})")
            continue
        blank = {"brand_slug": (r.get("브랜드") or "").strip(),
                 "source": "manual", "size_names": names or None, "sizes": {}}
        e = parts.setdefault(url, {}).setdefault(part, blank) if part == "하의" else acc.setdefault(url, blank)
        e["sizes"][lab] = nums
        if names and not e["size_names"]:
            e["size_names"] = names
        t = (r.get("품목") or "").strip()
        # 상의 줄의 품목도 싣는다 — 세트는 상품 품목이 하의(ronron-7966 「스커트」)라 앱이 상의 표를 어느
        # 핏 묶음과 견줄지 모른다(앱 세션 2026-09-25). export 가 세트일 때만 내보낸다.
        if t:
            e["t"] = t
    # 라벨마다 값 개수가 다르면 짧은 쪽에 맞춘다 — 사람도 오타를 낸다
    def _trim(e):
        n = min(len(v) for v in e["sizes"].values())
        e["sizes"] = {c: v[:n] for c, v in e["sizes"].items()}
        if e["size_names"]:
            e["size_names"] = e["size_names"][:n]
    for e in acc.values():
        _trim(e)
    for u, ps in parts.items():
        for e in ps.values():
            _trim(e)
        low = ps.get("하의")
        if u not in acc:
            # 하의 표만 있으면 그게 기본 표다 — size_parts 는 「기본 = 상의」일 때만 뜻이 있다
            acc[u] = low
            continue
        acc[u]["size_parts"] = [{"part": "하의", **({"t": low["t"]} if low.get("t") else {}),
                                 "size_names": low["size_names"], "sizes": low["sizes"]}]
    return acc




# OCR 이 알파벳 사이즈를 숫자로 흘려 쓴다 — S 가 8·5 로 온다(dnsr 「8 M L」 637벌).
# 옆 칸이 알파벳 사이즈일 때만 되돌린다. 「1 2 3」처럼 처음부터 숫자로 매기는 표는 안 건드린다.
_OCR_SIZE = {"8": "S", "5": "S", "$": "S"}
# 판독기 표에서 글자로만 된 이름 가운데 사이즈로 인정하는 것. 「S/P」·「Short」 같은 매장식 이름은 HTML 표에만
# 있어 여기 안 걸린다(이 목록은 ocr·sibling 표에만 쓴다).
_OCR_NAME_OK = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", "4XL",
                "F", "FREE", "ONE", "OS", "ONESIZE", "SS"}
_UNIT_TAIL = re.compile(r"\s*[\[(]\s*c?m\s*[\])]\s*$", re.I)


def _mergeable(vals: dict, a: int, b: int) -> bool:
    """두 자리의 값이 서로 어긋나지 않는가 — 한쪽이 비었거나 같으면 접어도 된다."""
    for v in vals.values():
        if b >= len(v):
            continue
        x, y = v[a], v[b]
        if x is not None and y is not None and x != y:
            return False
    return True


def _clash(vals: dict, a: int, b: int) -> list[str]:
    """두 자리에서 값이 서로 다른 라벨 목록."""
    out = []
    for lab, v in vals.items():
        if b >= len(v):
            continue
        x, y = v[a], v[b]
        if x is not None and y is not None and x != y:
            out.append(lab)
    return out


# 사이즈 이름이 없는 표가 많다 — 실측이 있는 30,889벌 가운데 이름이 있는 것은 40.5% 뿐이다.
# 그림에서 읽은 표는 머리글 줄이 흐려 이름이 통째로 날아간다. 그런데 매장은 같은 이름을
# 구매 옵션에 적어 둔다(「S | M | L」). 칸 수가 딱 맞으면 그걸 가져다 쓴다.
#
# 함부로 쓰면 안 된다 — 이름을 잘못 붙이면 없느니만 못하다. 그래서 셋을 다 만족할 때만 쓴다.
#   ① 옵션이 사이즈처럼 생겼고 개수가 표의 칸 수와 정확히 같다
#   ② 옵션이 작은 것부터 큰 것 차례로 적혀 있다(XS<S<M<L<XL, 숫자는 오름차순)
#   ③ 실측이 사이즈 따라 커진다 — 값이 다 있는 라벨 하나라도 단조증가이고, 줄어드는 라벨은 없다
# ③ 이 노이러 니트 두 벌을 걸렀다(소매길이 62.6 → 54.0 — 표가 거꾸로거나 잘못 읽혔다).
_OPT_SOLDOUT = re.compile(r"\s*[\[\(]?\s*(?:품절|sold\s?out|out\s*of\s*stock|일시\s?품절|재입고\s?예정)\s*[\]\)]?\s*$", re.I)
# 「S/M」·「L/XL」처럼 두 치수를 묶어 파는 옵션(합사이즈). 표에서는 그대로 받으면서 옵션에서는 버려서
# 두 칸 표 10벌이 이름 없이 남아 있었다(2026-09-10). 앞 글자로 차례를 매긴다 — S/M 다음은 L/XL 이다.
# 두 쪽이 다 치수 글자일 때만 — 「S/P」(Small/Petit)·「M/M」 같은 나라별 표기는 표에만 있고 옵션엔 없다.
# 뒤쪽이 앞쪽보다 커야 한다 — 「M/M」은 나라별 표기(Medium/Mediano)이지 합사이즈가 아니다.
_SIZE_COMBO = re.compile(r"^(XXS|XS|S|M|L|XL|XXL)\s*/\s*(XXS|XS|S|M|L|XL|XXL)$", re.I)


def _combo_rank(u: str):
    m = _SIZE_COMBO.match(u)
    if not m:
        return None
    a, b = _SIZE_RANK[m.group(1).upper()], _SIZE_RANK[m.group(2).upper()]
    return a if a < b else None
# 「00S」·「00M」처럼 앞에 0 을 붙여 파는 매장이 있다(한 매장 352벌이 옵션이 표와 칸 수까지 맞는데도
# 이름을 못 받고 있었다, 2026-09-12). 표를 읽는 쪽은 이미 00S 를 사이즈로 다룬다 — 옵션 쪽만 막혀 있었다.
_SIZE_OPT = re.compile(r"^(?:0*(?:XXS|XS|S|M|L|XL|XXL|2XL|3XL)|FREE|F|ONE ?SIZE|\d{1,2}|0\d"
                       r"|X{0,2}[\-\s]?SMALL|MEDIUM|X{0,2}[\-\s]?LARGE"
                       r"|엑스?스몰|스몰|미디움|미디엄|라지|라아지|엑스라지)$", re.I)
_ZERO_ALPHA = re.compile(r"^0+(XXS|XS|S|M|L|XL|XXL|2XL|3XL)$", re.I)
_SIZE_RANK = {"XXS": 0, "XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5, "XXL": 6, "2XL": 6, "3XL": 7}
# 매장은 옵션 이름 뒤에 재고 사정을 덧붙인다(hatching-room 「1(XS) Only 1 Left」·「4(L) Low Stock」).
_OPT_STOCK = re.compile(r"\s*[\[\(]?\s*(?:only\s*\d+\s*left|low\s*stock|품절\s*임박|재고\s*\d+\s*개?|"
                        r"\d+\s*개?\s*남음)\s*[\]\)]?\s*$", re.I)
# 사이즈 뒤에 변형을 붙여 파는 매장(diafvine 「M 실버지퍼」·「L엔틱지퍼 (+KRW 50,000)」).
# 맨 앞 낱말만 사이즈로 본다 — 뒤에 구분자나 한글이 와야 한다. 「MELANGE GRAY」는 안 걸린다.
# 「01_S」·「02_M」처럼 번호와 글자 사이즈를 밑줄로 이어 쓰는 매장이 있다(한 매장 214벌). 밑줄이 없으면
# 사이즈 이름이 하나도 안 나와 두 칸 표 64벌이 이름 없이 남았다(2026-09-08).
_SIZE_HEAD = re.compile(r"^(XXS|XS|3XL|2XL|XXL|XL|S|M|L|\d{1,2})(?=[\s(\[（_]|[가-힣])", re.I)


def _size_option_names(opts: list[str]) -> list[str]:
    """구매 옵션 목록에서 사이즈 이름만 앞에서부터 끊어 낸다.

    한 목록에 사이즈 칸과 색 칸이 함께 들어온다(수집기가 두 칸을 이어 붙이는데 사이즈 칸을
    앞에 세운다). 사이즈는 오름차순으로 놓이므로, 순서가 끊기는 자리에서 멈추면 색 칸이
    섞이지 않는다: hatching-room 「1(XS) · 2(S) · 3(M) · 4(L) · M · L」에서 앞 넷만 가져온다.
    """
    names: list[str] = []
    for o in opts:
        o = _OPT_STOCK.sub("", _OPT_SOLDOUT.sub("", o)).strip()
        if not o:
            continue
        if _SIZE_OPT.match(o) or _combo_rank(o) is not None:
            cand = o
        else:
            m = _SIZE_HEAD.match(o)
            if not m:
                if names:
                    break               # 사이즈 줄이 끝났다
                continue                # 아직 「- [필수] 사이즈 선택 -」 같은 안내 구간이다
            cand = m.group(1)
        r = _opt_rank(cand)
        if r is None:
            if names:
                break
            continue
        if names and (_opt_rank(names[-1]) or -1) >= r:
            break                       # 오름차순이 끊겼다 — 다른 칸이 시작된 것이다
        names.append(cand)
    return names


# 사이즈를 글자 그대로 풀어 적는 매장이 있다(「SMALL | MEDIUM」·「스몰 | 라지」).
# 판매중 1,986벌이 이렇게 파는데 한 글자짜리만 사이즈로 읽고 있어, 표가 멀쩡히 있는
# 46벌이 이름을 못 받았다(2026-09-13). 앱에서는 고를 수 없는 표가 된다.
_SPELLED = {
    "XXSMALL": "XXS", "XXSMALL.": "XXS",
    "XSMALL": "XS", "X-SMALL": "XS", "엑스스몰": "XS",
    "SMALL": "S", "스몰": "S",
    "MEDIUM": "M", "미디움": "M", "미디엄": "M",
    "LARGE": "L", "라지": "L", "라아지": "L",
    "XLARGE": "XL", "X-LARGE": "XL", "엑스라지": "XL",
    "XXLARGE": "XXL", "XX-LARGE": "XXL",
}


def _opt_rank(o: str):
    u = o.upper().replace(" ", "")
    if u in _SPELLED:
        u = _SPELLED[u]
    if u in _SIZE_RANK:
        return _SIZE_RANK[u]
    z = _ZERO_ALPHA.match(u)
    if z:
        return _SIZE_RANK[z.group(1).upper()]        # 「00S」는 S 자리
    c = _combo_rank(u)
    if c is not None:
        return c                                     # 「S/M」은 S 자리
    return 100 + int(u) if u.isdigit() else None


# 숫자로 된 사이즈 이름은 한 갈래 안에 있어야 한다 — 매장 자체 번호(0~9) ·
# 유럽·미국 데님 허리(20~52) · 가슴둘레(80~125). 갈래가 섞이면 OCR 이 자리를 흘린 것이다:
#   noirer  ['48', '5', '52']  ← 50 의 0 을 흘렸다        · ['48', '500']  ← 0 을 하나 더 붙였다
#   loeuvre ['015', '02']      ← 01 에 5 가 붙었다
#   easy-no-easy ['191', '2']  ← 191 은 사이즈가 아니라 모델 키다
# 틀린 이름은 없는 이름보다 나쁘다(앱에 그대로 칩으로 뜬다). 많은 쪽 갈래에 안 드는 이름을
# 비운다. 어느 쪽이 많은지 가릴 수 없으면(갈래마다 하나씩) 전부 비운다 — 짐작하지 않는다.
# 숫자를 고쳐 주지는 않는다. 「5 는 50 이겠지」는 우리가 지어내는 말이다(2026-09-07, 78벌).
def _num_band(x: int) -> int:
    if x <= 9:
        return 0
    if 20 <= x <= 52:          # 유럽(34~48) · 미국 데님 허리(24~36)가 여기 든다
        return 1
    if 80 <= x <= 125:
        return 2
    return 3          # 사이즈 숫자가 사는 자리가 아니다


def blank_stray_numbers(names: list[str]) -> list[str]:
    nums = [(i, int(x)) for i, x in enumerate(names) if re.fullmatch(r"\d{1,3}", x or "")]
    if len(nums) < 2 or len(nums) != len([x for x in names if x]):
        return names
    bands = Counter(_num_band(v) for _, v in nums if _num_band(v) != 3)
    if len(set(_num_band(v) for _, v in nums)) == 1 and 3 not in {_num_band(v) for _, v in nums}:
        return names                      # 다 한 갈래다 — 손대지 않는다
    top = bands.most_common()
    keep = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    out = list(names)
    for i, v in nums:
        if keep is None or _num_band(v) != keep:
            out[i] = ""
    return out


def _unreadable_only_differs(e: dict, have: list[str], names: list[str]) -> bool:
    """판독기(ocr·sibling)가 읽은 이름과 옵션 이름이 자리 수가 같고, 다른 자리는 전부
    사이즈로 읽히지 않는 글자(「SS」·「?」·「L.」)이며, 그런 자리가 하나라도 있는가."""
    if e.get("source") not in ("ocr", "sibling") or len(have) != len(names):
        return False
    differs = 0
    for h, o in zip(have, names):
        if h.upper().replace(" ", "") == o.upper().replace(" ", ""):
            continue
        # 「1 size 95」·「1size」처럼 사이즈 글자로 시작하는 매장식 이름도 읽힌 것이다 — 옵션 「1」로
        # 바꾸면 95 라는 정보만 잃는다(roughside 3벌·rshemiste 2벌, 2026-09-08).
        if _opt_rank(h) is not None or h.isdigit() or _SIZE_HEAD.match(h) or re.match(r"^\d{1,2}(?=\D)", h):
            return False                   # 둘 다 사이즈로 읽힌다 — 판단하지 않는다
        differs += 1
    return differs > 0


def names_from_options(out: dict, rows_by_url: dict) -> int:
    """이름 없는 표에 매장 구매 옵션의 사이즈 이름을 붙인다(위 세 조건을 다 만족할 때만).

    이름이 이미 있어도 같은 이름이 두 번 나오면 그 이름은 잘못 읽은 것이다 — 앱 사이즈 칩에
    같은 글자가 두 번 뜬다. 매장 옵션이 칸 수만큼 오름차순으로 있으면 그쪽을 믿는다
    (2026-09-08: noirer 오간자 크롭 보머 자켓이 「36, 36」인데 옵션은 「36 | 38」이었다).
    """
    n = 0
    for u, e in out.items():
        if not e.get("sizes"):
            continue
        have = [str(x).strip() for x in (e.get("size_names") or [])]
        r = rows_by_url.get(u)
        if not r:
            continue
        # 옛 창고 줄에는 「2 [품절]」처럼 품절 표시가 붙어 있다(그 표시를 떼는 코드가
        # 수집기에 들어오기 전에 담긴 줄이다). 표시만 떼면 327벌에 이름이 붙는다 —
        # 다시 수집할 때까지 기다릴 까닭이 없다(2026-09-07).
        opts = [o.strip() for o in (r.get("options") or "").split("|") if o.strip()]
        names = _size_option_names(opts)
        cols = max((len(v) for v in e["sizes"].values()), default=0)
        if len(names) < 2 or len(names) != cols:
            continue
        # 이름이 있고 겹치지도 않으면 손대지 않는다 — 단, 판독기가 읽은 이름 가운데 사이즈로
        # 읽히지 않는 것(「SS」·「IS」·「L.」·「?」)이 있고 나머지가 자리마다 옵션과 같으면 옵션을
        # 믿는다. frizmworks 348벌이 「SS, M, L, XL」인데 옵션은 「S | M | L | XL」이었다(2026-09-08).
        # 「1, 2, 3」 vs 「S | M | L」처럼 둘 다 사이즈로 읽히는데 다르면 어느 쪽이 맞는지 모른다 —
        # 그대로 둔다.
        # 이미 있는 이름이 고를 수 없는 것이면(「?」가 끼었거나 같은 이름이 두 번) 옵션이 이긴다.
        # 서로 다른 글자라는 이유로 「?, 5」 같은 이름을 지키느라 옵션 「M | L」을 버리고 있었다
        # (2026-09-12: 그렇게 지나친 상품이 나중에 이름을 통째로 잃었다).
        if have and not _name_row_bad(have) and not _unreadable_only_differs(e, have, names):
            continue
        rk = [_opt_rank(o) for o in names]
        if any(x is None for x in rk) or rk != sorted(rk) or len(set(rk)) != len(rk):
            continue
        up = down = False
        # 옵션 이름이 칸 수·차례가 맞는데 라벨 하나만 값이 줄어들면 그 라벨이 판독 오류다(「어깨 58 60 52」— 62 를
        # 52 로 읽음). 표 전체의 이름을 포기하지 않고 그 라벨만 뺀다 — 늘어나는 라벨이 둘 이상이고 줄어드는 라벨이
        # 소수일 때만. 전부 줄어들면 차례가 뒤집힌 표일 수 있으니 예전처럼 손대지 않는다(2026-09-11, 학습 예 2벌).
        inc = [lab for lab, v in e["sizes"].items()
               if len(v[:cols]) == cols and all(isinstance(x, (int, float)) for x in v[:cols])
               and all(v[i] <= v[i + 1] for i in range(cols - 1))]
        dec = [lab for lab, v in e["sizes"].items()
               if len(v[:cols]) == cols and all(isinstance(x, (int, float)) for x in v[:cols])
               and any(v[i + 1] < v[i] - 1.0 for i in range(cols - 1))]
        # 한 치수에 25cm 넘게 뛰는 라벨도 같은 눈으로 본다. 아래에서 이걸 만나면 표 전체의
        # 이름을 포기하는데, 뛰는 라벨은 대개 그 한 줄이 잘못 읽힌 것이다 —
        # 「총장 108·109·110·110·140」(140 은 111 을 잘못 읽음). 그 한 줄 때문에 멀쩡한
        # 다섯 줄이 사이즈 이름을 통째로 잃고, 앱에서는 고를 수 없는 표가 된다.
        # 줄어드는 라벨과 같은 잣대로 — 성한 라벨이 둘 이상이고 이상한 쪽이 더 적을 때만 뺀다.
        jump = [lab for lab, v in e["sizes"].items()
                if len(v[:cols]) == cols and all(isinstance(x, (int, float)) for x in v[:cols])
                and any(v[i + 1] > v[i] + 25.0 for i in range(cols - 1))]
        bad = set(dec) | set(jump)
        good = [lab for lab in inc if lab not in bad]
        if bad and len(good) >= 2 and len(bad) < len(good):
            for lab in bad:
                del e["sizes"][lab]
        for v in e["sizes"].values():
            vv = v[:cols]
            if len(vv) == cols and all(isinstance(x, (int, float)) for x in vv):
                if all(vv[i] <= vv[i + 1] for i in range(cols - 1)):
                    up = True
                # 한 칸이라도 1cm 넘게 줄면 그 표는 이 차례가 아니다. 감사기의 「사이즈가
                # 커지는데 값이 작아진다」와 같은 잣대를 쓴다 — 이름을 붙이면 감사기가
                # 그 표를 검사할 수 있게 되므로, 잣대가 다르면 내가 붙인 이름이 곧바로
                # 모순으로 잡힌다(andersson-bell 엉덩이 51.5·58.5·55.5, 2026-09-07).
                if any(vv[i + 1] < vv[i] - 1.0 for i in range(cols - 1)):
                    down = True
                # 한 치수 올라가는데 25cm 넘게 뛰면 그건 사이즈 차례가 아니라 딴 옷이거나
                # 잘못 읽은 값이다. 셋업(자켓+팬츠)을 한 표에 담은 매장이 있다 — 가슴
                # 53 · 93 이 나란히 있고 총장은 한 줄뿐이었다. 「ONE, ONE」을 「S, M」으로
                # 고쳐 놓으면 M 을 고른 사람이 팬츠 치수를 자켓 치수로 보게 된다.
                # 상한을 10cm 로 잡아 봤더니 멀쩡한 표 29개가 이름을 잃었다(가슴 57·67.5
                # 같은 큰 등급차는 실제로 있다). 25cm 로 두면 잃는 것은 셋 뿐이고 그 셋도
                # 소매길이 16·90 처럼 표 자체가 깨진 것들이다(2026-09-08).
                if any(vv[i + 1] > vv[i] + 25.0 for i in range(cols - 1)):
                    down = True
        if up and not down:
            # 매장이 소문자로 적어 두기도 한다(grove 「s | m」) — 글자 사이즈는 대문자로 맞춘다
            e["size_names"] = [o.upper() if o.upper() in _SIZE_RANK else o for o in names]
            n += 1
    return n


_ALPHA_LADDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"]


def _name_row_bad(names: list[str]) -> bool:
    """고를 수 없는 이름 줄인가 — 빈 자리·물음표가 있거나 같은 이름이 두 번 있다."""
    return (not names) or any(x in ("?", "") for x in names) or len(names) != len(set(names))


def _fill_alpha(names: list[str]) -> list[str] | None:
    """읽힌 자리가 S·M·L 사다리에서 칸 간격과 똑같이 떨어져 있으면 빈 자리를 메운다."""
    idx = [i for i, x in enumerate(names) if x.upper() in _ALPHA_LADDER]
    if len(idx) < 2 or len(idx) == len(names):
        return None
    pos = [_ALPHA_LADDER.index(names[i].upper()) for i in idx]
    if any(pos[j + 1] - pos[j] != idx[j + 1] - idx[j] for j in range(len(idx) - 1)):
        return None
    out = []
    for i, x in enumerate(names):
        if x.upper() in _ALPHA_LADDER:
            out.append(x.upper())
            continue
        p = pos[0] + (i - idx[0])
        if not 0 <= p < len(_ALPHA_LADDER):
            return None
        out.append(_ALPHA_LADDER[p])
    return out if len(set(out)) == len(out) else None


def _fill_number(names: list[str]) -> list[str] | None:
    """읽힌 자리가 등차 정수열이면 빈 자리를 메운다(「1, ?, 3」→ 2). 간격이 정수가 아니면
    손대지 않는다 — 「48 ? 51」처럼 이름 줄이 아니라 치수 줄을 읽은 것일 수 있다."""
    idx = [i for i, x in enumerate(names) if x not in ("?", "")]
    if len(idx) < 2 or len(idx) == len(names):
        return None
    vals = []
    for i in idx:
        m = re.fullmatch(r"0*(\d{1,3})", names[i])
        if not m:
            return None
        vals.append(int(m.group(1)))
    steps = {(vals[j + 1] - vals[j]) / (idx[j + 1] - idx[j]) for j in range(len(idx) - 1)}
    if len(steps) != 1:
        return None
    st = steps.pop()
    if st <= 0 or st != int(st):
        return None
    st, w = int(st), len(names[idx[0]])
    out = [x if x not in ("?", "") else str(vals[0] + st * (i - idx[0])).zfill(w)
           for i, x in enumerate(names)]
    return out if len(set(out)) == len(out) else None


# 단면으로는 있을 수 없는 값 — 이보다 크면 둘레로 잰 것일 수 있다(브랜드 단위 판정인 GIRTH_MIN 보다 높게 잡는다).
_HALF_MIN = {"가슴": 75, "허리": 55, "엉덩이": 65, "밑단": 50, "허벅지": 42, "어깨": 70, "암홀": 40, "소매단": 30}


def halve_unit_outliers(out: dict, rows: dict) -> Counter:
    """한 표 안에서만 둘레로 적힌 값을 단면으로 맞춘다 — 같은 라인에 절반 값이 실제로 있을 때만.

    브랜드×라벨 단위로 둘레를 가려내는 brand_girth 는 한 브랜드 안에서 어떤 상품은 단면, 어떤
    상품은 둘레인 경우를 못 잡는다. 같은 레깅스 라인에서 한쪽은 「엉덩이 65.5·69.5·73.5」이고
    다른 쪽은 「32.7·34.7·36.7」이었다 — 정확히 절반이다(2026-09-12 사람이 앱 화면에서 밑단
    차이를 보고 물어 찾음).

    짐작으로 반을 나누지 않는다. 같은 브랜드·같은 품목·색만 다른 같은 이름 무리 안에서
    ① 그 라벨의 가장 작은 값의 1.85~2.15배이고 ② 단면으로는 있을 수 없는 크기일 때만 접는다.
    둘 다 아니면 손대지 않는다 — 틀린 치수는 없는 치수보다 나쁘다.
    """
    n = Counter()
    grp = defaultdict(list)
    for u, e in out.items():
        r = rows.get(u)
        if r and r.get("item_type"):
            grp[(r["brand_slug"], r["item_type"], color_base(r["name"])[:14])].append(u)

    def med(v):
        x = [y for y in v if isinstance(y, (int, float))]
        return statistics.median(x) if x else None

    for us in grp.values():
        if len(us) < 2:
            continue
        for lab, mn in _HALF_MIN.items():
            vals = {u: med(out[u]["sizes"].get(lab) or []) for u in us}
            vals = {u: v for u, v in vals.items() if v}
            if len(vals) < 2:
                continue
            lo = min(vals.values())
            for u, v in vals.items():
                if not (v > mn and 1.85 <= v / lo <= 2.15):
                    continue
                # 치마는 밑단이 엉덩이보다 넓은 게 정상이다 — 접으면 A라인이 폭 좁은 치마가 된다
                # (2026-09-12 표본에서 미니스커트 밑단 54 → 27 이 되는 것을 보고 막았다).
                hip = med(out[u]["sizes"].get("엉덩이") or [])
                it = (rows.get(u) or {}).get("item_type", "")
                if lab == "밑단" and hip and ("스커트" in it or "치마" in it) and v / 2 < hip:
                    n["치마 밑단이라 두었다"] += 1
                    continue
                out[u]["sizes"][lab] = [round(x / 2, 1) if isinstance(x, (int, float)) else x
                                        for x in out[u]["sizes"][lab]]
                n[lab] += 1
    return n


def repair_names(out: dict, rows: dict) -> Counter:
    """판독기가 이름 줄의 한 칸을 놓쳐 「?」가 남거나 같은 이름이 두 번인 표를 고친다.

    앱 사이즈 칩에 「?」가 뜨거나 같은 글자가 두 번 뜬다 — 둘 다 고를 수 없는 값이다
    (2026-09-12 감사: 「?」 247벌 · 겹침 21벌). 채우는 길은 확신 순서로 셋이고, 어느 것도
    안 되면 이름 줄을 통째로 비운다 — 「고를 수 없음」이 「틀린 이름」보다 낫다.
      1. 색만 다른 형제가 온전한 이름을 같은 칸 수로 갖고 있으면 그대로 받는다(가장 확실).
      2. S·M·L 사다리에서 자리 간격이 맞으면 메운다(「?, M, L, XL」→ S).
      3. 등차 정수열이면 메운다(「?, 2, 3」→ 1). 간격이 정수가 아니면 손대지 않는다.
    매장 옵션으로 채우는 길은 names_from_options 가 앞서 처리한다.
    """
    n = Counter()
    by_base = defaultdict(list)
    for u, r in rows.items():
        if u in out:
            by_base[(r["brand_slug"], color_base(r["name"]))].append(u)

    def cols(e):
        return max((len(v) for v in e["sizes"].values()), default=0)

    for u, e in out.items():
        names = [str(x).strip() for x in (e.get("size_names") or [])]
        if names and not _name_row_bad(names):
            continue
        r = rows.get(u)
        got = None
        # 한 칸짜리 표에 형제의 이름을 붙여 봐야 고를 게 없다 — 「001」 같은 매장 코드만 남는다.
        if r and (names or cols(e) >= 2):
            for v in by_base.get((r["brand_slug"], color_base(r["name"])), []):
                ev = out.get(v)
                if v == u or not ev:
                    continue
                nv = [str(x).strip() for x in (ev.get("size_names") or [])]
                if nv and not _name_row_bad(nv) and len(nv) == cols(e) == cols(ev) and (not names or len(names) == cols(e)):
                    got, why = nv, "형제에게서 받음"
                    break
        if got is None:
            got = _fill_alpha(names)
            why = "S·M·L 사다리로 메움"
        if got is None:
            got = _fill_number(names)
            why = "등차 숫자로 메움"
        if got is not None:
            e["size_names"] = got
            n[why if names else "이름 없던 표가 형제에게서 받음"] += 1
        elif names:
            e["size_names"] = None          # 원래 없던 표는 그대로 둔다 — 셈이 부풀지 않게
            n["고를 수 없어 이름을 비움"] += 1
    return n


# 사이즈 자리에 「옷 이름」이 들어온 표 — 세트·팩 상품이다. 한 상품의 사이즈가 아니라
# 구성품 저마다의 실측이라, 사이즈 고르는 자리에 세우면 「a maxi t-sh」가 사이즈로 뜬다
# (사람이 앱 화면에서 짚어 줌, 2026-09-12 — 판매중 옷 2,400벌 가운데 한 벌).
# 그 페이지는 머리줄이 「Size(free)」다. 즉 사이즈는 하나뿐이고 칸은 구성품이다.
# 값 자체는 맞지만 이름을 그대로 두면 틀린 사이즈가 되고, 이름만 비우면 서로 다른 옷의
# 치수가 한 옷의 두 사이즈로 읽힌다(가슴 62.5 / 51). 그래서 통째로 뺀다 —
# 없는 치수보다 틀린 치수가 나쁘다.
_SET_PIECE = re.compile(
    r"(?i)t-?sh|shirt|blouse|tee|top\b|knit|cardigan|sweater|hood|sweat|jacket|coat|vest|"
    r"pants|denim|jean|skirt|dress|bolero|tube|camisole|slip\b|"
    r"티셔츠|셔츠|블라우스|니트|가디건|후드|맨투맨|자켓|재킷|코트|조끼|팬츠|바지|스커트|치마|원피스|볼레로|튜브|나시")


_COLOR_NAME = re.compile(
    r"(?i)^\s*(?:black|white|off\s*white|ivory|cream|beige|navy|charcoal|gr[ae]y|brown|khaki|olive|"
    r"green|blue|red|pink|purple|violet|yellow|orange|melange|denim|indigo|burgundy|wine|mint|sky|"
    r"camel|mocha|sand|silver|gold|blue\s*gr[ae]y|"
    r"블랙|화이트|아이보리|네이비|차콜|그레이|브라운|카키|올리브|그린|블루|레드|핑크|퍼플|옐로우|"
    r"베이지|크림|멜란지|와인|민트|카멜|모카|실버|골드)\s*$")


def drop_piece_tables(out: dict) -> tuple[int, int]:
    """칸 이름이 사이즈가 아닌 표를 뺀다. (옷 이름 = 세트·팩, 색 이름 = 색깔별 실측)

    둘 다 앱에서 「사이즈 고르기」 칸에 그대로 떠서, 손님이 「a maxi t-sh」나 「BLACK」을
    사이즈로 고르게 된다. 값이 맞든 틀리든 고를 수 없는 칸이라 통째로 뺀다 —
    없는 치수보다 틀린 치수가 나쁘다.
    """
    piece = color = 0
    for u in list(out):
        nm = out[u].get("size_names")
        if not nm or len(nm) < 2:
            continue
        if all(_SET_PIECE.search(str(x) or "") for x in nm):
            del out[u]; piece += 1
        elif all(_COLOR_NAME.match(str(x) or "") for x in nm):
            del out[u]; color += 1
    return piece, color


def drop_reversed_labels(out: dict, tol: float = 1.0) -> dict:
    """사이즈가 커지는데 값이 줄어드는 라벨을 그 상품에서 뺀다 — 틀린 치수는 없는 치수보다 나쁘다.

    판독기가 상세 그림의 숫자를 흘리거나 칸을 건너뛰면 한 줄이 밀린다(「52 5126 FOZ 50.8 64.8」— 총장이 빠져
    소매길이 자리에 어깨가 앉는다). 감사기가 33벌을 잡고 있었고 24벌이 한 매장에 몰렸다(2026-09-11). 어느 칸이
    틀렸는지 하나로 가려지는 것은 9벌뿐이라 짐작해 고치지 않는다 — 그 라벨을 통째로 비운다. 이름이 오름차순으로
    읽히는 표에서만(이름이 없거나 순서를 모르면 판단하지 않는다). 다른 라벨은 그대로 남는다."""
    n = Counter()
    for u, e in out.items():
        names = e.get("size_names") or []
        rk = [_opt_rank(str(x)) for x in names]
        if len(rk) < 2 or any(r is None for r in rk) or rk != sorted(rk) or len(set(rk)) != len(rk):
            continue
        for lab in list(e["sizes"]):
            v = e["sizes"][lab][:len(names)]
            if len(v) != len(names) or not all(isinstance(x, (int, float)) for x in v):
                continue
            if any(v[i + 1] < v[i] - tol for i in range(len(v) - 1)):
                del e["sizes"][lab]
                n[lab] += 1
        if not e["sizes"]:
            n["표가 비어 뺌"] += 1
    for u in [u for u, e in out.items() if not e.get("sizes")]:
        del out[u]
    return dict(n)


def clean_names(out: dict) -> dict:
    """사이즈 「이름」을 마지막에 한 번 훑는다. 값이 맞아도 이름이 「HEM」·「BLACK」이면 그 표는
    사이즈 표가 아니다 — 앱에서 사람이 그 글자를 그대로 본다(2026-09-05).

    조심할 것: 매장이 쓰는 이름은 생각보다 다양하다. 「3(M)」 686 · 「S (cm)」 639 ·
    「1 size 95」 458 · 「S/P」 52 는 전부 진짜다. 처음에 어휘 밖 이름을 모두
    「?」로 지우게 만들었다가 7,675개를 날릴 뻔했다. 그래서 **버리는 것은 좁게** 잡는다.
    (「SS」 369 도 진짜라고 적어 두었는데 틀렸다 — 한 매장 348벌의 옵션이 「S | M | L | XL」이었다.
    판독기가 S 를 SS 로 읽은 것. S 가 없고 M 이 있는 표의 SS 는 S 로 되돌린다, 2026-09-08.)

      · 이름이 전부 색이거나 전부 치수 이름이면 그 표는 사이즈 표가 아니다 — 통째로 버린다.
      · 뒤에 붙은 「(cm)」은 떼고, 「OOF」처럼 0을 O로 읽은 것은 되돌린다.
      · 알파벳 사이즈 사이에 낀 8·5 는 OCR 이 흘려 쓴 S 다.
      · 그 밖에는 매장이 적은 대로 둔다. 다만 치수 이름 하나(WIDTH·SIZE)나 한글 한 글자는 지운다.
    """
    try:
        import crawl_cafe24 as _cc
        colors = {a.strip().lower() for vs in _cc.COLOR_VOCAB.values() for a in vs}
        colors |= {k.lower() for k in _cc.COLOR_VOCAB}
    except Exception:
        colors = set()
    n = Counter()
    for u, e in list(out.items()):
        names = e.get("size_names")
        if not names:
            continue
        low = [str(x).strip() for x in names]
        if colors and len(low) > 1 and all(x.lower() in colors for x in low):
            n["색상표라 버림"] += 1
            del out[u]
            continue
        if all(canon_label(x) for x in low):
            n["치수 이름이라 버림"] += 1
            del out[u]
            continue
        alpha = sum(1 for x in low if re.fullmatch(r"[A-Za-z]{1,3}", x))
        new = []
        for x in low:
            y = _UNIT_TAIL.sub("", x).strip()
            if y != x:
                n["뒤의 (cm) 뗌"] += 1
            # 「OOF」·「OOM」·「OO1」— 앞의 00 을 O 로 읽은 것. 앞자리만 0 으로 되돌리고 끝 글자는 그대로
            # 둔다. 예전엔 M 도 2 로 바꿔 「OOM」이 「002」가 됐다 — 한 매장은 001·002·003 과 00S·00M·00L
            # 두 체계를 함께 쓰는데(HTML 표 145벌이 001·002, 판독기 값줄 머리 OOM 213·005 228), 그 바람에
            # 00M 옷 213벌이 002 라는 딴 체계의 이름을 달았다(2026-09-08).
            # 「OS」(one size)는 건드리지 않는다 — 앞자리가 둘이거나, 하나면 뒤가 숫자일 때만.
            if re.fullmatch(r"[0O]{2}[0-9OMSFL]|[0O][0-9]", y, re.I):
                z = y[:-1].upper().replace("O", "0") + y[-1].upper().replace("O", "0")
                if z != y.upper():
                    n["O 를 0 으로"] += 1
                y = z
            if y in _OCR_SIZE and alpha >= 1:
                y = _OCR_SIZE[y]; n["8·5 를 S 로"] += 1
            elif canon_label(y) or re.fullmatch(r"[가-힣]", y) or NOT_A_SIZE.match(y):
                y = ""; n["이름이 아니라 지움"] += 1
            new.append(y)
        # 「SS」는 S 를 겹쳐 읽은 것이다 — S 가 따로 없고 M 이 있을 때만(SS·S·M·L 로 파는 매장은 그대로).
        if "SS" in new and "S" not in new and "M" in new:
            new = ["S" if x == "SS" else x for x in new]
            n["SS 를 S 로"] += 1
        # 같은 표에 00M·00L·00F 가 있으면 「005」는 00S 다(S 를 5 로 읽음). 005 혼자면 그대로 — 짐작하지 않는다.
        if "005" in new and any(re.fullmatch(r"00[SMLF]", x) for x in new):
            new = ["00S" if x == "005" else x for x in new]
            n["005 를 00S 로"] += 1
        # 같은 표에 00S·00L 이 있고 00M 이 없으면 「002」는 00M 이다(M 을 2 로 읽음). 숫자 체계(001·002)는 글자
        # 체계와 한 표에 섞이지 않는다.
        if "002" in new and "00M" not in new and any(x in ("00S", "00L") for x in new):
            new = ["00M" if x == "002" else x for x in new]
            n["002 를 00M 으로"] += 1
        # 판독기 표에서 사이즈로 읽히지 않는 글자(「EPEE」·「S728」·「WIDTH」·「K」)와 네 자리 넘는 숫자
        # (「1915」·「7002」— 모델 번호·연도)는 이름이 아니다. 비운다(이름이 그것뿐이면 표는 이름 없는
        # 표가 되고, 값 있는 자리는 「?」로 남는다). 매장 옵션이 있으면 뒤에서 names_from_options 가 채운다.
        # 「L.」은 L 이다. 숫자 사이에 낀 「O」 하나는 0 이다. 틀린 이름은 없는 이름보다 나쁘다(2026-09-08, 128벌).
        if e.get("source") in ("ocr", "sibling"):
            digits_else = all(re.fullmatch(r"\d{1,3}", x) for x in new if x and x != "O")
            for i, y in enumerate(new):
                if not y:
                    continue
                yy = y.upper().rstrip(".")
                if y == "O" and digits_else and len(new) > 1:
                    new[i] = "0"; n["O 를 0 으로"] += 1
                elif re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*", y):
                    if yy in _OCR_NAME_OK:
                        if yy != y:
                            new[i] = yy; n["끝의 점 뗌"] += 1
                    else:
                        new[i] = ""; n["사이즈로 읽히지 않는 글자 비움"] += 1
                elif re.fullmatch(r"\d{4,}", y):
                    new[i] = ""; n["네 자리 넘는 숫자 비움"] += 1
        new = blank_stray_numbers(new)
        # ① 이름도 값도 없는 자리는 통째로 뺀다 — 표 밑에 붙은 「MODEL 179cm/68kg」 줄이
        #    사이즈 한 칸으로 잡혀 앱에 값 없는 「?」 사이즈가 섰다(frizmworks).
        vals = e.get("sizes") or {}
        keep = [i for i, y in enumerate(new)
                if y or any(i < len(v) and v[i] is not None for v in vals.values())]
        if keep and len(keep) < len(new):
            new = [new[i] for i in keep]
            e["sizes"] = {c: [v[i] for i in keep if i < len(v)] for c, v in vals.items()}
            n["값 없는 빈 자리 뺌"] += 1
        # ② 같은 이름이 잇달아 나오고 값이 서로 어긋나지 않으면 한 줄로 접는다 — 판독기가
        #    같은 줄을 두 번 읽은 것이다(mardi 「S M M L」). 값이 정말 다르면 접지 않는다.
        vals = e.get("sizes") or {}
        i = 1
        while i < len(new):
            # 이름이 같은 두 줄인데 딱 한 라벨만 어긋나면, 어느 쪽이 맞는지 알 수 없다.
            # 그 라벨만 비우고 접는다 — 어긋나는 값을 둘 다 이고 있느니 하나를 비우는 게 낫다.
            # 다른 라벨 둘 이상이 같아야 같은 옷으로 본다(mardi 「FREE·FREE」의 총장 65·65 ·
            # 어깨 46·46 · 가슴 54·54 인데 소매길이만 12·18 인 꼴, 2026-09-06 실측 24벌).
            clash = _clash(vals, i - 1, i) if new[i] and new[i] == new[i - 1] else None
            same = 0
            if clash is not None:
                same = sum(1 for lab, v in vals.items()
                           if i < len(v) and v[i - 1] is not None and v[i - 1] == v[i])
            if clash is not None and len(clash) == 1 and same >= 2:
                # 두 자리를 다 비운다. 한쪽만 비우면 아래 접기가 다른 쪽 값으로 채워
                # 결국 「둘 중 하나를 골라 준」 꼴이 된다 — 어느 쪽이 맞는지 우리는 모른다.
                vals[clash[0]][i - 1] = None
                vals[clash[0]][i] = None
                n["어긋나는 한 라벨을 비우고 접음"] += 1
                clash = []
            if new[i] and new[i] == new[i - 1] and (clash == [] or _mergeable(vals, i - 1, i)):
                for v in vals.values():
                    if i < len(v):
                        v[i - 1] = v[i - 1] if v[i - 1] is not None else v[i]
                        del v[i]
                del new[i]
                n["같은 이름 두 줄을 접음"] += 1
            else:
                i += 1
        if not any(new):
            e["size_names"] = None
        elif new != low:
            e["size_names"] = [x if x else "?" for x in new]
    return dict(n)


# 원래 아주 짧은 옷 — 총장을 건드리지 않는다. 볼레로는 24cm 가 맞고 코르셋은 28cm 가 맞다.
SHORT_BODY_NAME = re.compile(
    r"크롭|crop|볼레로|bolero|코르셋|corset|뷔스티에|bustier|브라렛|bralette|튜브\s?탑|"
    r"홀터\s?탑|비키니|bikini|파자마|pajama|잠옷|셋업|세트|\bset\b", re.I)

SHORT_SLEEVE_NAME = re.compile(
    r"반팔|숏\s?슬리브|short\s?sleeve|half\s?sleeve|하프\s?슬리브|캡\s?슬리브|cap\s?sleeve|"
    r"슬리브리스|sleeveless|민소매|나시|베스트|vest|조끼|s/s\b", re.I)


def drop_impossible(out: dict, rows_by_url: dict) -> int:
    """품목을 놓고 봤을 때 있을 수 없는 값을 비운다.

    라벨 전체 범위(size_labels.json 의 _ranges_cm)는 옷 종류를 안 가린다. 총장 24~160 이라
    코트의 총장 27cm 도, 청바지의 총장 160cm 도 통과한다. 그런데 같은 라벨이라도 품목마다
    사는 자리가 다르다. 그래서 창고가 스스로 말하게 한다 — 품목×라벨마다 지금 모인 값의
    0.5~99.5 백분위를 구하고, 거기서 위아래로 25% 더 나가면 비운다.

      frizmworks Nyco hooded oscar jacket   총장 27.5 (어깨 70 · 가슴 67 · 소매 65)
      far-from-what FAR FLARE JEAN          총장 140·160
      diafvine A-2 Flight Jacket            소매길이 8 (어깨 49 · 가슴 60.5 · 총장 66)
      andersson-bell DENIM LACE HEM SKIRT   허리 60.1 인데 엉덩이 48.5 — 허리가 더 넓다
      grove ELO MIDI SKIRT                  총장 126.5 (미디 스커트다)

    두 가지를 지킨다.
      · 그 줄의 다른 라벨이 둘 이상 제자리에 있을 때만 비운다. 표가 통째로 이상하면
        한 칸만 고쳐서 될 일이 아니고, 우리가 판단할 근거도 없다.
      · 이름이 반팔·민소매·베스트라고 말하면 소매길이는 건드리지 않는다.
        badblood 「알파1 유틸리티 숏슬리브 자켓」의 소매길이 11cm 는 맞는 값이다.

    값을 지어내지 않고 비우기만 한다 — 없는 치수보다 틀린 치수가 나쁘다.
    """
    pool: dict[tuple, list] = {}
    for u, e in out.items():
        cat = (rows_by_url.get(u) or {}).get("category")
        if not cat:
            continue
        for lab, vs in (e.get("sizes") or {}).items():
            for x in vs:
                if isinstance(x, (int, float)) and x > 0:
                    pool.setdefault((cat, lab), []).append(x)

    def pct(v, q):
        v = sorted(v)
        return v[min(len(v) - 1, int(len(v) * q))]

    lim = {k: (pct(v, 0.005) * 0.75, pct(v, 0.995) * 1.25)
           for k, v in pool.items() if len(v) >= 150}
    if not lim:
        return 0
    dropped = 0
    for u, e in out.items():
        r = rows_by_url.get(u) or {}
        cat = r.get("category")
        if not cat:
            continue
        sz = e.get("sizes") or {}
        ok = bad = 0
        for lab, vs in sz.items():
            k = (cat, lab)
            if k not in lim:
                continue
            lo, hi = lim[k]
            for x in vs:
                if isinstance(x, (int, float)) and x > 0:
                    if lo <= x <= hi:
                        ok += 1
                    else:
                        bad += 1
        if bad == 0 or ok < 2:
            continue
        name = r.get("name") or ""
        short = SHORT_SLEEVE_NAME.search(name)
        cropped = SHORT_BODY_NAME.search(name)
        for lab, vs in sz.items():
            k = (cat, lab)
            if k not in lim:
                continue
            if short and lab in ("소매길이", "화장", "소매단"):
                continue
            if cropped and lab in ("총장", "기장"):
                continue
            lo, hi = lim[k]
            for i, x in enumerate(vs):
                if isinstance(x, (int, float)) and x > 0 and not (lo <= x <= hi):
                    vs[i] = None
                    dropped += 1
        e["sizes"] = {lab: vs for lab, vs in sz.items()
                      if any(v is not None for v in vs)}
    return dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    rows = {(r["brand_slug"], r["product_no"]): r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    ocr: dict[tuple, str] = {}
    for p in OCR.glob("*.jsonl"):
        for d in iter_jsonl(p):
            ocr[(d["brand_slug"], str(d["product_no"]))] = d.get("ocr_text") or ""
    # 브라우저로 거둔 것 — 자바스크립트가 그리는 표는 여기밖에 없다(diafvine).
    brw: dict[str, dict] = {}
    if BROWSER.exists():
        for p2 in BROWSER.glob("*.jsonl"):
            for d2 in iter_jsonl(p2):
                if d2.get("source_url"):
                    brw[d2["source_url"]] = d2
    if brw:
        print(f"브라우저 기록 {len(brw)}건")
    # 사이즈가이드 창에서 **글로** 거둔 표(fetch_sizeguide 의 size_text). 그림보다 앞선다.
    sg_text: dict[str, str] = {}
    _sg_seen: dict[tuple, list] = defaultdict(lambda: [0, set()])
    for sub in ("sizeguide", "pagesize"):
        dd = CRAWL / sub
        if not dd.exists():
            continue
        for p3 in dd.glob("*.jsonl"):
            for d3 in iter_jsonl(p3):
                t3 = d3.get("size_text")
                u3 = d3.get("source_url")
                if not (t3 and u3):
                    continue
                if len(t3) > len(sg_text.get(u3, "")):
                    sg_text[u3] = t3
                r3 = {r["source_url"]: r for r in rows.values()}.get(u3) if False else None
    # 매장 공용 안내가 이 창에도 그대로 실린다. 다른 자리에서 쓰는 잣대와 같게 —
    # **같은 글이 열 벌 넘게 × 품목 셋 이상**에 붙으면 그 상품의 치수가 아니다.
    _by_url = {r["source_url"]: r for r in rows.values()}
    for u3, t3 in sg_text.items():
        r3 = _by_url.get(u3)
        if not r3:
            continue
        k3 = (r3["brand_slug"], t3)
        _sg_seen[k3][0] += 1
        if r3.get("category"):
            _sg_seen[k3][1].add(r3["category"])
    _sg_wide = {k for k, (n, cats) in _sg_seen.items() if n >= 10 and len(cats) >= 3}
    if _sg_wide:
        sg_text = {u: t for u, t in sg_text.items()
                   if (_by_url.get(u, {}).get("brand_slug"), t) not in _sg_wide}
        print(f"사이즈가이드 창의 글 가운데 매장 공용으로 판단해 버림 {len(_sg_wide)}가지")
    if sg_text:
        print(f"사이즈가이드 창의 글 {len(sg_text)}건")
    # 세로로 쌓인 표(parse_stack_rows)는 글 전체가 달라도 **표만** 매장 공용일 수 있다 —
    # lmood 는 가디건·셔츠·체인 목걸이 28벌에 같은 「44·46·48 총장 59.5…」가 실렸다
    # (2026-09-24). 위와 같은 잣대(열 벌 넘게 × 품목 셋 이상)를 읽어 낸 표에 한 번 더 건다.
    sg_stack: dict[str, tuple] = {}
    _st_seen: dict[tuple, list] = defaultdict(lambda: [0, set()])
    for u3, t3 in sg_text.items():
        r3 = _by_url.get(u3)
        if not r3:
            continue
        st = parse_stack_rows([x.strip() for x in t3.splitlines() if x.strip()])
        if not st:
            continue
        sg_stack[u3] = st
        k3 = (r3["brand_slug"], json.dumps(st, ensure_ascii=False, sort_keys=True))
        _st_seen[k3][0] += 1
        if r3.get("category"):
            _st_seen[k3][1].add(r3["category"])
    _st_wide = {k for k, (n, cats) in _st_seen.items() if n >= 10 and len(cats) >= 3}
    if _st_wide:
        sg_stack = {u: st for u, st in sg_stack.items()
                    if (_by_url[u]["brand_slug"], json.dumps(st, ensure_ascii=False, sort_keys=True))
                    not in _st_wide}
        print(f"세로로 쌓인 사이즈가이드 표 가운데 매장 공용으로 판단해 버림 {len(_st_wide)}가지")
    # 크리마 핏 위젯의 실측표(fetch_cremafit) — 상품마다 따로 받은 표라 매장 공용 거르기는 안 건다.
    # 둘레는 받을 때 이미 단면으로 바꿔 적었다. 첫 줄이 「Size | 이름 …」이다(2026-09-25 roem).
    crema: dict[str, tuple] = {}
    for p4 in sorted((CRAWL / "cremafit").glob("*.jsonl")) if (CRAWL / "cremafit").exists() else []:
        for d4 in iter_jsonl(p4):
            lines4 = [[c.strip() for c in ln.split("|")] for ln in (d4.get("size_text") or "").splitlines()]
            if len(lines4) < 3 or lines4[0][0].lower() not in ("size", "사이즈"):
                continue
            names4 = lines4[0][1:]
            cols4: dict[str, list] = {}
            for cells in lines4[1:]:
                lab4 = canon_label(cells[0])
                if lab4 and lab4 not in cols4 and len(cells) - 1 == len(names4):
                    cols4[lab4] = [fix_value(lab4, v) for v in cells[1:]]
            cols4 = {c: v for c, v in cols4.items() if any(x is not None for x in v)}
            if len(cols4) >= 2:
                crema[d4["source_url"]] = (names4, cols4)
    if crema:
        print(f"크리마 핏 실측표 {len(crema)}건")
    girth_keys = brand_girth(CRAWL)
    label_med = brand_label_median(CRAWL)
    shared = shop_wide_tables(CRAWL, rows)
    brw_shared = shop_wide_browser(brw, {r["source_url"]: r for r in rows.values()})
    if brw_shared:
        print(f"브라우저가 상품마다 같은 표를 담아 온 것 {len(brw_shared)}건 — 버린다")
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
        for d in iter_jsonl(p):
            k = (d["brand_slug"], str(d["product_no"]))
            r = rows.get(k)
            if not r:
                continue
            per_brand_tot[k[0]] += 1
            st = d.get("size_table")
            # 창고에 이미 담긴 표 가운데 「같은 표가 두 번 찍혀 칸이 배로 늘어난 것」을 여기서 접는다 —
            # 다시 수확하지 않고도 고쳐진다(2026-09-12, 한 매장 668벌).
            if isinstance(st, dict) and st:
                import crawl_cafe24 as _cc2
                st = _cc2.collapse_repeated_columns(dict(st))
            sizes, names, source = {}, None, None
            if isinstance(st, dict) and st and (k[0], json.dumps(st, sort_keys=True, ensure_ascii=False)) not in shared:
                # 공용 표 판정은 저장된 그대로의 표로 한다(위 줄). 걷어 내는 것은 그 뒤다.
                st = clean_html_table(st, "\n".join(t for t in (d.get("description") or "", d.get("detail_text") or "") if t))
                sizes = normalize_html(st, k[0], girth_keys, label_med) if st else {}
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
                # 라벨도 값도 빗금으로 이은 글(parse_slash_table 주석) — 매장이 상세
                # 설명에 적어 둔 표라 이미 창고에 있는데 읽는 갈래가 없었다.
                if len(sizes) < 2:
                    sl = parse_slash_table(body)
                    if sl and len(clean_ocr(sl[1])) > len(sizes):
                        sizes, names, source = clean_ocr(sl[1]), sl[0], "html"
                # 한 줄 한 사이즈 「1사이즈 (단면)가슴-62CM 어깨-55CM …」(parse_size_rows 주석)
                if len(sizes) < 2:
                    sr = parse_size_rows(body)
                    if sr and len(clean_ocr(sr[1])) > len(sizes):
                        sizes, names, source = clean_ocr(sr[1]), sr[0], "html"
            # HTML 표가 한 사이즈 몇 항목만 잡았는데(버뮤다: 1사이즈 밑단·총장, 패딩 셔츠: 1사이즈 네 항목) 설명글에
            # 사이즈별 줄이 다 있으면 그쪽을 쓴다. 기존 표의 항목이 다 들어 있고 값이 1cm 안에서 맞을 때만 — 다른 표로 바꾸지 않는다.
            elif source == "html":
                body = "\n".join(t for t in (d.get("description") or "", d.get("detail_text") or "") if t)
                sr = parse_size_rows(body)
                cs = clean_ocr(sr[1]) if sr else {}
                wide = lambda st: max((len(v) for v in st.values() if isinstance(v, list)), default=0)
                if cs and set(sizes) <= set(cs) and (len(cs) > len(sizes) or wide(cs) > wide(sizes)) and all(
                        isinstance(sizes[c], list) and len(sizes[c]) <= len(cs[c]) and all(
                            isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 1
                            for a, b in zip(sizes[c], cs[c])) for c in sizes):
                    sizes, names = cs, sr[0]
            # 브라우저가 본 표·설명글 — 서버 HTML 에 없던 것이 여기 있다
            b = brw.get(r["source_url"])
            if len(sizes) < 2 and b:
                raw = {} if r["source_url"] in brw_shared else (b.get("size_table_raw") or {})
                if raw:
                    cand = normalize_html(raw, k[0], girth_keys, label_med)
                    if len(cand) > len(sizes):
                        # 브라우저 표도 사이즈 이름 줄을 함께 담아 온다(2026-09-10). 자바스크립트로
                        # 표를 그리는 매장은 서버 HTML 에 표가 없어 이 길이 유일하다.
                        bn = raw.get("_names")
                        sizes, source = cand, "browser"
                        names = [str(x) for x in bn] if isinstance(bn, list) and bn else None
                if len(sizes) < 2:
                    flat2 = parse_flat(b.get("description") or "")
                    if flat2 and len(clean_ocr(flat2[1])) > len(sizes):
                        sizes, names, source = clean_ocr(flat2[1]), flat2[0], "browser"
                if len(sizes) < 2:
                    n2, s2 = from_ocr(b.get("description") or "")
                    if len(s2) > len(sizes):
                        sizes, names, source = s2, n2, "browser"
            # ③ 사이즈가이드 창에 **글로** 적힌 표 — 사진을 읽기 전에 본다.
            # 사람이 정한 차례다(2026-09-18): ①HTML → ②토글 펼친 HTML → ③버튼이 여는 창 →
            # ④그래도 없으면 사진. 글이 있으면 사진보다 훨씬 정확하니 앞에 세운다.
            sg = sg_text.get(r["source_url"])
            if len(sizes) < 2 and sg:
                # 이 갈래는 **이 창의 글에만** 건다. 창고 전수로 재 보니 OCR 글 전체에 걸었을 때
                # 다른 매장의 표를 가로챈다 — 얻음 108벌 옆에서 **775벌이 값을 잃고 12벌이
                # 통째로 사라졌다**(till-i-die 516 · crank 171 · known-better 81, 2026-09-18).
                # 이 꼴을 찾은 자리가 버튼이 여는 창이었으니 거기서만 쓴다.
                # **이 갈래로 읽힌 것만 받는다.** 안 되면 from_ocr 로 되돌리게 해 봤더니,
                # 같은 창에 실린 **카페24 공용 인치 환산표**(「가슴 (inches) 22 23 24 …」)가
                # 그대로 들어왔다 — 215벌 중 59벌이 그 되돌림에서 나왔고 거기에 섞여 있었다
                # (2026-09-18 실측: unaffected 4벌이 가슴 27·28.5·30·32 를 받았다. 인치다).
                # parse_pair_rows 는 라벨 뒤에 수가 붙어야 받으므로 그 표를 구조적으로 거른다.
                sgl = [x.strip() for x in sg.splitlines() if x.strip()]
                pr = parse_pair_rows(sgl)
                # 표가 반대로 누운 매장은 parse_grid_rows 가 읽는다(「라벨 | 값 | 값」).
                # 부르는 쪽이 매장 공용 줄을 이미 지웠고 이 갈래도 canon_label 을 통과하는
                # 줄만 받으므로, 카페24 인치 환산표는 두 겹으로 걸린다.
                if not pr:
                    pr = parse_grid_rows(sgl)
                # 칸마다 줄이 바뀐 표(legacy · lmood). 매장 공용 표는 위에서 이미 걸렀다.
                if not pr:
                    pr = sg_stack.get(r["source_url"])
                if pr:
                    s3 = clean_ocr(pr[1])
                    if len(s3) > len(sizes):
                        sizes, names, source = s3, pr[0], "sizeguide"
            # ④ 크리마 핏 위젯 표 — 매장 표를 위젯이 따로 들고 있다(roem). 글이라 사진보다 앞선다.
            if len(sizes) < 2 and r["source_url"] in crema:
                s4 = clean_ocr(crema[r["source_url"]][1])
                if len(s4) > len(sizes):
                    sizes, names, source = s4, crema[r["source_url"]][0], "cremafit"
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
            # 값이 하나도 안 남은 라벨은 뺀다 — 「가슴: [null]」만 남으면 앱에 빈 줄이 선다.
            sizes = {c: v for c, v in ((c, drop_lone_outlier(drop_strays(k[0], c, v, label_med)))
                                       for c, v in sizes.items())
                     if any(isinstance(x, (int, float)) for x in v)}
            if not sizes:
                continue
            # 잡화에 옷 실측 표를 붙이지 않는다 — 매장 공용 안내표가 모자·양말·백팩에까지
            # 「가슴 허리」를 물려 주고 있었다(thebarnnet 33 등 96건, 2026-09-05 검사).
            # 가방에 어깨너비가 있을 리 없고, 있다면 그건 남의 옷 표다.
            if r.get("category_code") in NON_APPAREL_CODES and set(sizes) & GARMENT_ONLY:
                continue
            # 가방·모자·신발에는 우리 치수 축이 하나도 안 맞는다. 위 문은 총장·허리를 통과시켜
            # 가방 스트랩 길이가 「총장」, 모자 둘레가 「허리」로 앱에 섰다(창고 전수 가방 645 ·
            # 모자 171 · 신발 58벌, 2026-09-23). 벨트·스카프(accessories)와 목걸이(jewelry)의
            # 길이는 총장이 맞는 말이라 남긴다.
            if r.get("category_code") in NO_SIZE_AXIS_CODES:
                continue
            # 사이즈 개수가 라벨마다 다르면(모델 치수 한 줄·OCR 누락) 칸 수를 골라 맞춘다.
            # 짧은 라벨은 뺀다 — 앞칸만 남겨 두면 M 의 치수가 그 옷의 유일한 치수로 적힌다.
            n = column_count(sizes, len(names or []))
            # 길이를 맞추고 나서 다시 본다 — 자르고 나면 값이 하나도 안 남는 라벨이 생긴다
            # (noirer 「가슴: [null]」 — 앱 상세에 빈 줄이 선다).
            sizes = {c: v[:n] for c, v in sizes.items() if len(v) >= n}
            sizes = {c: v for c, v in sizes.items() if any(isinstance(x, (int, float)) for x in v)}
            if not sizes:
                continue
            # 이름이 칸보다 적으면 붙이지 않는다 — 세 칸짜리 표에 이름 둘을 걸면 어긋난다.
            # 뒤에서 매장 옵션·형제에게서 다시 채운다.
            if names:
                names = names[:n] if len(names) >= n else None
            sizes = blank_lone_jump(sleeve_to_hwajang(sizes))
            out[r["source_url"]] = {"brand_slug": k[0], "source": source, "size_names": names, "sizes": sizes}
            src[source] += 1
            (per_brand_html if source in ("html", "browser") else per_brand_ocr)[k[0]] += 1
    # 사람이 직접 옮겨 적은 값. 기계가 못 읽는 자리(사이즈가이드 탭 그림 등)를 사람이 메운
    # 것이라 무엇보다 앞선다. 형제 물려주기보다 먼저 넣어야 같은 옷의 다른 색도 함께 산다.
    for u, ent in load_manual().items():
        if u in out and out[u].get("source") != "manual":
            print(f"   손으로 적은 값이 {out[u]['source']} 값을 덮는다 — {u}")
        out[u] = ent
        src["manual"] += 1

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
                                    "size_names": base_entry["size_names"], "sizes": base_entry["sizes"],
                                    # 세트는 색만 달라도 하의 표까지 같다
                                    **({"size_parts": base_entry["size_parts"]} if base_entry.get("size_parts") else {})}
            lent += 1
    if lent:
        print(f"색만 다른 형제에게서 물려받은 사이즈 {lent}벌")

    gone = drop_impossible(out, {r["source_url"]: r for r in rows.values()})
    if gone:
        print(f"품목에 견줘 있을 수 없는 값 {gone}칸을 비웠다")
    fixed = clean_names(out)
    named = names_from_options(out, {r["source_url"]: r for r in rows.values()})
    if named:
        print(f"매장 옵션에서 사이즈 이름을 채운 상품 {named}벌")
    half = halve_unit_outliers(out, {r["source_url"]: r for r in rows.values()})
    if half:
        print("같은 라인에 절반 값이 있어 둘레를 단면으로 접음: "
              + " · ".join(f"{k} {v}" for k, v in sorted(half.items())))
    if _BAD_LINES:
        print("** 창고에 읽히지 않는 줄이 있다 — 그 줄만 건너뛰었다: "
              + " · ".join(f"{k} {v}줄" for k, v in sorted(_BAD_LINES.items())))
    pieces, colors = drop_piece_tables(out)
    if pieces:
        print(f"칸 이름이 옷 이름인 표(세트·팩) {pieces}벌을 뺐다 — 사이즈가 아니다")
    if colors:
        print(f"칸 이름이 색 이름인 표 {colors}벌을 뺐다 — 사이즈가 아니다")
    rev = drop_reversed_labels(out)
    if rev:
        print("사이즈 커지는데 값 줄어드는 라벨 뺌: " + " · ".join(f"{k} {v}" for k, v in sorted(rev.items())))
    # 표를 정리하고 나면 칸 수가 바뀐다. 옵션 이름 붙이기를 한 번 더 돌린다 — 첫 판에서는
    # 「옵션 2개 vs 표 4칸」이라 지나쳤던 상품이, 어긋난 라벨이 빠진 뒤에는 딱 맞는다
    # (2026-09-12: 옵션과 칸 수가 맞는데도 이름이 없던 상품들의 원인이 이 차례였다).
    again = names_from_options(out, {r["source_url"]: r for r in rows.values()})
    if again:
        print(f"표를 정리한 뒤 옵션에서 이름을 더 채운 상품 {again}벌")
    rep = repair_names(out, {r["source_url"]: r for r in rows.values()})
    if rep:
        print("읽다 만 사이즈 이름: " + " · ".join(f"{k} {v}" for k, v in sorted(rep.items())))
    if fixed:
        print("사이즈 이름 정리: " + " · ".join(f"{k} {v}" for k, v in sorted(fixed.items())))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"사이즈 있는 상품 {len(out)} / {len(rows)} ({len(out)/len(rows):.0%}) — html {src['html']} · ocr {src['ocr']} → {OUT}")
    lab = Counter(c for e in out.values() for c in e["sizes"])
    print("정식 라벨 분포:", dict(lab.most_common()))
    suspect = sorted(((d, k, c, ok) for (k, c), (ok, d) in _COL_DROP.items()
                      if d >= 20 and d > ok * 2), reverse=True)
    if suspect:
        print("\n** 별칭이 의심스러운 칸 — 값의 절반 넘게가 라벨의 상식 범위 밖이다:")
        for d, k, c, ok in suspect[:15]:
            print(f"   버림 {d:5} · 살림 {ok:5}  {k!r} → {c}")
        print("   (한 열이 통째로 범위를 벗어나면 값이 아니라 이름이 틀린 것이다 — size_labels.json 을 볼 것)")
    if args.report:
        print("\n브랜드별 (전체 / html / ocr):")
        for b in sorted(per_brand_tot, key=lambda b: -(per_brand_ocr[b])):
            print(f"  {b:20s} {per_brand_tot[b]:5d} {per_brand_html[b]:5d} {per_brand_ocr[b]:5d}")


if __name__ == "__main__":
    main()
