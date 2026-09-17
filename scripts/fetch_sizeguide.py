"""카페24 「사이즈 가이드」 창에서 상품별 사이즈표 그림을 받아 둔다.

왜 있는가 — 사람이 상품 페이지를 직접 눌러 보고 알려 줬다(2026-09-17):

    「사이즈 가이드 누르면 표가 나온다 / 푸쉬버튼은 진짜 없고 나머진 다 토글이나
      버튼 눌러서 들어가야 나오네」

그때까지 우리는 상품 페이지의 서버 HTML 만 읽었고, 거기에는 그 표가 없다. 눌러야
열리니 브라우저가 필요하다고 봤는데 — **아니었다.** 카페24 기본 사이즈가이드 창은
그냥 주소다:

    /product/sizeguide.html?product_no=<번호>

버튼은 이 주소를 레이어로 띄울 뿐이라, 주소를 그대로 받으면 자바스크립트 없이 내용이
다 나온다. 상품마다 다른 그림이 들어 있으면 그것이 곧 그 상품의 사이즈표다.

무엇을 남기나 — data/crawl/sizeguide/<slug>.jsonl 에 {product_no, source_url,
size_images}. 크롤 기록을 덮지 않는다. 합치는 것은 ocr_detail_images 가 한다.

전수 조사(2026-09-17, 사이즈 없는 옷 7,347벌·매장 140곳, 매장마다 3벌씩 찔러 봄):

    상품별 그림 있음   매장   9곳 · 1,897벌
    그림 없음          매장 123곳 · 5,054벌
    매장 공용(다 같음) 매장   2곳 ·   252벌

사용: py scripts/fetch_sizeguide.py --brands cayl,99-is
      py scripts/fetch_sizeguide.py --all-missing      # 사이즈 없는 옷이 있는 매장 전부
"""
from __future__ import annotations
import argparse, collections, csv, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = CRAWL / "sizeguide"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
GARMENTS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim",
            "Skirts", "Dresses", "Suiting"}
IMG = re.compile(r'<img[^>]+?(?:ec-data-src|data-src|src)="([^"]+)"', re.I)
# 읽을 값어치가 없는 그림 — 아이콘·꾸밈, 그리고 남의 집 추적 화소(빈 1×1 이라 OCR 낭비다).
SKIP = re.compile(r"\.(?:svg|gif|ico)(?:\?|$)|/icon|/btn|blank\.|spacer|1x1"
                  r"|facebook\.com|google|doubleclick|analytics|criteo|kakao"
                  r"|naver\.com/|daum|tiktok|pinterest|channel\.io|cafe24img\.com/pc/", re.I)


def images_in(html: str, base: str) -> list[str]:
    out: list[str] = []
    for m in IMG.finditer(html):
        u = m.group(1).strip()
        if not u or SKIP.search(u):
            continue
        full = urljoin(base, u)
        if full not in out:
            out.append(full)
    return out


def load_targets(brand: str, only_missing: bool, cats: dict, sized: set) -> list[dict]:
    src = CRAWL / f"{brand}.jsonl"
    if not src.exists():
        return []
    out = []
    for l in src.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        u = d.get("source_url") or ""
        if not u or d.get("product_no") is None:
            continue
        if cats.get(u) not in GARMENTS:
            continue
        if only_missing and u in sized:
            continue
        out.append(d)
    return out


def fetch_brand(brand: str, recs: list[dict], delay: float, workers: int, limit: int) -> dict:
    if not recs:
        return {"brand": brand, "products": 0, "with_img": 0, "images": 0, "shop_wide": 0}
    m = re.match(r"(https?://[^/]+)", recs[0]["source_url"])
    if not m:
        return {"brand": brand, "products": 0, "with_img": 0, "images": 0, "shop_wide": 0}
    base = m.group(1)
    done: set[int] = set()
    dst = OUT / f"{brand}.jsonl"
    if dst.exists():
        for l in dst.read_text(encoding="utf-8").splitlines():
            if l.strip():
                try:
                    done.add(json.loads(l)["product_no"])
                except Exception:
                    pass
    todo = [d for d in recs if d["product_no"] not in done][:limit]
    sess = requests.Session()
    sess.headers.update(HDR)

    def one(d):
        url = f"{base}/product/sizeguide.html?product_no={d['product_no']}"
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=20)
                if r.status_code != 200:
                    return None
                return d, images_in(r.text, base)
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None

    got: list[tuple[dict, list[str]]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(one, todo):
            if res and res[1]:
                got.append(res)
            time.sleep(delay)

    # 매장 공용 안내 그림은 버린다 — 사이즈 환산표·세탁 안내가 상품마다 똑같이 붙는데,
    # 읽어 봐야 그 상품의 치수가 아니다. 다른 자리에서 쓰는 잣대와 같게 열 벌을 넘기면
    # 공용으로 본다.
    use = collections.Counter(u for _, urls in got for u in urls)
    floor = max(10, len(got) * 0.5)
    shop_wide = {u for u, c in use.items() if c >= floor}

    OUT.mkdir(parents=True, exist_ok=True)
    n_img = 0
    with dst.open("a", encoding="utf-8") as fh:
        for d, urls in got:
            keep = [u for u in urls if u not in shop_wide]
            if not keep:
                continue
            n_img += len(keep)
            fh.write(json.dumps({"brand_slug": brand, "product_no": d["product_no"],
                                 "source_url": d["source_url"], "size_images": keep[:8]},
                                ensure_ascii=False) + "\n")
    return {"brand": brand, "products": len(todo), "with_img": len(got),
            "images": n_img, "shop_wide": len(shop_wide)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", default="")
    ap.add_argument("--all-missing", action="store_true",
                    help="사이즈 없는 옷이 있는 매장 전부")
    ap.add_argument("--only-missing", action="store_true", default=True)
    ap.add_argument("--all-products", dest="only_missing", action="store_false",
                    help="사이즈가 이미 있는 옷까지 받는다")
    ap.add_argument("--limit", type=int, default=100000, help="매장마다 최대 상품 수")
    ap.add_argument("--delay", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    cats = {}
    pf = DATA / "products_full.csv"
    if pf.exists():
        for r in csv.DictReader(pf.open(encoding="utf-8-sig")):
            cats[r["source_url"]] = r["category"]
    sized: set[str] = set()
    sp = DATA / "product_sizes.json"
    if sp.exists():
        sized = set(json.loads(sp.read_text(encoding="utf-8")))

    if args.brands:
        brands = [b for b in args.brands.split(",") if b]
    elif args.all_missing:
        cnt: collections.Counter = collections.Counter()
        for f in sorted(CRAWL.glob("*.jsonl")):
            if f.name.startswith("_"):
                continue
            for l in f.read_text(encoding="utf-8").splitlines():
                if not l.strip():
                    continue
                try:
                    d = json.loads(l)
                except Exception:
                    continue
                u = d.get("source_url") or ""
                if cats.get(u) in GARMENTS and u not in sized:
                    cnt[f.stem] += 1
        brands = [b for b, _ in cnt.most_common()]
    else:
        ap.error("--brands 나 --all-missing 가운데 하나는 있어야 한다")

    tot = collections.Counter()
    for b in brands:
        recs = load_targets(b, args.only_missing, cats, sized)
        r = fetch_brand(b, recs, args.delay, args.workers, args.limit)
        if r["products"]:
            print(f"  {b:26s} {r['products']:5d}벌 물어봄 · 그림 나온 상품 {r['with_img']:5d}"
                  f" · 그림 {r['images']:5d}장"
                  + (f" · 공용으로 버린 그림 {r['shop_wide']}종" if r["shop_wide"] else ""),
                  flush=True)
        for k in ("products", "with_img", "images"):
            tot[k] += r[k]
    print(f"\n합: {tot['products']}벌 물어봄 · 그림 나온 상품 {tot['with_img']}벌 · 그림 {tot['images']}장")


if __name__ == "__main__":
    main()
