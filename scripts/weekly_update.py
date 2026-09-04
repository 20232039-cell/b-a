"""주간 갱신 — 신상·품절·재입고·삭제만 본다. 설명·소재·태그는 처음 받은 그대로 둔다(사람 결정 2026-09-02).

왜 이것만: 상세 설명이 바뀌는 것까지 쫓을 이유가 없다. 앱이 필요한 변화는 「새로 나온 옷」「품절된 옷」
「다시 들어온 옷」「매장이 내린 옷」 네 가지다. 마지막은 옷장(예전에 산 옷 찾기)에서 오히려 자산이라
지우지 않고 delisted 표시만 한다.

어떻게:
  1. 목록 스캔 — 사이트맵 ∪ 카테고리 목록 ∪ 홈. 이번 주 목록에 있는 상품 번호 집합 L. (매장당 10~20 요청)
  2. 신상 = L − 아는 것 → 상세를 한 번 받아 행 추가(설명·사이즈·상세 이미지까지, 크롤러와 같은 파서).
  3. 사라진 것 = 판매중인데 L 에 없음 → 상세로 확인. 품절 표시면 soldout, 404 면 missing_weeks += 1,
     2주 연속이면 delisted. (cafe24 는 매장 설정에 따라 품절 상품을 목록에서 숨기므로 목록 부재 ≠ 삭제)
  4. 재입고 = 품절인 것 → 상세로 확인. 목록만으로는 품절을 못 읽는다(kirsh 목록 12쪽에 품절 표시 0건,
     2026-09-02) — 상세의 JSON-LD availability / soldout_icon 이 정본. 180~365일 품절은 매달 첫 주에만,
     1년 넘으면 목록에 다시 나타날 때만(재발매). 매주 보는 건 최근 6개월 품절뿐.
  5. 가드레일 — 목록 상품 수가 아는 판매중 수의 절반 아래면 그 매장은 이번 주 상태를 바꾸지 않는다
     (사이트 장애·스킨 변경을 대량 품절로 오판하지 않기 위해). 신상 추가는 한다.

저장: crawl/<slug>.jsonl 을 상품당 최신 한 행으로 다시 쓴다(매주 8천 행을 append 하면 파일이 주당 25MB 씩
큰다). 행에 last_seen(목록에서 마지막으로 본 날), soldout_since, missing_weeks, delisted 를 더한다.
로그: crawl/_weekly_log.jsonl 에 매장별 한 줄. GITHUB_STEP_SUMMARY 가 있으면 표로도 쓴다.

사용:
    py scripts/weekly_update.py                       # 전 브랜드
    py scripts/weekly_update.py --brands blayer pioneers --workers 2
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cafe24 as cc  # noqa: E402

CRAWL_DIR = cc.CRAWL_DIR
LOG_PATH = CRAWL_DIR / "_weekly_log.jsonl"
GUARD_RATIO = 0.5          # 목록 수 < 아는 판매중 × 이 비율 → 상태 변경 보류
DELIST_AFTER_WEEKS = 2     # 연속 이만큼 목록에 없고 404 면 delisted
STALE_SOLDOUT_DAYS = 180   # 이보다 오래 품절이면 재입고 확인은 매달 첫 주만
DEAD_SOLDOUT_DAYS = 365    # 1년 넘은 품절은 더 안 본다 — 목록에 다시 나타날 때만(시그니처 재발매가 같은 상품 번호를 쓰는 경우)


def today() -> str:
    return datetime.now(cc.KST).strftime("%Y-%m-%d")


def load_rows(path: Path) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[int(d["product_no"])] = d
    return latest


def save_rows(path: Path, rows: dict[int, dict]) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for no in sorted(rows):
            f.write(json.dumps(rows[no], ensure_ascii=False) + "\n")
    tmp.replace(path)


def fetch_one(http: cc.PoliteSession, shop: cc.Shop, no: int, url: str) -> tuple[str, dict | None]:
    """crawl_brand 와 같은 판정 — ok / 404 / members-only / no-price / http"""
    if not shop.allowed(url):
        return "robots", None
    r = http.get(url, retries=2)
    if r is not None and r.status_code in (404, 410) and "product_no=" not in url:
        url = f"{shop.base}/product/detail.html?product_no={no}"
        r = http.get(url, retries=1)
    if r is None:
        return "http", None
    if r.status_code in (404, 410):
        return "404", None
    if r.status_code != 200:
        return "http", None
    if len(r.text) < 2000 and "member/login" in r.text:
        return "members-only", None
    d = cc.parse_detail(r.text, url, shop)
    if not d or not d.get("price"):
        return "no-price", None
    return "ok", d


def update_brand(http: cc.PoliteSession, shop: cc.Shop, log, first_week_of_month: bool) -> dict:
    path = CRAWL_DIR / f"{shop.slug}.jsonl"
    rows = load_rows(path)
    t0 = time.time()
    rep = {"date": today(), "slug": shop.slug, "known": len(rows), "listed": 0, "new": 0, "soldout": 0,
           "restock": 0, "delisted": 0, "price_changed": 0, "checked": 0, "failed": 0, "guard": "", "ok": True,
           "reasons": {}}   # 상세 확인 결과 분포(ok/404/members-only/no-price/http) — 실패가 늘면 여기서 원인을 본다

    cc.load_robots(http, shop)
    home = http.get(shop.base + "/", retries=1)
    if home is None or home.status_code != 200:
        rep.update(ok=False, guard=f"home {getattr(home, 'status_code', 'ERR')}")
        log(f"[{shop.slug}] 홈 실패 — 건너뜀")
        return rep
    final = urlparse(home.url)
    shop.base = f"{final.scheme}://{final.netloc}"
    cc.load_categories(http, shop, BeautifulSoup(home.text, "lxml"), home.text)
    cc.enumerate_by_sitemap(http, shop)
    cc.crawl_category_lists(http, shop)
    listed = dict(shop.product_urls)
    rep["listed"] = len(listed)

    active = {no for no, d in rows.items() if not d.get("soldout") and not d.get("delisted")}
    soldout = {no for no, d in rows.items() if d.get("soldout") and not d.get("delisted")}
    guard = len(active) > 20 and len(listed) < GUARD_RATIO * len(active)
    if guard:
        rep["guard"] = f"목록 {len(listed)} < 판매중 {len(active)}×{GUARD_RATIO} — 상태 변경 보류"
        log(f"[{shop.slug}] 가드레일: {rep['guard']}")

    now = today()
    for no in listed:
        if no in rows:
            rows[no]["last_seen"] = now

    def apply_detail(no: int, d: dict, prev: dict | None) -> None:
        """받은 상세를 행에 반영. 설명·사이즈 등은 새 행이 갖고 있으니 그대로 쓰되, 이전 행의 카테고리 소속을 이어받는다."""
        cates = sorted(shop.membership.get(no, set())) or (prev or {}).get("category_nos", [])
        d["category_nos"] = cates
        d["category_names"] = [shop.categories.get(c, str(c)) for c in cates] if shop.membership.get(no) else (prev or {}).get("category_names", [])
        d["brand_slug"] = shop.slug
        d["crawled_at"] = datetime.now(cc.KST).strftime("%Y-%m-%dT%H:%M:%S")
        d["last_seen"] = now if no in listed else (prev or {}).get("last_seen", "")
        if prev:
            for k in ("first_seen", "soldout_since", "missing_weeks"):
                if k in prev:
                    d[k] = prev[k]
            if prev.get("price") != d.get("price"):
                rep["price_changed"] += 1
            if not prev.get("soldout") and d.get("soldout"):
                d["soldout_since"] = now; rep["soldout"] += 1
            elif prev.get("soldout") and not d.get("soldout"):
                d.pop("soldout_since", None); rep["restock"] += 1
        else:
            d["first_seen"] = now
            if d.get("soldout"):
                d["soldout_since"] = now
        d["missing_weeks"] = 0
        d.pop("delisted", None)
        rows[no] = d

    def check(no: int, url: str):
        status, d = fetch_one(http, shop, no, url)
        rep["reasons"][status] = rep["reasons"].get(status, 0) + 1
        return status, d

    # 2. 신상
    new_ids = [no for no in listed if no not in rows]
    for no in new_ids:
        status, d = check(no, listed[no])
        rep["checked"] += 1
        if status == "ok":
            apply_detail(no, d, None); rep["new"] += 1
        elif status not in ("members-only", "no-price"):
            rep["failed"] += 1

    if not guard:
        # 3. 사라진 판매중 → 품절인지 삭제인지
        for no in sorted(active - set(listed)):
            prev = rows[no]
            url = prev.get("source_url", "")
            url = url if cc.product_no_of(url) else f"{shop.base}/product/detail.html?product_no={no}"
            status, d = check(no, url)
            rep["checked"] += 1
            if status == "ok":
                apply_detail(no, d, prev)
                if not d.get("soldout"):
                    # 목록에는 없는데 상세는 판매중 — 비노출 카테고리이거나 목록 스캔 누락. 건드리지 않는다
                    pass
            elif status == "404":
                prev["missing_weeks"] = int(prev.get("missing_weeks", 0)) + 1
                if prev["missing_weeks"] >= DELIST_AFTER_WEEKS:
                    prev["delisted"] = True; prev["soldout"] = True
                    prev.setdefault("soldout_since", now); rep["delisted"] += 1
            elif status in ("http",):
                rep["failed"] += 1
        # 4. 재입고 — 품절인 것 상세 확인 (오래된 품절은 매달 첫 주만)
        for no in sorted(soldout):
            prev = rows[no]
            since = prev.get("soldout_since") or (prev.get("crawled_at", "")[:10] or now)
            try:
                age = (date.fromisoformat(now) - date.fromisoformat(since)).days
            except ValueError:
                age = 0
            # 180일 넘은 품절이 재입고되는 일은 드물다(사람 지적 2026-09-03). 시즌 캐리오버(2월 품절 → 10월 재입고)와
            # 시그니처 재발매 정도라서: 180~365일은 매달 첫 주만, 1년 넘으면 목록에 다시 나타날 때만 본다.
            if no not in listed:
                if age > DEAD_SOLDOUT_DAYS:
                    continue
                if age > STALE_SOLDOUT_DAYS and not first_week_of_month:
                    continue
            url = prev.get("source_url", "")
            url = url if cc.product_no_of(url) else f"{shop.base}/product/detail.html?product_no={no}"
            status, d = check(no, url)
            rep["checked"] += 1
            if status == "ok":
                if not prev.get("soldout_since"):
                    prev["soldout_since"] = since
                apply_detail(no, d, prev)
            elif status == "404":
                prev["missing_weeks"] = int(prev.get("missing_weeks", 0)) + 1
                if prev["missing_weeks"] >= DELIST_AFTER_WEEKS and no not in listed:
                    prev["delisted"] = True; rep["delisted"] += 1
            elif status == "http":
                rep["failed"] += 1

    save_rows(path, rows)
    rep["total"] = len(rows)
    rep["sec"] = round(time.time() - t0)
    log(f"[{shop.slug}] 목록 {rep['listed']} · 신상 {rep['new']} · 품절 {rep['soldout']} · 재입고 {rep['restock']} · 삭제 {rep['delisted']} · 확인 {rep['checked']} {rep['reasons']} · {rep['sec']}s{' · ' + rep['guard'] if rep['guard'] else ''}")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--no-csv", action="store_true", help="CSV 재생성 생략")
    args = ap.parse_args()

    with cc.BRANDS_CSV.open(encoding="utf-8-sig") as f:
        brands = {r["slug"]: r for r in csv.DictReader(f)}
    brand_gender = {s: cc.BRAND_GENDER.get(r.get("gender", ""), "UNISEX") for s, r in brands.items()}
    slugs = args.brands or sorted(p.stem for p in CRAWL_DIR.glob("*.jsonl") if not p.name.startswith("_"))
    shops = []
    for s in slugs:
        b = brands.get(s)
        if not b or not b.get("official_url"):
            print(f"[{s}] brands_seed 에 없거나 official_url 없음 — 건너뜀", file=sys.stderr)
            continue
        u = urlparse(b["official_url"].strip())
        shops.append(cc.Shop(slug=s, base=f"{u.scheme or 'https'}://{u.netloc}", brand_gender=brand_gender.get(s, "UNISEX")))

    http = cc.PoliteSession(delay=args.delay)
    lock = threading.Lock()
    first_week = datetime.now(cc.KST).day <= 7

    def log(msg):
        with lock:
            print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    started = time.time()
    reports = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(update_brand, http, shop, log, first_week): shop for shop in shops}
        for fut in as_completed(futs):
            shop = futs[fut]
            try:
                reports.append(fut.result())
            except Exception as e:
                log(f"[{shop.slug}] 예외: {e!r}")
                reports.append({"date": today(), "slug": shop.slug, "ok": False, "guard": repr(e)})

    with LOG_PATH.open("a", encoding="utf-8") as f:
        for r in sorted(reports, key=lambda x: x["slug"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    tot = {k: sum(int(r.get(k, 0) or 0) for r in reports) for k in ("listed", "new", "soldout", "restock", "delisted", "price_changed", "checked", "failed")}
    guarded = [r["slug"] for r in reports if r.get("guard")]
    log(f"전부 끝 — 브랜드 {len(reports)} · 신상 {tot['new']} · 품절 {tot['soldout']} · 재입고 {tot['restock']} · 삭제 {tot['delisted']} · 가격 변동 {tot['price_changed']} · 상세 확인 {tot['checked']} · 요청 {http.requests_made} · {round(time.time() - started)}s")
    if guarded:
        log(f"보류/실패 매장: {', '.join(guarded)}")

    if not args.no_csv:
        n, info = cc.build_csv(brand_gender)
        log(f"CSV {n}행")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## 주간 갱신 {today()}\n\n신상 **{tot['new']}** · 품절 **{tot['soldout']}** · 재입고 **{tot['restock']}** · 삭제 **{tot['delisted']}** · 가격 변동 {tot['price_changed']} · 상세 확인 {tot['checked']} · 요청 {http.requests_made}\n\n")
            f.write("| 브랜드 | 목록 | 신상 | 품절 | 재입고 | 삭제 | 비고 |\n|---|---|---|---|---|---|---|\n")
            for r in sorted(reports, key=lambda x: x["slug"]):
                f.write(f"| {r['slug']} | {r.get('listed', '')} | {r.get('new', '')} | {r.get('soldout', '')} | {r.get('restock', '')} | {r.get('delisted', '')} | {r.get('guard', '')} |\n")


if __name__ == "__main__":
    main()
