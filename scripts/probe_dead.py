"""열리지 않는 상품 페이지를 찾아 적어 둔다 — 목록에서 뺄 것들.

왜 필요한가: 매장이 상품을 내리거나 회원 전용으로 돌려도 목록 페이지에는 남는다.
그런 상품은 우리가 상세를 못 받으니 사이즈·소재가 영원히 비고, 사람이 링크를 열어도
경고창만 본다(2026-09-05 사람이 네 브랜드에서 짚어 줬다).

  insilence  「회원만 접근권한이 있습니다」
  dunst      「고객님께서는 해당 상품에 접근이 불가능 합니다」
  haleine    「회원만 접근권한이 있습니다」
  matin-kim  404

브랜드마다 규칙을 적지 않는다 — 매장은 바뀐다. 실제로 페이지를 열어 보고 판정한다.

  → data/dead_products.csv   (브랜드, 상품번호, 왜, 링크)

사용: py scripts/probe_dead.py [--all]
      기본은 「사이즈 없는 옷」과 「상세가 통째로 빈 상품」만 본다.
      Actions(probe-dead.yml)가 매주 --all 로 판매중 전부를 연다 — 사람 지시(2026-10-10) 「판매중 표시는 아예 없으면 제외」.
      lookast 판매중 1,083벌 중 369벌이 홈으로 튕기는데 목록엔 판매중으로 남아 있었다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from collections import Counter
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GARMENTS = {"tops", "outer", "bottoms", "dress", "skirt", "suiting"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# 카페24가 경고창으로 띄우는 말. 「로그인」만 보면 안 된다 — 머리글에도 있다.
# 경고창(alert · historyBack) 안에 뜬 말만 본다 — 페이지 아무 데나 있는 「MEMBERS ONLY」(nomanual 네이버페이 층 제목)를 잡아
# 1,192벌을 회원 전용으로 잘못 뺄 뻔했다(2026-10-10 전수).
BLOCKED = re.compile(r"(?:alert|historyBack)\(\s*['\"][^'\"]{0,80}?(?:회원만\s*접근권한|접근이\s*불가능|회원\s*전용|"
                     r"등급의?\s*회원만|members\s*only)", re.I)
_last: dict[str, float] = {}
_lock = threading.Lock()


def polite(url: str, delay: float = 0.4):
    host = url.split("/")[2] if "//" in url else url
    with _lock:
        wait = delay - (time.monotonic() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()
    return requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)


def verdict(url: str) -> str | None:
    """열리지 않으면 까닭을, 멀쩡하면 None."""
    try:
        r = polite(url)
    except requests.RequestException:
        # 한 번 못 받은 것으로 지웠다고 보지 않는다 — 판매중 전부(--all)를 돌리면 일시 오류가 수십 벌 나온다(2026-10-10 facade-pattern 16벌).
        return None
    if r.status_code == 404:
        return "404 — 페이지가 없다"
    if r.status_code >= 500:
        return None                      # 매장 쪽 일시 오류 — 지웠다고 볼 수 없다
    if BLOCKED.search(r.text):
        return "회원 전용 — 상세를 볼 수 없다"
    # 상세로 갔는데 상품이 아닌 곳으로 튕겼다. **튕겼을 때만** 본다 — 처음엔 「주소에 /product/ · product_no= 가 없으면」으로 봐서
    # 카페24 가 아닌 매장(ader-error /kr/shop/N · Shopify /products/ · goods_view.php · shop_view/?idx=)의 멀쩡한 상품 10,829벌을
    # 뺄 뻔했다(2026-10-10 전수 — rebuild 를 끊었다). 튕긴 뒤 주소에 상품을 가리키는 값(번호 · 마지막 경로 토막)이 남았으면 멀쩡하다.
    # 카페24 「접속 차단」 화면(block_state.html)은 상품이 아니라 우리가 막힌 것이다 — anglan 판매중 222벌이 홈까지 다 이리로 갔다(2026-10-10).
    if "block_state" in r.url or "/blocked" in r.url:
        return None
    if r.history and _product_key(url) not in unquote(r.url):
        return "상품 페이지가 아닌 곳으로 넘어감"
    return None


def _product_key(url: str) -> str:
    u = urlsplit(url)
    q = parse_qs(u.query)
    for k in ("product_no", "pno", "goodsNo", "idx", "branduid", "no"):
        if q.get(k):
            return q[k][0]
    seg = [x for x in unquote(u.path).split("/") if x]
    return seg[-1] if seg else url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="판매중 상품 전부 (기본은 의심스러운 것만)")
    ap.add_argument("--workers", type=int, default=6)
    # 판매중 전부(8만 벌 남짓)를 러너 하나로 돌렸더니 4시간 제한에 걸려 끊겼다(2026-10-10). 매장을 몫으로 나눠 러너 여럿이 돈다 —
    # 몫마다 data/dead/<i>.csv 를 따로 써서 푸시가 엉키지 않는다. crawl_cafe24.load_dropped 가 그 파일들도 읽는다.
    ap.add_argument("--shard", default="", help="i/N — 매장 이름 해시로 나눈 i번째 몫만")
    ap.add_argument("--recheck", action="store_true", help="지난 명단(dead_products · dead/*.csv)만 다시 연다 — 판정 규칙을 고친 뒤 빨리 바로잡을 때")
    args = ap.parse_args()

    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    rows = {(r["brand_slug"], r["product_no"]): r
            for r in csv.DictReader((DATA / "products_full.csv").open(encoding="utf-8-sig"))}

    import zlib
    si, sn = (int(x) for x in args.shard.split("/")) if args.shard else (0, 1)

    def mine(slug: str) -> bool:
        return zlib.crc32(slug.encode()) % sn == si

    cand = []
    for path in sorted(DATA.glob("crawl/*.jsonl")):
        slug = path.stem
        if slug.startswith("_") or not mine(slug):
            continue
        latest: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            latest[str(d["product_no"])] = d
        for no, d in latest.items():
            r = rows.get((slug, no))
            if not r or r["status"] != "ON_SALE":
                continue
            empty = (not (d.get("detail_text") or "").strip()
                     and not (d.get("detail_images") or [])
                     and not (d.get("spec") or {}))
            nosize = (r["category_code"] in GARMENTS
                      and not (sizes.get(r["source_url"]) or {}).get("sizes"))
            if (args.all or empty or nosize) and not args.recheck:
                cand.append((slug, no, r["source_url"], (d.get("name") or "")[:60]))

    # 지난번에 뺀 상품은 목록(products_full)에서 이미 빠져 위에서 안 잡힌다 — 그대로 두면 다음 판에 「멀쩡」으로 돌아와 목록에 다시 선다.
    # 지난 명단도 다시 열어 보고, 여전히 안 열리면 남긴다(다시 열리면 빠진다).
    seen = {(c[0], c[1]) for c in cand}
    for prev in [DATA / "dead_products.csv", *sorted((DATA / "dead").glob("*.csv"))]:
        if not prev.exists():
            continue
        with prev.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                k = ((row.get("브랜드") or "").strip(), (row.get("상품번호") or "").strip())
                if k[0] and k[1] and row.get("링크") and k not in seen and mine(k[0]):
                    cand.append((k[0], k[1], row["링크"].strip(), (row.get("상품명") or "")[:60]))
                    seen.add(k)
    print(f"열어 볼 상품 {len(cand):,}", file=sys.stderr)
    out, done = [], [0]

    def one(item):
        slug, no, url, name = item
        why = verdict(url)
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"  {done[0]:,}/{len(cand):,}", file=sys.stderr)
        if why:
            out.append({"브랜드": slug, "상품번호": no, "왜": why, "상품명": name, "링크": url})

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(one, cand))

    # 매장 하나가 통째로(판매중 20벌 넘게 · 90% 넘게) 안 열리면 상품이 아니라 매장 쪽 문제(차단 · 이전 · 점검)로 본다 — 빼지 않고 알린다.
    on_sale = Counter(slug for (slug, _), r in rows.items() if r["status"] == "ON_SALE")
    flagged = Counter(x["브랜드"] for x in out)
    whole = {b for b, n in flagged.items() if on_sale[b] >= 20 and n >= 0.9 * on_sale[b]}
    for b in sorted(whole):
        print(f"   매장 통째로 안 열림 — 빼지 않음: {b} {flagged[b]}/{on_sale[b]}", file=sys.stderr)
    out = [x for x in out if x["브랜드"] not in whole]
    out.sort(key=lambda x: (x["왜"], x["브랜드"], x["상품명"]))
    if args.shard:
        (DATA / "dead").mkdir(exist_ok=True)
        p = DATA / "dead" / f"{si}.csv"
    else:
        p = DATA / "dead_products.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["왜", "브랜드", "상품명", "링크", "상품번호"])
        w.writeheader()
        w.writerows(out)
    import collections
    print(f"\n열리지 않는 상품 {len(out)} → {p}")
    for k, v in collections.Counter(x["왜"] for x in out).most_common():
        b = collections.Counter(x["브랜드"] for x in out if x["왜"] == k)
        print(f"   {k:28} {v:5}  {', '.join(f'{s} {n}' for s, n in b.most_common(6))}")


if __name__ == "__main__":
    main()
