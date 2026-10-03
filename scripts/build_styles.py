"""색상마다 한 줄인 상품을 같은 옷(스타일)으로 묶는다 → data/styles.csv · data/styles_stats.json.

왜 (2026-10-03, 코파일럿 세션 부탁)
  products_full.csv 는 색상마다 한 줄이다(product_no 가 행마다 다르다). 「이 옷은 몇 가지 색으로 나오나」,
  「같은 옷의 다른 색」을 말하려면 행들을 하나로 묶는 style_id 가 있어야 한다.

어떻게
  ① 같은 브랜드 안에서만 묶는다.
  ② 상품명 끝의 색 부분을 뗀다 — 「_네이비」 · 「 / NAVY」 · 「(블랙)」 · 「[BLACK]」 · 「 - BLACK」 · 끝 낱말 「… 셔츠 네이비」.
     뗀 조각에 색 낱말이 하나라도 있어야 뗀다(crawl_cafe24.match_color). 「 / BLACK RIPSTOP」처럼 색 뒤에 원단 이름이
     붙어도 조각째 뗀다 — 색 이름표(color_label)는 그 조각 그대로 남긴다.
     「_3 COLOR」 · 「(2color)」처럼 한 행에 여러 색이면 조각을 떼고 color_label 을 그대로 둔다(색은 옵션에서 읽는다).
  ③ 뗀 이름(소문자 · 빈칸 정리)이 같은 행끼리 한 스타일. 이름에 색이 없는 행도 이름이 똑같으면 같이 묶는다
     (매장이 색을 옵션에만 적고 같은 이름으로 따로 올린 경우).
  ④ 묶인 행의 갈래(category)가 다르면 갈래별로 나눈다 — 「BASIC TEE」와 「BASIC TEE」 원피스 같은 우연을 막는다.

  카페24 상세의 「관련 상품」 칸은 수집하지 않고 있어 이번에는 못 쓴다. 상품 하나가 색 옵션을 여럿 가진 경우
  (한 행에 여러 색)는 colors 칸에 옵션의 색을 다 적는다.

  products_full.csv 처럼 CI 가 만든다. 로컬에서 만든 결과는 밀지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cafe24 as C  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
SRC = DATA / "products_full.csv"
OUT = DATA / "styles.csv"
STATS = DATA / "styles_stats.json"

# 「3 COLOR」 「2color」 「4컬러」 「5 colors」 — 한 행에 여러 색
MULTI = re.compile(r"(?i)^\s*\d{1,2}\s*(?:colou?rs?|컬러|색상?)\s*$")
# 끝 조각을 가르는 구분자. 오른쪽 것부터 본다 — 「S/S 슬림핏 쿨 슬랙스 - 블랙」(legacy)은 맨 왼쪽 「/」가 시즌 표기라
# 왼쪽부터 자르면 이름이 「S」만 남는다. 「NAME_BLACK_UDPA6B230BK」는 맨 오른쪽 조각(품번)이 색이 아니니 하나 왼쪽으로 간다.
SEP = re.compile(r"\s*(?:_|\s/\s|/|\s-\s|\s–\s|\s—\s)\s*")
TAIL_PAREN = re.compile(r"^(.*?\S)\s*[(\[]([^()\[\]]{1,40})[)\]]\s*$")


@lru_cache(maxsize=None)
def _match_color(s: str) -> str:
    return C.match_color(s)


def _is_color(seg: str) -> str:
    """조각이 색(또는 여러 색 표시)이면 대표 색, 아니면 "".

    조각이 **색으로 시작**해야 한다 — 「BLACK RIPSTOP」 「(Black)_2GMUOP09」는 받고, mmlg 「EWM_26SS_LIGHT V NECK KNIT
    (POWDER PINK)_1」에서 「_」 뒤 조각 전체(옷 이름)는 안 받는다. 그 조각이 받아지면 「EWM_26SS」만 남아 다른 옷 29벌이
    한 스타일이 됐다. 낱말 넷이 넘는 조각도 색 이름표가 아니다.
    """
    if MULTI.match(seg):
        return "멀티"
    words = re.sub(r"[()\[\]]", " ", seg).split()
    if not words or len(words) > 4:
        return ""
    if not (_match_color(words[0]) or (len(words) > 1 and _match_color(" ".join(words[:2])))
            or words[0].lower() in _MODS):
        return ""
    return _match_color(seg)


def split_color(name: str) -> tuple[str, str, str]:
    """상품명 → (뗀 이름, 색 이름표 원문, 대표 색). 색을 못 찾으면 (이름, "", "")."""
    n = re.sub(r"\s+", " ", (name or "")).strip()
    m = TAIL_PAREN.match(n)
    if m and _is_color(m.group(2)):
        return m.group(1).strip(), m.group(2).strip(), _is_color(m.group(2))
    for sm in reversed(list(SEP.finditer(n))):
        base, seg = n[:sm.start()].strip(), n[sm.end():].strip()
        if not base or not seg or len(seg) > 40:
            continue
        c = _is_color(seg)
        if c:
            return base, seg, c
    # 구분자 없는 끝 낱말 — 「내추럴 러브 심볼 티셔츠 네이비」. 낱말 1~3개가 통째로 색일 때만.
    words = n.split(" ")
    for k in (3, 2, 1):
        if len(words) <= k + 1:
            continue
        seg = " ".join(words[-k:])
        # 끝 낱말은 글자만으로 된 낱말이어야 한다 — 「(POWDER PINK)_1」의 「PINK)_1」을 색 낱말로 떼면 괄호가 반쪽 남는다
        if not all(re.fullmatch(r"[A-Za-z가-힣.]+", w) for w in words[-k:]):
            continue
        c = _match_color(seg)
        if c and _all_color_words(seg):
            return " ".join(words[:-k]), seg, c
    return n, "", ""


_MODS = {"light", "dark", "deep", "pale", "off", "jet", "dust", "dusty", "soft", "medium", "melange", "washed",
         "라이트", "다크", "딥", "페일", "오프", "소프트", "멜란지", "워시드", "연", "진"}


def _all_color_words(seg: str) -> bool:
    """「SOFT PINK」 · 「DARK NAVY」처럼 낱말마다 색이거나 색 꾸밈말인가 — 「COTTON PINK」는 아니다."""
    for w in seg.lower().split():
        if w in _MODS:
            continue
        if not _match_color(w):
            return False
    return True


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\s\-_·.,'\"`]+", " ", s)
    return s.strip()


def option_colors(opt: str) -> list[str]:
    out: list[str] = []
    for o in (opt or "").split(" | "):
        c = C.field_color(o) if o else ""
        if c and c not in out:
            out.append(c)
    return out


# 두 번째 묶기의 이름 — 색 낱말이 앞이나 가운데에 있는 것(「핑크 몰리 니트 스커트」 · 「SS25 PURPLE FITTED … T-SHIRT」),
# 색마다 품번이 다른 것(「Rose Print Sweatshirt in Mint VW1WE143-31」), 머리말 「[아울렛]」 「[10/29 발송]」이 붙은 것.
# 이것들을 다 떼고 같아지면 묶되 **값이 같을 때만** 묶는다 — 품번만 남은 이름이 「tee」 하나로 줄어 다른 옷이 섞이는 것을 막는다.
_BRACKET = re.compile(r"[\[(（【][^\])）】]{0,40}[\])）】]")
_CODE = re.compile(r"(?i)^(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9-]{5,}$")
_FILLER = {"in", "color", "colour", "컬러", "색상", "ver", "버전", "new", "신상"}


_GENDER_MARK = re.compile(r"(?i)^\s*(?:w|m|women|men|우먼|맨)\s*$")


def _bracket_drop(m: re.Match) -> str:
    """두 번째 묶기에서 뗄 괄호인가. 맨 앞 머리말(「[문가영 PICK]」 「[10/14 예약배송]」), 품번(「(MRC-5411 HBL SV)」
    「[TISU08BL02]」), 색 · 색 약자(「[NA/CH]」 「[PK]」)만 뗀다. 「V.S.C SWEAT(OLYMPIA)」의 OLYMPIA 같은 그림 이름은
    다른 옷이라 남기고(outstanding 25벌이 한 스타일이 됐다), (W) 같은 성별 표시도 남긴다(여성판은 따로 판다)."""
    inner = m.group(0)[1:-1].strip()
    if _GENDER_MARK.match(inner):
        return m.group(0)
    if m.start() == 0:
        return " "
    toks = re.split(r"[\s/]+", inner)
    if any(_CODE.match(t.strip(".-")) for t in toks) or _all_color_words(inner.lower()):
        return " "
    if ("/" in inner or len(toks) == 1) and all(re.fullmatch(r"[A-Za-z]{1,4}", t) for t in toks if t):
        return " "
    return m.group(0)


def loose(name: str) -> str:
    t = name or ""
    # 맨 앞 머리말은 여럿 붙기도 한다 — 「[사나 착용][2차] …」
    prev = None
    while prev != t:
        prev = t
        t = _BRACKET.sub(_bracket_drop, t, count=1) if t.lstrip().startswith(("[", "(", "【", "（")) else t
        t = t.lstrip()
    t = _BRACKET.sub(_bracket_drop, t)
    t = re.sub(r"[_/·,|]+", " ", t)
    out = []
    for w in t.split():
        lw = w.lower().strip(".-:'\"")
        if not lw or lw in _FILLER or _CODE.match(lw):
            continue
        if _all_color_words(lw):
            continue
        out.append(re.sub(r"[^0-9a-z가-힣]", "", lw))
    return " ".join(x for x in out if x)


def main() -> None:
    rows = list(csv.DictReader(SRC.open(encoding="utf-8-sig")))
    groups: dict[tuple, list[dict]] = defaultdict(list)
    shape = Counter()
    recs = []
    for r in rows:
        base, label, color = split_color(r["name"])
        tail = r["name"][len(base):]
        if label:
            shape["_" if "_" in tail else "/" if "/" in tail else "()" if re.search(r"[(\[]", tail)
                  else "-" if re.search(r"\s[-–—]\s", tail) else "끝낱말"] += 1
        else:
            shape["없음"] += 1
        key = (r["brand_slug"], norm(base), r.get("category") or "")
        rec = {"brand_slug": r["brand_slug"], "product_no": r["product_no"], "source_url": r["source_url"],
               "base_name": base, "color_label": label,
               "color": color or r.get("representative_color", "").split("·")[0],
               "colors": "·".join(option_colors(r.get("options", ""))) or r.get("representative_color", ""),
               "price": r.get("list_price") or r.get("price") or "", "full_name": r["name"],
               "method": "name_tail" if label else "same_name"}
        groups[key].append(rec)
        recs.append(rec)
    # 두 번째 묶기 — 첫 묶음끼리 합친다
    second: dict[tuple, list[tuple]] = defaultdict(list)
    for key, g in groups.items():
        lo = loose(g[0]["full_name"])
        if len(lo.split()) < 2 or len(lo.replace(" ", "")) < 4:
            continue
        prices = {x["price"] for x in g}
        if len(prices) != 1:
            continue
        second[(key[0], key[2], lo, prices.pop())].append(key)
    final: dict[tuple, list[dict]] = {}
    owner: dict[tuple, tuple] = {}
    for k2, keys in second.items():
        if len(keys) < 2:
            continue
        fk = (k2[0], f"~{k2[2]}~{k2[3]}", k2[1])
        final[fk] = []
        for k in keys:
            owner[k] = fk
            for x in groups[k]:
                x["method"] = "loose_name+price"
                final[fk].append(x)
    for key, g in groups.items():
        if key not in owner:
            final[key] = g
    groups = final
    sid: dict[tuple, str] = {}
    for key, g in groups.items():
        h = hashlib.sha1(f"{key[0]}|{key[1]}|{key[2]}".encode()).hexdigest()[:10]
        for x in g:
            x["style_id"] = f"{key[0]}:{h}"
            x["style_size"] = len(g)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        cols = ["brand_slug", "product_no", "style_id", "style_size", "color_label", "color", "colors", "base_name",
                "method", "source_url"]
        w.writerow(cols)
        for rec in recs:
            w.writerow([rec[c] for c in cols])
    by_brand: dict[str, dict] = defaultdict(lambda: {"rows": 0, "styles": 0, "multi_color_styles": 0, "max_rows": 0})
    for key, g in groups.items():
        b = by_brand[key[0]]
        b["rows"] += len(g)
        b["styles"] += 1
        b["multi_color_styles"] += len(g) > 1
        b["max_rows"] = max(b["max_rows"], len(g))
    sizes = Counter(min(len(g), 10) for g in groups.values())
    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": len(rows), "styles": len(groups),
        "multi_color_styles": sum(1 for g in groups.values() if len(g) > 1),
        "rows_in_multi": sum(len(g) for g in groups.values() if len(g) > 1),
        "name_tail_shape": dict(shape),
        "rows_by_method": dict(Counter(x["method"] for x in recs)),
        "style_size_hist": {("10+" if k == 10 else str(k)): v for k, v in sorted(sizes.items())},
        "by_brand": dict(sorted(by_brand.items())),
    }
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"행 {stats['rows']} → 스타일 {stats['styles']} · 둘 이상 {stats['multi_color_styles']} "
          f"(행 {stats['rows_in_multi']}) · 꼬리 {dict(shape)} · 크기 {stats['style_size_hist']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        OUT, STATS = Path(sys.argv[1]), Path(sys.argv[2])
    main()
