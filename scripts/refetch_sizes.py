"""상세를 다시 열어 고른 필드만 갱신한다 — 기본은 사이즈 표, --fields text 를 주면 설명(description·detail_text·spec·detail_images)도.

왜: 파서가 놓치던 표 모양(rssc 의 행렬 표, 2026-09-03)을 고친 뒤 그 브랜드의 표만 채우려고. 전체 재수집은
설명·이미지까지 다시 받아 무겁고, 사람 결정으로 설명은 갱신하지 않는다.
사용: py scripts/refetch_sizes.py --brands rssc [--only-missing]
      py scripts/refetch_sizes.py --brands all --fields size,text --select short-desc-or-no-size   # 2026-09-03 1회 보정
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_cafe24 as cc  # noqa: E402
from weekly_update import load_rows, save_rows  # noqa: E402


GARMENT_CATS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim", "Skirts", "Dresses", ""}


def wants(d: dict, select: str, cat: str) -> bool:
    if select == "all":
        return True
    if select == "garments":
        return cat in GARMENT_CATS          # 옷 전부(액세서리·가방·신발 제외) — 파서를 고친 뒤 한 번 다시 받을 때
    if select == "no-size":
        return not d.get("size_table")
    if select == "no-detail-images":
        # 상세 그림을 한 장도 못 건진 상품 — 수집기가 그림을 버렸던 자리다
        # (2026-09-05: 이름에 「logo」가 든 옷 611벌이 그랬다)
        return not d.get("detail_images")
    # short-desc-or-no-size: JSON-LD 요약(≤300자)만 있거나, 옷인데 사이즈 표가 없는 것
    short = d.get("description_source") == "json-ld" and len(d.get("description") or "") <= 300
    nosize = not d.get("size_table") and cat in GARMENT_CATS
    return short or nosize


def refetch(http: cc.PoliteSession, shop: cc.Shop, only_missing: bool, log, fields=("size",), select="no-size", cats=None,
            shard=(0, 1), out_dir: Path | None = None, max_minutes: float = 0) -> dict:
    """shard=(k, n): 대상을 product_no 순으로 n등분해 k번째만. out_dir 를 주면 본 파일을 건드리지 않고 갱신한 행만
    out_dir/<slug>.<k>.jsonl 에 쓴다 — Actions 러너 여럿이 같은 브랜드를 나눠 받을 때(collect 가 합친다)."""
    path = cc.CRAWL_DIR / f"{shop.slug}.jsonl"
    rows = load_rows(path)
    cc.load_robots(http, shop)
    home = http.get(shop.base + "/", retries=1)
    if home is not None and home.status_code == 200:
        fin = urlparse(home.url)
        shop.base = f"{fin.scheme}://{fin.netloc}"
    cats = cats or {}
    sel = "no-size" if only_missing else select
    todo = sorted(no for no, d in rows.items() if (d.get("price") or 0) > 1000 and wants(d, sel, cats.get(no, "")))
    k, n = shard
    todo = todo[k::n]
    touched: list[int] = []
    got = fail = 0
    started = time.time()
    # 한 매장이 유독 느리면 그 브랜드만 접고 나머지를 살린다 — amomento 는 259건에 요청당 25초가 걸려
    # (우리 대기는 1초다) 묶음 하나가 전체 run 을 108분 붙잡았다(2026-09-04). 나머지 11묶음은 32분 안에 끝났다.
    for i, no in enumerate(todo, 1):
        if max_minutes and (time.time() - started) / 60 > max_minutes:
            log(f"[{shop.slug}] 시간 상한 {max_minutes:.0f}분 초과 — {i - 1}/{len(todo)} 에서 접는다(나머지는 다음 실행에)")
            break
        d = rows[no]
        url = d.get("source_url", "")
        url = url if cc.product_no_of(url) else f"{shop.base}/product/detail.html?product_no={no}"
        if not shop.allowed(url):
            continue
        r = http.get(url, retries=2)
        if r is None or r.status_code != 200:
            fail += 1
            continue
        if "text" in fields:
            nd = cc.parse_detail(r.text, url, shop)
            if nd and nd.get("price"):
                for key in ("description", "description_source", "detail_text", "spec", "detail_images", "size_table", "soldout", "price"):
                    if nd.get(key) not in (None, "", [], {}):
                        d[key] = nd[key]
                d["refetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                touched.append(no)
                if nd.get("size_table"):
                    got += 1
                continue
        st = cc.extract_size_table(r.text)
        if st:
            d["size_table"] = st
            d["size_source"] = "html"
            touched.append(no)
            got += 1
        if i % 100 == 0:
            log(f"[{shop.slug}] … {i}/{len(todo)} · 표 {got}")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / f"{shop.slug}.{k}.jsonl").open("w", encoding="utf-8") as f:
            for no in touched:
                f.write(json.dumps(rows[no], ensure_ascii=False) + "\n")
    else:
        save_rows(path, rows)
    log(f"[{shop.slug}] 끝 — 대상 {len(todo)} (조각 {k + 1}/{n}) · 표 얻음 {got} · 갱신 {len(touched)} · 실패 {fail}")
    return {"slug": shop.slug, "todo": len(todo), "got": got, "failed": fail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="+", required=True)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fields", default="size", help="size 또는 size,text")
    ap.add_argument("--select", default="no-size", choices=["no-size", "short-desc-or-no-size", "no-detail-images", "garments", "all"])
    ap.add_argument("--shard", default="1/1", help="k/n (Actions 샤딩)")
    ap.add_argument("--out-dir", help="갱신 행만 조각 파일로 (collect 가 합침)")
    ap.add_argument("--max-minutes", type=float, default=0, help="브랜드 하나에 쓸 시간 상한(분) — 넘으면 그 브랜드만 접는다")
    ap.add_argument("--plan", action="store_true", help="브랜드별 대상 수만 JSON 으로 출력 (매트릭스 계획용)")
    ap.add_argument("--units", help="러너 하나가 동시에 맡을 (브랜드:k/n) 묶음, 쉼표로 — 계정 동시 실행 한도(20잡) 안에서 처리량을 올린다")
    args = ap.parse_args()
    k, n = (int(x) for x in args.shard.split("/"))
    shard = (k - 1, n)
    out_dir = Path(args.out_dir) if args.out_dir else None
    fields = tuple(x.strip() for x in args.fields.split(","))
    cats: dict[str, dict[int, str]] = {}
    for r in csv.DictReader(open(cc.DATA / "products_full.csv", encoding="utf-8-sig")):
        cats.setdefault(r["brand_slug"], {})[int(r["product_no"])] = r["category"]
    if args.brands == ["all"]:
        args.brands = sorted(p.stem for p in cc.CRAWL_DIR.glob("*.jsonl") if not p.name.startswith("_"))
    with cc.BRANDS_CSV.open(encoding="utf-8-sig") as f:
        brands = {r["slug"]: r for r in csv.DictReader(f)}
    shops = []
    for s in args.brands:
        if s not in brands or not brands[s].get("official_url"):
            continue
        u = urlparse(brands[s]["official_url"].strip())
        shops.append(cc.Shop(slug=s, base=f"{u.scheme or 'https'}://{u.netloc}", brand_gender="UNISEX"))
    if args.units:
        # 묶음 실행: 서로 다른 호스트를 스레드로 동시에(호스트당 1초 예의는 PoliteSession 이 지킨다)
        units = []
        for u in args.units.split(","):
            brand, sh = u.strip().split(":")
            k, n = (int(x) for x in sh.split("/"))
            if brand in brands and brands[brand].get("official_url"):
                pu = urlparse(brands[brand]["official_url"].strip())
                units.append((cc.Shop(slug=brand, base=f"{pu.scheme or 'https'}://{pu.netloc}", brand_gender="UNISEX"), (k - 1, n)))
        http = cc.PoliteSession(delay=1.0)
        lock = threading.Lock()

        def ulog(m):
            with lock:
                print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, len(units))) as ex:
            for fut in as_completed([ex.submit(refetch, http, sh, False, ulog, fields, args.select, cats.get(sh.slug), shard_u, out_dir, args.max_minutes) for sh, shard_u in units]):
                fut.result()
        return
    if args.plan:
        plan = {}
        for sh in shops:
            rows = load_rows(cc.CRAWL_DIR / f"{sh.slug}.jsonl")
            c = cats.get(sh.slug, {})
            plan[sh.slug] = sum(1 for no, d in rows.items() if (d.get("price") or 0) > 1000 and wants(d, args.select, c.get(no, "")))
        print(json.dumps(plan, ensure_ascii=False))
        return
    http = cc.PoliteSession(delay=1.0)
    lock = threading.Lock()

    def log(m):
        with lock:
            print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(refetch, http, sh, args.only_missing, log, fields, args.select, cats.get(sh.slug), shard, out_dir, args.max_minutes) for sh in shops]):
            fut.result()


if __name__ == "__main__":
    main()
