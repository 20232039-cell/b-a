"""식스샵(sixshop) 매장 수집 — 사이트맵으로 상품 주소를 받고 상품 페이지를 한 벌씩 읽는다.

    python scripts/crawl_sixshop.py --brands gonak
    python scripts/crawl_sixshop.py              # platforms.SIXSHOP 전부

카페24·Shopify 매장과 **같은 모양의 줄**을 data/crawl/<slug>.jsonl 에 쓴다. 뒤 단계(build-csv ·
size_from_ocr · tag_items · OCR · export)가 매장 종류를 몰라도 되게.

식스샵은 목록 화면이 자바스크립트라 카페24 수집기로는 0벌이다. 그런데 `/sitemap.xml` 에 상품 주소가
다 있고(고낙 130 · 포스센스티브 74 · 일류 61 — 아스트라 D 조사, 2026-09-23), 상품 페이지 HTML
하나에 필요한 것이 다 들어 있다: 이름(#shopProductName) · 값(.productPriceSpan) · 사이즈별 품절
(data-soldout) · 사진(#shopProductImgsMainDiv) · 설명(#productDescriptionDetailPage — 혼용률과
실측표가 **글로** 적혀 있다). 그래서 목록·상세를 따로 훑지 않고 매 판 상품 페이지를 다 연다.
사이트맵의 lastmod 는 품절이 바뀌어도 그대로일 수 있어 캐시 기준으로 쓰지 않는다.

판단은 crawl_shopify 와 같다(merge_rows 를 같이 쓴다): 값·재고 자국은 cc.carry_over, 사라진 상품은
연속 두 판 없으면 delisted, 받은 목록이 알던 판매중의 절반도 안 되면 상태를 안 바꾼다.
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs
from platforms import SIXSHOP

CRAWL_DIR = cc.CRAWL_DIR
PAGE_DELAY = 4.0          # 상품 페이지 간격(초) — crawl_shopify 와 같다


def product_urls(http: cc.PoliteSession, base: str, log=print) -> list[str] | None:
    r = cs.get_patient(http, f"{base}/sitemap.xml", log)
    if r is None or r.status_code != 200:
        log(f"  {base} 사이트맵 받기 실패({getattr(r, 'status_code', '없음')}) — 이 판은 상태를 안 바꾼다")
        return None
    urls = [html.unescape(u).strip() for u in re.findall(r"<loc>([^<]+)</loc>", r.text)]
    return list(dict.fromkeys(u for u in urls if "/product/" in u))


def _num(s: str) -> int:
    """칸의 **첫** 값만 — 「91,800원」. 칸 여럿을 한꺼번에 읽어 숫자를 다 이으면
    포스센스티브 정가가 「9180010800016200」(할인가·정가·할인액)이 됐다."""
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group(0).replace(",", "")) if m else 0


def _big(u: str) -> str:
    """썸네일 주소(`/thumbnails/…_1000.jpg`)를 원본(`…/image_N.jpg`)으로 — 크기가 달라도 같은 그림이 같은 주소가 되게."""
    u = (u or "").split("?")[0]
    # 무신사 그림 창고의 상세 그림은 「//image.msscdn.net/…」처럼 스킴 없이 온다. 그대로 두면 OCR 이
    # 내려받지 못해 포스센스티브 11벌에서 그림 96장을 못 읽었다(2026-09-24).
    if u.startswith("//"):
        u = "https:" + u
    return re.sub(r"/thumbnails(/uploadedFiles/.+?)_\d+(\.\w+)$", r"\1\2", u)


def _desc_text(el) -> str:
    """설명 글 — 실측표의 사이즈 줄과 숫자 줄이 갈라져 오는 것을 붙인다.

    식스샵 편집기는 「1(S)」 다음 줄에 「53.2  64.5  60.6  65.4」를 둔다. size_from_ocr.from_ocr 는
    한 줄에 이름과 값이 같이 있어야 읽는다. 숫자만 있는 줄을 앞줄에 붙이면 읽힌다(고낙 자켓으로 확인).
    칸 사이는 대개 줄바꿈 없는 공백(\\xa0)이라 먼저 보통 공백으로 고른다.
    """
    if el is None:
        return ""
    t = el.get_text("\n", strip=True)
    t = re.sub(r"[\xa0　]", " ", t)
    # 「77.1 / 78.1」「77/ 78」「76/77」(앞/뒤 두 값)이 든 숫자 줄도 숫자 줄이다 — 못 붙여 고낙 셔츠 표가 갈라졌다
    t = re.sub(r"\n(?=[\d.]+(?:(?:[ \t]*/[ \t]*|[ \t]+)[\d.]+)+[ \t]*$)", " ", t, flags=re.M)
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


_FB_HEAD = re.compile(r"\(\s*앞\s*/\s*뒤\s*\)")
_FB_PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")


def front_back(text: str) -> str:
    """「총장(앞/뒤)」 칸에 「77.1 / 78.1」처럼 값이 둘인 표 — 매장이 기준을 적었을 때만 하나를 고른다.

    고낙 셔츠 19벌이 이 꼴이라 표를 못 읽었다. 같은 글에 「* 총장 넥 뒷중심 기준입니다」가 있다 — 매장이
    말한 기준이 뒤이므로 뒤 값을 쓴다. 기준이 안 적힌 표는 그 칸을 뺀다 — 고르면 짐작이고, 그대로 두면
    표 해석기가 앞 값을 조용히 집는다(washed glitch half shirt 에서 총장 75 가 들어갔다).
    값 줄(숫자 셋 이상, kg 없음)에서만 바꾼다 — 「168 / 58kg」 같은 모델 줄은 건드리지 않는다.
    """
    if not _FB_HEAD.search(text or ""):
        return text
    if re.search(r"뒷\s*중심|뒤\s*중심|뒤\s*기준|back\s*(?:center|neck)", text, re.I):
        pick = 2
    elif re.search(r"앞\s*중심|앞\s*기준", text):
        pick = 1
    else:
        pick = 0          # 기준이 없다 — 그 칸(총장)만 빼고 어깨·가슴·소매처럼 확실한 칸은 살린다
    out = []
    for ln in text.splitlines():
        if _FB_HEAD.search(ln):
            ln = _FB_HEAD.sub("", ln) if pick else re.sub(r"\S*\(\s*앞\s*/\s*뒤\s*\)", " ", ln)
        elif len(re.findall(r"\d+(?:\.\d+)?", ln)) >= 4 and not re.search(r"(?i)kg|모델|model", ln):
            ln = _FB_PAIR.sub(lambda m: m.group(pick) if pick else " ", ln)
        out.append(ln)
    return "\n".join(out)


_SIZE_ROW = re.compile(r"\bSIZE\s*([A-Z0-9]{1,4})\s*[:：]\s*(.*?)(?=\bSIZE\s*[A-Z0-9]{1,4}\s*[:：]|$)", re.I | re.S)
_PAIR = re.compile(r"([A-Za-z가-힣][A-Za-z가-힣 .]*?)\s*[-:：]\s*(\d+(?:[.,]\d+)?)")


def size_rows(text: str) -> str:
    """「SIZE 1: SHOULDER - 63 / CHEST - 125 … SIZE 2: …」 한 줄을 행렬 글로 — from_ocr 가 읽는 꼴이다.

    메리메이드는 실측을 JSON-LD 설명에만 두고 그 글은 줄바꿈이 없다. 사이즈 둘 이상이 **같은 부위**를
    같은 차례로 말할 때만 바꾼다 — 부위가 엇갈리면 짐작이 되니 그대로 둔다. 「64,5」는 소수점이다.
    """
    rows = []
    for name, body in _SIZE_ROW.findall(text or ""):
        pairs = [(k.strip().lower(), v.replace(",", ".")) for k, v in _PAIR.findall(body)]
        if len(pairs) >= 2:
            rows.append((name, pairs))
    if len(rows) < 2 or len({tuple(k for k, _ in p) for _, p in rows}) != 1:
        return text
    head = "Size " + " ".join(k.replace(" ", "") for k, _ in rows[0][1])
    return text + "\n" + head + "\n" + "\n".join(n + " " + " ".join(v for _, v in p) for n, p in rows)


_PERSONAL = re.compile(r"개인\s*결제|\(\s*code\s*:|\bvvip\s+personal\b", re.I)
_NOTICE_FIRST = re.compile(r"\s*[\[【<(]?\s*(?:배송\s*안내|교환\s*(?:및|/|&)?\s*반품|반품\s*안내|shipping|delivery)", re.I)


def parse_page(html_text: str, url: str, slug: str, now: str) -> dict | None:
    s = BeautifulSoup(html_text, "lxml")
    m = re.search(r'data-shopProductNo="(\d+)"', html_text)
    name_el = s.select_one("#shopProductName")
    if not m or name_el is None:
        return None
    name = name_el.get_text(" ", strip=True)
    if _PERSONAL.search(name):
        # 개인결제창 — 옷이 아니라 손님 한 사람 몫의 결제 페이지다. 폴리테루는 사이트맵 2,322줄 중
        # 2,144줄이 이것이었다(「(CODE:VVIP PERSONAL 최*민님)」, 2026-09-27). 실패가 아니라 건너뜀.
        return {"product_no": int(m.group(1)), "_skip": "개인결제창"}
    # 할인이 없으면 .productPriceSpan 하나, 있으면 할인가 .productDiscountPriceSpan 과 정가
    # .productPriceWithDiscountSpan 둘이 온다(.productPriceSpan 은 없다 — 포스센스티브에서 확인).
    sale = s.select_one(".productDiscountPriceSpan")
    listed = s.select_one(".productPriceWithDiscountSpan") or s.select_one(".productPriceSpan")
    base_price = _num(listed.get_text(" ", strip=True)) if listed else 0
    price = _num(sale.get_text(" ", strip=True)) if sale else base_price
    if not price:
        return None                   # 값을 못 읽은 줄은 쓰지 않는다 — 0원 옷보다 빈 줄이 낫다
    # 사이즈 — PC·모바일 두 벌이 같이 있다. 옵션 차례(data-option-index)가 둘이면 색·사이즈 따위라
    # 이름이 size/사이즈 인 쪽만 받는다(아스트라 D: 「Color 그룹은 사이즈에서 제외」).
    groups: dict[str, list[tuple[str, bool]]] = {}
    labels: dict[str, str] = {}
    for wrap in s.select(".productOption"):
        lab = ""
        t = wrap.get_text("\n", strip=True).split("\n")
        if t:
            lab = t[0].strip().lower()
        for o in wrap.select("[data-option-value]"):
            gi = o.get("data-option-index") or "0"
            labels.setdefault(gi, lab)
            v = (o.get("data-option-value") or "").strip()
            if v and v not in [x for x, _ in groups.get(gi, [])]:
                groups.setdefault(gi, []).append((v, o.get("data-soldout") == "true"))
    size_gi = [g for g in groups if re.search(r"size|사이즈", labels.get(g, ""))]
    pick = size_gi[0] if size_gi else (next(iter(groups)) if len(groups) == 1 else None)
    opts = groups.get(pick, []) if pick else []
    imgs = []
    for im in s.select("#shopProductImgsMainDiv img"):
        u = _big(im.get("src") or im.get("data-src") or "")
        if u and u not in imgs:
            imgs.append(u)
    # 썸네일 배치(type_thumbnails)는 사진을 <img> 가 아니라 imgSrc 속성을 단 div 배경으로 둔다 — 피노아친퀘
    # 133벌이 사진 1장(og:image)뿐이었다(2026-09-27). 차례(data-shopProductSequence)대로 받는다.
    if not imgs:
        seq = sorted(s.select(".shopProductImgMain[imgSrc]"), key=lambda e: int(e.get("data-shopproductsequence") or 0))
        for el in seq:
            u = el.get("imgsrc") or ""
            u = _big(("https://contents.sixshop.com" + u) if u.startswith("/uploadedFiles/") else u)
            if u and u not in imgs:
                imgs.append(u)
    if not imgs:
        og = s.select_one('meta[property="og:image"]')
        if og and og.get("content"):
            imgs = [_big(og["content"])]
    desc_el = s.select_one("#productDescriptionDetailPage")
    text = _desc_text(desc_el)
    if not text or _NOTICE_FIRST.match(text):
        # 상세 칸이 빈 매장은 상품 소개를 JSON-LD description 에만 둔다(피노아친퀘 133벌 전부, 2026-09-27).
        # 상세 칸이 **배송·교환 공지로 시작하는** 매장도 같다 — 메리메이드는 그 칸이 공지뿐이고 소개·소재·
        # 모델·실측(「SIZE 1: SHOULDER - 63 / CHEST - 125 …」)이 JSON-LD 에만 있었다(코덱스 검증 004, 2026-09-28).
        # 공지는 뒤에 그대로 둔다 — 앱 설명을 만들 때 product_desc 가 걷는다.
        ld = cc.parse_json_ld_product(html_text) or {}
        raw = ld.get("description") or ""
        if not raw:
            # 매장 글의 인치 표기(「(57"-58")」)가 따옴표를 안 가려 JSON-LD 가 통째로 안 읽힌다(메리메이드).
            # 설명 칸만 다음 칸 이름(sku·brand·offers) 앞까지 글자로 떠 온다.
            mr = re.search(r'"@type"\s*:\s*"Product".*?"description"\s*:\s*"(.*?)"\s*,\s*"(?:sku|brand|offers|image)"',
                           html_text, re.S)
            raw = mr.group(1).replace('\\"', '"') if mr else ""
        ld_text = re.sub(r"\s*˙\s*", "\n", html.unescape(raw)).strip()
        if not text:
            text = ld_text
        elif len(ld_text) >= 30:
            text = ld_text + "\n" + text
    detail_imgs = []
    if desc_el is not None:
        for im in desc_el.find_all("img"):
            u = _big(im.get("data-src") or im.get("src") or "")
            if u and u not in detail_imgs:
                detail_imgs.append(u)
    mw = re.search(r'data-productSoldOut="(\w+)"', html_text)
    whole = mw.group(1) if mw else ""
    import size_from_ocr
    names, cols = size_from_ocr.from_ocr(front_back(size_rows(text))) if text else (None, {})
    d = {
        "product_no": int(m.group(1)),
        "name": name,
        "price": price,
        # 상품 전체의 품절은 data-productSoldOut(「soldOut」/「notSoldOut」)이 말한다 — 옵션이 없는 벨트·
        # 모자(포스센스티브 15벌)는 이것뿐이다. 옵션이 다 품절이어도 품절이다.
        "soldout": whole == "soldOut" or (bool(opts) and all(so for _, so in opts)),
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": url,
        "description": text,
        "description_source": "sixshop",
        "detail_text": text,
        "size_table": ({**cols, **({"_names": names} if names else {})} if len(cols) >= 2 else {}),
        "spec": {"상품명": name},
        "detail_images": detail_imgs,
        "options": [v for v, _ in opts],
        "soldout_options": [v for v, so in opts if so],
        "category_nos": [],
        "category_names": [],
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "sixshop",
    }
    if not opts and whole not in ("soldOut", "notSoldOut"):
        d["soldout_unknown"] = True   # 옵션도 품절 표시도 없다 — 모르는 것을 판매중으로 믿지 않게 적어 둔다
    if sale and base_price > price:
        d["price_listed"] = base_price
    return d


def crawl_one(http: cc.PoliteSession, slug: str, log=print) -> dict:
    base = SIXSHOP[slug]
    now_ts = datetime.now(cc.KST).strftime("%Y-%m-%dT%H:%M:%S")
    rep = {"slug": slug, "listed": 0, "new": 0, "soldout": 0, "restock": 0, "delisted": 0, "guard": "", "failed": 0}
    urls = product_urls(http, base, log)
    prev = cs.load_prev(slug)
    if urls is None:
        rep["guard"] = "사이트맵 받기 실패"
        return rep
    page_http = cc.PoliteSession(delay=max(http.delay, PAGE_DELAY))
    got: list[dict] = []
    for u in urls:
        r = cs.get_patient(page_http, u, log)
        if r is None or r.status_code != 200:
            rep["failed"] += 1
            continue
        d = parse_page(r.text, u, slug, now_ts)
        if d is None:
            rep["failed"] += 1
            continue
        if d.get("_skip"):
            rep["skipped"] = rep.get("skipped", 0) + 1
            continue
        got.append(d)
    rep["listed"] = len(got)
    # 페이지를 절반 넘게 못 읽었으면 판을 버린다 — 못 연 상품을 「목록에 없음」으로 세면 멀쩡한 옷이 내려간다
    if urls and rep["failed"] > (len(urls) - rep.get("skipped", 0)) / 2:
        rep["guard"] = f"상품 페이지 {rep['failed']}/{len(urls)} 실패 — 상태 변경 보류"
        log(f"[{slug}] 가드레일: {rep['guard']}")
        return rep
    return cs.merge_rows(slug, prev, got, now_ts, rep, log)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", help="slug 목록. 없으면 platforms.SIXSHOP 전부")
    ap.add_argument("--delay", type=float, default=2.0, help="같은 호스트 요청 간격(초)")
    args = ap.parse_args()
    slugs = args.brands or sorted(SIXSHOP)
    bad = [s for s in slugs if s not in SIXSHOP]
    if bad:
        sys.exit(f"식스샵 매장이 아니다: {' '.join(bad)} (platforms.SIXSHOP 에 없음)")
    CRAWL_DIR.mkdir(parents=True, exist_ok=True)
    http = cc.PoliteSession(delay=args.delay)
    reps = [crawl_one(http, s) for s in slugs]
    print(json.dumps(reps, ensure_ascii=False))
    if any(r["guard"] for r in reps):
        print("가드레일에 걸린 매장: " + ", ".join(r["slug"] for r in reps if r["guard"]), file=sys.stderr)


if __name__ == "__main__":
    main()
