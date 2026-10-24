"""합쇼체(~ㅂ니다/~습니다) 문장 끝을 해요체로. 모르는 끝말은 None(바꾸지 않음)."""
import re

BASE, CHO, JUNG = 0xAC00, 588, 28
JUNGS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONGS = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]


def dec(c):
    o = ord(c) - BASE
    if not 0 <= o < 11172:
        return None
    return o // CHO, JUNGS[(o % CHO) // JUNG], JONGS[o % JUNG]


def com(cho, jung, jong=""):
    return chr(BASE + cho * CHO + JUNGS.index(jung) * JUNG + JONGS.index(jong))


def has_batchim(c):
    d = dec(c)
    return bool(d and d[2])


# 어간 끝 ㅂ 이 「워」로 바뀌는 것(ㅂ 불규칙) — 넓 · 좁 · 입 · 잡 · 씹 은 규칙
B_IRR = ("럽", "가볍", "무겁", "부드럽", "가깝", "쉽", "어렵", "귀엽", "아름답", "차갑", "뜨겁", "두껍", "무섭", "즐겁", "반갑", "사랑스럽",
         "자연스럽", "고급스럽", "여성스럽", "멋스럽", "실용적이", "깔끔스럽", "세련스럽", "스럽", "롭", "답", "새롭", "향기롭", "여유롭", "자유롭", "평화롭")
B_REG = ("넓", "좁", "입", "잡", "씹", "업", "접", "뽑", "굽", "곱")
# ㄹ 받침이 「ㅂ니다」 앞에서 빠진 것: 만듭니다(만들) · 깁니다(길) · 답니다(달) · 늡니다(늘) · 붑니다(불) · 엽니다(열)
L_DROP = {"만드": "만들", "드": "들", "기": "길", "다": "달", "느": "늘", "부": "불", "여": "열", "어": "얼", "무": "물", "푸": "풀", "이끄": "이끌", "끄": "끌", "흔드": "흔들", "어우러": "어우러"}
L_STEMS = {"만드", "흔드", "이끄"}   # 이 셋만 ㄹ 탈락으로 본다 — 「기 · 다 …」는 다른 뜻(입다→입니? 아님)과 겹쳐 쓰지 않는다
SIOT = {"낫": "나", "짓": "지", "붓": "부", "잇": "이", "긋": "그"}   # 웃 · 씻 · 벗 · 솟 · 빼앗은 규칙
DIGEUT = {"걷": "걸", "듣": "들", "싣": "실", "깨닫": "깨달"}


def ayo(stem):
    """어간 → 해요체(~아요/~어요). 모르면 None."""
    if not stem:
        return None
    if stem.endswith("하"):
        return stem[:-1] + "해요"
    last = stem[-1]
    d = dec(last)
    if not d:
        return None
    cho, jung, jong = d
    if jong == "ㅆ":                                      # 았 · 었 · 했 · 겠 → ~어요
        return stem + "어요"
    if jong:
        # 받침 있는 어간
        for k, v in SIOT.items():
            if stem.endswith(k):
                pre = stem[: -len(k)] + v
                return pre + ("아요" if dec(v[-1])[1] in "ㅏㅗ" else "어요")
        for k, v in DIGEUT.items():
            if stem.endswith(k):
                return stem[: -len(k)] + v + "어요"
        if jong == "ㅂ" and not stem.endswith(B_REG) and (stem.endswith(B_IRR) or stem.endswith("스럽") or stem.endswith("롭")):
            base = stem[:-1] + com(cho, jung)
            return base + ("와요" if stem.endswith(("돕", "곱")) else "워요")
        if jong == "ㅎ" and stem.endswith(("갛", "랗", "맣", "렇", "떻")):   # 빨갛다 → 빨개요 · 그렇다 → 그래요(넣 · 놓 · 좋은 규칙)
            return stem[:-1] + com(cho, "ㅐ") + "요"
        return stem + ("아요" if jung in "ㅏㅗ" else "어요")
    # 받침 없는 어간
    if stem.endswith("르") and len(stem) >= 2:          # 다르다 → 달라요 · 흐르다 → 흘러요
        p = dec(stem[-2])
        if p and not p[2]:
            pre = stem[:-2] + com(p[0], p[1], "ㄹ")
            return pre + ("라요" if p[1] in "ㅏㅗ" else "러요")
    if jung == "ㅡ":                                      # 쓰다 → 써요 · 예쁘다 → 예뻐요
        p = dec(stem[-2]) if len(stem) >= 2 else None
        v = "ㅏ" if p and p[1] in "ㅏㅗ" else "ㅓ"
        return stem[:-1] + com(cho, v) + "요"
    m = {"ㅣ": "ㅕ", "ㅜ": "ㅝ", "ㅗ": "ㅘ", "ㅚ": "ㅙ"}
    if jung in m:
        if jung == "ㅣ" and cho == 11:                      # 이 → 여 (보이다 → 보여요)
            return stem[:-1] + "여요"
        return stem[:-1] + com(cho, m[jung]) + "요"
    if jung in "ㅏㅐㅓㅔㅕㅖㅘㅙㅝㅞㅟㅢ":                    # 가요 · 내요 · 서요 · 쉬어요(ㅟ 는 「쉬어요」가 맞다)
        return stem + ("어요" if jung in "ㅟㅢ" else "요")
    return None


def copula(noun_end):
    """명사 + 입니다 → 예요/이에요."""
    return "이에요" if has_batchim(noun_end) else "예요"


# 끝말 그대로 바꾸는 것(규칙으로 틀리는 것) — 긴 것부터 본다
SPECIAL = {"아닙니다": "아니에요", "돕습니다": "도와요", "감돕니다": "감돌아요", "돕니다": "돌아요", "갖습니다": "가져요",
           "띱니다": "띠어요", "따릅니다": "따라요", "치릅니다": "치러요", "들릅니다": "들러요", "끕니다": "끌어요",
           "머뭅니다": "머물러요", "내밉니다": "내밀어요", "팝니다": "팔아요", "삽니다": "살아요", "깁니다": "길어요",
           "멉니다": "멀어요", "놉니다": "놀아요", "답니다": "달아요"}
VERB_I = ("돋보입니다", "선보입니다", "보입니다", "섞입니다", "놓입니다", "쌓입니다", "쓰입니다", "높입니다", "붙입니다",
          "묶입니다", "기울입니다", "녹입니다", "덮입니다", "먹입니다", "속입니다", "끓입니다", "바뀝니다")


def convert_word(w):
    """문장 끝 낱말(…니다) 하나를 해요체로. 모르면 None."""
    if not w.endswith("니다"):
        return None
    for k in sorted(SPECIAL, key=len, reverse=True):
        if w.endswith(k):
            return w[: -len(k)] + SPECIAL[k]
    if w == "줄입니다":
        return "줄여요"
    body = w[:-2]
    if body.endswith("습"):                               # 있습니다 → 있어요
        return ayo(body[:-1])
    if body.endswith("입") and len(body) >= 2:            # 코트입니다 · 보입니다(보이다)
        stem_try = body[:-1] + "이"
        if w.endswith(VERB_I):
            return ayo(stem_try)                          # 보입니다 → 보여요 · 섞입니다 → 섞여요
        if body == "입":
            return None
        return body[:-1] + copula(body[-2])               # 코트입니다 → 코트예요 · 셔츠입니다 → 셔츠예요
    last = body[-1] if body else ""
    d = dec(last)
    if not d or d[2] != "ㅂ":
        return None
    stem = body[:-1] + com(d[0], d[1])                    # 어울립 → 어울리
    if stem.endswith("드"):                              # 만들 · 흔들 · 줄어들 · 스며들 · 들다
        return stem[:-1] + "들어요"
    if stem in ("기", "머"):                             # 깁니다(길다) · 멉니다(멀다)
        return {"기": "길어요", "머": "멀어요"}[stem]
    if stem in L_STEMS or stem.endswith(tuple(L_STEMS)):
        for k in L_STEMS:
            if stem.endswith(k):
                return stem[: -len(k)] + L_DROP[k] + "어요"
    return ayo(stem)


END_RX = re.compile(r"([가-힣]+니다)(?=[.!?…]*\s*$)")


def _prev_char(s, i):
    j = i - 1
    while j >= 0 and s[j] == " ":
        j -= 1
    return s[j] if j >= 0 else ""


def to_haeyo(text):
    """요약 전체를 해요체로. (바뀐 글, 못 바꾼 문장 수)"""
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    out, fail = [], 0
    for s in sents:
        m = END_RX.search(s)
        if not m:
            out.append(s)
            continue
        new = convert_word(m.group(1))
        if m.group(1) == "입니다":                        # 「포인트 입니다」처럼 띄어 쓴 것
            pc = _prev_char(s, m.start(1))
            new = copula(pc) if pc else None
        if new is None:
            fail += 1
            out.append(s)
        else:
            head = s[: m.start(1)].rstrip() if m.group(1) == "입니다" else s[: m.start(1)]
            out.append(head + new + s[m.end(1):])
    return " ".join(out), fail
