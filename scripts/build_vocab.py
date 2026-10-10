"""어휘 사전(schema/vocab.json)을 지금 쓰는 표들에서 모아 낸다.

왜 모으기만 하나 (2026-10-03, 코파일럿 세션 부탁)
  LAYER 데이터를 온톨로지처럼 쓰려면 같은 개념이 한 곳에 정의돼 있어야 한다. 그런데 품목 분류만 해도
  크롤러(crawl_cafe24.ITEM_TYPE_VOCAB · GROUP_OF), 앱(layer-web garmentCategory.ts TABLE), 코파일럿
  (layer-copilot src/taxonomy.js GROUPS) 세 곳에 따로 적혀 있고, 「재킷/자켓」 같은 표기 차이로 비교군이
  빠진 일이 있었다. 이 스크립트는 **새 규칙을 만들지 않는다** — 크롤러 · 태거 · 혼용률 해석이 실제로 쓰는
  표를 읽어 한 파일로 옮긴다. 그래서 표가 바뀌면 다시 돌리기만 하면 사전이 따라간다.
  손으로 정한 것은 아래 _HIER(큰 분류 묶음 — 코파일럿 GROUPS 를 바탕으로) · 부위 규칙 · 부자재 후보 ·
  photo_type 정의뿐이다.

  이번에는 사전만 낸다. product_tags_full.json 같은 출력값은 바꾸지 않는다(사전 검증 뒤 따로).

    python scripts/build_vocab.py --out ../layer-brand-agent-master/schema/vocab.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cafe24 as C  # noqa: E402
import product_desc as P  # noqa: E402
import tag_items as T  # noqa: E402
import grade_origin as GO  # noqa: E402

VERSION = "1.2.0"

# ─── 품목 계층 ─────────────────────────────────────────────────────────────────
# 갈래 → 큰 분류 → 세부(카탈로그 subtype 값 그대로).
# 큰 분류 묶음은 코파일럿 GROUPS 를 그대로 따르고, 카탈로그에 있는데 GROUPS 에 없던 세부만 더했다
# (바디수트 → 탑 · 수영복/파자마 → 이너·홈·스윔 · 수영복하의/언더웨어 → 이너·스윔 하의). 더한 것은 copilot=false.
# base 는 크롤 원본이 더 나누지 않은 이름(코트 · 재킷 · 팬츠 …) — 큰 분류에는 들고 세부 목록에는 안 보인다.
from item_hier import HIER as _HIER  # 품목 계층은 item_hier.py 한 곳에(의존성 없는 파일 — export · similar 도 읽는다)
# 코파일럿 GROUPS 에 없던 것(이 사전이 더한 것)
_NOT_IN_COPILOT = {"무스탕", "라이더자켓", "레이서자켓", "블라우스", "하프셔츠", "로브코트", "니트집업", "하프집업", "폴로", "트레이닝팬츠", "퍼티그팬츠", "버뮤다팬츠", "데님팬츠", "슬리브리스", "캐미솔", "브라탑", "튜브탑", "니트베스트", "패딩베스트", "슬랙스", "미니스커트", "미디스커트", "롱스커트", "미니원피스", "미디원피스", "롱원피스", "슬립원피스", "셔츠원피스", "바디수트", "뷔스티에", "트랙자켓", "오버셔츠", "이너·홈·스윔", "수영복", "파자마", "이너·스윔 하의", "수영복하의", "언더웨어", "기타 의류", "슈트"}
_COPILOT_CATEGORY = {"코트": "Outerwear", "자켓": "Outerwear", "점퍼·패딩": "Outerwear", "니트": "Knitwear",
                     "셔츠": "Shirts", "티셔츠": "Tops", "맨투맨·후드": "Tops", "탑": "Tops", "베스트": "Tops",
                     "이너·홈·스윔": "Tops", "팬츠": "Pants", "데님팬츠": "Denim", "스커트": "Skirts",
                     "이너·스윔 하의": "Pants", "원피스": "Dresses", "슈트": "Suiting"}
# 함께 보일 갈래(also) — 2026-10-03 갈래 결정(코파일럿 세션 추천 · 사람 전달). 대표 갈래는 하나(비교군이 겹치지 않게),
# 탐색 화면은 also 갈래에서도 찾히게. 원피스는 하의 갈래(2026-10-06, item_hier 주석 — 크롤러 group 칸은 아직 상의).
_ALSO = {"가디건": ["아우터"], "베스트": ["아우터"], "집업": ["아우터"], "후드집업": ["아우터"], "오버셔츠": ["상의"]}
_SECTION_ID = {"상의": "tops", "하의": "bottoms", "아우터": "outer", "원피스": "dress", "기타 의류": "other-apparel",
               "가방": "bags", "신발": "shoes", "모자": "headwear", "액세서리": "accessories", "주얼리": "jewelry"}

# 앱(garmentCategory.ts TABLE)만 쓰는 이름 → 카탈로그 subtype. 카탈로그 상품명 전수로 확인한 값이다
# (예: 「denim shorts」 343벌이 숏팬츠, 「overshirt」 49벌이 셔츠, 「track jacket」 134벌이 재킷).
_WEB_ALIAS = {"후디": "후드", "슬리브리스": "슬리브리스", "래글런": "롱슬리브", "스웨터": "니트", "블라우스": "블라우스", "폴로": "폴로",
              "데님팬츠": "데님팬츠", "쇼츠": "숏팬츠", "데님쇼츠": "숏팬츠", "미디스커트": "미디스커트", "미니스커트": "미니스커트",
              "치노팬츠": "치노", "조거": "트레이닝팬츠", "슬랙스": "슬랙스", "파라슈트": "파라슈트팬츠",
              # 「레더/라이더스」(앱 칩)는 소재(레더)와 모양(라이더스)이 섞인 이름이라 카탈로그 짝이 없다 — 앱이 라이더자켓 + 소재 칩으로 바꿀 때까지 어긋남 표에 남긴다(2026-10-05)
              "플리스/뽀글이": "플리스", "다운재킷": "패딩", "나일론/코치재킷": "코치자켓",
              "트레이닝재킷": "트랙자켓", "트렌치코트": "트렌치", "아노락": "바람막이", "바시티/스타디움": "바시티/스타디움",
              "오버셔츠": "오버셔츠", "아우터셔츠": "오버셔츠"}
# 앱 TABLE 의 값(tops/bottoms/outer/null) — 어긋남 표를 만들 때 쓴다. layer-web master 의 garmentCategory.ts 를 옮겨 적었다.
_WEB_TABLE = {
    **{k: "tops" for k in "티셔츠 셔츠 블라우스 하프셔츠 니트 폴로니트 하프집업니트 니트집업 하프집업 슬리브리스 캐미솔 브라탑 튜브탑 니트베스트 패딩베스트 후디 롱슬리브 저지 반팔 탑 래글런 라글란 링거티 케이블니트 아가일니트 스웨터 블라우스 폴로 맨투맨 상의 웨스턴셔츠".split()},
    **{k: "bottoms" for k in "팬츠 슬랙스 데님팬츠 쇼츠 카고팬츠 버뮤다 스웨트팬츠 데님쇼츠 스커트 미디스커트 미니스커트 롱스커트 미니원피스 미디원피스 롱원피스 슬립원피스 니트원피스 셔츠원피스 치노팬츠 트랙팬츠 치노 파티그팬츠 카펜터팬츠 파라슈트팬츠 조거 슬랙스 파라슈트 원피스 하의".split()},
    **{k: "outer" for k in ("재킷 가디건 집업 점퍼 후드집업 트러커재킷 파카 블루종 MA-1/봄버 바람막이 코트 레더/라이더스 플리스/뽀글이 "
                            "블레이저 다운재킷 나일론/코치재킷 트레이닝재킷 트렌치코트 트렌치 싱글코트 더블코트 더플코트 맥코트 피코트 발마칸 "
                            "라이더자켓 무스탕 레이서자켓 로브코트 워크자켓 필드자켓 사파리자켓 퀼팅자켓 코치자켓 해링턴 트러커 바시티 아노락 "
                            "바시티/스타디움 베스트 아우터셔츠 오버셔츠 아우터").split()},
    "액세서리": None, "슈트": None,
}
_WEB_ALSO_TOPS = {"가디건", "집업", "후드집업", "아우터셔츠", "오버셔츠", "플리스/뽀글이", "플리스", "베스트"}
_SEC_EN = {"상의": "tops", "하의": "bottoms", "아우터": "outer"}


def _syn(words) -> list[str]:
    out = []
    for w in words:
        w = str(w).strip()
        if w and w not in out:
            out.append(w)
    return out


def build_items(counts: Counter, cat_of: dict[str, Counter]) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    seen: set[str] = set()
    vocab = C.ITEM_TYPE_VOCAB
    web_back: dict[str, list[str]] = {}
    for w, s in _WEB_ALIAS.items():
        web_back.setdefault(s, []).append(w)
    sections = []
    for sec, *_ in _HIER:
        if sec not in sections:
            sections.append(sec)
    for sec in sections:
        items.append({"id": f"section:{_SECTION_ID[sec]}", "level": "section", "name": sec, "parent": None,
                      "synonyms": []})
    for sec, cat, base, subs in _HIER:
        cid = f"category:{cat}"
        items.append({"id": cid, "level": "category", "name": cat, "parent": f"section:{_SECTION_ID[sec]}",
                      "base_subtype": base, "copilot": cat not in _NOT_IN_COPILOT,
                      "copilot_category": _COPILOT_CATEGORY.get(cat), "synonyms": []})
        for s in ([base] if base else []) + subs:
            if s in seen:
                continue
            seen.add(s)
            items.append({
                "id": f"subtype:{s}", "level": "subtype", "name": s, "parent": cid,
                "is_base": s == base,
                "synonyms": _syn(list(vocab.get(s, [])) + web_back.get(s, [])),
                "crawler_category_code": C.ITEM_TO_CATEGORY.get(s),
                "catalog_rows": counts.get(s, 0),
                "copilot": s not in _NOT_IN_COPILOT,
                "also_sections": _ALSO.get(s, []),
            })
    # 잡화 — 크롤러 ACC_TYPE_VOCAB · 신발 품목. 갈래 = 큰 분류 하나.
    acc_sec = {"Bags": "가방", "Shoes": "신발", "Headwear": "모자", "Accessories": "액세서리", "Jewelry": "주얼리"}
    acc: dict[str, list[str]] = {}
    for a, cat in C.ACC_TO_CATEGORY.items():
        sec = acc_sec.get(C.CATEGORY_LABEL.get(cat, ""))
        if sec:
            acc.setdefault(sec, []).append(a)
    for s in ("스니커즈", "부츠", "샌들", "구두"):
        if s not in acc.get("신발", []):
            acc.setdefault("신발", []).append(s)
    # 잡화 세부의 동의어 — 신발 「구두」는 크롤러 옷 어휘(ITEM_TYPE_VOCAB)에 loafer · derby · maryjane 을 품고 있는데,
    # 이것들은 잡화 어휘의 로퍼 · 더비 · 메리제인이 먼저 잡는다(구두는 그 뒤의 받침). 사전에서는 먼저 잡는 쪽에만 둔다.
    acc_claimed = {w.strip().lower() for vs in C.ACC_TYPE_VOCAB.values() for w in vs}

    def acc_syn(s):
        if s in C.ACC_TYPE_VOCAB:
            return _syn(C.ACC_TYPE_VOCAB[s])
        return _syn(w for w in vocab.get(s, []) if w.strip().lower() not in acc_claimed)
    for sec in ("가방", "신발", "모자", "액세서리", "주얼리"):
        sid = f"section:{_SECTION_ID[sec]}"
        items.append({"id": sid, "level": "section", "name": sec, "parent": None, "accessory": True, "synonyms": []})
        cid = f"category:{sec}"
        items.append({"id": cid, "level": "category", "name": sec, "parent": sid, "base_subtype": None,
                      "accessory": True, "synonyms": []})
        for s in acc.get(sec, []):
            if s in seen:
                continue
            seen.add(s)
            items.append({"id": f"subtype:{s}", "level": "subtype", "name": s, "parent": cid, "accessory": True,
                          "synonyms": acc_syn(s),
                          "sub_code": C.ACC_SUB_CODE.get(s), "catalog_rows": counts.get(s, 0)})
    # 카탈로그에 실제로 나온 subtype 가운데 표에 없는 것 — 사람이 manual_items.csv 로 직접 적은 값들이다.
    # 그 행들의 category 로 자리를 찾아 넣고 표시해 둔다(빠뜨린 게 있으면 바로 보이게).
    missing = sorted(s for s in counts if s and s not in seen)
    # 사람이 manual_items 에 적은 이름이 다른 세부의 동의어면(「캡」 → 볼캡) 세부를 따로 세우지 않고 그 동의어로 둔다
    syn_of = {w.strip().lower(): i["name"] for i in items if i["level"] == "subtype" for w in i["synonyms"]}
    manual_alias = {s: syn_of[s.lower()] for s in missing if s.lower() in syn_of}
    seen |= set(manual_alias)
    missing = [s for s in missing if s not in manual_alias]
    for s in missing:
        sec = acc_sec.get(cat_of[s].most_common(1)[0][0]) if cat_of.get(s) else None
        if not sec:
            continue
        seen.add(s)
        items.append({"id": f"subtype:{s}", "level": "subtype", "name": s, "parent": f"category:{sec}",
                      "accessory": True, "synonyms": [], "catalog_rows": counts[s], "catalog_only": True})
    missing = [s for s in missing if s not in seen]

    # 어긋남 표: 앱 TABLE · 코파일럿 GROUPS · 크롤러 group 칸이 사전과 다른 갈래를 내는 이름
    sec_of: dict[str, str] = {}
    by_id = {i["id"]: i for i in items}
    for i in items:
        if i["level"] == "subtype":
            sec_of[i["name"]] = by_id[by_id[i["parent"]]["parent"]]["name"]
    conflicts = []
    for name, web in sorted(_WEB_TABLE.items(), key=lambda kv: kv[0]):
        canon = _WEB_ALIAS.get(name, name)
        if name in ("상의", "하의", "아우터"):
            continue
        mine = sec_of.get(canon)
        if mine is None:
            continue
        mine_en = _SEC_EN.get(mine)
        if mine == "원피스":
            mine_en = "dress"
        if web != mine_en:
            also_en = [_SEC_EN.get(a) for a in _ALSO.get(canon, [])]
            conflicts.append({"name": name, "subtype": canon, "vocab_section": mine,
                              "vocab_also": _ALSO.get(canon, []), "web_table": web,
                              "web_also_tops": name in _WEB_ALSO_TOPS,
                              "crawler_group": C.GROUP_OF.get(_crawler_label(canon), ""),
                              "resolution": ("앱은 대표 갈래를 사전대로 바꾸고 지금 갈래를 also 로 읽는다" if web in also_en
                                             else "앱 · 크롤러 group 을 사전 갈래로 바꾼다")})
    return items, [{"missing_subtypes_in_catalog": missing, "manual_alias": manual_alias}] + conflicts


def _crawler_label(s: str) -> str:
    if s == "데님":
        return "Denim"
    if s in ("니트", "가디건", "케이블니트", "아가일니트"):
        return "Knitwear"
    if s in ("셔츠", "웨스턴셔츠"):
        return "Shirts"
    return C.CATEGORY_LABEL.get(C.ITEM_TO_CATEGORY.get(s, ""), "")


# ─── 섬유 ──────────────────────────────────────────────────────────────────────
def build_fibers(vocab: dict) -> tuple[list[dict], list[dict]]:
    mat = vocab.get("material") or {}
    groups = (vocab.get("_groups") or {}).get("material") or {}
    not_garment = set(vocab.get("_not_garment_material") or [])
    fib: dict[str, dict] = {}

    def get(name: str) -> dict:
        if name not in fib:
            fib[name] = {"id": f"fiber:{name}", "name": name, "synonyms": [], "abbr": [], "groups": [],
                         "kind": "fiber"}
        return fib[name]

    blend_fibers = set(P._MIX_FIBERS)
    for k, vs in mat.items():
        f = get(k)
        f["synonyms"] = _syn(f["synonyms"] + list(vs if isinstance(vs, list) else [vs]))
    for src in (P._MIX_ALIAS, T._MAT_SYN, P._MIX_FUZZY):
        for w, k in src.items():
            if w == k:
                get(k)
                continue
            f = get(k)
            f["synonyms"] = _syn(f["synonyms"] + [w])
    for a, k in P._MIX_ABBR.items():
        f = get(k)
        f["abbr"] = _syn(f["abbr"] + [a])
    for g, members in groups.items():
        for m in members:
            get(m)["groups"].append(g)
    # 짜임·원단 · 금속 · 장신구 소재는 혼용률의 「섬유」가 아니다 — 태그(material 축)에만 쓴다
    weave = set(groups.get("데님·트윌", [])) | set(groups.get("짜임", []))
    for name, f in fib.items():
        if name in not_garment or name in set(groups.get("금속·보석", [])):
            f["kind"] = "non_garment_material"
        elif name in weave:
            f["kind"] = "weave"
        elif name in ("가죽", "천연가죽", "인조가죽", "소가죽", "양가죽", "송아지가죽", "염소가죽", "말가죽", "스웨이드", "누벅",
                      "퍼", "페이크퍼", "시어링"):
            f["kind"] = "leather_fur"
        f["in_blend"] = name in blend_fibers or name in set(T._MAT_SYN.values()) or name in set(P._MIX_ALIAS.values())
    rules = [
        {"id": "polyurethane_lt50", "text": "폴리우레탄이 50% 미만이면 스판덱스로 적는다(늘어나는 실). 50% 이상이면 폴리우레탄 그대로(코팅 · 인조가죽 겉면).",
         "source": "tag_items.load_manual_mat · product_desc"},
        {"id": "abbr_only_after_label", "text": "한두 글자 약자(C · P · SP …)는 「혼용률 · composition · fabric · material · 겉감」 이름표 바로 뒤 줄기에서만 섬유로 읽는다. 첫 부위 합이 90~110 일 때만.",
         "source": "tag_items._ABBR_LABEL · product_desc._MIX_ABBR"},
        {"id": "fuzzy_ocr_only", "text": "철자 맞추기(편집 거리 1, 아홉 글자 넘으면 2)는 그림 글(OCR)에서만, 후보가 하나일 때만. model · lining 같은 낱말은 뺀다.",
         "source": "product_desc._MIX_FUZZY · _MIX_FUZZY_STOP"},
        {"id": "down_feather_split", "text": "다운과 깃털은 혼용률에서 가른다(「다운 80% 깃털 20%」). 태그(material 축)에서는 둘 다 다운.",
         "source": "product_desc._MIX_ALIAS"},
        {"id": "weave_is_not_fiber", "text": "옥스포드 · 트윌 · 데님 · 저지 · 새틴 · 쉬폰 · 오간자 · 코듀로이 · 플리스 · 니트는 짜임 이름이라 혼용률 섬유로 받지 않는다.",
         "source": "apply_mat 거름(2026-10-03)"},
        {"id": "sum_check", "text": "부위마다 합이 99~101 일 때만 받는다. 한 섬유를 빠뜨린 상품은 통째로 안 쓴다.",
         "source": "tag_items.load_manual_mat"},
    ]
    return sorted(fib.values(), key=lambda f: (f["kind"], f["name"])), rules


# ─── 원단 부위 ─────────────────────────────────────────────────────────────────
def build_parts() -> tuple[list[dict], list[dict]]:
    canon: dict[str, list[str]] = {}
    for w, k in P._MIX_PART_CANON.items():
        canon.setdefault(k, [])
        if w != k:
            canon[k].append(w)
    for k, v in P._MIX_MC_NAME.items():
        canon.setdefault(v, []).append(f"{k} -")
    order = ["겉감", "안감", "배색", "충전재", "시보리", "포켓감", "부분", "벨트"]
    parts = [{"id": f"part:{k}", "name": k, "synonyms": _syn(canon.get(k, [])),
              "display_empty_name": k == "겉감"} for k in order if k in canon]
    rules = [
        {"id": "empty_is_shell", "text": "부위 이름이 빈칸(\"\")이면 겉감이다. 부위가 겉감 하나뿐인 상품은 앱이 부위 이름을 떼고 보여 준다."},
        {"id": "number_1", "text": "「겉감 1」 「안감 1」처럼 1번은 번호를 떼어 겉감 · 안감으로 읽는다. 단 같은 상품에 이미 「겉감」(또는 빈칸)이 따로 있으면 떼지 않는다 — 2026-10-03 전수에서 77벌(겉감 70 · 배색 7)이 둘 다 가졌고, 값이 다른 경우가 있다(adererror 1075: 겉감 1 울77/나일론15/레이온8 · 겉감 울58/나일론29/…)."},
        {"id": "number_2plus", "text": "「겉감 2」 「안감 2」 「배색 3」 등 2번부터는 별도 부위다. 번호를 떼고 합치면 한 부위 합이 170 · 200 이 된다."},
        {"id": "needs_shell", "text": "겉감(또는 빈칸)이 없는 결과 — 안감만 · 배색만 — 는 버린다(product_desc.mix_pick)."},
        {"id": "seen_noncanonical", "text": "2026-10-03 출력에 표준 밖 부위 이름이 남아 있다: COLORBLOCK 23 · 가디건 2 · 나시 2 · 몸판 2. 몸판 → 겉감 · COLORBLOCK → 배색 이 맞고, 가디건 · 나시는 세트 상품의 옷 이름이다. 해석기 쪽 정리는 따로 한다."},
    ]
    return parts, rules


# ─── 부자재 ────────────────────────────────────────────────────────────────────
# 후보 — 원문에서 찾았지만 아직 태거에 안 넣은 이름. 괄호 수는 원문 언급 브랜드 수(코파일럿 세션 집계 2026-10-03).
_HW_CANDIDATES = [
    ("비슬론", 41, "지퍼", ["vislon"]), ("엑셀라", 34, "지퍼", ["excella"]), ("리리", 15, "지퍼", ["riri"]),
    ("람포", 14, "지퍼", ["lampo"]), ("탈론", 7, "지퍼", ["talon"]),
    ("도트단추", 109, "단추", ["도트 단추", "dot button"]), ("너트단추", 49, "단추", ["너트 단추"]),
    ("호른단추", 23, "단추", ["호른 단추", "horn button"]), ("메탈단추", 19, "단추", ["메탈 단추", "metal button"]),
    ("우드단추", 16, "단추", ["우드 단추", "wood button", "wooden button"]),
    ("진주단추", 7, "단추", ["진주 단추", "pearl button"]),
    ("후크", 113, None, ["hook", "훅"]), ("D링", 80, None, ["d-ring", "d ring", "디링"]),
    ("가죽패치", 67, None, ["가죽 패치", "leather patch"]), ("황동", 63, None, ["brass"]),
    ("토글", 52, None, ["toggle", "떡볶이 단추"]), ("와펜", 44, None, ["wappen"]),
]


def build_hardware(vocab: dict) -> list[dict]:
    hw = vocab.get("hardware") or {}
    groups = (vocab.get("_groups") or {}).get("hardware") or {}
    grp_of = {m: g for g, ms in groups.items() for m in ms}
    out = [{"id": f"hardware:{k}", "name": k, "synonyms": _syn(vs), "status": "사용", "group": grp_of.get(k)}
           for k, vs in hw.items()]
    taken = {s.lower(): k for k, vs in hw.items() for s in [k, *vs]}
    for name, n, parent, syn in _HW_CANDIDATES:
        note = None
        hit = [taken[w.lower()] for w in [name, *syn] if w.lower() in taken]
        if hit:
            note = f"지금은 「{hit[0]}」의 동의어로 접혀 있다 — 따로 세우려면 그 동의어에서 빼야 한다"
        out.append({"id": f"hardware:{name}", "name": name, "synonyms": syn, "status": "후보", "brand_mentions": n,
                    "parent": f"hardware:{parent}" if parent else None, "note": note})
    return out


# ─── 색상 ──────────────────────────────────────────────────────────────────────
def build_colors(vocab: dict) -> tuple[list[dict], list[dict]]:
    rollup = vocab.get("_color_rollup") or {}
    groups = (vocab.get("_groups") or {}).get("color") or {}
    grp_of = {m: g for g, ms in groups.items() for m in ms}
    fine = []
    for k, vs in C.COLOR_VOCAB.items():
        base = [v for v in vs if not re.fullmatch(r"(?:light|dark|deep|pale|off|jet|dust|soft|medium|melange) ?[a-z]+", v)
                or v in ("off white", "light blue", "light pink", "lightblue", "lightpink", "melange grey", "light denim")]
        r = rollup.get(k, k)
        fine.append({"id": f"color:{k}", "name": k, "synonyms": _syn(base), "rollup": r, "group": grp_of.get(r),
                     "modifier_forms": "light · dark · deep · pale · off · jet · dust · soft · medium · melange + 영문 이름(붙여 쓰기 · 띄어 쓰기 둘 다)"})
    coarse = sorted(set(rollup.values()))
    rolls = [{"id": f"color_rollup:{c}", "name": c, "group": grp_of.get(c)} for c in coarse]
    return fine, rolls


# ─── 사진 유형 ─────────────────────────────────────────────────────────────────
PHOTO_TYPES = [
    {"id": "photo_type:packshot", "name": "누끼", "en": "packshot",
     "definition": "사람 없이 상품만, 흰색이나 단색 배경. 바닥에 놓고 찍은 것 · 행거 · 마네킹(사람 아님)도 여기.",
     "not": "모델이 조금이라도(손 · 다리 일부라도) 입고 있으면 착용 사진이다."},
    {"id": "photo_type:studio_worn", "name": "스튜디오 착용", "en": "studio_worn",
     "definition": "모델이 입고 있고 배경이 흰색 · 단색 · 무늬 없는 스튜디오 벽.",
     "not": "배경에 장소(거리 · 실내 가구 · 자연)가 보이면 화보 · 야외 착용이다."},
    {"id": "photo_type:editorial", "name": "화보·야외 착용", "en": "editorial",
     "definition": "모델이 입고 있고 배경이 장소다 — 야외, 꾸민 실내, 조명 · 연출이 들어간 캠페인 컷.",
     "not": None},
    {"id": "photo_type:detail", "name": "디테일 클로즈업", "en": "detail",
     "definition": "옷의 일부(원단 결 · 단추 · 지퍼 · 라벨 · 자수)를 가까이 찍어 옷 전체가 안 보이는 것. 착용 중이어도 부분만 크게 보이면 여기.",
     "not": None},
    {"id": "photo_type:other", "name": "기타", "en": "other",
     "definition": "사이즈표 · 글 · 안내 이미지 · 로고 · 색상표 · 여러 상품을 모은 판 등 옷 사진이 아닌 것.",
     "not": None},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    vocab = json.loads((T.DATA / "vocab_aliases.json").read_text(encoding="utf-8"))
    counts: Counter = Counter()
    cat_of: dict[str, Counter] = {}
    pf = T.DATA / "products_full.csv"
    if pf.exists():
        for r in csv.DictReader(pf.open(encoding="utf-8-sig")):
            counts[r.get("subtype") or ""] += 1
            cat_of.setdefault(r.get("subtype") or "", Counter())[r.get("category") or ""] += 1
    items, item_notes = build_items(counts, cat_of)
    fibers, fiber_rules = build_fibers(vocab)
    parts, part_rules = build_parts()
    colors, color_rollup = build_colors(vocab)
    out = {
        "version": VERSION,
        "_comment": ("LAYER 어휘 사전. scripts/build_vocab.py 가 크롤러 · 태거 · 혼용률 해석이 실제로 쓰는 표에서 모아 낸다 — "
                     "손으로 고치지 말고 원래 표를 고친 뒤 다시 돌린다. 항목마다 id · name(표시 이름) · synonyms · parent."),
        "sources": {
            "item": "crawl_cafe24.ITEM_TYPE_VOCAB · ITEM_TO_CATEGORY · GROUP_OF · ACC_TYPE_VOCAB · layer-copilot taxonomy.js GROUPS · layer-web garmentCategory.ts TABLE",
            "fiber": "data/vocab_aliases.json material · product_desc._MIX_ALIAS · _MIX_ABBR · _MIX_FUZZY · tag_items._MAT_SYN",
            "part": "product_desc._MIX_PART_CANON · _MIX_MC_NAME",
            "hardware": "data/vocab_aliases.json hardware · _groups.hardware (+ 후보)",
            "color": "crawl_cafe24.COLOR_VOCAB(세밀) · vocab_aliases _color_rollup(거친) · _groups.color(무리)",
            "grade · fabric_origin": "scripts/grade_origin.py(GRADES · MILLS · COUNTRY_FABRIC)",
        },
        "item": {"levels": ["section", "category", "subtype"], "entries": items,
                 "lookup": {**{s: i["name"] for i in items if i["level"] == "subtype" for s in i["synonyms"]},
                            **{i["name"]: i["name"] for i in items if i["level"] == "subtype"},
                            **_WEB_ALIAS},
                 "conflicts": item_notes},
        "fiber": {"entries": fibers, "rules": fiber_rules},
        "part": {"entries": parts, "rules": part_rules},
        "hardware": {"entries": build_hardware(vocab)},
        "color": {"entries": colors, "rollup": color_rollup,
                  "groups": [{"name": g, "members": ms} for g, ms in ((vocab.get("_groups") or {}).get("color") or {}).items()]},
        "photo_type": {"entries": PHOTO_TYPES},
        # 소재 등급 · 원단 출처(v1.2, 코파일럿 세션 부탁 2026-10-03) — 어휘와 규칙은 scripts/grade_origin.py 한 곳에.
        # 상품마다의 값은 product_tags_full.json 의 "grade"([{k: 축, v: 값, of: 상위 섬유}]) · "origin"([{mill, country}]).
        "grade": {"axes": {"울": "울 등급", "캐시미어": "캐시미어 등급", "원피": "가죽을 낸 짐승", "가공": "가죽 가공"},
                  "entries": GO.vocab_entries()["grade"], "faux_guard": GO.vocab_entries()["faux_guard"],
                  "rules": GO.vocab_entries()["rules"]},
        "fabric_origin": {"entries": GO.vocab_entries()["fabric_origin"]},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n = Counter(i["level"] for i in items)
    print(f"품목 {dict(n)} · 섬유 {len(fibers)} · 부위 {len(parts)} · 부자재 {len(out['hardware']['entries'])} · 색 {len(colors)}")
    print("카탈로그에만 있는 subtype:", item_notes[0]["missing_subtypes_in_catalog"])
    print("어긋남", len(item_notes) - 1)


if __name__ == "__main__":
    main()
