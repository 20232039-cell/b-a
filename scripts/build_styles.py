"""이름으로 색 형제 후보를 찾고 소재·실측·무늬·판매 구성 충돌을 검사한다.

원본 data/는 입력이며 출력 경로 두 개를 인자로 지정할 수 있다.
누락된 상세 정보는 동일성의 증거도 충돌의 증거도 아니다. 묶음의 모든 쌍을
검사해 정보가 없는 중간 상품을 통한 충돌 상품의 연쇄 병합을 막는다.
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
# 끝 조각을 가르는 구분자. 왼쪽 것부터 본다 — 「S/S 슬림핏 쿨 슬랙스 - 블랙」(legacy)은 맨 왼쪽 「/」가 시즌 표기라
# 왼쪽부터 자르면 이름이 「S」만 남는다. 「NAME_BLACK_UDPA6B230BK」는 맨 오른쪽 조각(품번)이 색이 아니니 하나 왼쪽으로 간다.
SEP = re.compile(r"\s*(?:_|\s/\s|/|\s-\s|\s–\s|\s—\s)\s*")
# 괄호 뒤에 품번 꼬리가 붙기도 한다 — and-you 「홀터넥 린넨 맥시 원피스 (Black)_2GMUOP09」
TAIL_PAREN = re.compile(r"^(.*?\S)\s*[(\[]([^()\[\]]{1,40})[)\]]\s*(?:[_-]\s*[A-Za-z0-9-]{2,20})?\s*$")


@lru_cache(maxsize=None)
def _match_color(s: str) -> str:
    return C.match_color(s)


@lru_cache(maxsize=65536)
def _suffix_color(seg: str) -> str:
    """명시된 색 꼬리에만 축약·매장 색 어휘를 적용한다."""
    key = seg.strip().lower()
    # k4 SALONDEJU 공식 옵션 MT(민트). 기존 정규색 어휘의 mint로만 연결한다.
    key = {"mt": "mint"}.get(key, key)
    # L/PURPLE은 사이즈 L과 보라색이 아니라 light purple이라는 색 표현이다.
    key = re.sub(r"^(?:l|lt)/(?=[a-z])", "light ", key)
    return _match_color(key) or C._FIELD_COLOR.get(key, "") or C._TAIL_COLOR.get(key, "")


# 색 수식어(NATURAL SOAP, EVERY BLACK)는 허용하되 디자인·소재·핏 표지는 남긴다.
_SUFFIX_DETAIL = re.compile(
    r"(?i)\b(?:ripstop|cotton|nylon|polyester|wool|linen|stripe[sd]?|check(?:ed)?|dot|camo(?:uflage)?|"
    r"leopard|buckle|button|zipper|wet|matt[e]?|coated|print|graphic|set|fit|slim|oversized?|"
    r"tee|shirt|jacket|hoodie|pants|skirt|dress|knit)\b|스트라이프|체크|도트|카모|버클|리퍼브|B품|세트|핏")


@lru_cache(maxsize=65536)
def _is_color(seg: str) -> str:
    if MULTI.fullmatch(seg):
        return "멀티"
    # 품번은 색의 일부가 아니다. BLACK_UDPA6B225BK 같은 꼬리에서만 분리한다.
    seg = re.sub(r"_(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{5,}$", "", seg).strip()
    seg = re.sub(r"(?i)^(?:l|lt)/(?=[a-z])", "light ", seg)
    words = re.split(r"[\s/&+]+", seg.strip("()[] "))
    if not words or len(words) > 4 or _SUFFIX_DETAIL.search(seg):
        return ""
    if not (_suffix_color(words[0]) or _suffix_color(" ".join(words[:2])) or words[0].lower() in _MODS):
        return ""
    # LIGHT DENIM 같은 등록된 복합 색명은 단어별로 쪼개기 전에 기존 어휘로 읽는다.
    exact = next((color for color, aliases in C.COLOR_VOCAB.items()
                  if seg.lower() in aliases), "") or C._FIELD_COLOR.get(seg.lower(), "")
    if exact:
        return exact
    # 순수한 색 조합에서는 이름에 적힌 순서의 첫 색을 쓴다. 낯선 수식어는 기존 해석을 유지한다.
    if all(w.lower() in _MODS or _suffix_color(w) for w in words):
        return next((_suffix_color(w) for w in words if _suffix_color(w)), "")
    return _suffix_color(seg)


def split_color(name: str) -> tuple[str, str, str]:
    """상품명 → (뗀 이름, 색 이름표 원문, 대표 색). 색을 못 찾으면 (이름, "", "")."""
    n = re.sub(r"\s+", " ", (name or "")).strip()
    m = TAIL_PAREN.match(n)
    if m and _is_color(m.group(2)):
        return m.group(1).strip(), m.group(2).strip(), _is_color(m.group(2))
    # 왼쪽부터 본다 — 꼬리 조각이 색으로 시작하고 낱말 넷 이하인 **가장 긴** 꼬리를 뗀다. 오른쪽부터 보면 years-ago 「KUVAIKA BASQUE
    # TEE V2 - SOFT VIOLET/NAVY」가 「/」에서 잘려 「… - SOFT VIOLET」이 이름으로 남고, 색마다 이름이 달라 다섯 색이 다 홀로 섰다
    # (코덱스 스타일 검증 245~264, 2026-10-03). 「S/S 슬림핏 쿨 슬랙스 - 블랙」의 맨 왼쪽 「/」 뒤는 색으로 시작하지 않아 지나간다.
    for sm in re.finditer(SEP.pattern + r"|(?<=[가-힣])-(?=[가-힣])", n):
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
        c = _match_color(seg) if _all_color_words(seg) else ""
        if c:
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


@lru_cache(maxsize=65536)
def option_colors(opt: str) -> list[str]:
    out: list[str] = []
    for o in (opt or "").split(" | "):
        c = (_is_color(o) or C.field_color(o)) if o else ""
        if c and c not in out:
            out.append(c)
    return out


# 두 번째 묶기의 이름 — 색 낱말이 앞이나 가운데에 있는 것(「핑크 몰리 니트 스커트」 · 「SS25 PURPLE FITTED … T-SHIRT」),
# 색마다 품번이 다른 것(「Rose Print Sweatshirt in Mint VW1WE143-31」), 머리말 「[아울렛]」 「[10/29 발송]」이 붙은 것.
# 이것들을 다 떼고 같아지면 묶되 **값이 같을 때만** 묶는다 — 품번만 남은 이름이 「tee」 하나로 줄어 다른 옷이 섞이는 것을 막는다.
_BRACKET = re.compile(r"[\[(（【][^\])）】]{0,40}[\])）】]")
_CODE = re.compile(r"(?i)^(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9-]{5,}$")
_FILLER = {"in", "color", "colour", "컬러", "색상", "ver", "버전", "new", "신상"}


_PROMO = re.compile(r"착용|PICK|예약|발송|배송|재입고|restock|last\s+(?:one|stock|piece)|^\d+(?:st|nd|rd|th)$|아울렛|라스트\s*피스|품절|^\d+차$", re.I)

_GENDER_MARK = re.compile(r"(?i)^\s*(?:w|m|women|men|우먼|맨)\s*$")


def _bracket_drop(m: re.Match) -> str:
    """홍보·예약 안내, 품번과 명시된 끝 색 괄호만 제거한다.

    상태·협업·성별·그래픽 이름은 보존하며, 원문의 표지도 별도로 충돌 검사한다.
    """
    inner = m.group(0)[1:-1].strip()
    if _GENDER_MARK.match(inner):
        return m.group(0)
    if m.start() == 0 and _PROMO.search(inner):
        return " "
    toks = re.split(r"[\s/]+", inner)
    tail = m.string[m.end():].strip()
    at_tail = not tail or bool(re.fullmatch(r"[_-][A-Za-z0-9-]{2,20}", tail))
    if any(_CODE.match(t.strip(".-")) for t in toks) or (at_tail and _is_color(inner)):
        return " "
    if at_tail and ("/" in inner or len(toks) == 1) and all(C._FIELD_COLOR.get(t.lower()) for t in toks if t):
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
    for i, w in enumerate(t.split()):
        lw = w.lower().strip(".-:'\"")
        if not lw or lw in _FILLER or _CODE.match(lw):
            continue
        if _all_color_words(lw):
            # 부품색은 옷 색과 다르다. Black/Silver Buckle을 지우면 다른 디자인이 된다.
            following = t.split()[i + 1:i + 2]
            if not following or following[0].lower() not in {"buckle", "버클", "button", "버튼", "zipper", "지퍼"}:
                continue
        out.append(re.sub(r"[^0-9a-z가-힣]", "", lw))
    return " ".join(x for x in out if x)


# 색조인 멜란지는 허용한다. 무늬·원단 구조의 차이만 충돌로 본다.
_PLAIN_PATTERN = {"단색", "무지", "멜란지"}
_FIT = {
    "slim": r"slim(?:[ -]?fit)?|슬림(?:핏)?",
    "regular": r"regular(?:[ -]?fit)?|레귤러(?:핏)?",
    "over": r"over[ -]?fit|oversized?|오버(?:핏)?",
    "loose": r"loose[ -]?fit|루즈(?:핏)?",
    "wide": r"wide[ -]?fit|와이드",
}


def _fits(text: str) -> frozenset[str]:
    return frozenset(k for k, rx in _FIT.items() if re.search(rx, text, re.I))


def _material(rec: dict) -> dict:
    out = {}
    for part in rec.get("mat") or []:
        values = part.get("v") or []
        if not values or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for _, v in values):
            continue
        if not 99 <= sum(v for _, v in values) <= 101:
            continue
        key = part.get("p") or "겉감"
        value = tuple(sorted((fiber, float(value)) for fiber, value in values))
        # 부위 없는 줄과 겉감 줄이 상충하면 그 부위는 모호하므로 비교하지 않는다.
        if key in out and out[key] != value:
            out[key] = None
        elif key not in out:
            out[key] = value
    return {k: v for k, v in out.items() if v is not None}


def _mat_differs(x: tuple, y: tuple) -> bool:
    """섬유가 다르거나 같은 섬유의 비율이 3%p 넘게 다르면 다른 원단 — 색마다 1~2%p 다르게 적는 매장이 있다
    (dunst 헤어리 스웨터 아크릴 45/46 · 나일론 29/28 은 같은 옷)."""
    dx, dy = dict(x), dict(y)
    return dx.keys() != dy.keys() or any(abs(dx[f] - dy[f]) > 3 for f in dx)


def _measurements(rec: dict) -> dict:
    keys = rec.get("size_keys") or rec.get("size_names") or []
    result = {}
    for axis, values in (rec.get("sizes") or {}).items():
        for key, value in zip(keys, values):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                continue
            key = re.sub(r"[\s_-]+", "", str(key).upper())
            key = {"F": "FREE", "OS": "FREE", "ONESIZE": "FREE", "ONE": "FREE"}.get(key, key)
            result[(key, axis)] = float(value)
    return result


def evidence(row: dict, tagged: dict, sized: dict, label: str) -> dict:
    name = row.get("name", "")
    tags = tagged.get("tags") or {}
    pattern = frozenset(tags.get("pattern", [])) - _PLAIN_PATTERN
    # 옵션의 핏은 여러 핏을 함께 설명하는 상세 태그보다 우선한다.
    fit = _fits(row.get("options", "")) or _fits(name) or _fits(" ".join(tags.get("silhouette", [])))
    collab = re.findall(r"(?:\b[\w.-]+\s+[xX×]\s+[\w.-]+\b|\[[^\]]*(?:협업|콜라보)[^\]]*\])", name)
    options = option_colors(row.get("options", ""))
    colors = frozenset(options or row.get("representative_color", "").split("·")) - {""}
    count = re.search(r"(?i)(?<!\d)(\d{1,2})\s*(?:colou?rs?|컬러|색상?)[)\]]?\s*"
                      r"(?:[_-][A-Za-z0-9-]{2,20}|\([A-Za-z0-9-]{2,20}\))?\s*$", name)
    declared = int(count.group(1)) if count else 0
    return {
        "mat": _material(tagged), "measure": _measurements(sized), "size_source": sized.get("source", ""),
        "pattern": pattern, "pattern_known": bool(tags.get("pattern")),
        "fit": fit, "collab": tuple(norm(x) for x in collab),
        "condition": bool(re.search(r"B\s*품|리퍼브|리퍼비시|refurb|B[ -]?GRADE|불량", name, re.I)),
        "set": bool(re.search(r"(?<![a-z])set(?![a-z])|세트|셋업\s*세트|\d+\s*(?:팩|pack)\b", name, re.I)),
        "gender": tuple(re.findall(r"(?i)\b(?:mens?|womens?|unisex)\b|남성용|여성용", name.lower())),
        "label": norm(re.sub(r"_[A-Za-z0-9-]{5,}$", "", label)) if label and not MULTI.fullmatch(label) else "",
        "representative": row.get("representative_color", ""),
        "container": len(colors) > 1 or declared > 1, "colors": colors,
        "incomplete_colors": declared > len(colors),
    }


def conflicts(a: dict, b: dict) -> list[str]:
    """누락을 충돌로 취급하지 않고 관측된 차이만 반환한다."""
    a, b = a["evidence"], b["evidence"]
    why = []
    for key in ("condition", "set", "collab"):
        if a[key] != b[key]:
            why.append(key)
    if a["gender"] and b["gender"] and a["gender"] != b["gender"]:
        why.append("gender")
    if a["fit"] and b["fit"] and a["fit"].isdisjoint(b["fit"]):
        why.append("fit")
    if a["pattern_known"] and b["pattern_known"] and a["pattern"] != b["pattern"]:
        why.append("pattern")
    if any(_mat_differs(a["mat"][k], b["mat"][k]) for k in a["mat"].keys() & b["mat"].keys()):
        why.append("material")
    different = [k for k in a["measure"].keys() & b["measure"].keys()
                 if abs(a["measure"][k] - b["measure"][k]) > 2]
    # OCR 표에서 세 개 이상의 값이 옆 항목으로 그대로 이동했다면 열 정렬 오류 후보다.
    # 올바른 축이 보장되지 않은 표를 실측 충돌의 근거로 쓰지 않는다.
    shifted = False
    for suspect, other in ((a, b), (b, a)):
        if suspect["size_source"] != "ocr":
            continue
        moved_axes = {k[1] for k in different
                      if any(size == k[0] and axis != k[1] and abs(value - suspect["measure"][k]) < .05
                             for (size, axis), value in other["measure"].items())}
        shifted |= len(moved_axes) >= 3
    if different and not shifted:
        why.append("measurement")
    if ((a["container"] or b["container"]) and a["colors"] & b["colors"]
            or a["incomplete_colors"] or b["incomplete_colors"]):
        why.append("option_overlap")
    if a["label"] and a["label"] == b["label"]:
        why.append("duplicate_color")
    elif bool(a["label"]) != bool(b["label"]):
        # 한 등록만 색 꼬리가 없을 때: 단일 기본색·동일 혼용·실측이 모두 있는 중복 후보만 제외.
        labeled, plain = (a, b) if a["label"] else (b, a)
        common_m = a["mat"].keys() & b["mat"].keys()
        common_s = a["measure"].keys() & b["measure"].keys()
        basic = C._FIELD_COLOR.get(labeled["label"]) or _match_color(labeled["label"])
        if (len(labeled["label"].split()) == 1 and basic and basic == plain["representative"]
                and common_m and all(a["mat"][k] == b["mat"][k] for k in common_m)
                and len(common_s) >= 2 and all(a["measure"][k] == b["measure"][k] for k in common_s)):
            why.append("duplicate_color")
    return why


def partition(g: list[dict]) -> list[list[dict]]:
    """안정된 순서의 complete-link 분할: 모든 쌍이 통과해야 같은 묶음이다."""
    out: list[list[dict]] = []
    for rec in sorted(g, key=lambda r: (not r["active"], r["product_no"], r["source_url"])):
        for cluster in out:
            if all(not conflicts(rec, other) for other in cluster):
                cluster.append(rec)
                break
        else:
            out.append([rec])
    return out


def build(rows: list[dict], tags: dict | None = None, sizes: dict | None = None) -> tuple[list[dict], dict, Counter]:
    tags, sizes = tags or {}, sizes or {}

    groups: dict[tuple, list[dict]] = defaultdict(list)
    shape = Counter()
    recs = []
    for r in rows:
        base, label, color = split_color(r["name"])
        tail = r["name"][len(base):]
        if not label:
            opts = [o.strip() for o in r.get("options", "").split(" | ")]
            if len(opts) == 1 and _is_color(opts[0]):
                color = _is_color(opts[0])
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
               "method": "name_tail" if label else "same_name",
               "active": r.get("status") == "ON_SALE" and not r.get("delisted")}
        rec["evidence"] = evidence(r, tags.get(r["source_url"], {}), sizes.get(r["source_url"], {}), label)
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
    checked = {}
    for key, candidates in groups.items():
        clusters = partition(candidates)
        for cluster in clusters:
            # 분리되지 않은 묶음의 ID는 보존한다. 분리된 묶음은 구성원으로 식별한다.
            suffix = "|" + ",".join(sorted(x["product_no"] for x in cluster)) if len(clusters) > 1 else ""
            h = hashlib.sha1(f"{key[0]}|{key[1]}|{key[2]}{suffix}".encode()).hexdigest()[:10]
            checked[key + (suffix,)] = cluster
            for x in cluster:
                x["style_id"] = f"{key[0]}:{h}"
                x["style_size"] = len(cluster)
    return recs, checked, shape


def main() -> None:
    with SRC.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # 입력 경로가 없으면 조용히 무검사 병합하지 않는다.
    with (DATA / "product_tags_full.json").open(encoding="utf-8") as f:
        tags = json.load(f)
    with (DATA / "product_sizes.json").open(encoding="utf-8") as f:
        sizes = json.load(f)
    recs, groups, shape = build(rows, tags, sizes)
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
