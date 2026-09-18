"""이미 읽어 둔 OCR 글에서 **상품 글**을 뽑는다 — 소재·취급·설명.

왜 새로 긁지 않나: 판매중 의류 76,966벌 중 상세글에 알맹이가 없는 것이 18,460벌인데,
그중 14,084벌(79%)은 `data/crawl/ocr/<slug>.jsonl` 에 이미 읽어 둔 글 안에 소재가
들어 있었다(2026-09-18 전수). 사이즈를 뽑으려고 읽은 그림에 상품 설명이 같이 있었고,
우리가 그걸 상품 줄에 안 옮겼을 뿐이다.

OCR 글은 사진 잡음이 섞여 있어 통째로 쓰면 안 된다("ae:", "a pic ^ 시 | i |e x ¢").
그래서 셋만 골라 뽑는다:

  소재   섬유 낱말 + 퍼센트. 합이 100 언저리일 때만 받는다 — 잡음 숫자를 거르는 잣대다.
  취급   드라이·손세탁 같은 말이 든 줄.
  설명   한글 문장으로 읽히는 줄. 잡음 줄은 문장으로 안 끝난다.

성공 기준은 **소재를 얻었거나 설명 문장이 둘 이상**이다. 글자 수가 아니라 알맹이다
(사람 기준 2026-09-18: 「성공이라고 기록하려면 소재나 디테일 색 이런거 있어야지」).

무엇을 쓰나: data/crawl/detail/<slug>.jsonl — 상품 줄은 건드리지 않는다. 되돌릴 수 있어야
한다.
"""
from __future__ import annotations
import argparse, collections, io, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRAWL = ROOT / "data" / "crawl"
OUT_DIR = CRAWL / "detail"

# 섬유 낱말 — 매장이 실제로 쓴 말만 모았다. 모르는 낱말이 섞이면 합이 안 맞아 통째로 버려진다.
FIBER = (r"면|코튼|목면|폴리에스터|폴리에스테르|폴리|피이티|울|양모|모|아크릴|나일론|레이온|"
         r"비스코스|린넨|마|실크|견|캐시미어|알파카|앙고라|모헤어|스판덱스|스판|폴리우레탄|"
         r"텐셀|모달|아세테이트|큐프라|대나무|헴프|램스울|메리노|" 
         r"COTTON|POLYESTER|POLYAMIDE|POLYURETHANE|POLY|WOOL|ACRYLIC|NYLON|RAYON|VISCOSE|"
         r"LINEN|SILK|CASHMERE|ALPACA|ANGORA|MOHAIR|SPANDEX|ELASTANE|TENCEL|MODAL|ACETATE|"
         r"CUPRO|HEMP|LYOCELL|PU|PVC")
_FIBER_PCT = re.compile(rf"({FIBER})\s*[:：]?\s*(\d{{1,3}})\s*%", re.I)
# 「[겉감] … [안감] …」처럼 부위를 적는 매장이 있다 — 부위별로 따로 센다
_PART = re.compile(r"[\[\(]?\s*(겉감|안감|배색|충전재|테이프|시보리|원단)\s*[\]\)]?", re.I)
_CARE = re.compile(r"(드라이\s*클?리?닝|손\s*세탁|물\s*세탁|세탁기|단독\s*세탁|표백|건조기|다림질|"
                   r"DRY\s*CLEAN|HAND\s*WASH|MACHINE\s*WASH)", re.I)
# 한글 문장 — 잡음 줄은 문장으로 안 끝난다
_SENT = re.compile(r"[가-힣].{10,}?(?:다|요|죠|음|함)\s*[.!]?\s*$")
_HANGUL = re.compile(r"[가-힣]")
# 상품 글이 아닌 줄 — 매장 공지·법적 고지·배송 안내
# 상품 글이 아닌 줄 — 매장 공지·법적 고지·배송 안내, 그리고 소식받기·행사 홍보.
# 「신상품 발매와 세일 소식을 가장 먼저 받아보세요」가 상품 설명으로 들어가 있었다.
_BOILER = re.compile(r"(교환|반품|환불|배송|택배|상표권|법적|무단|고지|주의사항|모니터|해상도|"
                     r"오차가|착용 컷|상세 컷|고객센터|영업일|입금|적립금|쿠폰|"
                     r"신상품|발매|세일|소식|받아보세요|구독|회원가입|이벤트|할인|"
                     r"카카오|인스타|팔로우|문의|재입고|품절|주문|결제|무이자|사은품)")


def _clean_lines(text: str) -> list[str]:
    out = []
    for ln in (text or "").splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
    return out


def materials(lines: list[str]) -> dict[str, str]:
    """부위별 혼용률. 합이 90~110 인 묶음만 받는다 — 잡음 숫자가 섞이면 합이 어긋난다."""
    got: dict[str, list[tuple[str, int]]] = {}
    part = "겉감"
    for ln in lines:
        m = _PART.search(ln)
        if m:
            part = m.group(1)
        pairs = [(a.upper(), int(b)) for a, b in _FIBER_PCT.findall(ln)]
        if not pairs:
            continue
        got.setdefault(part, [])
        for p in pairs:
            if p not in got[part]:
                got[part].append(p)
    out = {}
    for part, pairs in got.items():
        total = sum(v for _, v in pairs)
        if 90 <= total <= 110:
            out[part] = " ".join(f"{a} {b}%" for a, b in pairs)
    return out


def care(lines: list[str]) -> list[str]:
    seen, out = set(), []
    for ln in lines:
        if not _CARE.search(ln) or _BOILER.search(ln):
            continue
        ln = ln.strip(" :=|·-")
        # 세탁 기호표를 읽은 줄이 섞인다("30도 염소표백", "Haze 손세탁 SSAA 금지 금").
        # 한글이 충분히 차고 문장처럼 읽히는 줄만 받는다.
        han = len(_HANGUL.findall(ln))
        if han < 6 or han / max(1, len(ln)) < 0.5:
            continue
        if 6 <= len(ln) <= 80 and ln not in seen:
            seen.add(ln); out.append(ln)
    return out[:4]


# 상품 글인지 가리는 마지막 잣대 — 낱말을 막는 쪽(_BOILER)만으로는 안 됐다. 매장 안내문이
# 끝없이 다른 말로 들어온다("측정 방법에 따라…", "보내주신 피드백을 바탕으로…",
# "컬러 별로 약간의 차이가 있을 수 있습니다"). 뽑아 둔 설명의 32.8%가 그런 줄이었다.
# 그래서 막는 대신 **옷 이야기를 하는 줄만 받는다**. 문장은 둘 중 하나는 말한다 —
# 옷의 성질(핏·두께·신축·안감·포켓·짜임…)이거나, 입었을 때의 인상(무드·실루엣…)이다.
_HARD = re.compile(r"(핏\b|오버핏|루즈핏|슬림핏|레귤러핏|크롭|와이드|테이퍼|스트레이트|기장|총장|"
                   r"두께|도톰|얇은|얇게|가벼운|중량|신축|스트레치|늘어나|비침|비치지|안감|겉감|기모|"
                   r"포켓|주머니|지퍼|단추|버튼|스냅|밴딩|스트링|카라|칼라|넥라인|라운드넥|브이넥|"
                   r"소매|커프스|봉제|스티치|절개|다트|주름|플리츠|자수|프린팅|워싱|가공|짜임|조직감|"
                   r"원단|소재|통기|보온|방수|방풍|수축|이염|보풀|착용감|실루엣|밑단|허리|어깨|"
                   r"셋업|레이어드|배색|컬러웨이|"
                   # 핏 조언 — 「한 사이즈 크게 선택하시기를 권장드립니다」는 옷 이야기다.
                   # 안내문("권장 사이즈를 참고하셔서 구매하시는 것을 추천")과는 말이 다르다.
                   r"한 사이즈|사이즈 업|정사이즈|사이즈를 권장|사이즈 추천|사이즈를 추천)")
_SOFT = re.compile(r"(여리여리|사랑스|러블리|무드|분위기|세련|시크|우아|고급스|감각적|트렌디|"
                   r"스타일리시|매력|빈티지|캐주얼|미니멀|클래식)")


def detail_kind(sents: list[str]) -> str:
    """설명이 무엇을 말하나 — 옷의 성질이면 「성질」, 인상뿐이면 「분위기」."""
    if any(_HARD.search(s) for s in sents):
        return "성질"
    return "분위기" if sents else ""


def description(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if _BOILER.search(ln) or not _SENT.search(ln):
            continue
        han = len(_HANGUL.findall(ln))
        # 한글이 절반 넘게 차야 문장이다 — OCR 잡음은 기호·로마자가 많다
        if han < 10 or han / max(1, len(ln)) < 0.45:
            continue
        # 옷 이야기를 하는 줄만 받는다 — 위의 잣대
        if not (_HARD.search(ln) or _SOFT.search(ln)):
            continue
        if ln not in out:
            out.append(ln)
    return out[:8]


def mine(text: str) -> dict:
    lines = _clean_lines(text)
    mat, ca, desc = materials(lines), care(lines), description(lines)
    ok = bool(mat) or len(desc) >= 2
    return {"material": mat, "care": ca, "description": desc, "ok": ok}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=0, help="매장마다 이만큼만 — 시험용")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 세기만 한다")
    args = ap.parse_args()

    paths = sorted((CRAWL / "ocr").glob("*.jsonl"))
    if args.brands:
        want = set(args.brands)
        paths = [p for p in paths if p.stem in want]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tally = collections.Counter()
    per = collections.Counter()
    for p in paths:
        rows = []
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            n += 1
            if args.limit and n > args.limit:
                break
            got = mine(d.get("ocr_text") or "")
            tally["읽은 상품"] += 1
            if not got["ok"]:
                tally["알맹이 못 찾음"] += 1
                continue
            tally["알맹이 찾음"] += 1
            if got["material"]:
                tally["소재 얻음"] += 1
            if got["description"]:
                tally["설명 얻음"] += 1
            if got["care"]:
                tally["취급 얻음"] += 1
            per[p.stem] += 1
            rows.append({"brand_slug": p.stem, "product_no": d.get("product_no"),
                         "material": got["material"], "care": got["care"],
                         "description": got["description"],
                         "detail_kind": detail_kind(got["description"]), "source": "ocr"})
        if rows and not args.dry_run:
            with (OUT_DIR / f"{p.stem}.jsonl").open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for k, v in tally.most_common():
        print(f"  {k:12}{v:8,}")
    print("\n많이 얻은 매장:", ", ".join(f"{b} {c:,}" for b, c in per.most_common(10)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
