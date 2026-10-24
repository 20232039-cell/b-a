"""민주킴(minju-kim) — 제작사 자체 틀 「dcore」(그림·스크립트가 d239tqqxder3e.cloudfront.net, 화면은 jQuery ·
materialize · smoothState/pjax). /sitemap.xml 은 404 다.

목록: /ko/shop(View all)이 한 쪽 12벌, /ko/shop/page/N 으로 넘긴다(2026-09-27 11쪽 129벌). 마지막 쪽을 넘긴
번호도 200 으로 마지막 쪽을 다시 주므로 쪽 수는 쪽 매김 줄의 가장 큰 번호로 정한다. 품절은 목록 칸에만
글자 없이 class 「info sold-out-」로 붙는다 — 상품 페이지 HTML 은 품절 버튼이 늘 hide 이고 재고는 스크립트가
POST(/variation/isinstock)로 묻는다. 그래서 목록에서 품절 표시를 같이 읽어 둔다.
칸 이름(Outer·Dress…)은 상품 페이지에 없어(data-product-category="") 칸 목록(/ko/shop/category/N)을 한 번씩 훑는다.
상세: 사이즈 옵션의 disabled 가 그 사이즈 재고 0 이다(isinstock 으로 확인 — 34 는 stock 2, disabled 36 은 0).
실측은 「SIZING INFORMATION」 글(「36SIZE / Total length: 73.5cm / Waist: 71.4cm」)이다. 「SIZE CHART」 표는
모든 상품에 같은 나라별 호수 대조표(XS=44=0-2…)라 실측이 아니다 — 쓰지 않는다.
robots: /admin · /api · /user 만 막는다.
"""
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

import crawl_pages
import crawl_shopify as cs

BASE = "https://www.minjukim.co"
PAGE_DELAY = 3.0

# list_urls 가 채운다: 상품 주소 → 칸 이름들 / 목록에서 품절 표시가 붙은 상품 주소
_CATS: dict[str, list[str]] = {}
_SOLD: set[str] = set()
_LISTED: set[str] = set()


def _norm(href: str) -> str:
    """칸 목록의 주소는 「/ko/shop/<slug>/category/8」 — 칸 꼬리를 떼어 한 상품이 한 주소가 되게."""
    m = re.match(r"(?:https?://[^/]+)?/(?:ko|en)/shop/([^/?#]+)", href or "")
    if not m or m.group(1) in ("category", "page", "search"):
        return ""
    return f"{BASE}/ko/shop/{m.group(1)}"


def _last_page(s) -> int:
    nums = [int(a.get_text(strip=True)) for a in s.select(".dcore-pagination a, .dcore-pagination li, .dcore-pagination span")
            if a.get_text(strip=True).isdigit()]
    return max(nums) if nums else 1


def _items(s) -> list[tuple[str, bool]]:
    out = []
    for a in s.select(".shop-list .item a.link"):
        u = _norm(a.get("href"))
        info = a.select_one(".info")
        if u:
            out.append((u, bool(info and "sold-out-" in (info.get("class") or []))))
    return out


def _walk(http, first_url: str, page_url: str, log) -> list[tuple[str, bool]] | None:
    r = cs.get_patient(http, first_url, log)
    if r is None or r.status_code != 200:
        log(f"  [minju-kim] 목록 받기 실패({getattr(r, 'status_code', '없음')}) {first_url}")
        return None
    s = BeautifulSoup(r.text, "lxml")
    got = _items(s)
    last = _last_page(s)
    for n in range(2, last + 1):
        r = cs.get_patient(http, page_url.format(n=n), log)
        if r is None or r.status_code != 200:
            log(f"  [minju-kim] 목록 {n}쪽 받기 실패({getattr(r, 'status_code', '없음')}) {first_url}")
            return None
        got += _items(BeautifulSoup(r.text, "lxml"))
    return got


def list_urls(http, log) -> list[str] | None:
    allp = _walk(http, f"{BASE}/ko/shop", BASE + "/ko/shop/page/{n}", log)
    if allp is None:
        return None
    _CATS.clear()
    _SOLD.clear()
    _LISTED.clear()
    urls = list(dict.fromkeys(u for u, _ in allp))
    _SOLD.update(u for u, so in allp if so)
    _LISTED.update(urls)
    # 칸 이름 — 메뉴의 Shop 아래 칸들. 칸 하나를 못 받아도 판은 버리지 않는다(이름만 빈다)
    r = cs.get_patient(http, f"{BASE}/ko/shop/category/8", log)
    cats: list[tuple[str, str]] = []
    if r is not None and r.status_code == 200:
        s = BeautifulSoup(r.text, "lxml")
        for li in s.select("#nav-site li[class*='menu-ko-shop-category-'] > a"):
            m = re.search(r"/shop/category/(\d+)$", li.get("href") or "")
            if m and (m.group(1), li.get_text(" ", strip=True)) not in cats:
                cats.append((m.group(1), li.get_text(" ", strip=True)))
    for cno, cname in cats:
        got = _walk(http, f"{BASE}/ko/shop/category/{cno}", BASE + f"/ko/shop/category/{cno}/page/{{n}}", log)
        for u, _ in got or []:
            _CATS.setdefault(u, [])
            if cname not in _CATS[u]:
                _CATS[u].append(cname)
            # View all 에 없는 상품이 칸에만 있으면 그것도 받는다
            if u not in _LISTED:
                urls.append(u)
                _LISTED.add(u)
        _SOLD.update(u for u, so in got or [] if so)
    log(f"  [minju-kim] 상품 {len(urls)}벌(목록 품절 {len(_SOLD)}) · 칸 {len(cats)}곳")
    return urls


def _bg(el) -> str:
    m = re.search(r"url\(['\"]?([^'\")]+)", el.get("style") or "")
    if not m:
        return ""
    u = m.group(1).strip()
    return ("https:" + u) if u.startswith("//") else u


def _block_text(el) -> str:
    t = el.get_text("\n", strip=True)
    t = re.sub(r"[\xa0　]", " ", t)
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


# 실측 글이 끝나는 곳 — 뒤는 사이즈 편차 · 모델 치수 · 원단 안내라 표가 아니다
_SIZ_END = re.compile(r"(?i)^\*?\s*(?:사이즈\s*편차|모델\s*사이즈|한\s*사이즈당|fabric\s*information|model)")
# 치수 머리 — 「36SIZE」 「36 SIZE (단위 CM)」 「기준 사이즈 36 (cm)」 「34사이즈 기준」 「기준 사이즈 S - M (cm)」 「F(OS)」
_SIZ_HEAD = re.compile(r"(?i)^(?:기준\s*사이즈\s*)?(?P<n>F\s*\(\s*OS\s*\)|[A-Z0-9]{1,4}(?:\s*-\s*[A-Z0-9]{1,4})?)"
                       r"\s*(?P<k>SIZE|사이즈)?\s*(?P<b>기준)?\s*(?:\(\s*(?:단위\s*)?cm\s*\))?$")
_LAB = r"[A-Za-z가-힣][A-Za-z가-힣 .]*?"
_NUM = r"\d+(?:\.\d+)?"
_PAIR1 = re.compile(rf"^({_LAB})\s*[/:：]?\s*({_NUM})\s*(?:cm)?$", re.I)


def _girths(blocks: list[list]) -> None:
    """「가슴둘레 110」 — 둘레 값을 단면으로 맞춘다(제자리에서 고친다).

    이 매장은 「둘레」 칸에 단면과 둘레를 섞어 적는다 — 한 벌 안에서도 「가슴둘레 42 · 엉덩이둘레 96」
    (라운드 컷 미니 드레스). 그래서 한 벌에 **단면으로 있을 수 없는 값**(라벨 범위 상한 초과)이 하나라도 있으면
    그 벌은 둘레로 적었다고 보고 둘레 칸을 다 반으로 나눈다. 다만 반으로 나눠 하한 밑이 되는 값(가슴둘레 42 → 21)은
    이미 단면이라 그대로 둔다. 치수마다 따로 정하면 「허리 72 · 38.5」(36·38 사이즈)처럼 한 표 안에서 기준이 갈렸다.
    반으로 나눠도 상한을 넘으면(풀 스커트 「밑단둘레 194」, 오타 「1092」) 「-」 — 해석기에 넘기면 세 자리 수를
    소수점 빠진 OCR 로 보고 10 으로 나눠 가짜 값(109.2)을 만든다.
    둘레를 넘는 값이 하나도 없는 벌(가슴둘레 76 · 허리둘레 65)은 둘레인지 단면인지 가릴 수 없다. 처음엔 그대로
    뒀는데, 그러면 단면 칸에 둘레 값이 들어가 두 배로 헐렁한 옷이 된다 — 코덱스 검증(004, 2026-09-28)이 이 매장
    셋 모두를 틀림으로 짚었다. 사람 결정: 애매하면 비운다(「틀린 값보다 빈 값」). 그 벌의 둘레 칸만 「-」로 두고
    어깨·총장처럼 둘레가 아닌 칸은 살린다.
    """
    import size_from_ocr

    def rng(lab):
        c = size_from_ocr.canon_label(lab)
        return size_from_ocr.RANGES.get(c, (3, 200)) if c else (3, 200)

    def is_g(lab):
        # 영어 칸(「Bust: 96cm / Waist: 116cm / Hip: 120cm」 세일러 롱코트)도 둘레로 적는다
        return bool(re.search(r"(?i)둘레|circum|\bcir\b|^(?:bust|chest|waist|hip|hem(?:\s*line)?)$", lab.strip()))

    whole = any(is_g(a) and float(v) > rng(a)[1] for _, pairs in blocks for a, v in pairs)
    for b in blocks:
        out = []
        for a, v in b[1]:
            lo, hi = rng(a)
            x = float(v)
            if is_g(a) and not whole:
                out.append((a, "-"))
                continue
            if is_g(a) and whole and x / 2 >= lo:
                x /= 2
            # 범위를 넘는 값은 어느 칸이든 「-」 — 넘기면 해석기가 세 자리 수를 10 으로 나눈다(밑단 150 → 15)
            out.append((a, f"{x:g}" if x <= hi else "-"))
        b[1] = out


def _canon(blocks: list[list]) -> None:
    """라벨을 정식 이름(총장·엉덩이…)으로 — 해석기는 줄글에서 「엉덩이둘레 52」 · 「밑단둘레 31」을 흘렸다
    (「총장 55 / 엉덩이 52」 꼴은 읽는다, 버뮤다 팬츠). 「Sleeve cir」는 정식 이름표가 소매길이로 잡으니 소매통으로 먼저 돌린다.
    모르는 라벨(가로·세로·바닥폭)은 그대로 둔다 — 해석기가 알아서 버린다."""
    import size_from_ocr
    for b in blocks:
        out, seen = [], set()
        for a, v in b[1]:
            if re.search(r"(?i)sleeve\s*(?:cir|circum|width|opening)", a):
                c = "소매통"
            else:
                c = size_from_ocr.canon_label(a) or a
            if c not in seen:
                out.append((c, v))
                seen.add(c)
        b[1] = out


def _sizing_text(t: str, opts: list[str] | None = None) -> str:
    """SIZING INFORMATION 글을 표 해석기가 읽는 꼴로 — 치수가 이름과 함께 오면 「Size 총기장 … / 36 82 …」 행렬로.

    매장 글은 꼴이 여럿이다(2026-09-27 20벌): 「총기장 / 38」(가름표가 / 라 해석기가 못 읽었다) ·
    「Total length: 120cm / Bust: 96cm / …」(한 줄) · 「가로」 다음 줄 「17」 · 「36 SIZE … 38 SIZE …」(치수마다 한 묶음,
    해석기는 첫 묶음만 읽었다). 머리의 치수 이름을 _names 로 남기려면 행렬이어야 한다.
    뒤따르는 사이즈 편차(「허리 5cm / 힙 5cm」)·모델 치수(「키 / 176」)는 잘라 낸다 — 그대로 두면 실측으로 읽힌다.
    """
    lines = []
    for ln in t.splitlines():
        ln = ln.strip()
        if _SIZ_END.search(ln):
            break
        # 한 줄에 「라벨: 값」이 여럿이면 쪼갠다(가름표 / 는 「총기장 / 38」 에선 라벨-값 사이라 콜론 있을 때만)
        if len(re.findall(rf"{_LAB}\s*[:：]\s*{_NUM}", ln)) >= 2:
            lines += [x.strip() for x in ln.split("/") if x.strip()]
        elif ln:
            lines.append(ln)
    blocks: list[list] = []            # [이름, [(라벨, 값)…]]
    i = 0
    while i < len(lines):
        ln = lines[i]
        mh = _SIZ_HEAD.match(ln)
        if mh and (mh.group("k") or mh.group("b") or ln.startswith("기준") or "(" in mh.group("n")):
            blocks.append([re.sub(r"\s+", "", mh.group("n")).upper(), []])
            i += 1
            continue
        mp = _PAIR1.match(ln)
        if not mp and i + 1 < len(lines) and re.fullmatch(_LAB, ln) and re.fullmatch(rf"{_NUM}\s*(?:cm)?", lines[i + 1], re.I):
            mp = _PAIR1.match(ln + " " + lines[i + 1])       # 「가로」 / 「17」 두 줄
            i += 1
        if mp:
            if not blocks:
                blocks.append(["", []])
            lab = mp.group(1).strip()
            # 한글 라벨 속 띄어쓰기(「어깨 너비」)는 행렬 머리에서 칸이 둘로 갈린다 — 붙인다
            lab = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", lab)
            blocks[-1][1].append((lab, mp.group(2)))
        i += 1
    blocks = [b for b in blocks if len(b[1]) >= 2]
    if not blocks:
        return ""
    _girths(blocks)
    _canon(blocks)
    # 치수 머리가 없어도 옵션이 하나(F(OS))뿐이면 그 치수의 값이다
    if len(blocks) == 1 and not blocks[0][0] and opts and len(opts) == 1:
        blocks[0][0] = re.sub(r"\s+", "", opts[0]).upper()
    if all(b[0] for b in blocks):
        labs: list[str] = []
        for _, pairs in blocks:
            labs += [a for a, _ in pairs if a not in labs]
        rows = ["Size " + " ".join(labs)]
        for nm, pairs in blocks:
            got = dict(pairs)
            rows.append(nm + " " + " ".join(got.get(a, "-") for a in labs))
        return "\n".join(rows)
    # 치수 이름이 없다 — 첫 묶음만 「총장 55 / 허리 37.5 / …」 한 줄로(해석기가 가장 잘 읽는 꼴)
    return " / ".join(f"{a} {v}" for a, v in blocks[0][1] if v != "-")


def parse_page(html_text, url, slug, now, http=None) -> dict | None:
    s = BeautifulSoup(html_text, "lxml")
    form = s.select_one("form.dcore-add-to-cart")
    if form is None or not (form.get("data-uid") or "").isdigit():
        return None
    pno = int(form["data-uid"])
    title = s.select_one(".product-title")
    name = re.sub(r"\s+", " ", (title.get_text(" ", strip=True) if title else form.get("data-product-name") or "")).strip()
    try:
        price = int(float(form.get("data-product-price") or 0))
    except ValueError:
        price = 0
    if not name or price <= 0:
        return None
    src = _norm(url) or url

    # 사이즈 옵션 — select 이름표(label)가 SIZE 인 것만. 옵션의 disabled 가 그 사이즈 재고 0
    opts, sold_opts = [], []
    items = [it for it in form.select(".dcore-options .item") if it.select_one("select.f-option")]
    for it in items:
        sel = it.select_one("select.f-option")
        lab = it.select_one("label")
        lt = lab.get_text(" ", strip=True) if lab else ""
        # 이름표가 빈 선택칸도 있다(버뮤다 팬츠 34·36·38) — 선택칸이 하나뿐이면 사이즈로 본다
        if not re.search(r"(?i)size|사이즈", lt) and not (not lt and len(items) == 1):
            continue                  # 색 따위 다른 축은 옵션에 넣지 않는다
        for o in sel.select("option[value]"):
            v = (o.get("data-label") or o.get_text(" ", strip=True)).strip()
            if not o.get("value") or not v or v in opts:
                continue
            opts.append(v)
            if o.has_attr("disabled"):
                sold_opts.append(v)

    listed_sold = src in _SOLD
    soldout = listed_sold or (bool(opts) and len(sold_opts) == len(opts))

    # 사진 — 본문 옆 그림들(원본·확대 두 벌이 같은 주소)
    # 같은 파일을 번호만 달리 두 번 올린 것(…/91218/231206-1 (5).jpg · …/91219/231206-1 (5).jpg)은 한 장으로 센다
    imgs, names = [], set()
    for c in s.select(".product-detail-image .original-image .cover, .product-detail-image .zoom-image .cover"):
        u = _bg(c)
        fn = unquote(u.rsplit("/", 1)[-1]).lower()
        if u and fn not in names:
            imgs.append(u)
            names.add(fn)
    if not imgs:
        og = s.select_one('meta[property="og:image"]')
        if og and og.get("content"):
            imgs = [og["content"]]

    # 글 — DETAILS · SIZING INFORMATION 같은 머리(u) + 몸(.dcore-body) 묶음. SIZE CHART(호수 대조표)와 Q&A 는 뺀다
    info = s.select_one(".product-detail-info")
    parts, sizing, detail_imgs = [], "", []
    if info is not None:
        for blk in info.find_all("div", recursive=False):
            if "size-chart" in (blk.get("class") or []) or "qna-button" in (blk.get("class") or []):
                continue
            head = blk.find("u")
            body = blk.select_one(".dcore-body")
            if body is None:
                continue
            h = head.get_text(" ", strip=True) if head else ""
            bt = _block_text(body)
            if not bt:
                continue
            parts.append((h + "\n" + bt) if h else bt)
            if re.search(r"(?i)siz", h):
                sizing += bt + "\n"
            for im in body.find_all("img"):
                u = im.get("data-src") or im.get("src") or ""
                u = ("https:" + u) if u.startswith("//") else u
                if u and u not in detail_imgs:
                    detail_imgs.append(u)
    text = "\n".join(parts)
    # SIZING 머리가 없는 벌은 설명 글 전체에서 찾는다
    src_t = sizing or text
    size_table = crawl_pages.size_table_from_text(_sizing_text(src_t, opts) or src_t) if src_t else {}

    spec = {"상품명": name}
    mm = re.search(r"(?im)^(.*\d+\s*%.*)$", text)
    if mm:
        spec["소재"] = mm.group(1).strip()

    d = {
        "product_no": pno,
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": src,
        "description": text,
        "description_source": "dcore",
        "detail_text": text,
        "size_table": size_table,
        "spec": spec,
        "detail_images": detail_imgs,
        "options": opts,
        "soldout_options": list(opts) if listed_sold else sold_opts,
        "category_nos": [],
        "category_names": list(_CATS.get(src, [])),
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "dcore",
    }
    # 옵션 없는 상품의 품절은 목록 칸에서만 안다 — 목록을 안 거친 한 벌(--url)이면 모른다고 적는다
    if not opts and src not in _LISTED:
        d["soldout_unknown"] = True
    return d
