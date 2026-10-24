"""앱 「이 옷 읽기」의 범위표 size-ranges.json — 세부품목 × 성별갈래 × 사이즈 × 칸의 분위수.

앱 세션 요청(layer-web docs/garment-reading.md §9, 2026-10-09). 앱은 상품의 대표 사이즈 값을 이 표의 p10 · p25 · p75 · p90 과
견줘 「보통보다 넉넉해요」 같은 문장을 만든다. 그래서 표본이 틀리면 문장이 틀린다 — 아래 규칙은 다 그것을 막는 것이다.

- 판매중 의류만, product_sizes 의 size_match 가 「같음」 · 「차례로」인 표만(「모름」은 어느 칸이 M 인지 모른다).
- 사이즈 이름은 size_from_ocr.size_key 로 맞춘 size_keys 를 쓴다 — 앱도 같은 규칙으로 옵션을 바꾼다(norm 항목).
- 값은 모두 단면 cm(size_from_ocr.unit_guard 를 거친 뒤다). 밴딩 허리 범위(ranges)는 넣지 않는다.
- 밴딩(construction · pants_type 「밴딩」) 옷의 허리 · 엉덩이, 드롭숄더 · 래글런 옷의 어깨는 표본에서 뺀다 —
  재는 자리가 달라 「아주 작다 / 크다」는 틀린 말이 나온다(앱 세션 2026-10-09).
- 칸은 갈래마다 뜻이 달라(밑단: 바지 발목 폭 · 상의 허리 끝 폭) 세부품목을 넘어 합치지 않는다.
- p1 · p99 밖은 잘라 내고 분위수를 다시 낸다(아동 · 반려동물 같은 분류 오류가 범위를 흔든다).
- 표본 30벌 미만 칸은 넣지 않는다. 핏별 중앙값(silhouette 태그)은 15벌 미만이면 뺀다.
- 같은 옷의 색만 다른 상품은 표가 같아 한 벌을 여러 번 센다 — 브랜드 · 이름 앞머리 · 값이 같은 표는 한 번만 센다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

GARMENT = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}
LABELS = ("총장", "어깨", "가슴", "소매길이", "화장", "소매통", "밑단", "허리", "엉덩이", "허벅지", "밑위")
MIN_N, MIN_FIT_N = 30, 15
# 바지 종류 무리(앱 세션 · 사람 결정 2026-10-09): 반바지를 숏팬츠로 옮겨도 크기는 데님끼리 · 슬랙스끼리 견준다.
# 「세부품목·종류」 열쇠를 세부품목 줄 옆에 더 싣는다. 종류는 pants_type 에서 이 차례로 처음 맞는 것(밴딩은 종류가 아니다).
# 세부품목 이름이 이미 종류를 말하면(데님·진 · 슬랙스·슬랙스 · 카고팬츠·카고 …) 만들지 않는다. 핏별 중앙값은 이 무리엔 없다.
PANTS_KINDS = ("진", "슬랙스", "스웨트", "치노", "카고", "조거", "트랙", "워크", "카펜터", "퍼티그", "파라슈트", "벌룬", "레깅스", "파자마")


def pants_kind(subtype: str, pt: set) -> str | None:
    for k in PANTS_KINDS:
        if k in pt:
            said = ("데님", "진") if k == "진" else (k,)
            # 「파티그팬츠」는 퍼티그와 같은 말이다(표기만 다름)
            if k == "퍼티그":
                said = ("퍼티그", "파티그")
            return None if any(w in (subtype or "") for w in said) else k
    return None


def _gender(g: str) -> str:
    return "W" if (g or "").upper().startswith("WOMEN") else "MU"


def _q(xs: list[float], p: float) -> float:
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    v = xs[f] + (xs[c] - xs[f]) * (k - f)
    return round(v * 2) / 2


def _tags(t: dict, k: str) -> set:
    v = (t or {}).get(k)
    return set(v) if isinstance(v, list) else ({v} if v else set())


def build(rows: list[dict], tags: dict, sizes: dict) -> dict:
    import size_from_ocr as S
    samp: dict[str, list[float]] = defaultdict(list)
    fit: dict[str, list[float]] = defaultdict(list)
    seen: set = set()
    dropped = defaultdict(int)
    for r in rows:
        if r.get("category") not in GARMENT or r.get("status") != "ON_SALE" or not r.get("subtype"):
            continue
        e = sizes.get(r["source_url"]) or {}
        if e.get("size_match") not in ("같음", "차례로") or e.get("axis") == "head" or e.get("size_parts"):
            continue
        keys = e.get("size_keys") or []
        # 기장 · 핏 선택이 사이즈와 엇갈린 표(arend 「xs-short · xs-long …」, lookast 「S · M · SHORT S」)는 같은 사이즈 말이 두 번 나온다 —
        # 숏 · 롱 값이 한 사이즈에 섞여 범위를 흐린다. 표본에서 뺀다(판매중 210벌, 2026-10-10 점검).
        kk = [k for k in keys if k]
        if len(kk) != len(set(kk)):
            dropped["기장 · 핏 선택이 섞인 표"] += 1
            continue
        sz = e.get("sizes") or {}
        t = (tags.get(r["source_url"]) or {}).get("tags") or {}
        cons = _tags(t, "construction") | _tags(t, "pants_type")
        band = "밴딩" in cons
        drop = bool(cons & {"드롭숄더", "래글런"})
        sil = _tags(t, "silhouette")
        kind = pants_kind(r["subtype"], _tags(t, "pants_type")) if r.get("category") in ("Pants", "Denim") else None
        # 색만 다른 형제 — 이름 앞머리(색 표기 앞) · 값이 같으면 한 벌
        stem = re.split(r"[\(\[_/-]|\s-\s", r.get("name") or "")[0].strip().lower()
        sig = (r["brand_slug"], stem, tuple(sorted((k, tuple(v)) for k, v in sz.items() if isinstance(v, list))))
        if sig in seen:
            continue
        seen.add(sig)
        g = _gender(r.get("gender_target"))
        for lab in LABELS:
            vals = sz.get(lab)
            if not isinstance(vals, list) or len(vals) != len(keys):
                continue
            if band and lab in ("허리", "엉덩이"):
                dropped["밴딩 허리 · 엉덩이"] += 1
                continue
            if drop and lab == "어깨":
                dropped["드롭숄더 · 래글런 어깨"] += 1
                continue
            for k, v in zip(keys, vals):
                if not k or not isinstance(v, (int, float)):
                    continue
                base = f'{r["subtype"]}|{g}|{k}|{lab}'
                samp[base].append(float(v))
                if kind:
                    samp[f'{r["subtype"]}·{kind}|{g}|{k}|{lab}'].append(float(v))
                for s in sil:
                    fit[f"{base}|{s}"].append(float(v))
    out_rows: dict[str, dict] = {}
    for key, xs in samp.items():
        if len(xs) < MIN_N:
            continue
        xs.sort()
        lo, hi = _q(xs, 0.01), _q(xs, 0.99)
        ys = [x for x in xs if lo <= x <= hi]
        if len(ys) < MIN_N:
            continue
        out_rows[key] = {"n": len(ys), **{f"p{p}": _q(ys, p / 100) for p in (1, 10, 25, 50, 75, 90, 99)}}
    for key, xs in fit.items():
        if len(xs) < MIN_FIT_N or key.rsplit("|", 1)[0] not in out_rows:
            continue
        xs.sort()
        out_rows[key] = {"n": len(xs), "p50": _q(xs, 0.5)}
    return {
        "v": 1,
        "built": date.today().isoformat(),
        "unit": "단면 cm(가슴 · 허리 · 엉덩이 · 허벅지 · 밑단은 반 둘레). 둘레로 적힌 표는 게시 전에 반으로 맞췄다",
        "key": "세부품목|성별갈래(MU=남성·유니섹스, W=여성)|사이즈(size_keys)|칸 — 핏별 중앙값은 끝에 |silhouette. "
               "하의는 「세부품목·바지종류」 열쇠도 있다(pants_kind — 숏팬츠·진, 팬츠·카고)",
        "excluded": dict(dropped),
        "norm": {
            "설명": "옵션 · 표 이름 → 견줄 말. size_from_ocr.size_aliases 와 같은 규칙. 앞에서부터 처음 맞는 것",
            "순서": ["품절 · 재고 꼬리말 지움(품절, Low Stock, Only 1 Left …)", "대문자 · 공백 지움",
                    "색 · 시즌 앞머리 떼기: / _ - 로 나눈 마지막 토막도 본다(WHITE/1 → 1)", "SIZE · 사이즈 · = 지움",
                    "괄호 밖 · 안 토막에서: 글자 사이즈 > 하나 사이즈(FREE) > 숫자(앞 0 뗌). 숫자 둘이면 한 자리(매장 번호)가 앞"],
            "글자 사이즈": list(S._SIZE_RANK),
            "풀어 쓴 말": S._SPELLED,
            "하나 사이즈": S._SK_FREE.pattern,
            "0 붙은 글자": S._ZERO_ALPHA.pattern,
        },
        "rows": out_rows,
    }
