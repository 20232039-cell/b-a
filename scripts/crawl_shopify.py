"""Shopify 매장 수집 — `products.json` 한 주소로 상품 전부를 받는다.

    python scripts/crawl_shopify.py --brands recto hyein-seo
    python scripts/crawl_shopify.py              # platforms.SHOPIFY 전부

카페24 매장과 **같은 모양의 줄**을 data/crawl/<slug>.jsonl 에 쓴다. 그래야 뒤 단계
(build-csv · size_from_ocr · tag_items · OCR · export)가 매장 종류를 몰라도 된다.

한 판이 첫 수집이자 주간 점검이다. Shopify 의 공개 목록은 품절까지 포함한 게시 상품 전부라
(`variants[].available` 로 품절을 가른다) 카페24 처럼 목록·상세를 따로 훑을 필요가 없다.
그래서 weekly_update 가 이 매장들을 만나면 여기로 넘긴다(platforms.NOT_CAFE24).

판단은 카페24 쪽과 같게 둔다:
- 값·재고 자국은 cc.carry_over 로 — 한 곳에서만 판단한다.
- 목록에서 사라진 상품은 지우지 않는다. 연속 두 판 없으면 delisted(weekly_update 와 같은 수).
- 받은 목록이 알던 판매중의 절반도 안 되면 상태를 바꾸지 않는다(같은 가드레일) —
  목록 한 쪽이 실패했을 때 멀쩡한 상품이 전부 품절로 넘어가는 것을 막는다.

값은 원화로 **청해서** 받는다(`currency=KRW`). 안 그러면 요청한 곳의 나라 돈으로 온다 —
fetch_all 주석. 원화 값은 센트 단위가 아니라 그대로라 100 으로 나누지 않는다
(세 매장 모두 `"price": "395000"` 꼴, 아스트라 조사와 내 확인이 같다).
"""
import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import crawl_cafe24 as cc
from platforms import SHOPIFY, SHOPIFY_META_DESC, SHOPIFY_PAGES

CRAWL_DIR = cc.CRAWL_DIR
PAGE_LIMIT = 250          # Shopify 가 한 쪽에 주는 최대
GUARD_RATIO = 0.5         # weekly_update 와 같다
DELIST_AFTER = 2          # 연속 이만큼 목록에 없으면 delisted
# 상품 페이지 간격(초). 목록은 매장당 몇 번이지만 페이지는 수백 번이라 더 천천히 — 봇 이름으로
# 연 상품 페이지에 매장이 429 를 준 적이 있다(get_patient 주석).
PAGE_DELAY = 4.0


def _text(body_html: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", body_html or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


def _img(u: str) -> str:
    """Shopify 그림 주소의 `?v=` 는 캐시 깨기일 뿐이다 — 떼야 같은 그림이 같은 주소가 된다."""
    return (u or "").split("?")[0]


def get_patient(http: cc.PoliteSession, url: str, log=print, tries: int = 4):
    """429 를 받으면 매장이 말한 만큼(Retry-After, 없으면 30·60·120초) 쉬고 다시 받는다.

    PoliteSession 은 429 에 2·4초만 물러나고 포기한다. Shopify 는 IP 하나에 짧은 시간 요청이 몰리면
    429 를 주는데, 상품 페이지를 한 벌씩 여는 이 수집기는 매장당 수백 번을 부른다. 같은 날 여러 판을
    돌리다 렉토·PAF 가 목록부터 429 로 막았다(2026-09-23, 가드레일이 걸려 데이터는 안 바뀌었다).
    """
    r = None
    for i in range(tries):
        r = http.get(url, retries=0)
        if r is None or r.status_code != 429:
            return r
        try:
            wait = float(r.headers.get("Retry-After") or 0)
        except ValueError:
            wait = 0
        wait = max(wait, 30 * (2 ** i))
        log(f"  429 — {wait:.0f}초 쉬고 다시 ({i + 1}/{tries}) {url[:70]}")
        time.sleep(wait)
    return r


def fetch_all(http: cc.PoliteSession, base: str, log=print) -> list[dict] | None:
    out: list[dict] = []
    for page in range(1, 200):
        # **통화를 못 박는다.** Accept-Language 를 붙이면 Shopify 가 요청 IP 의 나라 돈으로 바꿔
        # 준다 — 이 컨테이너(미국)에서 렉토 395,000원이 「315.00」(달러)으로 왔다. Actions
        # 러너도 미국이다. currency=KRW 를 붙이면 원화로 온다(2026-09-23 실측).
        r = get_patient(http, f"{base}/products.json?limit={PAGE_LIMIT}&page={page}&currency=KRW", log)
        if r is None or r.status_code != 200:
            log(f"  {base} {page}쪽 받기 실패({getattr(r, 'status_code', '없음')}) — 이 판은 상태를 안 바꾼다")
            return None
        cur = r.cookies.get("cart_currency")
        if cur and cur != "KRW":
            log(f"  {base} 가 {cur} 로 답했다 — 원화가 아니면 값을 쓰지 않는다")
            return None
        try:
            got = r.json().get("products") or []
        except ValueError:
            log(f"  {base} {page}쪽이 JSON 이 아니다 — 이 판은 상태를 안 바꾼다")
            return None
        if not got:
            break
        out.extend(got)
    return out


def to_row(p: dict, slug: str, base: str, now: str) -> dict:
    body = p.get("body_html") or ""
    text = _text(body)
    vs = p.get("variants") or []
    prices = [int(float(v["price"])) for v in vs if v.get("price")]
    # Shopify 는 원화를 「395000」으로, 달러를 「315.00」으로 준다. 소수점이 있으면 원화가 아니다 —
    # 위(fetch_all)에서 못 걸렀어도 여기서 멈춘다. 틀린 값을 쓰느니 판을 버린다.
    if any("." in str(v.get("price") or "") for v in vs):
        raise ValueError(f"원화가 아닌 값: {p.get('title')} {[v.get('price') for v in vs][:3]}")
    listed = [int(float(v["compare_at_price"])) for v in vs if v.get("compare_at_price")]
    imgs = [_img(i.get("src")) for i in (p.get("images") or []) if i.get("src")]
    names = [str(t) for t in ([p.get("product_type")] + list(p.get("tags") or [])) if t]
    d = {
        "product_no": int(p["id"]),
        "name": p.get("title") or "",
        "price": min(prices) if prices else 0,
        "soldout": not any(v.get("available") for v in vs),
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{base}/products/{p['handle']}",
        "description": text,
        "description_source": "shopify",
        "detail_text": text,
        "size_table": cc.extract_size_any(body),
        "spec": {"상품명": p.get("title") or "", "product_type": p.get("product_type") or ""},
        "detail_images": [_img(u) for u in re.findall(r'<img[^>]+src="([^"]+)"', body)],
        # 옵션 이름은 카페24 줄과 같게 「값/값」으로 잇는다 — 사이즈 이름을 옵션에서 채우는 단계가 읽는다
        "options": [v.get("title") or "" for v in vs],
        # 사이즈마다 품절을 따로 준다. 이게 없으면 카페24 규칙(cc.is_soldout)이 「옵션은 있는데
        # 품절 표시가 없다」며 다 판 옷을 판매중으로 읽는다 — 첫 판에서 품절 62벌이 전부 판매중이었다.
        "soldout_options": [v.get("title") or "" for v in vs if not v.get("available")],
        "category_nos": [],
        "category_names": names,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "shopify",
    }
    if listed and max(listed) > d["price"]:
        d["price_listed"] = max(listed)
    return d


_META_DESC = re.compile(r'(?is)<meta\s+(?:property="og:description"|name="description")\s+content="([^"]*)"')
_TAB = re.compile(r'(?is)<details[^>]*>\s*<summary[^>]*>(.*?)</summary>(.*?)</details>')
_RICH = re.compile(r'(?is)<div class="metafield-rich_text_field">(.*?)</div>')


def page_extras(html_text: str) -> dict:
    """상품 페이지에만 있는 것 — 목록 API(products.json)에는 안 온다.

    - meta description: 렉토는 진짜 상품 설명이 여기에만 있다(body_html 은 실측표뿐).
      「플레어 핏 실루엣의 수트 팬츠 … 고밀도 울 소재 MADE IN KOREA」
    - 접이식 탭의 메타필드: PAF 는 「Composition / Cotton 100%」, 「Size Guide / Size(Length/
      Chest/Arm/Shoulder) S : 69.5cm/54cm/…」가 여기에만 있다.
    메타필드가 든 탭만 받는다 — 테마의 탭에는 배송·반품 같은 매장 공용 안내도 있고, 그건 상품 글이 아니다.
    """
    meta = ""
    m = _META_DESC.search(html_text or "")
    if m:
        meta = html.unescape(m.group(1)).strip()
    tabs: list[tuple[str, str]] = []
    for head, body in _TAB.findall(html_text or ""):
        rich = _RICH.findall(body)
        if not rich:
            continue
        h = _text(head).splitlines()[-1] if _text(head) else ""
        tabs.append((h, "\n".join(_text(x) for x in rich)))
    return {"meta": meta, "tabs": tabs}


def enrich(d: dict, old: dict | None, updated_at: str, http: cc.PoliteSession) -> None:
    """상품 페이지를 읽어 설명·스펙·실측을 채운다. 상품이 안 바뀌었으면 지난 판 것을 쓴다."""
    ex = None
    if old and old.get("page_updated_at") == updated_at and "page_extras" in old:
        ex = old["page_extras"]
    else:
        r = get_patient(http, d["source_url"])
        if r is not None and r.status_code == 200:
            ex = page_extras(r.text)
            d["page_updated_at"] = updated_at
    if not ex:
        return
    d["page_extras"] = ex
    parts = [d.get("description") or ""]
    # meta 설명은 그것이 진짜 상품 설명인 매장에서만 쓴다(platforms.SHOPIFY_META_DESC 주석).
    meta = (ex.get("meta") or "") if d["brand_slug"] in SHOPIFY_META_DESC else ""
    flat = re.sub(r"\s+", " ", d.get("description") or "")
    if meta and re.sub(r"\s+", " ", meta)[:40] not in flat:
        parts.insert(0, meta)
    tab_text = "\n".join(f"{h}\n{b}" for h, b in ex.get("tabs") or [])
    d["description"] = "\n".join(x for x in parts if x).strip()
    d["detail_text"] = "\n".join(x for x in (d["description"], tab_text) if x).strip()
    spec = dict(d.get("spec") or {})
    for h, b in ex.get("tabs") or []:
        if h and b:
            spec[h] = b
            if re.search(r"(?i)composition|material|fabric|소재|혼용", h):
                spec["소재"] = b
    d["spec"] = spec
    # 탭의 실측은 「Size(Length/Chest/Arm/Shoulder) / S : 69.5cm/54cm/…」 꼴이다. HTML 표 추출기
    # (extract_size_any)로는 첫 줄 S 하나만 잡혔다 — 글 표를 읽는 from_ocr 는 네 줄을 다 읽는다.
    import size_from_ocr
    width = max((len(v) for k, v in (d.get("size_table") or {}).items()
                 if not k.startswith("_") and isinstance(v, list)), default=0)
    for h, b in ex.get("tabs") or []:
        if not re.search(r"(?i)size|사이즈|실측", h):
            continue
        # 머리줄에 「Size(」가 없는 꼴(「Length/Chest/Sleeve/Shoulder / S: 62.5cm/52cm/…」)은 from_ocr 가
        # 사이즈 이름 줄로 보지 않는다 — 감싸 주면 읽힌다(PAF 옷 66벌이 이 꼴이었다).
        b = re.sub(r"^\s*([A-Za-z][A-Za-z ]*(?:/\s*[A-Za-z][A-Za-z ]*)+)\s*$", r"Size(\1)", b, count=1, flags=re.M)
        names, cols = size_from_ocr.from_ocr(b)
        n = max((len(v) for v in cols.values()), default=0)
        if len(cols) >= 2 and n > width:
            d["size_table"] = {**cols, **({"_names": names} if names else {})}
            break


def load_prev(slug: str) -> dict[int, dict]:
    p = CRAWL_DIR / f"{slug}.jsonl"
    rows: dict[int, dict] = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            rows[int(d["product_no"])] = d
    return rows


def crawl_one(http: cc.PoliteSession, slug: str, log=print) -> dict:
    base = SHOPIFY[slug]
    now_ts = datetime.now(cc.KST).strftime("%Y-%m-%dT%H:%M:%S")
    today = now_ts[:10]
    rep = {"slug": slug, "listed": 0, "new": 0, "soldout": 0, "restock": 0, "delisted": 0, "guard": ""}
    got = fetch_all(http, base, log)
    prev = load_prev(slug)
    if got is None:
        rep["guard"] = "목록 받기 실패"
        return rep
    rep["listed"] = len(got)
    active = {no for no, d in prev.items() if not d.get("soldout") and not d.get("delisted")}
    guard = len(active) > 20 and len(got) < GUARD_RATIO * len(active)
    if guard:
        rep["guard"] = f"목록 {len(got)} < 판매중 {len(active)}×{GUARD_RATIO} — 상태 변경 보류"
        log(f"[{slug}] 가드레일: {rep['guard']}")
        return rep
    rows = dict(prev)
    seen: set[int] = set()
    page_http = cc.PoliteSession(delay=max(http.delay, PAGE_DELAY)) if slug in SHOPIFY_PAGES else None
    for p in got:
        d = to_row(p, slug, base, now_ts)
        no = d["product_no"]
        seen.add(no)
        old = prev.get(no)
        if page_http:
            enrich(d, old, p.get("updated_at") or "", page_http)
        if old:
            for k in ("first_seen", "soldout_since"):
                if k in old:
                    d[k] = old[k]
            # 다른 단계가 붙여 둔 것(상세 보정 시각 등)은 잃지 않는다
            for k, v in old.items():
                d.setdefault(k, v)
            if not old.get("soldout") and d["soldout"]:
                d["soldout_since"] = today; rep["soldout"] += 1
            elif old.get("soldout") and not d["soldout"]:
                d.pop("soldout_since", None); rep["restock"] += 1
        else:
            d["first_seen"] = today; rep["new"] += 1
            if d["soldout"]:
                d["soldout_since"] = today
        cc.carry_over(old or {}, d, now_ts)
        d["last_seen"] = today
        d["missing_weeks"] = 0
        d.pop("delisted", None)
        rows[no] = d
    for no, d in rows.items():
        if no in seen or d.get("delisted"):
            continue
        d["missing_weeks"] = int(d.get("missing_weeks", 0)) + 1
        if d["missing_weeks"] >= DELIST_AFTER:
            d["delisted"] = True; d["soldout"] = True
            d.setdefault("soldout_since", today); rep["delisted"] += 1
    # 한 상품 한 줄로 다시 쓴다 — 창고의 다른 매장 파일도 합치기 단계가 그렇게 접는다
    out = CRAWL_DIR / f"{slug}.jsonl"
    tmp = out.with_suffix(".jsonl.part")
    with tmp.open("w", encoding="utf-8") as f:
        for no in sorted(rows):
            f.write(json.dumps(rows[no], ensure_ascii=False) + "\n")
    tmp.replace(out)
    log(f"[{slug}] 목록 {rep['listed']} · 신상 {rep['new']} · 품절 {rep['soldout']} · 재입고 {rep['restock']} · 삭제 {rep['delisted']}")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", help="slug 목록. 없으면 platforms.SHOPIFY 전부")
    ap.add_argument("--delay", type=float, default=2.0, help="같은 호스트 요청 간격(초)")
    args = ap.parse_args()
    slugs = args.brands or sorted(SHOPIFY)
    bad = [s for s in slugs if s not in SHOPIFY]
    if bad:
        sys.exit(f"Shopify 매장이 아니다: {' '.join(bad)} (platforms.SHOPIFY 에 없음)")
    CRAWL_DIR.mkdir(parents=True, exist_ok=True)
    http = cc.PoliteSession(delay=args.delay)
    reps = [crawl_one(http, s) for s in slugs]
    if any(r["guard"] for r in reps):
        print("가드레일에 걸린 매장: " + ", ".join(r["slug"] for r in reps if r["guard"]), file=sys.stderr)


if __name__ == "__main__":
    main()
