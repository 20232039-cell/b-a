"""로라로라(rolarola) — 위사몰(wm_engine_SW). 목록 페이지로 상품 주소를 받고 상품 페이지를 한 벌씩 읽는다.

2026-10-01 첫 시험. 컨셉원(stores/concepts1one.py)과 엔진은 같지만 틀(스킨)이 달라 해석기를 따로 둔다.
- robots.txt · sitemap.xml 이 없다(둘 다 404). 그래서 목록 페이지를 쓴다 — VIEW ALL(big_section.php?cno1=1786)이
  「상품이 모두 1764개」, 한 쪽 15벌인데 `&rows=200` 을 붙이면 한 쪽 200벌이 온다(9쪽).
- 칸 이름은 상품 페이지에 없다. 큰 칸(OUTWEAR · TOP · DRESS · BOTTOM · SWIM · ACC · HOME · KIDS)도 같은 길로 훑어
  상품 → 칸 이름을 붙인다(몇 쪽 더). OUTLET · NEW · BEST 같은 기획 칸은 분류가 아니라 붙이지 않는다.
- 상품 페이지(shop/detail.php?pno=<32자>) — 이름 input[name=product_name] · 판매가 input[name=total_prc]
  (화면 큰 글씨 · pay_prc 는 할인가라 쓰지 않는다 — concepts1one 과 같은 판단) · 정가 .consumer · 판매 상태
  input[name=stat](2 = 정상) · 상품 번호 data-pno · 사진 #mainImg · .addimg · 상세 그림 .detail_info img.
- 상세 글이 없다 — 소재 · 실측표 · 모델 정보가 모두 상세 **그림** 맨 아래 「Details」 칸에 있다(NUBASIC STRIPE
  BOATNECK … Size Chart 총장 64 · 어깨단면 55 · 가슴둘레 114 …). detail_images 에 넣어 OCR 이 읽게 한다.
- 사이즈별 재고는 정적 HTML 에 없다(옵션 값 「FREE::0::0::13090::cpx0::」에 재고가 없다) — 옵션이 있으면
  soldout_unknown 을 단다(concepts1one 과 같다).
"""
import html as _html
import re

from bs4 import BeautifulSoup

import crawl_shopify as cs

BASE = "https://www.rolarola.com"
PAGE_DELAY = 2.0          # robots.txt 가 없다(Crawl-delay 도 없다). 1,764벌이라 3초면 한 판 90분 — 주간 묶음(170분)에 다른 매장과 함께 들게
ALL_CATE = "1786"           # VIEW ALL
CATES = {"1788": "OUTWEAR", "1789": "TOP", "1790": "DRESS", "1791": "BOTTOM", "1793": "SWIM", "1794": "ACC",
         "2825": "HOME", "2770": "KIDS"}
ROWS = 200

_PNO = re.compile(r"detail\.php\?pno=([0-9A-Fa-f]{32})")
_CATS_OF: dict[str, list[str]] = {}       # pno(대문자) → 칸 이름들 — list_urls 가 채우고 parse_page 가 읽는다


def _walk(http, cno: str, log) -> tuple[list[str], int | None] | None:
    seen: dict[str, None] = {}
    total = None
    for page in range(1, 40):
        r = cs.get_patient(http, f"{BASE}/shop/big_section.php?cno1={cno}&rows={ROWS}&page={page}", log)
        if r is None or r.status_code != 200:
            log(f"  [rolarola] 목록 {cno} {page}쪽 받기 실패({getattr(r, 'status_code', '없음')})")
            return None
        if total is None:
            m = re.search(r"상품이 모두 <strong>([\d,]+)</strong>", r.text)
            total = int(m.group(1).replace(",", "")) if m else None
        # 목록 칸(ul.prd_basic) 안의 링크만 — 머리 메뉴 · 최근 본 상품(click_prd)의 주소가 섞이지 않게
        body = r.text.split('class="prd_basic', 1)[1] if 'class="prd_basic' in r.text else ""
        got = [p.upper() for p in _PNO.findall(body.split("<!-- 페이징", 1)[0])]
        new = [p for p in dict.fromkeys(got) if p not in seen]
        if not new:
            break
        seen.update(dict.fromkeys(new))
        if len(set(got)) < ROWS:
            break
    return list(seen), total


def list_urls(http, log=print) -> list[str] | None:
    got = _walk(http, ALL_CATE, log)
    if got is None:
        return None
    pnos, total = got
    log(f"  [rolarola] VIEW ALL {len(pnos)}벌 / 화면 {total}")
    # 목록이 화면 수보다 많이 적으면(틀이 바뀜 · 쪽 하나 실패) 판을 버린다 — 멀쩡한 옷이 「목록에 없음」이 된다
    if not pnos or (total and len(pnos) < total * 0.9):
        return None
    cats: dict[str, list[str]] = {}
    for cno, name in CATES.items():
        g = _walk(http, cno, log)
        if g is None:
            return None
        cpnos, ctotal = g
        log(f"  [rolarola] {name}({cno}) 목록 {len(cpnos)}벌 / 화면 {ctotal}")
        # 상품 페이지에는 칸 이름이 없어 이 목록이 유일한 근거다. 틀이 바뀌어 ([], 100)이 돌아와도 받으면 그 칸의 상품이
        # 조용히 분류를 잃는다 — VIEW ALL 과 같은 90% 문으로 판을 버린다(코덱스 리뷰 d4443a5 · 067). 화면 수가 0 이거나
        # 없는 빈 칸(철 지난 기획전)은 그대로 둔다.
        if ctotal and len(cpnos) < ctotal * 0.9:
            log(f"  [rolarola] {name} 목록이 TOTAL 보다 많이 적다 — 판을 버린다")
            return None
        for p in cpnos:
            cats.setdefault(p, []).append(name)
    _CATS_OF.clear()
    _CATS_OF.update(cats)
    return [f"{BASE}/shop/detail.php?pno={p}" for p in pnos]


def _won(s) -> int:
    m = re.search(r"\d[\d,]*", str(s or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def _abs(u: str) -> str:
    u = (u or "").strip().split("#")[0]      # 부가 사진 주소 끝의 「#addimg」 표식을 뗀다
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE + u
    return u


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    if 'name="prdFrm"' not in html_text:
        return None                       # 없는 상품 · 오류 화면
    s = BeautifulSoup(html_text, "lxml")

    def val(name: str) -> str:
        el = s.select_one(f'form[name="prdFrm"] input[name="{name}"]')
        return (el.get("value") or "").strip() if el is not None else ""

    name = _html.unescape(val("product_name"))
    if not name:
        el = s.select_one(".wrap_prd .info h3.name")
        name = el.get_text(" ", strip=True) if el is not None else ""
    price = _won(val("total_prc"))
    con = s.select_one(".wrap_prd .info .consumer")
    listed = _won(con.get_text(" ", strip=True)) if con is not None else 0
    if not name or not price:
        return None                       # 값을 못 읽은 줄은 쓰지 않는다 — 0원 옷보다 빈 줄이 낫다
    m = re.search(r'data-pno="(\d+)"', html_text)
    if not m:
        return None
    product_no = int(m.group(1))
    mp = _PNO.search(url) or re.search(r'name="pno" value="([0-9A-Fa-f]{32})"', html_text)
    pno = mp.group(1).upper() if mp else ""

    # 판매 상태 — 위사몰 stat 2 = 정상(3 품절 · 4 숨김). concepts1one 머리말과 같은 판단
    stat = val("stat")
    opts: list[str] = []
    for sel in s.select('form[name="prdFrm"] select[name^="option"]'):
        if not re.search(r"사이즈|size", sel.get("data-name") or "", re.I):
            continue
        for o in sel.find_all("option"):
            v = (o.get("value") or "").split("::")[0].strip()
            if v and v not in opts:
                opts.append(_html.unescape(v))
    whole_sold = stat != "" and stat != "2"

    # 사진 — 대표(#mainImg)와 부가 사진. 부가 사진은 주소 끝에 「#addimg」 표식이 붙은 것만 — .addimg 칸이 제대로
    # 닫히지 않아 lxml 이 상세 그림까지 그 안에 넣는다(NUBASIC STRIPE … 의 상세 그림 둘이 사진에 섞였다).
    imgs: list[str] = []
    main = s.select_one("#mainImg")
    srcs = [main.get("src") if main is not None else ""] + [
        im.get("src") for im in s.find_all("img") if (im.get("src") or "").endswith("#addimg")]
    for u in srcs:
        u = _abs(u)
        if u and u not in imgs:
            imgs.append(u)

    detail = s.select_one(".detail_info")
    detail_imgs: list[str] = []
    for im in (detail.find_all("img") if detail is not None else []):
        u = _abs(im.get("src") or im.get("data-src") or "")
        if u and u not in detail_imgs and not re.search(r"/_skin/|/_data/banner/|coupon|icon|logo", u, re.I):
            detail_imgs.append(u)
    text = ""
    if detail is not None:
        text = "\n".join(ln.strip() for ln in detail.get_text("\n", strip=True).splitlines() if ln.strip())

    d = {
        "product_no": product_no,
        "name": name,
        "price": price,
        "soldout": whole_sold,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/shop/detail.php?pno={pno}" if pno else url,
        "description": text,
        "description_source": "wisa",
        "detail_text": text,
        "size_table": {},
        "spec": {"상품명": name},
        "detail_images": detail_imgs,
        "options": opts,
        "soldout_options": [],
        "category_nos": [],
        "category_names": list(_CATS_OF.get(pno, [])),
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "wisa",
    }
    if opts and not whole_sold:
        d["soldout_unknown"] = True   # 사이즈별 재고는 정적 HTML 에 없다(머리말) — 판매중으로 믿지 않게
    if listed > price:
        d["price_listed"] = listed
    return d
