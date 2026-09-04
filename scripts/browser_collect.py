"""브라우저로 상품 페이지를 열어 자바스크립트가 그리는 것까지 거둔다.

왜 필요한가 — requests + BeautifulSoup 로는 안 보이는 것이 있다(2026-09-04 확인):
  · diafvine 사이즈표: 페이지는 200 으로 잘 받아지고 상품명·가격도 있는데 「어깨/가슴/총장」이
    0개다. 표를 자바스크립트가 그린다. 그래서 사이즈 22% 에 머물렀고, 얻은 23개는 전부
    표가 그림으로 들어간 상품에서 OCR 로 건진 것이었다.
  · insilence 갤러리: <img> 태그에 상품 사진이 0장. 사진은 ambient.diskn.com 에 있고
    나중에 불러온다.
  · perenn 소재: 소재 없는 62벌 중 53벌은 설명글에 소재 낱말이 아예 없다.
  · coor: 갤러리 0.3장 · 상세그림 0장.

무엇을 거두나 — 사이즈표(정식 라벨로), 설명글, 상품 사진 주소. 기존 크롤 결과를 덮지 않고
data/crawl/browser/<slug>.jsonl 에 따로 쌓는다. 합치는 것은 size_from_ocr·tag_items 가 한다.

사용: py scripts/browser_collect.py --brands diafvine,perenn --limit 40
주의: 이 세션의 프록시로는 크로미움이 못 나간다(ws_closed_mid_exchange). Actions 러너에서 돈다.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = CRAWL / "browser"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SIZE_WORD = re.compile(r"어깨|가슴|총장|소매|밑단|허리|허벅지|밑위|암홀|엉덩이|화장"
                       r"|shoulder|chest|length|sleeve|waist|thigh|rise|hem", re.I)
PHOTO = re.compile(r"/web/product/|/detailimg/|/product/.*\.(?:jpg|jpeg|png|webp)", re.I)


def find_exe() -> str | None:
    """러너에 깔린 크로미움을 찾는다 — playwright 버전과 브라우저 빌드가 어긋날 때 대비."""
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        hits = sorted(Path("/").glob(pat.lstrip("/")))
        if hits:
            return str(hits[-1])
    return None


def read_table(page) -> dict[str, list[str]]:
    """치수 낱말이 든 표를 찾아 {라벨: [값…]} 로. 정방향(헤더가 라벨)·전치(첫 칸이 라벨) 둘 다.
    상세는 iframe 안에 있는 스킨이 있어 page.frames 를 모두 본다."""
    for fr in list(page.frames):
        got = _read_table_in(fr)
        if got:
            return got
    return {}


def _read_table_in(page) -> dict[str, list[str]]:
    try:
        tables = page.query_selector_all("table")
    except Exception:
        return {}
    for t in tables:
        txt = t.inner_text() or ""
        if not SIZE_WORD.search(txt):
            continue
        grid = []
        for tr in t.query_selector_all("tr"):
            cells = [(" ".join((c.inner_text() or "").split())) for c in tr.query_selector_all("th,td")]
            if cells:
                grid.append(cells)
        if len(grid) < 2:
            continue
        out: dict[str, list[str]] = {}
        # 전치형 — 각 행의 첫 칸이 라벨
        for row in grid:
            if len(row) >= 2 and SIZE_WORD.search(row[0]):
                out[row[0]] = row[1:]
        if len(out) >= 2:
            return out
        # 정방향 — 첫 행이 라벨
        head = grid[0]
        if sum(1 for h in head if SIZE_WORD.search(h)) >= 2:
            for j, h in enumerate(head):
                if not SIZE_WORD.search(h):
                    continue
                out[h] = [r[j] for r in grid[1:] if len(r) > j]
            if len(out) >= 2:
                return out
    return {}


def open_details(page) -> None:
    """상세를 실제로 그리게 만든다.

    diafvine 은 사이즈표가 DETAILS 탭 안에 있어, 페이지만 열고 기다리면 #prdDetail 이
    공백 문자 8~10자뿐이다(2026-09-04 사람이 화면으로 확인). 탭을 누르고 끝까지 내려
    게으른 이미지·표까지 그려지게 한다."""
    for label in ("DETAILS", "상세정보", "상세보기", "DETAIL", "SIZE", "사이즈"):
        try:
            el = page.get_by_text(label, exact=False).first
            if el and el.is_visible(timeout=800):
                el.click(timeout=1500)
                page.wait_for_timeout(700)
                break
        except Exception:
            continue
    try:
        for _ in range(6):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight/5)")
            page.wait_for_timeout(450)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


LOOKWORD = re.compile(r"룩북|룩\b|컬렉션|코디|스타일링|LOOKBOOK|COLLECTION|EDITORIAL|STYLING|LOOK", re.I)
PRODUCT = re.compile(r"/product/(?!list\.html)[^\"'\s>]*?/(\d+)/|product_no=(\d+)")


def survey_lookbook(page, base: str) -> dict:
    """룩북 페이지에 「이 룩의 상품」 링크가 붙어 있는지 본다.

    왜 중요한가(2026-09-04): 지금 우리에게 없는 것은 사진이 아니라 정답표다. 룩 사진은
    14,174장 있는데 어느 룩에 어느 상품이 쓰였는지를 아는 경우가 262건뿐이다. 룩북에
    상품 링크가 붙어 있으면 그 표를 공짜로 얻는다.

    정적 HTML 로는 확인이 안 된다 — insilence·frizmworks 는 메뉴가, lmood 는 룩북 내용이
    자바스크립트다. lmood 룩북에서 찾은 상품 링크 23개는 전부 /product/list.html 카테고리
    링크였다(개별 상품 0개). 그래서 브라우저로 본다."""
    out = {"base": base, "menu": [], "pages": []}
    try:
        page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
    except Exception:
        return out
    seen = []
    for a in page.query_selector_all("a"):
        try:
            t = " ".join((a.inner_text() or "").split())
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        if not href or href.startswith("#") or "/product/list" in href:
            continue
        if LOOKWORD.search(t) or LOOKWORD.search(href):
            full = href if href.startswith("http") else base + ("" if href.startswith("/") else "/") + href
            if full not in seen:
                seen.append(full)
    out["menu"] = seen[:6]
    for u in seen[:3]:
        try:
            page.goto(u, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            for _ in range(4):
                page.evaluate("window.scrollBy(0, document.body.scrollHeight/4)")
                page.wait_for_timeout(400)
            html = page.content()
        except Exception:
            continue
        nos = {m[0] or m[1] for m in PRODUCT.findall(html)}
        imgs = len(page.query_selector_all("img"))
        out["pages"].append({"url": u, "product_nos": sorted(nos)[:40],
                             "n_products": len(nos), "n_images": imgs})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookbook", action="store_true",
                    help="상품을 열지 않고, 브랜드 룩북 페이지에 상품 링크가 있는지만 조사한다")
    ap.add_argument("--brands", required=True)
    ap.add_argument("--limit", type=int, default=50, help="브랜드마다 최대 상품 수")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--only-missing", choices=["size", "material", "any"], default="",
                    help="size: 사이즈 없는 것만 · material: 소재 태그 없는 것만 · any: 둘 중 하나라도 없는 것")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright


    sizes, mats = {}, {}
    p = DATA / "product_sizes.json"
    if args.only_missing in ("size", "any") and p.exists():
        sizes = json.loads(p.read_text(encoding="utf-8"))
    p3 = DATA / "product_tags_full.json"
    if args.only_missing in ("material", "any") and p3.exists():
        tg = json.loads(p3.read_text(encoding="utf-8"))
        mats = {u for u, v in tg.items() if (v.get("tags") or {}).get("material")}

    OUT.mkdir(parents=True, exist_ok=True)
    exe = find_exe()
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    launch = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if exe:
        launch["executable_path"] = exe
    if proxy:
        launch["proxy"] = {"server": proxy}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(user_agent=UA, viewport={"width": 1400, "height": 1000})
        if args.lookbook:
            res = {}
            for brand in [b for b in args.brands.split(",") if b]:
                src = CRAWL / f"{brand}.jsonl"
                if not src.exists():
                    continue
                base = ""
                for l in src.read_text(encoding="utf-8").splitlines():
                    if l.strip():
                        m = re.match(r"(https?://[^/]+)", json.loads(l).get("source_url") or "")
                        if m:
                            base = m.group(1); break
                if not base:
                    continue
                r = survey_lookbook(page, base)
                res[brand] = r
                best = max((p["n_products"] for p in r["pages"]), default=0)
                print(f"  {brand}: 룩 메뉴 {len(r['menu'])}개 · 상품 링크 최대 {best}개", flush=True)
                time.sleep(args.delay)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "lookbook_survey.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\n{OUT / 'lookbook_survey.json'} 에 남겼다")
            browser.close()
            return
        for brand in [b for b in args.brands.split(",") if b]:
            src = CRAWL / f"{brand}.jsonl"
            if not src.exists():
                print(f"  {brand}: 크롤 기록이 없다", flush=True)
                continue
            recs = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
            todo = [d for d in recs if d.get("source_url")]
            if args.only_missing == "size":
                todo = [d for d in todo if d["source_url"] not in sizes]
            elif args.only_missing == "material":
                todo = [d for d in todo if d["source_url"] not in mats]
            elif args.only_missing == "any":
                todo = [d for d in todo if d["source_url"] not in sizes or d["source_url"] not in mats]
            todo = todo[:args.limit]
            got_t = got_i = 0
            with (OUT / f"{brand}.jsonl").open("a", encoding="utf-8") as fh:
                for d in todo:
                    try:
                        page.goto(d["source_url"], wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(1500)
                        open_details(page)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            pass
                        table = read_table(page)
                        # 프레임·선택자를 모두 보고 가장 긴 글을 쓴다 — 첫 선택자가 빈 껍데기인
                        # 스킨이 있다(diafvine #prdDetail 이 공백 문자 8자).
                        desc = ""
                        for fr in list(page.frames):
                            for sel in ("#prdDetail", ".xans-product-detail", ".detailArea",
                                        "#detail", ".prd-detail", "body"):
                                try:
                                    el = fr.query_selector(sel)
                                except Exception:
                                    continue
                                if not el:
                                    continue
                                t = " ".join((el.inner_text() or "").split())
                                if len(t) > len(desc):
                                    desc = t[:8000]
                        imgs = []
                        for im in page.query_selector_all("img"):
                            s = im.get_attribute("src") or im.get_attribute("data-src") or ""
                            if s and PHOTO.search(s) and s not in imgs:
                                imgs.append(s)
                        fh.write(json.dumps({"brand_slug": brand, "product_no": d.get("product_no"),
                                             "source_url": d["source_url"], "size_table_raw": table,
                                             "description": desc, "images": imgs[:20]},
                                            ensure_ascii=False) + "\n")
                        got_t += bool(table)
                        got_i += bool(imgs)
                    except Exception as e:
                        print(f"    ! {d.get('product_no')} {type(e).__name__}", flush=True)
                    time.sleep(args.delay)
            print(f"  {brand}: {len(todo)}벌 열었다 · 표 {got_t} · 사진 {got_i}", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
