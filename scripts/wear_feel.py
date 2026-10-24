"""착용감(두께 · 비침 · 신축성 · 무게 · 광택 · 안감)과 매장이 적은 계절감을 상품 글에서 뽑는다.

왜 따로 두나 (2026-10-06, 사람 「상세 설명에서 더 추출할거 추출하자. 최대한 뽑아 내야돼」)
  매장 상세에 「두께감 보통 / 비침 없음 / 신축성 약간 있음 / 안감 없음」 같은 표가 흔한데(상의 · 하의 · 아우터 · 원피스 85,624벌 중
  두께 9.8% · 신축성 11.6% · 안감 12.1% · 비침 7.8% · 광택 3.5% · 무게 2.2%) 지금 태그 축에는 담을 데가 없다. 값이 정도(얇음 · 보통 ·
  두꺼움)라서 낱말 태그가 아니라 칸 하나에 값 하나로 싣는다 — product_tags_full.json 의 "feel" · "season_txt".
  「신축성이 전혀 없는」이 function 신축성으로 붙던 것도 이 값으로 걷는다(tag_items).

  읽는 법: 칸 이름(두께감 · 비침 …) 바로 뒤 16자(다음 칸 이름 앞에서 끊음)의 값 낱말. 선택지를 다 늘어놓은 표(「두께감 얇음 보통
  두꺼움」 — 고른 칸은 색 · 체크로만 표시돼 글로는 모른다)와 체크 칸 OCR 은 값이 둘 이상 나오므로 버린다. 「있다 · 없다」는 흔한
  낱말이라 칸 이름 바로 뒤(조사 · 정도 부사만 사이에)에 올 때만 읽는다(「두께의 카라가 덧대어져 있는」 ✗). 후기 문장은 건너뛴다.

  「비슷한 옷」 점수에는 넣지 않았다 — 3 · 4 · 5차 사람 답 1,506쌍에서 착용감 +0.1%p(쓰인 쌍 97), 계절 −0.7%p, 설명 낱말 겹침
  −0.3~−1.4%p 로 사진을 보고 고른 답에 더 맞게 하지 못했다(2026-10-06).
"""
from __future__ import annotations

import re
KEYS = {
    "두께": r"두께감|두께|thickness",
    "비침": r"비침감|비침|비치는|비치지|시스루|see[- ]?through|sheerness",
    "신축성": r"신축성|신축감|신축력|stretch(?:ability|iness)?|탄성|신촉성",
    "무게": r"무게감|중량감|무게|weight",
    "광택": r"광택감|광택|윤기|sheen|gloss",
    "안감": r"안감|lining",
}
# 낱말 값(있다 · 없다 말고)
VALS = {
    "두께": [("얇음", r"얇|thin|sheer"), ("보통", r"보통|중간|적당|medium|normal|regular|moderate"), ("두꺼움", r"두꺼|두껍|두툼|도톰|heavy|thick")],
    "비침": [("없음", r"방지|none|않"), ("약간", r"약간|조금|살짝|다소|미세|은은|있을 수|발생할 수|slight|little|중간|보통"), ("있음", r"비칩|많이|심함|심한|yes")],
    "신축성": [("없음", r"none|non|거의 없"), ("약간", r"낮음|낮은|약간|조금|살짝|다소|적당|보통|중간|약함|약한|slight|little|some|moderate"),
              ("좋음", r"좋|우수|뛰어|탁월|높|많음|good|excellent|high")],
    "무게": [("가벼움", r"가벼|가볍|light"), ("보통", r"보통|적당|중간|medium|normal|moderate"), ("무거움", r"무거|무겁|묵직|heavy")],
    "광택": [("없음", r"매트|matte|무광"), ("약간", r"은은|약간|살짝|은근|보통|slight|subtle"), ("있음", r"glossy|shiny|유광|강한|높")],
    "안감": [("없음", r"없이|unlined|미사용|no lining"), ("있음", r"사용|부착|full|(?<!un)lined|전면|부분|[:：]\s*[a-z가-힣]+\s*\d")],
}
EXIST = {"두께": ("두꺼움", "얇음"), "비침": ("있음", "없음"), "신축성": ("좋음", "없음"), "무게": ("무거움", "가벼움"),
         "광택": ("있음", "없음"), "안감": ("있음", "없음")}
EXIST_RX = re.compile(r"^\s*[:：\-)]?\s*(?:이|가|은|는|도|감이|감은|감도|성이|성은|성도|력이)?\s*"
                      r"(약간|조금|살짝|다소|좀|꽤|거의|전혀|많이|적당히|매우|아주|적당히)?\s*(있|없|no\b|yes\b)")
PRE = {   # 앞에 오는 꼴 — 「적당한 두께감」 「은은한 광택」
    "두께": [("보통", r"적당한|적당히"), ("얇음", r"얇은|얇게"), ("두꺼움", r"두툼한|도톰한|두꺼운|헤비한|(?:도톰|두툼)하고\s*탄탄한")],   # 「중간」은 앞 칸 값(「비침 중간 두께감 얇음」) · 「탄탄한」은 두께가 아니다
    "비침": [("약간", r"약간의|은은한|살짝|미세한|약한")],
    "광택": [("약간", r"은은|살짝|은근|약간|미세한"), ("있음", r"고급스러운|선명한|강한|높은|유광|글로시")],
    "무게": [("가벼움", r"가벼운|가볍게"), ("무거움", r"묵직한|무거운")],
    "신축성": [("약간", r"가벼운|약간의|적당한|은은한"), ("좋음", r"뛰어난|우수한|좋은|높은|탁월한")],
}
KEY_RX = {k: re.compile(r"(?<![가-힣a-z\-])(?:" + v + r")", re.I) for k, v in KEYS.items()}   # font-weight ✗
ANY_KEY = re.compile("|".join(f"(?:{v})" for v in KEYS.values()) + r"|촉감|핏|사이즈|소재|세탁|겉감|충전재|배색", re.I)
VAL_RX = {k: [(lab, re.compile(rx, re.I)) for lab, rx in vs] for k, vs in VALS.items()}
PRE_RX = {k: [(lab, re.compile(r"(?:" + rx + r")[가-힣]{0,2}\s*$")) for lab, rx in vs] for k, vs in PRE.items()}   # 괄호 없이 붙여 마지막 낱말만 바로 앞이어야 했다 — 「활용하기 좋은 셔츠 신축성 없음」이 좋음(2026-11-09)
# 값이 아닌 문장: 「신축성에 따라 오차」 「비침이 우려되니 …」(일반 안내)
NOT_VALUE = re.compile(r"에 따라|따라서|에 의해|차이가|측정|오차|세탁|우려|주의|생각하시|않을까|궁금", re.I)
# 선택지 표 · 체크 칸(그림 글 OCR) — 무엇을 골랐는지 글로는 모른다
CHECKBOX = re.compile(r"[ㅁ□■●○◎◉✓✔☑☐@]|\(\s*\)|\[\s*\]|\[\s*[가-힣]?\s*[\]\s]")
CHOICE_W = re.compile(r"있음|없음|보통|좋음|얇음|두꺼움|가벼움|무거움|중간|약간|조금|많음|심함|높음|낮음"
                      r"|(?<![a-z])(?:thin|medium|thick|standard|none|slightly|no stretch|light|heavy|moderate)(?![a-z])")
SOFT = {"약간", "조금", "있음"}
# 후기 문장 — 산 사람 말은 매장 값이 아니다
REVIEW = re.compile(r"리뷰|평소\s*사이즈|입었어요|샀어요|했어요|좋아요|네요|더라구요|거든요|구매했|후기|재구매|별점|\d{2}\.\d{2}\.\d{2}")
WIN = 16
PART_RX = {
    "신축성": re.compile(r"(?:신축성|신축감|신축력|stretch)\s*(?:이|이\s*좋은|이\s*있는|있는|좋은)\s*(?:소매|밑단|밴드|허리|커프스|카라|넥|끝단|시보리|립)"),
    "광택": re.compile(r"단추|자개|버튼|자수|지퍼|메탈|금속|하드웨어|스터드|비즈|큐빅|로고|프린트|장식|안감|button|zipper|hardware|lining"),
}
# 한 부분 · 다른 것 얘기(2026-11-09 코덱스 검수 300벌):
#   신축성 — 「신축성 있는 이밴드가 내장된 소매 커프스」 「신축성있는 죠거밴딩」 「소매와 밑단을 신축성이 뛰어난 커버 스티치 봉제」
#   비침   — 「덕 다운으로 비침 현상을 최소화」(충전재) · 「비침이 없지 않아」(이중 부정)
#   광택   — 「… 사용해 은은한 광택과 고급스러운 디테일」(단추 · 부자재)
#   무게   — 「겉감보다 조금 더 무게감」(비교) · 「무게감있는 블랙라인」(색 얘기)
NEAR_BAD = {
    "신축성": None,   # 아래 STRETCH_PART
    "비침": (re.compile(r"다운|충전|패딩|없지\s*않|최소"), 25, 20),   # 「비침을 최소화」는 정도를 모른다(2차 검증)
    # 「탄성」은 원단 신축 말고 가죽 · 섬유의 질(「탄성과 내구성이 뛰어납니다」 diafvine 카프스킨 · 「탄성과 내열성」 테트론)일 때가 있다
    "탄성": (re.compile(r"가죽|레더|leather|카프|내구성|내열|마모|복원"), 30, 14),
    "광택": (re.compile(r"디테일|단추|버튼|부자재|지퍼|스냅"), 0, 16),
    "두께": (re.compile(r"단추|버튼|지퍼|스냅|자개"), 0, 12),
    "안감": (re.compile(r"주머니|포켓|pocket", re.I), 12, 0),
    "무게": (re.compile(r"보다|컬러|색감|블랙|톤|라인|젖|세탁|걸어"), 14, 14),   # 「니트는 젖었을 때 무거워지는데」 세탁 안내(2차 검증 150벌)
}
STRETCH_PART = re.compile(r"(?:신축성|신축감|신축력)\s*(?:이|가)?\s*(?:있는|좋은|뛰어난|우수한|있어)?\s*[가-힣a-z]{0,5}\s*"
                          r"(?:이?밴드|밴딩|커프스|시보리|스티치|봉제|스트링|고무줄)")
STRETCH_BEFORE = re.compile(r"(?:소매|밑단|허리|밴드|밴딩|커프스)[가-힣]{0,2}\s*(?:을|를|에|은|는|와|과)?\s*$")
EN_CHOICE = re.compile(r"(?<![a-z])(?:thin|mediu[mn]|thick|light|heavy|stretchy|no stretch|slight(?:ly)?|none|sheer|see-through|moderate)(?![a-z])")
# 세탁 · 관리 안내 문단 — 「건조 시에는 무게감 있는 아이템을 수건 위에」「니트는 젖었을 때 무거워지는데」(shirter · massnoun 공통 안내, 2차 검증)
CARE = re.compile(r"건조\s*시|세탁망|다림질|스팀을|물기|옷걸이|젖었을")
GENERIC = re.compile(r"특성상|사이즈의?\s*편차|참고로만|주관적")


def _labels(k: str, tail: str) -> list:
    hit = [lab for lab, vr in VAL_RX[k] if vr.search(tail)]
    m = EXIST_RX.match(tail)
    if m:
        adv, verb = m.group(1), m.group(2)
        pos = verb in ("있", "yes")
        if adv == "전혀" and pos:
            pos = None
        if pos is not None:
            lab = EXIST[k][0 if pos else 1]
            if pos and adv in ("약간", "조금", "살짝", "다소", "좀"):
                lab = {"두께": "보통", "무게": "보통", "안감": "있음"}.get(k, "약간")
            if adv == "거의" and not pos and k in ("비침", "신축성", "광택"):
                lab = "없음"
            hit.append(lab)
    return list(dict.fromkeys(hit))


def feel_of(text: str) -> dict:
    """{칸: 값} — 칸마다 문단 안에서 분명한 값 하나만. 상충하면 그 칸은 비운다."""
    text = (text or "").lower()
    votes: dict = {}
    for k, rx in KEY_RX.items():
        for m in rx.finditer(text):
            around = text[max(0, m.start() - 40):m.end() + 40]
            if REVIEW.search(around) or REVIEW_HARD.search(around) or CARE.search(around):
                continue
            # 옷 전체가 아니라 한 부분 얘기 — 「신축성 있는 밑단 밴드」 「은은한 광택의 자개 단추」 「사틴 자수가 은은한 광택으로」
            if k in PART_RX and PART_RX[k].search(text[max(0, m.start() - 20):m.end() + (40 if k == "광택" else 14)]):
                continue
            if GENERIC.search(around):
                continue
            if k == "신축성" and (STRETCH_PART.match(text, m.start()) or STRETCH_BEFORE.search(text[max(0, m.start() - 14):m.start()])):
                continue
            if m.group(0).startswith("탄성"):
                rx, back, fwd = NEAR_BAD["탄성"]
                if rx.search(text[max(0, m.start() - back):m.start()] + " " + text[m.end():m.end() + fwd]):
                    continue
            if NEAR_BAD.get(k):
                rx, back, fwd = NEAR_BAD[k]
                if rx.search(text[max(0, m.start() - back):m.start()] + " " + text[m.end():m.end() + fwd]):
                    continue
            # 영문 선택지 줄(「stretchy · slight stretch · no stretch」 「thickness thin medium」 — 고른 칸을 글로는 모른다)
            if re.match(r"[a-z]", m.group(0)) and len(set(EN_CHOICE.findall(text[max(0, m.start() - 25):m.end() + 25]))) >= 2:
                continue
            tail = text[m.end():m.end() + WIN]
            nk = ANY_KEY.search(tail)               # 다음 칸 이름 앞에서 끊는다(「비침 없음 신축성 좋음」)
            if nk and nk.start() > 0:
                tail = tail[:nk.start()]
            if NOT_VALUE.search(tail) or CHECKBOX.search(text[m.end():m.end() + 30]):
                continue
            hit = _labels(k, tail)
            # 넓게 봐서 같은 칸 값이 또 있으면 선택지 표(「두께감 얇음 보통 두꺼움」) — 버린다
            wide = text[m.end():m.end() + 36]
            nk = ANY_KEY.search(wide)
            if nk and nk.start() > 0:
                wide = wide[:nk.start()]
            if len(set(_labels(k, wide)) - {"약간"}) >= 2 or len(set(_labels(k, wide))) >= 3:
                continue
            ws = set(CHOICE_W.findall(wide))
            if len(ws) >= 3 or (len(ws) == 2 and not ws <= SOFT):
                continue
            if k != "비침" and re.search(r"않|안\s", tail):      # 「두께감이 두껍지 않은」 — 부정은 값이 흐리다
                continue
            if k in PRE_RX:                       # 「적당한 두께감 있는」 — 앞말이 있으면 앞말을 따른다
                pre = text[max(0, m.start() - 10):m.start()]
                ph = [lab for lab, pr in PRE_RX[k] if pr.search(pre)][:1]
                if ph:
                    hit = ph
            if len(hit) == 2 and "약간" in hit:      # 「약간 있음」은 약간
                hit = ["약간"]
            if len(hit) == 1:
                votes.setdefault(k, []).append(hit[0])
    out = {}
    for k, vs in votes.items():
        c = {v: vs.count(v) for v in set(vs)}
        best = max(c.values())
        tops = [v for v, n in c.items() if n == best]
        if len(tops) == 1:
            out[k] = tops[0]
    return out


# 계절감
SEAS = {"봄": r"봄|spring", "여름": r"여름|한여름|하계|summer|썸머|서머", "가을": r"가을|fall|autumn", "겨울": r"겨울|한겨울|winter|윈터"}
KEY = re.compile(r"계절감|계절|season|시즌|착용\s*시기|추천\s*계절", re.I)
TRANS = re.compile(r"간절기|환절기")
ALL = re.compile(r"사계절|올\s*시즌|all\s*season|4\s*계절|어느 계절", re.I)
SRX = {k: re.compile(v, re.I) for k, v in SEAS.items()}
NOT = re.compile(r"배송|세일|sale|이벤트|신상|오픈|입고|예약|발송|후기|리뷰|지난|시즌오프|off|특가|쿠폰|연휴|혜택|할인", re.I)
ORDER = ("봄", "여름", "가을", "겨울")
# 계절은 매장 글도 「~좋아요」「~네요」로 끝나는 일이 흔해서(etce 「사계절 편안하게 레이어링하기 좋아요」) REVIEW 보다 좁게 —
# 후기에만 있는 표시(리뷰 · 후기 · 별점 · 가린 이름 · 작성일 · 게시판 조회수 · 키/몸무게 · 평소 사이즈 · 스펙 칸에 담긴 후기 목록 '7': '…')만 본다.
REVIEW_HARD = re.compile(r"리뷰|후기|별점|★|평소\s*사이즈|구매했|재구매|몸무게|키\s*[:：]?\s*\d{3}|[가-힣a-z]\*{3,}|\d{2}\.\d{2}\.\d{2}|\d{4}-\d{2}-\d{2}"
                         r"|조회\s*\d|'\d{1,3}'\s*:\s*'[가-힣a-z][^']{10,}")   # 「'105': '55'」 같은 치수 칸은 후기가 아니다(legacy)
SEASON_RANGE = re.compile(r"(봄|여름|가을|겨울)\s*(?:부터|에서|~|-|–)\s*(봄|여름|가을|겨울)")


def season_of(text: str) -> list:
    """매장이 글에 적은 계절(「계절감 겨울」 「간절기」 「여름 시즌」). 시즌 코드(26SS)는 쓰지 않는다 — 그건 products 칸에 이미 있다."""
    t = (text or "").lower(); got = set()
    # 후기 문장은 건너뛴다 — 산 사람 말은 매장 값이 아니다(feel_of 와 같은 REVIEW). 상세 글에 후기가 섞여 드는 매장에서
    # 「간절기에 아주 잘어울리고」(steady-everywear-1557 후기)가 봄 · 가을로 붙었다(2026-11-09 검수 표본).
    rev = lambda a, b: bool(REVIEW_HARD.search(t[max(0, a - 40):b + 40]))
    for m in KEY.finditer(t):
        w = t[m.end():m.end() + 24]
        if NOT.search(t[max(0, m.start() - 15):m.end() + 24]) or rev(m.start(), m.end()): continue
        if re.search(r"[ㅁ□■●○◎@]|\[\s*\]", w): continue
        hit = {k for k, r in SRX.items() if r.search(w)}
        if 0 < len(hit) < 4: got |= hit
    for m in TRANS.finditer(t):
        if not NOT.search(t[max(0, m.start() - 15):m.end() + 15]) and not rev(m.start(), m.end()): got |= {"봄", "가을"}
    # 「봄부터 여름까지」 「가을~겨울」 — 범위는 사이 계절까지 다 (아래 「…까지」 줄은 끝 계절만 잡았다, 2026-11-09)
    for m in SEASON_RANGE.finditer(t):
        if NOT.search(t[max(0, m.start() - 15):m.end() + 10]) or rev(m.start(), m.end()): continue
        a, b = ORDER.index(m.group(1)), ORDER.index(m.group(2))
        got |= {ORDER[(a + i) % 4] for i in range((b - a) % 4 + 1)}
    # 「겨울철」 「여름 시즌」 「봄, 가을에 입기」 — 간절기와 함께 적힌 것도 더한다(「간절기 아우터 및 겨울철 레이어드」)
    for m in re.finditer(r"(봄|여름|가을|겨울)(?:\s*[,·/&]\s*(봄|여름|가을|겨울))*\s*(?:철|시즌|에\s*(?:입기|착용|활용)|용|까지)", t):
        if not NOT.search(t[max(0, m.start() - 15):m.end() + 10]) and not rev(m.start(), m.end()):
            got |= set(re.findall(r"봄|여름|가을|겨울", m.group(0)))
    if not got and any(not rev(m.start(), m.end()) for m in ALL.finditer(t)): got = {"봄", "여름", "가을", "겨울"}
    return [s for s in ("봄", "여름", "가을", "겨울") if s in got]
