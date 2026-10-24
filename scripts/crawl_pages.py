"""카페24·Shopify·식스샵이 아닌 매장 수집 — 매장마다 해석기 하나(scripts/stores/<slug>.py).

    python scripts/crawl_pages.py --brands concepts1one
    python scripts/crawl_pages.py              # platforms.PAGES 전부

고도몰 · 위사몰 · 메이크샵 · Cargo · 매장이 직접 만든 사이트는 틀이 매장마다 달라, 식스샵처럼
틀 하나에 수집기 하나를 둘 수가 없다. 그래서 **걷고 합치는 일은 여기서 하나로**, 페이지를 읽는 일만
매장별 해석기에 맡긴다(2026-09-27 — 그날 사람이 성별을 판정한 매장 가운데 여덟 곳이 이 꼴이었다).

해석기(stores/<slug 의 - 를 _ 로>.py)가 내놓을 것:

    BASE = "https://…"                         # 가게 주소
    PAGE_DELAY = 3.0                           # (없어도 됨) 상품 페이지 간격(초)
    def list_urls(http, log) -> list[str] | None     # 상품 페이지 주소들. 못 받으면 None
    def parse_page(html, url, slug, now, http) -> dict | None   # 한 벌. 못 읽으면 None

편집숍처럼 걷지 않을 상품(남의 브랜드)은 parse_page 가 {"product_no": …, "_skip": "까닭"} 을 돌려준다 —
None(못 읽음)과 달리 가드레일에 세지 않는다.

목록 API 가 줄을 통째로 주는 매장은 list_urls · parse_page 대신 이것 하나를 둔다:

    def fetch_rows(http, slug, now, log) -> list[dict] | None

줄 모양은 crawl_sixshop.parse_page 와 같다(뒤 단계가 매장 종류를 몰라도 되게). 합치기·가드레일·
품절 자국은 crawl_shopify.merge_rows 를 같이 쓴다 — 판단이 매장 종류마다 갈리지 않게.
"""
import argparse
import importlib
import json
import re
import sys
from datetime import datetime

import crawl_cafe24 as cc
import crawl_shopify as cs
from platforms import PAGES

CRAWL_DIR = cc.CRAWL_DIR
PAGE_DELAY = 3.0


def store(slug: str):
    return importlib.import_module("stores." + slug.replace("-", "_"))


def size_table_from_text(text: str) -> dict:
    """설명 글 속 실측표 — 식스샵과 같은 해석기(size_from_ocr.from_ocr)로 읽는다."""
    import crawl_sixshop
    import size_from_ocr
    if not text:
        return {}
    names, cols = size_from_ocr.from_ocr(crawl_sixshop.front_back(text))
    if len(cols) >= 2:
        return {**cols, **({"_names": names} if names else {})}
    # 총장 **한 칸만** 있는 옷도 받는다(사람 결정 2026-09-29 「다 받아」) — 둘레를 비우고 나면 총장만 남는 원피스
    # (minju-kim 「총기장 77 · 밑단둘레 136」) · 고무 허리 치마(doucan 「Waist 33-47 / Length 97」).
    # 총장이 글에 **딱 한 번** 나올 때만 — 여러 번이면 어느 사이즈 값인지 모른다.
    hits = re.findall(r"(?i)(?<![가-힣A-Za-z])(?:총\s?기장|총\s?장|총\s?길이|total\s*length|length)\s*[:：]?\s*(\d{2,3}(?:\.\d)?)\s*(?:cm)?(?![\d.~\-])", text)
    if len(hits) == 1:
        v = size_from_ocr.fix_value("총장", hits[0])
        if v is not None:
            return {"총장": [v]}
    return {}


def crawl_one(http: cc.PoliteSession, slug: str, log=print) -> dict:
    mod = store(slug)
    now_ts = datetime.now(cc.KST).strftime("%Y-%m-%dT%H:%M:%S")
    rep = {"slug": slug, "listed": 0, "new": 0, "soldout": 0, "restock": 0, "delisted": 0, "guard": "", "failed": 0}
    prev = cs.load_prev(slug)
    page_http = cc.PoliteSession(delay=max(http.delay, getattr(mod, "PAGE_DELAY", PAGE_DELAY)))
    if hasattr(mod, "fetch_rows"):
        got = mod.fetch_rows(page_http, slug, now_ts, log)
        if got is None:
            rep["guard"] = "목록 받기 실패"
            return rep
    else:
        urls = mod.list_urls(page_http, log)
        if urls is None:
            rep["guard"] = "상품 목록 받기 실패"
            return rep
        got = []
        for u in urls:
            r = cs.get_patient(page_http, u, log)
            if r is None:
                # 연결이 한 번 끊긴 것(get_patient 는 retries=0 으로 부른다) — 한 번만 더 받는다.
                # 칼린 시험에서 끊긴 주소를 다시 받으면 멀쩡히 읽혔다(2026-09-27).
                r = cs.get_patient(page_http, u, log)
            if r is None or r.status_code != 200:
                rep["failed"] += 1
                continue
            try:
                d = mod.parse_page(r.text, u, slug, now_ts, page_http)
            except Exception as e:           # 한 벌이 이상해도 판을 버리지 않는다 — 대신 세어 둔다
                log(f"  [{slug}] 해석 실패 {u[:80]}: {type(e).__name__}: {e}")
                d = None
            if d is None:
                rep["failed"] += 1
                continue
            if d.get("_skip"):
                rep["skipped"] = rep.get("skipped", 0) + 1
                continue
            got.append(d)
        # 페이지를 절반 넘게 못 읽었으면 판을 버린다 — 못 연 상품을 「목록에 없음」으로 세면 멀쩡한 옷이 내려간다
        if urls and rep["failed"] > (len(urls) - rep.get("skipped", 0)) / 2:
            rep["listed"] = len(got)
            rep["guard"] = f"상품 페이지 {rep['failed']}/{len(urls)} 실패 — 상태 변경 보류"
            log(f"[{slug}] 가드레일: {rep['guard']}")
            return rep
    # 같은 상품 번호가 두 번 오면(언어별 주소 따위) 먼저 온 것을 둔다
    seen: dict[int, dict] = {}
    for d in got:
        d.setdefault("brand_slug", slug)
        d.setdefault("crawled_at", now_ts)
        d.setdefault("platform", "pages")
        seen.setdefault(int(d["product_no"]), d)
    got = list(seen.values())
    rep["listed"] = len(got)
    return cs.merge_rows(slug, prev, got, now_ts, rep, log)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", help="slug 목록. 없으면 platforms.PAGES 전부")
    ap.add_argument("--delay", type=float, default=2.0, help="같은 호스트 요청 간격(초)")
    args = ap.parse_args()
    slugs = args.brands or sorted(PAGES)
    bad = [s for s in slugs if s not in PAGES]
    if bad:
        sys.exit(f"crawl_pages 매장이 아니다: {' '.join(bad)} (platforms.PAGES 에 없음)")
    CRAWL_DIR.mkdir(parents=True, exist_ok=True)
    http = cc.PoliteSession(delay=args.delay)
    reps = [crawl_one(http, s) for s in slugs]
    print(json.dumps(reps, ensure_ascii=False))
    if any(r["guard"] for r in reps):
        print("가드레일에 걸린 매장: " + ", ".join(r["slug"] for r in reps if r["guard"]), file=sys.stderr)


if __name__ == "__main__":
    main()
