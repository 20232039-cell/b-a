#!/usr/bin/env python3
"""사진이 말하는 무늬 · 색 — 이미 계산한 사진 임베딩(Marqo fashionSigLIP)에 글 프롬프트를 대어 상품마다 확률을 적는다.

    python scripts/photo_attrs.py                 # data/photo_attrs.json
    python scripts/photo_attrs.py --out /tmp/pa.json

왜: 무늬(pt)와 대표색(co)은 글(이름 · 설명 · 옵션)에서만 정했다. 그래서
  · 설명 끝 세탁 안내 「중성세제 뒷면을 체크하여」가 lartisan 의류 518벌을 체크무늬로 만들었고(사진 17장 중 10장 무지),
  · 이름에 카모 · 레오파드가 없으면 큰 프린트도 무늬가 안 됐고,
  · 「_RED IVORY」 같은 두 색 이름에서 로고색을 대표색으로 골랐다(mardi-mercredi 12/12 틀림).
사진 선별기(qa27 zs.py)를 360벌 눈으로 검증해 방향마다 믿을 문턱을 쟀다(2026-10-27, zs/verify_stats.md).
그 문턱은 이 파일의 결정 함수(pattern_photo · color_photo)에 그대로 적었다 — 숫자를 바꾸려면 다시 눈으로 재고 바꾼다.

임베딩은 옷만 자른 판(siglip_crop)이 있으면 그것, 없으면 대표 사진 통째(siglip) — 선별기와 같은 순서다
(similar_items 처럼 판 하나로 통일하지 않는다: 여기는 상품 하나씩 따로 재는 값이라 섞여도 된다).

결과: data/photo_attrs.json — {"about", "pattern": [프롬프트 이름…], "color": [계열…],
  "items": {source_url: {"p": [무늬 확률 ×10000 …], "c": [색 계열 확률 ×1000 …]}}}
비교는 결정 함수만 한다 — 읽는 쪽(similar_items · export_app_data)은 load() 와 결정 함수만 부른다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "photo_attrs.json"
EMB_DIRS = ("siglip", "siglip_crop")   # 뒤가 이긴다 — 옷만 자른 판이 있으면 그것
MODEL = "hf-hub:Marqo/marqo-fashionSigLIP"

# ── 무늬 프롬프트(선별기와 같은 문장 · 같은 차례 — 차례가 곧 저장 자리다) ─────────────
PAT = [("plain", "a plain solid color garment"), ("stripe", "a striped garment"),
       ("check", "a checked plaid garment"), ("graphic", "a garment with a graphic print"),
       ("floral", "a floral pattern garment"), ("camo", "a camouflage pattern garment"),
       ("leopard", "a leopard print garment"), ("dot", "a polka dot garment"),
       ("logo", "a garment with a large logo print")]
PAT_NAMES = [k for k, _ in PAT]
# 사진 무늬 → 태그 사전 값(similar_items 의 같은 무늬 묶기에 쓴다)
PAT_TAG = {"stripe": "스트라이프", "check": "체크", "graphic": "그래픽", "floral": "플로럴", "camo": "카모",
           "leopard": "애니멀프린트", "dot": "도트", "logo": "그래픽"}

# ── 색: 계열 단위로(검정 · 차콜 · 네이비는 사진에서 헷갈린다 — 선별기와 같은 표) ─────────
COL = ["black", "gray", "brown", "navy", "white", "charcoal", "blue", "ivory", "beige", "pink", "khaki", "green",
       "cream", "red", "olive", "sky blue", "indigo", "yellow", "purple", "burgundy", "silver", "mint", "oatmeal",
       "camel", "orange"]
FAM = {"black": "dark", "charcoal": "dark", "navy": "dark", "indigo": "blue", "blue": "blue", "sky blue": "blue",
       "white": "light", "ivory": "light", "cream": "light", "oatmeal": "light", "beige": "beige", "camel": "beige",
       "brown": "brown", "gray": "gray", "silver": "gray", "khaki": "green", "olive": "green", "green": "green",
       "mint": "green", "pink": "pink", "red": "red", "burgundy": "red", "purple": "purple", "yellow": "yellow",
       "orange": "orange"}
FAMS = sorted(set(FAM.values()))

# 앱 대표색(수집기 어휘) → 사진 계열. 표에 없는 색(멀티 · 골드 · 라벤더 …)은 사진으로 견주지 않는다.
CO_FAM = {"블랙": "dark", "차콜": "dark", "차콜그레이": "dark", "네이비": "dark", "잉크": "dark", "건메탈": "dark",
          "그레이": "gray", "실버": "gray", "그레이지": "gray",
          "브라운": "brown", "초코": "brown", "모카": "brown", "다크브라운": "brown",
          "화이트": "light", "아이보리": "light", "크림": "light", "오트밀": "light", "내추럴": "light",
          "블루": "blue", "인디고": "blue", "스카이블루": "blue", "코발트": "blue",
          "베이지": "beige", "카멜": "beige", "샌드": "beige",
          "핑크": "pink", "카키": "green", "올리브": "green", "그린": "green", "민트": "green",
          "레드": "red", "버건디": "red", "퍼플": "purple", "옐로우": "yellow", "오렌지": "orange"}
# 사진 계열 → 채울 때 쓰는 앱 색(유채 계열만 — light · dark 는 검정 · 차콜 · 네이비 / 흰 · 아이보리 · 크림을 못 가른다)
FAM_CO = {"gray": "그레이", "pink": "핑크", "blue": "블루", "green": "그린", "brown": "브라운", "red": "레드",
          "purple": "퍼플"}
CHROMA = set(FAM_CO)

# 사진 선별기를 쓰는 품목 — 검증한 것이 옷뿐이다(잡화는 모델이 같이 입은 옷을 읽는다, verify_stats 「Category」 1번).
GARMENT_CODES = ("tops", "outer", "bottoms", "skirt", "dress")
# 숄·볼레로는 몸판이 열려 함께 입은 옷이 더 크게 찍힌다(open-yy 3774, 2026-10-28 표본).
_LAYER_PIECE = re.compile(r"\b(?:shrug|bolero|shawl)\b|볼레로|슈러그|숄(?:\s|$)", re.I)


def photo_safe(name: str) -> bool:
    return not _LAYER_PIECE.search(name or "")


def load(path: Path | None = None) -> dict:
    p = path or OUT
    if not p.exists():
        return {}
    return (json.loads(p.read_text(encoding="utf-8")) or {}).get("items") or {}


# ── 결정 함수 — 문턱은 zs/verify_stats.md 의 눈 검증(2026-10-27) ─────────────────────────
def pattern_photo(e: dict | None) -> tuple[str, str]:
    """→ ("plain" | "pattern" | "", 사진 무늬 이름). 빈 값은 사진이 확신하지 못한다는 뜻.

    · 무지: P(plain) > 0.9 — 앱 pt=1 중 이것에 걸린 29벌 가운데 24벌이 정말 무지였고, 틀린 5벌은 모두 이름에
      무늬 낱말이 있었다(STRIPED JACQUARD · CHECKED · Gingham · Stripe). 이름 낱말을 이기지 않는 것은 부르는 쪽 몫이다.
    · 줄무늬 · 체크 · 꽃 · 카모 · 호피 · 도트: 1 − P(plain) ≥ 0.998 이면 22/22(360벌 검증), 손 검증 2,514벌에서
      ≥ 0.998 1005/1034 · 0.997~0.998 115/124 — 0.997 부터 쓴다.
    · 큰 그래픽 · 큰 로고: 1 − P(plain) ≥ 0.985 이면 40/41. 작은 로고 프린트는 무지(대표님 2026-10-06)라
      작은 로고가 여기 닿지 않는 문턱이어야 한다 — 0.985 아래에서 작은 가슴 로고가 섞였다(ir-ryu-6122288 · birthdaysuit-1331).
    선별기와 같은 조건(P(plain) < 0.05 · 가장 센 무늬 > 0.7)을 먼저 본다.
    """
    p = (e or {}).get("p")
    if not p:
        return "", ""
    pr = [x / 10000 for x in p]
    plain = pr[0]
    if plain > 0.9:
        return "plain", "plain"
    k = max(range(1, len(pr)), key=lambda i: pr[i])
    name = PAT_NAMES[k]
    # 검증한 선별기의 조건을 그대로 쓴다 — 그래픽·로고 확률을 합치면 미검증 표본까지 늘어난다(2026-10-28).
    top = pr[k]
    if plain < 0.05 and top > 0.7:
        # 줄무늬 · 체크 등: 처음 360벌 검증은 0.998 이었고, 손으로 본 불일치 후보 2,514벌(qa27 vis/out_pattern1, 2026-10-28)에서
        # 0.997~0.998 띠가 115/124(93%)라 0.997 로 내렸다. 0.995~0.997 은 118/138(86%)이라 안 쓴다.
        need = 0.985 if name in ("graphic", "logo") else 0.997
        if 1 - plain >= need - 1e-9:
            return "pattern", name
    return "", name


def color_family(e: dict | None) -> tuple[str, float, dict]:
    c = (e or {}).get("c")
    if not c:
        return "", 0.0, {}
    fam = {f: v / 1000 for f, v in zip(FAMS, c)}
    top = max(fam, key=fam.get)
    return top, fam[top], fam


def color_photo(co: str, e: dict | None, *, alt: str = "") -> str:
    """사진이 대표색을 고치거나 채우라고 할 때 그 색, 아니면 "". alt: 이름이 함께 적은 다른 색(두 색 이름의 나머지 하나).

    · 대표색이 비었으면: 사진이 유채 계열(그레이 · 핑크 · 블루 · 그린 · 브라운 · 레드 · 퍼플)을 0.8 넘게 말할 때만
      그 계열 이름으로 채운다. 흰 · 검정 계열은 아이보리/크림 · 블랙/차콜/네이비를 못 가르니 안 채운다.
    · 대표색이 있으면: 선별기 조건(사진 계열 > 0.8 · 지금 색 계열 < 0.03)에서
        - 유채 계열이면 고친다(37/40 맞음). 옐로우 → light, 브라운 → dark 는 절대 안 고친다(1/16 · 4/10).
        - light · dark 는 0.95 이상이고 이름이 적은 다른 색이 그 계열일 때만 그 색으로(52% → 상 띠 88%,
          「_NAVY IVORY」 남색 레깅스 · 「black ecru」 에크루 가방).
      이름이 적은 다른 색이 사진 계열과 같으면 계열 이름보다 그 색이 낫다(「_MINT WHITE」는 그린이 아니라 민트).
    """
    top, pv, fam = color_family(e)
    if not top:
        return ""
    if not co:
        return FAM_CO[top] if top in CHROMA and pv > 0.8 else ""
    mine = CO_FAM.get(co)
    if not mine or top == mine or pv <= 0.8 or fam.get(mine, 0) >= 0.03:
        return ""
    if (co == "옐로우" and top == "light") or (mine == "brown" and top == "dark"):
        return ""
    if alt and CO_FAM.get(alt) == top and (top in CHROMA or pv >= 0.95):
        return alt
    if top in CHROMA:
        return FAM_CO[top]
    return ""


def color_among(colors: list[str], e: dict | None) -> str:
    """여러 색을 한 목록에 파는 상품(대표색 「블랙·그레이」) — 대표 사진이 보이는 색이 판매색 중 딱 하나의 계열이면 그 색.
    검증한 유채 계열만 0.8 넘을 때 채운다. light · dark 는 두 색 이름의 잘못 고른 색을 고칠 때만 쓴다(color_photo).
    같은 계열 판매색이 둘이면(블랙 · 차콜) 못 고른다.
    grove 790/915 벌이 이 꼴이라 대표색이 비어 색 필터에서 빠졌다(2026-10-27 사진 QA)."""
    top, pv, _ = color_family(e)
    if not top:
        return ""
    if top not in CHROMA or pv <= 0.8:
        return ""
    hit = [c for c in colors if CO_FAM.get(c) == top]
    return hit[0] if len(hit) == 1 else ""


# ── 무늬 있음(pt) — 상품 하나씩, 「비슷한 옷」 목록과 따로 ─────────────────────────────
# 예전에는 similar_items 가 판매중 옷의 기준별 목록을 만들며 낸 비트(f & 8)를 export 가 옮겼다. 그래서 기준별 이웃이
# 하나도 안 남은 상품 · 품절 · 잡화는 무늬가 있어도 pt 가 비었다(카모 · 레오파드 · 체크가 이름에 있는데 pt 없음 1,390벌,
# 2026-10-27 사진 QA). 이제 두 곳이 모두 이 함수 하나를 부른다.
#
# 근거의 세기 차례: ① 이름의 무늬 낱말 > ② 사진(옷만 · 검증한 문턱) > ③ 본문 태그 · 그래픽 태그.
#   ① 사진이 무지라 해도 이름이 이긴다 — 사진이 틀린 5벌이 모두 이름에 무늬가 있었다(마이크로 깅엄 · 톤온톤 스트라이프).
#   ③ 본문은 남의 말이 섞인다(세탁 안내 「체크하여」 · 안감 무늬 · 함께 파는 옷) — 사진이 무지라 하면 버린다.
# 작은 로고 프린트는 무지 · 큰 그래픽 · 전면 프린트는 무늬(대표님 2026-10-06). 사진이 있으면 그래픽은 사진이 크다고
# 할 때만(1 − P(plain) ≥ 0.985) 무늬다 — 그래픽 태그 + photo_pred graphic ≥ 0.7 만으로는 cayl 의 작은 가슴 로고 약 370벌이
# 무늬가 됐다(사진 13장 중 12장 무지).
# 무지로 치는 태그 — 멜란지는 실 색, 케이블은 짜임이다(케이블 니트가 무늬로 뜬 것 uniform-bridge-10561 · nothing-written-3547).
PLAIN_PAT = ("단색", "멜란지", "케이블")
# 이름에서만 읽는 무늬 낱말 — 로고 이름이 되는 낱말(flower · dot 홀로 · checkmate)은 뺐다:
# 이름 낱말 36벌 표본에서 12벌이 「FLOWER」 로고 · 「DOTCOM」 · 「Checkmate」 참이었다(photoB stats).
NAME_PAT = {
    "스트라이프": r"(?<![a-z])(?:pin)?stripe[sd]?(?![a-z])|(?<![a-z])striping(?![a-z])|(?:핀\s*)?스트라이프|줄무늬|단가라",
    "체크": r"(?<![a-z])check(?:ed|s)?(?![a-z])(?!\s*(?:point|out|in\b))|(?<![a-z])(?:plaid|tartan|gingham|madras|checker(?:ed|board)?)(?![a-z])"
            r"|체크(?!\s*(?:하|해|했|포인트|아웃|리스트))|깅엄|타탄|플레이드|체커보드|마드라스",
    "하운드투스": r"(?<![a-z])houndstooth|hound'?s[\s-]?tooth|하운드\s*투스|천조",
    "헤링본": r"(?<![a-z])herringbone(?![a-z])(?!\s*(?:tape|band))|헤링본(?!\s*(?:테이프|밴드|스트링))",
    "카모": r"(?<![a-z])camo(?:uflage|flage)?(?![a-z])|카모(?!마일)|밀리터리\s*패턴",
    "애니멀프린트": r"(?<![a-z])(?:leopard|zebra|cheetah|python|snake\s*skin|snakeskin|animal\s*print|tiger\s*print|cow\s*print)(?![a-z])"
                  r"|레오파드|호피|지브라|파이톤|뱀피|애니멀\s*프린트|카우\s*프린트",
    "플로럴": r"(?<![a-z])(?:floral|flower(?:ed)?\s*(?:print|pattern)|paisley|botanical\s*print)(?![a-z])|플로럴|꽃무늬|꽃\s*패턴|플라워\s*(?:프린트|패턴)|페이즐리|잔꽃",
    "도트": r"(?<![a-z])polka[\s-]?dots?|(?<![a-z])dot(?:s|ted)?(?![a-z])(?!\s*(?:button|snap|com))|도트(?!\s*(?:단추|버튼|스냅))|물방울",
    "아가일": r"(?<![a-z])argyle(?![a-z])|아가일|(?<![a-z])fair[\s-]?isle(?![a-z])|페어\s*아일|노르딕",
    "타이다이": r"(?<![a-z])tie[\s-]?dye(?:d)?(?![a-z])|타이\s*다이",
    "패치워크": r"(?<![a-z])patchwork(?![a-z])|패치\s*워크",
    "자카드": r"(?<![a-z])jacquard(?![a-z])|자카드|자가드",
}
_NAME_PAT = [(k, re.compile(v, re.I)) for k, v in NAME_PAT.items()]
# 이름에 무늬 낱말이 있어도 무늬가 아닌 꼴 — 「NO STRIPE」 같은 부정 · 「카모 컬러」만이 아닌 것은 위에서 거른다
_NAME_NOT = re.compile(r"\bno\s+(?:stripe|check)|무지", re.I)


# 홀로는 로고 · 라인 이름일 수 있는 낱말(FLOWERMARDI · ROSE POINT · PATTERN) — 사진도 같은 무늬를 가리킬 때만 받는다.
# 사진이 문턱(0.997)에 못 미친 꽃무늬 누락이 손 검증 표본에서 이 꼴로 몰려 있었다(「FLOWER CARDIGAN」 · 「Flower Half Denim Pants」).
_WEAK_NAME = re.compile(r"(?<![a-z])(?:flowers?|rose|pattern(?:ed)?|dots?)(?![a-z])|플라워|꽃|로즈|패턴|도트", re.I)
_WEAK_KIND = {"floral", "dot", "stripe", "check", "camo", "leopard"}


def weak_name_photo(name: str, e: dict | None) -> str:
    """약한 이름 낱말 + 사진(무지 < 0.05 · 고전 무늬가 가장 셈)이 함께 말하면 그 무늬, 아니면 ""."""
    m = _WEAK_NAME.search(name or "")
    p = (e or {}).get("p")
    if not m or not p:
        return ""
    pr = [x / 10000 for x in p]
    k = max(range(1, len(pr)), key=lambda i: pr[i])
    kind = PAT_NAMES[k]
    if pr[0] >= 0.05 or kind not in _WEAK_KIND:
        return ""
    w = m.group(0).lower()
    if w in ("flower", "flowers", "rose", "플라워", "꽃", "로즈") and kind != "floral":
        return ""
    if w in ("dot", "dots", "도트") and kind != "dot":
        return ""
    return kind


def name_patterns(name: str) -> set[str]:
    low = (name or "").lower()
    if _NAME_NOT.search(low):
        return set()
    return {k for k, rx in _NAME_PAT if rx.search(low)}


# 아주 가는 줄무늬를 쓰는 매장 — 대표 사진(작은 그림)에서는 무지로 보인다. 사진이 무지라 해도 글의 무늬를 지우지 않는다
# (손 검증 2026-10-28: dunst · coor · divein · steady-everywear · ourselves · belier 의 「무지로 보이는」 줄무늬).
FINE_STRIPE_STORES = {"dunst", "coor", "divein", "steady-everywear", "ourselves", "belier"}


def pattern_keys(name: str, t: dict, *, photo: dict | None = None, graphic: float = 0.0,
                 garment: bool = True, brand: str = "") -> set[str]:
    """상품의 무늬 값 모음 — 비면 무지(또는 모름). t: product_tags 의 tags, graphic: photo_pred 의 graphic 확신,
    photo: photo_attrs 의 한 줄(옷만), garment: 옷이면 본문 태그 · 사진도 본다(잡화는 이름만 — 사진 선별기를 검증 안 했다)."""
    named = name_patterns(name)
    if not garment:
        return named
    text = {v for v in (t.get("pattern") or []) if v not in PLAIN_PAT}
    if not photo_safe(name):
        return named | text
    tag_graphic = "그래픽" in (t.get("design_element") or []) and graphic >= GRAPHIC_MIN
    verdict, kind = pattern_photo(photo) if photo else ("", "")
    if named:
        return named | text
    if verdict == "plain":
        return text if brand in FINE_STRIPE_STORES else set()
    if verdict == "pattern":
        return text | {PAT_TAG[kind]}
    weak = weak_name_photo(name, photo)
    if weak:
        return text | {PAT_TAG[weak]}
    if photo:
        # 사진 단독 추가 문턱과 기존 글+분류기 근거의 보존은 다르다. 그래픽/로고로 확률이 나뉘어 보류된 큰 프린트를
        # 전부 지우면 「19」 맨투맨까지 무지가 된다(beyond-closet 11099). 글과 기존 분류기가 함께 그래픽이라 하고,
        # 사진도 무지 확률 < 0.1 · 그래픽/로고 합 > 0.7일 때만 남긴다. 작은 가슴 로고(cayl 3686)는 이 조건에 못 든다.
        pr = photo.get("p") or []
        keep = (tag_graphic and len(pr) == len(PAT_NAMES) and pr[0] < 1000
                and pr[PAT_NAMES.index("graphic")] + pr[PAT_NAMES.index("logo")] > 7000)
        return text | ({"그래픽"} if keep else set())
    return text | ({"그래픽"} if tag_graphic else set())


GRAPHIC_MIN = 0.7
# 사람이 사진으로 확인한 무늬 고침 — data/pattern_fix.csv(id,pt,why). color_fix.csv 처럼 무엇보다 앞선다.
# 규칙이 못 잡는 꼴(함께 입은 옷의 무늬를 읽은 사진 · 톤온톤 무늬 · 문턱 아래 사진)을 한 벌씩 고친다(2026-10-28).
PATTERN_FIX = DATA / "pattern_fix.csv"


def load_pattern_fix(path: Path | None = None) -> dict[str, int]:
    p = path or PATTERN_FIX
    if not p.exists():
        return {}
    with p.open(encoding="utf-8-sig") as f:
        return {r["id"].strip(): int(r["pt"]) for r in csv.DictReader(f) if (r.get("pt") or "").strip() in ("0", "1")}


def apply_pattern_fix(pid: str, keys: set[str], fix: dict[str, int]) -> set[str]:
    v = fix.get(pid)
    if v == 0:
        return set()
    if v == 1 and not keys:
        return {"무늬"}
    return keys


# ── 계산 ────────────────────────────────────────────────────────────────────────
def score_embeddings(urls: set[str], TP, TC, scale: float) -> dict:
    """조각 하나씩 점수만 남긴다. 전체 임베딩 두 판과 모델을 함께 잡으면 메모리가 넘친다(2026-10-28)."""
    import numpy as np

    fi = {f: i for i, f in enumerate(FAMS)}
    agg = np.zeros((len(COL), len(FAMS)), np.float32)
    for k, c in enumerate(COL):
        agg[k, fi[FAM[c]]] = 1

    def soft(X):
        X = X - X.max(1, keepdims=True)
        e = np.exp(X)
        return e / e.sum(1, keepdims=True)

    out = {}
    for model_dir in EMB_DIRS:
        for path in sorted((DATA / "emb" / model_dir).glob("part_*.npz")):
            with np.load(path, allow_pickle=False) as z:
                meta = json.loads(str(z["meta"]))
                take = [i for i, m in enumerate(meta) if m["u"] in urls]
                if not take:
                    continue
                V = np.asarray(z["E"][take], dtype=np.float32)
            V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-8
            P = soft(scale * (V @ TP.T))
            C = soft(scale * (V @ TC.T)) @ agg
            for i, p, c in zip(take, P, C):
                # 통째 사진 뒤 자른 사진을 읽어 같은 주소를 덮는다. 누락된 상품은 통째 판의 점수를 보존한다.
                out[meta[i]["u"]] = {"p": [int(round(x * 10000)) for x in p],
                                     "c": [int(round(x * 1000)) for x in c]}
        print(f"{model_dir}: 누적 {len(out):,}벌", flush=True)
    return {u: out[u] for u in sorted(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    import numpy as np
    import open_clip
    import torch

    urls = {r["source_url"] for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    model, _, _ = open_clip.create_model_and_transforms(MODEL)
    tok = open_clip.get_tokenizer(MODEL)
    model.eval()

    def txt(prompts):
        with torch.no_grad():
            t = model.encode_text(tok(prompts)).float()
        t /= t.norm(dim=-1, keepdim=True)
        return t.numpy()

    scale = float(model.logit_scale.exp().detach())
    TP = txt([s for _, s in PAT])
    TC = txt([f"a {c} colored garment" for c in COL])
    # 글 임베딩만 남기고 큰 모델은 점수 계산 전에 놓는다.
    del model, tok
    import gc
    gc.collect()
    out = score_embeddings(urls, TP, TC, scale)
    print(f"임베딩 있는 상품 {len(out):,} / {len(urls):,}", flush=True)
    Path(args.out).write_text(json.dumps({
        "about": "사진 임베딩(Marqo fashionSigLIP, 옷만 자른 판 우선)에 글 프롬프트를 댄 확률. p 무늬 ×10000(pattern 차례), "
                 "c 색 계열 ×1000(color 차례). 문턱과 쓰는 법은 scripts/photo_attrs.py 의 결정 함수.",
        "model": MODEL, "pattern": PAT_NAMES, "color": FAMS, "items": out,
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    n_plain = sum(1 for e in out.values() if pattern_photo(e)[0] == "plain")
    n_pat = sum(1 for e in out.values() if pattern_photo(e)[0] == "pattern")
    print(f"저장 {len(out):,}벌 → {args.out} · 사진 무지 {n_plain:,} · 사진 무늬 {n_pat:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
