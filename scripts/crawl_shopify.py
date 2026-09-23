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
from datetime import datetime
from pathlib import Path

import crawl_cafe24 as cc
from platforms import SHOPIFY

CRAWL_DIR = cc.CRAWL_DIR
PAGE_LIMIT = 250          # Shopify 가 한 쪽에 주는 최대
GUARD_RATIO = 0.5         # weekly_update 와 같다
DELIST_AFTER = 2          # 연속 이만큼 목록에 없으면 delisted


def _text(body_html: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", body_html or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


def _img(u: str) -> str:
    """Shopify 그림 주소의 `?v=` 는 캐시 깨기일 뿐이다 — 떼야 같은 그림이 같은 주소가 된다."""
    return (u or "").split("?")[0]


def fetch_all(http: cc.PoliteSession, base: str, log=print) -> list[dict] | None:
    out: list[dict] = []
    for page in range(1, 200):
        # **통화를 못 박는다.** Accept-Language 를 붙이면 Shopify 가 요청 IP 의 나라 돈으로 바꿔
        # 준다 — 이 컨테이너(미국)에서 렉토 395,000원이 「315.00」(달러)으로 왔다. Actions
        # 러너도 미국이다. currency=KRW 를 붙이면 원화로 온다(2026-09-23 실측).
        r = http.get(f"{base}/products.json?limit={PAGE_LIMIT}&page={page}&currency=KRW")
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
    for p in got:
        d = to_row(p, slug, base, now_ts)
        no = d["product_no"]
        seen.add(no)
        old = prev.get(no)
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
