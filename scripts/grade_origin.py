"""소재 등급(울 · 캐시미어 등급, 가죽 원피 · 가공)과 원단 출처(원단사 · 원산국)를 상품 글에서 뽑는다.

왜 따로 두나 (2026-10-03, 코파일럿 세션 부탁)
  혼용률 해석은 메리노 · 엑스트라파인 · 램스울을 모두 「울」로 합친다 — 함량 계산(「울 70% 이상」)에는 그게 맞다. 그런데 비싼 소재는
  등급에 따라 값이 크게 갈려 비교 기준에 등급이 필요하다. 그래서 함량은 지금처럼 상위 섬유로 두고, 등급은 따로 뽑아 별도 칸
  (product_tags_full.json 의 "grade" · "origin")에 싣는다. 어휘는 여기 한 곳에 두고 사전(build_vocab)과 태거(tag_items)가 함께 읽는다.

  값마다 정규식 하나. 영문은 낱말 경계, 한글은 띄어 쓴 꼴 · 붙여 쓴 꼴을 다 받는다. 더 좁은 이름이 이긴다
  (「extra fine merino」는 엑스트라파인 메리노 하나로, 메리노를 따로 세지 않는다).
"""
from __future__ import annotations

import re

# (축, 값, 상위 섬유, 정규식, 덮는 값들) — 덮는 값: 이 값이 잡히면 같은 자리의 넓은 값은 세지 않는다
_W = r"(?<![A-Za-z가-힣])"
_E = r"(?![A-Za-z])"

GRADES: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    # 울 등급
    ("울", "엑스트라파인 메리노", "울",
     rf"{_W}(?:extra[\s-]?(?:super[\s-]?)?fine|x[\s-]?fine|엑스트라\s?파인|엑스트라\s?화인)\s*(?:merino|메리노|wool|울){_E}(?:\s*(?:wool|울){_E})?", ("메리노",)),
    ("울", "메리노", "울", rf"{_W}(?:merino|메리노){_E}", ()),
    ("울", "램스울", "울", rf"{_W}(?:lamb'?s?[\s-]?wool|램스\s?울|램즈\s?울|양모\s?\(?램스){_E}", ()),
    ("울", "버진울", "울", rf"{_W}(?:virgin[\s-]?wool|버진\s?울){_E}", ()),
    ("울", "리사이클울", "울", rf"{_W}(?:recycled[\s-]?wool|리사이클\s?울|재생\s?울){_E}", ()),
    ("울", "슈퍼 100's 이상", "울", rf"{_W}(?:super|슈퍼)\s?(?:1[0-9]0|200)\s?['’]?\s?s{_E}", ()),
    # 캐시미어 등급
    ("캐시미어", "베이비 캐시미어", "캐시미어", rf"{_W}(?:baby[\s-]?cashmere|베이비\s?캐시미어){_E}", ()),
    ("캐시미어", "몽골리안 캐시미어", "캐시미어",
     rf"{_W}(?:mongolian[\s-]?cashmere|inner[\s-]?mongolia[n]?\s?cashmere|몽골(?:리안)?\s?캐시미어|내몽골\s?캐시미어){_E}", ()),
    # 가죽 원피(가죽을 낸 짐승) — 값은 짐승, raw 에 적힌 이름
    ("원피", "소(송아지)", "가죽", rf"{_W}(?:calf[\s-]?skin|calf[\s-]?leather|calf[\s-]?suede|카프\s?스킨|카프\s?레더|송아지\s?가죽){_E}", ("소",)),
    ("원피", "소", "가죽", rf"{_W}(?:cow[\s-]?hide|cow[\s-]?(?:leather|leatehr|lether|leahter|leater)|(?:천연\s?|코팅\s?)?가죽\s?\(\s?소\s?\)|cow[\s-]?skin|cow[\s-]?suede|cow[\s-]?split|split[\s-]?cow|카우\s?하이드|카우\s?레더|소\s?가죽|우피){_E}", ()),
    ("원피", "양", "가죽",
     rf"{_W}(?:lamb[\s-]?skin|lamb[\s-]?leather|(?:천연\s?)?가죽\s?\(\s?양\s?\)|lamb[\s-]?shearling|sheep[\s-]?shearling|lamb[\s-]?suede|sheep[\s-]?leather|sheep[\s-]?skin|양털\s?가죽|sheep[\s-]?leather|램\s?스킨|램\s?레더|시프\s?스킨|쉽\s?스킨|양\s?가죽|양피){_E}", ()),
    ("원피", "염소", "가죽", rf"{_W}(?:goat[\s-]?skin|goat[\s-]?leather|goat[\s-]?suede|goat[\s-]?nubuck|고트\s?스킨|고트\s?레더|염소\s?가죽){_E}", ()),
    ("원피", "말", "가죽", rf"{_W}(?:horse[\s-]?hide|horse[\s-]?leather|호스\s?하이드|말\s?가죽){_E}", ()),
    ("원피", "돼지", "가죽", rf"{_W}(?:pig[\s-]?skin|pig[\s-]?leather|돈피|돼지\s?가죽){_E}", ()),
    # 가죽 가공
    ("가공", "베지터블 태닝", "가죽", rf"{_W}(?:vegetable[\s-]?tann?(?:ed|ing)|veg[\s-]?tan(?:ned)?|베지터블(?:\s?태닝|\s?레더)?|식물성\s?무두질){_E}", ()),
    ("가공", "나파", "가죽", rf"{_W}(?:nappa|napa\s?leather|나파\s?(?:가죽|레더)?){_E}", ()),
    ("가공", "스웨이드", "가죽", rf"{_W}(?:suede|스웨이드){_E}", ()),
    ("가공", "누벅", "가죽", rf"{_W}(?:nubuck|누벅){_E}", ()),
]

# 안감 · 내피 · 깔창의 가죽은 그 상품의 원피가 아니다 — salondeju 「외피 / 양가죽 … 내피/ 돈내피 Pig leather」(2026-10-03 조사)
LINING_BEFORE = re.compile(r"(?:내피|안감|안쪽|lining|라이닝|insole|인솔|까래|깔창|바닥\s?까래|내부|trim|contrast|배색|트림|sole|밑창|홍창|sub(?![a-z])|서브|부속|트리밍|trimming)"
                           r"(?:\s?\d)?\s*[)\]]?\s*[,/:：·\-]?\s*(?:\d{1,3}(?:\.\d)?\s?%\s*)?(?:[가-힣A-Za-z]+\s*){0,2}[,/:：·\-]?\s*$", re.I)
# 가죽 낱말 바로 뒤에 붙은 쓰임 — 「이태리산 소가죽 홍창」 「cow leather sole」은 밑창 가죽이지 상품 원피가 아니다(코덱스 부탁 336)
PART_AFTER = re.compile(r"^\s*(?:\d{1,3}\s?%\s*)?(?:홍창|밑창|창(?![가-힣])|안창|깔창|인솔|sole|insole|outsole|lining|라이닝|안감|내피|trim|트림|배색|piping|파이핑|패치|patch|라벨|label|로고|logo|태그|tag|트리밍|trimming|손잡이|핸들|handle|지퍼|zipper|풀러|puller)", re.I)
# 견줌 — 「일반 메리노 울보다 부드러운」은 이 상품 소재가 아니다(코덱스 부탁 340)
COMPARE_AFTER = re.compile(r"^\s*(?:울|wool|가죽|leather)?\s*(?:보다|대비|만큼|처럼|같은|like|than)", re.I)
# 가공 낱말 뒤의 관리 안내 — 「스웨이드 브러시로 관리」 「스웨이드/누벅 소재는 물에 약해」(코덱스 부탁 336 · 337)
CARE_AFTER = re.compile(r"^\s*(?:[/·,및&]\s*(?:누벅|스웨이드|nubuck|suede)\s*)?(?:전용\s?)?(?:관리|브러시|브러쉬|brush|care|클리너|cleaner|용품|크림|스프레이|소재(?:는|의\s?특성)|가죽(?:은|의\s?특성)|제품(?:은|의)|특성상)", re.I)
# 등급 낱말 바로 뒤의 함량 — 「MERINO WOOL 50%」. 상품 혼용률과 견줘 매장 견본 줄을 가린다(with_evidence).
PCT_AFTER = re.compile(r"[\s\w()'’-]{0,14}?\s*[:：]?\s*(\d{1,3}(?:\.\d)?)\s?%")
# 천연 가죽이 아닌 표시 — 앞에 오면 그 가죽 낱말은 등급으로 안 받는다(「페이크 스웨이드」 「인조 양가죽 느낌」)
FAUX = re.compile(r"(?:faux|fake|vegan|eco|synthetic|imitation|artificial|pu|페이크|인조|합성|에코|비건|느낌의?|터치)\s*[-]?\s*$", re.I)
FAUX_AFTER = re.compile(r"^\s*(?:touch|look|feel|like|느낌|터치|감성|st)", re.I)

# 원단 출처 — (원단사, 원산국, 정규식). 원단사 없이 나라만 말하는 것은 원단사를 비운다.
MILLS: list[tuple[str, str, str]] = [
    ("카이하라", "일본", rf"{_W}(?:kaihara|카이하라){_E}"),
    ("쿠라보", "일본", rf"{_W}(?:kurabo|쿠라보){_E}"),
    ("토레이", "일본", rf"{_W}(?:toray|토레이){_E}"),
    ("니신보", "일본", rf"{_W}(?:nisshinbo|니신보){_E}"),
    ("쿠로키", "일본", rf"{_W}(?:kuroki|쿠로키){_E}"),
    ("해리스트위드", "영국", rf"{_W}(?:harris[\s-]?tweed|해리스\s?트위드){_E}"),
    ("에르메네질도 제냐", "이탈리아", rf"{_W}(?:(?:ermenegildo\s)?zegna|제냐){_E}"),
    ("로로피아나", "이탈리아", rf"{_W}(?:loro[\s-]?piana|로로\s?피아나){_E}"),
    ("비탈레 바르베리스 카노니코", "이탈리아", rf"{_W}(?:vitale[\s-]?barberis|vbc|비탈레\s?바르베리스){_E}"),
    ("마르조토", "이탈리아", rf"{_W}(?:marzotto|마르조토){_E}"),
    ("레다", "이탈리아", rf"{_W}(?:reda\s?(?:fabric|wool|1865)|레다\s?(?:원단|울)){_E}"),
    ("칸디아니", "이탈리아", rf"{_W}(?:candiani|칸디아니){_E}"),
    ("콘 데님", "미국", rf"{_W}(?:cone\s?(?:mills?|denim)|콘\s?데님){_E}"),
    ("이스코", "터키", rf"{_W}(?:isko|이스코)\s?(?:denim|데님)?{_E}"),
    ("라벤햄", "영국", rf"{_W}(?:lavenham|라벤햄){_E}"),
    ("폭스 브라더스", "영국", rf"{_W}(?:fox\s?brothers|폭스\s?브라더스){_E}"),
    ("리버티", "영국", rf"{_W}(?:liberty\s?(?:fabric|print|art\s?fabric|of\s?london)|리버티\s?(?:원단|프린트)){_E}"),
]
_COUNTRY = {"일본": r"일본|japan(?:ese)?|jp", "이탈리아": r"이탈리아|이태리|ital(?:y|ian)", "영국": r"영국|british|uk|england|english|scotland|scottish|스코틀랜드",
            "프랑스": r"프랑스|french|france", "미국": r"미국|usa|american", "포르투갈": r"포르투갈|portug(?:al|uese)", "터키": r"터키|turk(?:ey|ish)", "호주": r"호주|australia[n]?", "뉴질랜드": r"뉴질랜드|new\s?zealand"}
# 「일본 원단」 「이태리산 원사」 「Japanese fabric」 「fabric made in Italy」 — 옷이 아니라 **원단 · 원사**의 나라만
COUNTRY_FABRIC = [
    (c, re.compile(rf"{_W}(?:{p})\s?(?:산|제)?\s?(?:수입\s?)?(?:(?!제조|생산|made)[\w.]+\s){{0,2}}?(?:원단|원사|소재|패브릭|직물|fabric|textile|yarn|cloth|wool|denim|merino|메리노){_E}", re.I))
    for c, p in _COUNTRY.items()
] + [
    (c, re.compile(rf"{_W}(?:fabric|textile|yarn|cloth|원단|원사)\s?(?:made|woven|produced|imported|milled)?\s?(?:in|from)\s?(?:{p}){_E}", re.I))
    for c, p in _COUNTRY.items()
]

# 값 매김이 곧 뒤에 오면 상품 목록의 다른 상품 이름이다
LISTING_NEAR = re.compile(r"₩\s?\d|\d{1,3},\d{3}\s?원|KRW\s?\d")

_GRADE_RX = [(ax, v, top, re.compile(p, re.I), cov) for ax, v, top, p, cov in GRADES]
_MILL_RX = [(m, c, re.compile(p, re.I)) for m, c, p in MILLS]


def grades(text: str) -> list[dict]:
    """[{"k": 축, "v": 값, "of": 상위 섬유}] — 축: 울 · 캐시미어 · 원피 · 가공."""
    if not text:
        return []
    found: dict[tuple, dict] = {}
    covered: list[tuple[int, int, str]] = []
    for ax, v, top, rx, cov in _GRADE_RX:
        for m in rx.finditer(text):
            if top == "가죽" and (FAUX.search(text[max(0, m.start() - 20):m.start()]) or FAUX_AFTER.search(text[m.end():m.end() + 12])):
                continue
            if top == "가죽" and (LINING_BEFORE.search(text[max(0, m.start() - 24):m.start()]) or PART_AFTER.search(text[m.end():m.end() + 14])):
                continue
            if ax == "가공" and CARE_AFTER.search(text[m.end():m.end() + 16]):
                continue
            if COMPARE_AFTER.search(text[m.end():m.end() + 10]):
                continue
            if any(a <= m.start() < b and v in cvs for a, b, cvs in covered):
                continue
            g = found.setdefault((ax, v), {"k": ax, "v": v, "of": top})
            pm = PCT_AFTER.match(text, m.end())
            if pm:
                g.setdefault("pct", []).append(float(pm.group(1)))
            else:
                g["bare"] = True      # 함량 없이 선 자리도 있다 — 견본 줄 거르기(pct_ok)는 함량 달린 자리만 있을 때만 쓴다
            if cov:
                covered.append((m.start(), m.end(), cov))
    # 좁은 값이 잡힌 자리에서 넓은 값을 지운다(같은 낱말에서만 — 위 covered). 다른 자리에 홀로 선 넓은 값은 남는다.
    return list(found.values())


def origins(text: str) -> list[dict]:
    """[{"mill": 원단사 또는 "", "country": 나라}]"""
    if not text:
        return []
    out: dict[tuple, dict] = {}
    for mill, c, rx in _MILL_RX:
        if rx.search(text):
            out[(mill, c)] = {"mill": mill, "country": c}
    have = {c for (_, c) in out}
    for c, rx in COUNTRY_FABRIC:
        if c in have:
            continue
        for m in rx.finditer(text):
            # 다른 상품 목록(관련 상품 · 함께 본 상품) 속 이름 — 「BRITISH WOOL MUFFLER / Multi ₩202,000」(코덱스 부탁 341)
            if LISTING_NEAR.search(text[m.end():m.end() + 40]):
                continue
            out[("", c)] = {"mill": "", "country": c}
            have.add(c)
            break
    return list(out.values())


def vocab_entries() -> dict:
    """사전(schema/vocab.json)에 실을 꼴 — build_vocab 이 부른다."""
    return {
        "grade": [{"axis": ax, "name": v, "of": top, "pattern": p, "covers": list(cov)} for ax, v, top, p, cov in GRADES],
        "fabric_origin": [{"mill": m, "country": c, "pattern": p} for m, c, p in MILLS]
                         + [{"mill": "", "country": c, "pattern": rx.pattern} for c, rx in COUNTRY_FABRIC],
        "faux_guard": {"before": FAUX.pattern, "after": FAUX_AFTER.pattern},
        "rules": [
            "혼용률(mat)의 섬유 함량은 상위 섬유(울 · 소가죽 …)로 그대로 둔다 — 등급은 별도 칸 grade.",
            "좁은 이름이 이긴다: 「extra fine merino」는 엑스트라파인 메리노 하나, 「calfskin」은 소(송아지) 하나(같은 낱말 자리에서만).",
            "가죽 원피 · 가공은 앞뒤에 faux · fake · 인조 · 페이크 · 에코 · 느낌 · 터치가 붙으면 안 받는다.",
            "원단 출처의 나라는 원단 · 원사 · fabric 과 붙어 나올 때만(「Made in Japan」 옷 생산국은 아니다).",
            "매장 공통 안내 줄(store_repeats — 매장 상품 40% · 15벌 넘게 되풀이되는 문장, % 없는 것)은 버린다(tag_items).",
            "상품 증거와 맞아야 받는다: 울 등급은 그 상품 혼용률(mat)에 울이 있거나(혼용률이 없으면 소재 태그 · 이름에 울), 캐시미어 등급은 캐시미어가, "
            "가죽 원피 · 가공은 혼용률 · 소재 태그에 천연 가죽이 있을 때만(인조가죽뿐이면 안 받음). noice 영문 견본 「MERINO WOOL 50%」 같은 줄을 막는다.",
            "안감 · 내피 · 깔창에 쓴 가죽은 원피로 안 센다(salondeju 「내피/ 돈내피 Pig leather」).",
        ],
    }


HIDE_OF_FIBER = {"소가죽": "소", "송아지가죽": "소(송아지)", "양가죽": "양", "염소가죽": "염소", "말가죽": "말", "돼지가죽": "돼지"}
# 본체가 아닌 부위 — 이 부위의 가죽은 상품 원피가 아니다
NON_MAIN_PART = re.compile(r"sub(?![a-z])|서브|안감|내피|충전|배색|트림|trim|장식|밑창|아웃솔|인솔|깔창|안창|창(?![가-힣])|sole|lining|리브|심지|부속|패치|라벨|파이핑|바이어스", re.I)
LEATHER_FIBERS = {"가죽", "천연가죽", "소가죽", "양가죽", "송아지가죽", "염소가죽", "말가죽", "돼지가죽", "스웨이드", "누벅"}
WOOL_NAME = re.compile(r"wool|울(?![가-힣])|merino|메리노|lambswool|램스울|cashmere|캐시미어", re.I)


def with_evidence(gr: list[dict], mat: list, material_tags: list, name: str) -> list[dict]:
    """상품 증거와 어긋나는 등급을 지운다 — 매장 견본 줄 · 다른 상품 이야기를 막는다."""
    fibers = {f for part in (mat or []) for f, _ in (part.get("v") or [])}
    mtags = set(material_tags or [])
    wool_ok = ("울" in fibers) if fibers else bool(mtags & {"울", "캐시미어"} or WOOL_NAME.search(name or ""))
    cash_ok = ("캐시미어" in fibers) if fibers else bool("캐시미어" in mtags or re.search(r"cashmere|캐시미어", name or "", re.I))
    # 혼용률이 있으면 그것만 믿는다 — 소재 태그의 「스웨이드」는 폴리 스웨이드(인조)에도 붙는다. 혼용률이 없으면 천연 가죽 태그나
    # 글에 적힌 짐승 이름(lamb leather …)이 있어야 한다.
    hides = LEATHER_FIBERS - {"스웨이드", "누벅"}
    if fibers:
        leather_ok = bool(fibers & hides)
    else:
        leather_ok = bool(mtags & (hides - {"가죽"})) or any(g["k"] == "원피" for g in gr)
    def pct_ok(g) -> bool:
        """등급 뒤에 함량이 적혀 있으면 그 상품 혼용률의 같은 섬유 함량과 맞아야 한다(±1). noice 의 모든 상품 끝에 붙은 영문 견본
        「fabric :MERINO WOOL 50% NYLON 30% ACRYLIC 20%」가 울 100% 니트에 메리노를 달던 것을 막는다."""
        if not g.get("pct") or g.get("bare"):
            return True
        if not fibers:
            return False      # 함량을 적은 줄인데 이 상품 혼용률을 못 읽었다 — 견본 줄인지 가릴 수 없으니 안 받는다
        want = {"울"} if g["of"] == "울" else {"캐시미어"} if g["of"] == "캐시미어" else hides
        have = [x for part in (mat or []) for f, x in (part.get("v") or []) if f in want]
        return any(abs(p - h) <= 1 for p in g["pct"] for h in have)
    # 혼용률 본체 부위에 짐승이 적힌 가죽이 있으면 원피는 그것으로 정한다 — 글의 짐승 이름은 안감 · 배색 · 밑창 · 다른 상품 이야기일
    # 때가 많다(코덱스 부탁 336 · 337: 「(LINING) PIG SKIN」 「Trim: cow leather」, 혼용률은 소가죽 100% 인데 글에 양가죽).
    # 본체 부위에 가죽이 아예 없으면(배색에만 가죽) 원피 · 가공을 안 받는다.
    main_hides, main_leather = set(), False
    if fibers:
        for part in mat or []:
            if NON_MAIN_PART.search(part.get("p") or ""):
                continue
            for f, _ in part.get("v") or []:
                if f in hides:
                    main_leather = True
                if f in HIDE_OF_FIBER:
                    main_hides.add(HIDE_OF_FIBER[f])
        if not main_leather:
            leather_ok = False
    out = []
    if main_hides:
        gr = [g for g in gr if g["k"] != "원피" or g["v"] in main_hides or (g["v"] == "소(송아지)" and "소" in main_hides)]
        have = {g["v"] for g in gr if g["k"] == "원피"}
        for h in sorted(main_hides):
            if h == "소" and "소(송아지)" in have:
                continue
            if h not in have:
                gr = gr + [{"k": "원피", "v": h, "of": "가죽"}]
    for g in gr:
        if not pct_ok(g):
            continue
        g = {k: v for k, v in g.items() if k not in ("pct", "bare")}
        if g["k"] == "울" and not wool_ok:
            continue
        if g["k"] == "캐시미어" and not cash_ok:
            continue
        if g["k"] in ("원피", "가공") and not leather_ok:
            continue
        out.append(g)
    return out
