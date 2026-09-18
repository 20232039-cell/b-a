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
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = CRAWL / "sizeguide"
OUT_PAGE = CRAWL / "pagesize"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
GARMENTS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim",
            "Skirts", "Dresses", "Suiting"}
IMG = re.compile(r'<img[^>]+?(?:ec-data-src|data-src|src)="([^"]+)"', re.I)
# 상품 페이지 안에서 사이즈표가 붙는 머리말. 아코디언·탭이라도 내용은 대개 서버 HTML 에
# 있고 CSS 로 숨겨져 있을 뿐이다 — 그러면 브라우저가 필요 없다(2026-09-17 실측: 한 매장의
# SIZE CHART 칸이 그랬다. 8벌 중 7벌에 그림이 있었다).
HEAD = re.compile(r"size\s*chart|size\s*guide|size\s*info|사이즈\s*차트|사이즈\s*가이드"
                  r"|사이즈\s*정보|사이즈\s*표|measurement|실측|size\s*&?\s*fit", re.I)
# 읽을 값어치가 없는 그림 — 아이콘·꾸밈, 그리고 남의 집 추적 화소(빈 1×1 이라 OCR 낭비다).
SKIP = re.compile(r"\.(?:svg|gif|ico)(?:\?|$)|/icon|/btn|blank\.|spacer|1x1"
                  r"|facebook\.com|google|doubleclick|analytics|criteo|kakao"
                  r"|naver\.com/|daum|tiktok|pinterest|channel\.io|icons8|bidswitch|cre\.ma|cafe24img\.com/pc/", re.I)


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


def images_after_heading(html: str, base: str) -> list[str]:
    """사이즈 머리말 바로 뒤에 오는 그림. 머리말마다 2,500자까지만 본다 — 더 멀리 보면
    다음 칸(배송·교환 안내)의 그림까지 딸려 온다."""
    out: list[str] = []
    for m in HEAD.finditer(html):
        seg = html[m.end():m.end() + 2500]
        n = 0
        for im in IMG.finditer(seg):
            u = im.group(1).strip()
            # 카페24 갤러리 썸네일(/product/big|medium|small/)은 상품 사진이다. 이미 갤러리로
            # 들어와 있고 사이즈표가 아니라서, 읽을 목록 맨 앞자리를 먹으면 손해다.
            if not u or SKIP.search(u) or re.search(r"/product/(?:big|medium|small|tiny)/", u, re.I):
                continue
            full = urljoin(base, u)
            if full not in out:
                out.append(full)
            n += 1
            if n >= 3:        # 머리말 하나에 셋까지 — 그 뒤는 다음 칸의 그림일 공산이 크다
                break
    return out[:6]


SIZEY = re.compile(r"(?:총장|기장|어깨|가슴|소매|허리|밑단|밑위|허벅지|암홀|"
                   r"LENGTH|CHEST|SHOULDER|SLEEVE|WAIST|HEM|THIGH)\s*[:=]?\s*\d", re.I)


def text_in(html: str) -> list[str]:
    """그 창에 **글로** 적힌 치수를 줄 단위로 거둔다.

    여태 이 창에서는 그림만 찾았다. 그런데 사람이 상품 페이지의 「INFORMATION」을 직접 눌러
    보고 알려 줬다 — 그 창의 내용이 그림이 아니라 글인 매장이 있다(2026-09-18):

        M  총장 74.5 어깨 55.5 가슴 64 소매 22.5
        L  총장 76.5 어깨 57.5 가슴 66 소매 23.5

    그림만 보던 탓에 이런 매장은 통째로 「없음」으로 세었다. 못 채운 옷이 20벌 넘는 매장
    47곳을 찔러 보니 네 곳이 이 꼴이다.
    """
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    out = []
    for ln in soup.get_text("\n").splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
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


def fetch_brand(brand: str, recs: list[dict], delay: float, workers: int, limit: int,
                source: str = "sizeguide", retext: bool = False, force: bool = False) -> dict:
    """source=sizeguide: 카페24 사이즈가이드 창 · source=page: 상품 페이지의 사이즈 머리말 뒤"""
    if not recs:
        return {"brand": brand, "products": 0, "with_img": 0, "images": 0, "shop_wide": 0}
    m = re.match(r"(https?://[^/]+)", recs[0]["source_url"])
    if not m:
        return {"brand": brand, "products": 0, "with_img": 0, "images": 0, "shop_wide": 0}
    base = m.group(1)
    outdir = OUT if source == "sizeguide" else OUT_PAGE
    # 이미 받아 둔 상품은 건너뛴다. 다만 **글을 거두기 시작한 것은 나중**이라(2026-09-18),
    # 그림만 있는 기록은 글이 있는지 아직 모른다 — retext 를 켜면 그런 기록을 다시 받는다.
    done: set[int] = set()
    dst = outdir / f"{brand}.jsonl"
    if dst.exists():
        for l in dst.read_text(encoding="utf-8").splitlines():
            if l.strip():
                try:
                    o = json.loads(l)
                except Exception:
                    continue
                if force or (retext and not o.get("size_text")):
                    continue
                done.add(o["product_no"])
    todo = [d for d in recs if d["product_no"] not in done][:limit]
    sess = requests.Session()
    sess.headers.update(HDR)

    def one(d):
        if source == "sizeguide":
            url = f"{base}/product/sizeguide.html?product_no={d['product_no']}"
        else:
            url = d["source_url"]
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=25)
                if r.status_code != 200:
                    return None
                imgs = (images_in(r.text, base) if source == "sizeguide"
                        else images_after_heading(r.text, base))
                return d, imgs, text_in(r.text)
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None

    got: list[tuple[dict, list[str], list[str]]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(one, todo):
            if res and (res[1] or res[2]):
                got.append(res)
            time.sleep(delay)

    # 매장 공용 안내 그림은 버린다 — 사이즈 환산표·세탁 안내가 상품마다 똑같이 붙는데,
    # 읽어 봐야 그 상품의 치수가 아니다. 다른 자리에서 쓰는 잣대와 같게 열 벌을 넘기면
    # 공용으로 본다.
    # 열 벌 넘게 겹치는 그림은 그 상품의 치수가 아니다 — 다른 자리에서 쓰는 잣대와 같게
    # 맞춘다. 처음엔 「절반 넘게」로 느슨하게 잡았다가, 한 매장의 브랜드 소개 그림 석 장이
    # 136벌 가운데 50벌쯤에 붙어 그대로 통과했다(2026-09-17 실측: 그 매장 적중률 0%).
    use = collections.Counter(u for _, urls, _ in got for u in urls)
    shop_wide = {u for u, c in use.items() if c >= 10}
    # 글도 같은 잣대로 거른다 — 이 창에는 상품별 실측과 **매장 공용 환산표**가 함께 실린다
    # (saintpain: 「M 총장 74.5 어깨 55.5 …」 밑에 「가슴 (inches) 22 23 24 …」가 붙어 있다).
    # 열 벌 넘게 똑같이 나오는 줄은 그 상품의 치수가 아니다.
    line_use = collections.Counter(l for _, _, lines in got for l in set(lines))
    # **짧은 줄은 안 버린다.** 사이즈 이름이 한 줄에 혼자 서는 매장이 있는데(「M」·「L」·「XL」),
    # 그 줄은 당연히 상품마다 같아서 공용으로 몰려 지워진다 — 값은 남고 이름만 사라졌다
    # (2026-09-18 실측: saintpain 표가 이름 없이 들어왔다). 공용 안내표는 한 줄이 길다.
    shop_lines = {l for l, c in line_use.items() if c >= 10 and len(l) >= 8}

    outdir.mkdir(parents=True, exist_ok=True)
    n_img = n_txt = 0
    # force 로 전부 다시 받을 때는 덧붙이지 말고 새로 쓴다 — 안 그러면 같은 상품이 두 줄이 된다.
    with dst.open("w" if force else "a", encoding="utf-8") as fh:
        for d, urls, lines in got:
            keep = [u for u in urls if u not in shop_wide]
            mine = [l for l in lines if l not in shop_lines]
            txt = "\n".join(mine)
            if not keep and not SIZEY.search(txt):
                continue
            n_img += len(keep)
            rec = {"brand_slug": brand, "product_no": d["product_no"],
                   "source_url": d["source_url"], "size_images": keep[:8]}
            if SIZEY.search(txt):
                n_txt += 1
                rec["size_text"] = txt[:4000]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"brand": brand, "products": len(todo), "with_img": len(got),
            "images": n_img, "texts": n_txt, "shop_wide": len(shop_wide)}


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
    ap.add_argument("--force", action="store_true",
                    help="이미 받아 둔 기록도 무시하고 전부 다시 받는다")
    ap.add_argument("--retext", action="store_true",
                    help="그림만 받아 둔 기록을 다시 받아 **글**도 거둔다(글 거두기는 나중에 붙었다)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--source", choices=["sizeguide", "page", "both"], default="sizeguide",
                    help="sizeguide: 카페24 사이즈가이드 창 · page: 상품 페이지의 사이즈 머리말 뒤")
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

    sources = ["sizeguide", "page"] if args.source == "both" else [args.source]
    tot = collections.Counter()
    for b in brands:
        recs = load_targets(b, args.only_missing, cats, sized)
        for src in sources:
            r = fetch_brand(b, recs, args.delay, args.workers, args.limit, src, args.retext, args.force)
            if r["products"]:
                tag = "사이즈가이드 창" if src == "sizeguide" else "머리말 뒤"
                print(f"  {b:26s} [{tag}] {r['products']:5d}벌 물어봄 · 그림 나온 상품 "
                      f"{r['with_img']:5d} · 그림 {r['images']:5d}장"
                      + (f" · 공용으로 버린 그림 {r['shop_wide']}종" if r["shop_wide"] else ""),
                      flush=True)
            for k in ("products", "with_img", "images"):
                tot[k] += r[k]
    print(f"\n합: {tot['products']}벌 물어봄 · 그림 나온 상품 {tot['with_img']}벌 · 그림 {tot['images']}장")


if __name__ == "__main__":
    main()
