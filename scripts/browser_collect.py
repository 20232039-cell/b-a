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
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = CRAWL / "browser"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
SIZE_WORD = re.compile(r"어깨|가슴|총장|소매|밑단|허리|허벅지|밑위|암홀|엉덩이|화장"
                       r"|shoulder|chest|length|sleeve|waist|thigh|rise|hem", re.I)
PHOTO = re.compile(r"/web/product/|/detailimg/|/product/.*\.(?:jpg|jpeg|png|webp)", re.I)
# 눌러서 나타난 그림 가운데 읽을 만한 것. 아이콘·꾸밈 그림에 OCR 예산을 쓰지 않는다.
_SKIP_IMG = re.compile(r"\.(?:svg|gif|ico)(?:\?|$)|/icon|/btn|/banner|blank\.|spacer|"
                       r"1x1|loading|placeholder|data:image", re.I)


def keep_size_image(u: str) -> bool:
    return bool(u) and not u.startswith("data:") and not _SKIP_IMG.search(u)


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
        out = grid_to_table(grid)
        if out:
            return out
    return {}


def grid_to_table(grid: list[list[str]]) -> dict[str, list[str]]:
    """표 격자를 {라벨: [값…]} 로. 사이즈 이름 줄·열은 「_names」로 함께 담는다.

    이름을 버리면 앱 사이즈 칩에 무엇을 적을지 알 수 없다. 자바스크립트로 표를 그리는 매장은
    서버 HTML 에 표가 없어 이 길이 유일한데(한 매장 324벌), 여기서 이름 줄을 떨구고 있었다
    (2026-09-10). 전치형은 라벨이 아닌 첫 줄이, 정방향은 라벨이 아닌 첫 열이 이름이다.
    """
    out: dict[str, list[str]] = {}
    # 전치형 — 각 행의 첫 칸이 라벨
    width = 0
    for row in grid:
        if len(row) >= 2 and SIZE_WORD.search(row[0]):
            out[row[0]] = row[1:]
            width = max(width, len(row) - 1)
    if len(out) >= 2:
        for row in grid:                      # 라벨이 아닌 줄 가운데 칸 수가 맞는 첫 줄이 이름 줄
            if len(row) >= 2 and not SIZE_WORD.search(row[0]):
                cand = [c for c in row[1:] if c.strip()]
                if len(cand) == width:
                    out["_names"] = cand
                    break
        return out
    # 정방향 — 첫 행이 라벨
    head = grid[0]
    if sum(1 for h in head if SIZE_WORD.search(h)) >= 2:
        name_col = next((j for j, h in enumerate(head) if not SIZE_WORD.search(h)), None)
        for j, h in enumerate(head):
            if not SIZE_WORD.search(h):
                continue
            out[h] = [r[j] for r in grid[1:] if len(r) > j]
        if len(out) >= 2:
            if name_col is not None:
                nm = [r[name_col].strip() for r in grid[1:] if len(r) > name_col and r[name_col].strip()]
                n = max((len(v) for v in out.values()), default=0)
                if len(nm) == n and n >= 2:
                    out["_names"] = nm
            return out
    return {}


# 누를 것의 이름. 사람이 상품 페이지를 직접 눌러 보고 알려 준 것들이다(2026-09-17):
# 「사이즈 차트」 토글 · 「사이즈 가이드」 버튼 · 「SIZE GUIDE」 · 「INFORMATION」 아코디언.
# 앞엣것일수록 먼저 누른다 — 사이즈라고 이름 붙은 것이 표를 열 확률이 높고, 누를 횟수에
# 상한이 있어서 순서가 곧 우선순위다.
TOGGLE_WORDS = (
    r"size\s*chart", r"사이즈\s*차트", r"size\s*guide", r"사이즈\s*가이드",
    r"size\s*info(?:rmation)?", r"사이즈\s*정보", r"사이즈\s*표", r"size\s*&?\s*fit",
    r"measurement", r"실측", r"sizing", r"사이즈",
    r"product\s*info(?:rmation)?", r"상품\s*정보", r"인포메이션", r"information",
    r"item\s*details?", r"상세\s*정보", r"상세\s*보기", r"details?", r"spec",
)
_TOGGLE_RX = [re.compile(w, re.I) for w in TOGGLE_WORDS]
# 무엇을 「누를 수 있는 것」으로 볼까. 버튼·링크·탭만 보면 놓친다 — 매장 토글은 dt·th·li
# 이거나 class 에 toggle·accordion 이 든 div 인 경우가 많고, 어떤 것은 태그로는 아무 표시가
# 없고 **손가락 커서**로만 「눌러도 된다」고 말한다(2026-09-17 실측: 한 매장의 「Size Info」가
# li.menu-title 이었다). 그래서 커서까지 본다. 브라우저 안에서 한 번에 훑고 표를 붙여 둔다.
_FIND_TOGGLES = """(pats) => {
  const rx = pats.map(p => new RegExp(p, 'i'));
  let n = 0;
  for (const e of document.querySelectorAll('*')) {
    if (n > 60) break;
    delete e.dataset.ccrToggle;
    const t = (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
    // 글이 길면 토글이 아니라 그 안의 본문이다. 안내 「문단」을 눌러 봐야 아무 일도
    // 안 일어나고 예산만 먹는다(2026-09-05 그 매장의 「…버튼을 클릭하시면」 문단).
    if (!t || t.length > 40) continue;
    let pri = -1;
    for (let i = 0; i < rx.length; i++) if (rx[i].test(t)) { pri = i; break; }
    if (pri < 0) continue;
    const tag = e.tagName.toLowerCase();
    const cls = String(e.className || '');
    const clickable =
      ['a', 'button', 'summary', 'dt', 'th', 'label'].includes(tag)
      || e.hasAttribute('onclick') || e.hasAttribute('role')
      || /toggle|accordion|accodi|tab|guide|btn|menu-title/i.test(cls)
      || getComputedStyle(e).cursor === 'pointer';
    if (!clickable) continue;
    e.dataset.ccrToggle = String(pri);
    n++;
  }
  return n;
}"""


def _img_srcs(page) -> list[str]:
    """지금 화면에 걸린 그림 주소 — 게으른 그림은 data-src 에 들어 있다."""
    out: list[str] = []
    for fr in list(page.frames):
        try:
            els = fr.query_selector_all("img")
        except Exception:
            continue
        for im in els:
            try:
                s = (im.get_attribute("src") or im.get_attribute("data-src")
                     or im.get_attribute("data-original") or "")
            except Exception:
                continue
            if s and s not in out:
                out.append(s)
    return out


def _toggle_candidates(page) -> list[tuple[object, str]]:
    """누를 만한 것을 우선순위 순으로. (요소, 다시 안 누르려고 쓰는 표) 짝."""
    found: list[tuple[int, object, str]] = []
    for fr in list(page.frames):
        try:
            fr.evaluate(_FIND_TOGGLES, list(TOGGLE_WORDS))
            els = fr.query_selector_all("[data-ccr-toggle]")
        except Exception:
            continue
        for el in els:
            try:
                pri = int(el.get_attribute("data-ccr-toggle") or "99")
                t = " ".join((el.inner_text() or "").split())
            except Exception:
                continue
            if t:
                found.append((pri, el, t.lower()))
    found.sort(key=lambda x: x[0])
    return [(el, key) for _, el, key in found]



def _scroll_through(page) -> None:
    """끝까지 내렸다 올린다 — 게으른 그림·표·토글이 그려지게."""
    try:
        for _ in range(6):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight/5)")
            page.wait_for_timeout(450)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass


def open_details(page) -> list[str]:
    """상세를 실제로 그리게 만들고, **눌러서 새로 나타난 그림 주소**를 돌려준다.

    diafvine 은 사이즈표가 DETAILS 탭 안에 있어, 페이지만 열고 기다리면 #prdDetail 이
    공백 문자 8~10자뿐이다(2026-09-04 사람이 화면으로 확인). 탭을 누르고 끝까지 내려
    게으른 이미지·표까지 그려지게 한다.

    2026-09-17 사람 지적으로 넓혔다 — 「푸쉬버튼은 진짜 없고 나머진 다 토글이나 버튼
    눌러서 들어가야 나오네」. 그때까지 이 함수는 (1) 이름을 여섯 개만 알았고 (2) 버튼·
    링크·탭만 눌렀으며 (3) **열린 뒤에 나타난 그림을 담지 않았다**. 표가 그림으로 된
    매장은 눌러도 소용이 없었던 이유가 이 셋이다.
    """
    url0 = page.url
    # 먼저 한 번 끝까지 내린다 — 토글 자체가 게으르게 그려지는 매장이 있다(2026-09-17
    # 실측: 한 매장의 「Size Info」는 굴리기 전에는 문서에 아예 없었다). 누를 것을 찾기
    # 전에 내려야 한다.
    _scroll_through(page)
    before = set(_img_srcs(page))
    seen: set[str] = set()
    got: list[str] = []

    def take(urls, force=False):
        # force: 접힌 칸 **속**에서 찾은 그림. 아코디언은 내용을 문서에 두고 숨길 뿐이라
        # 누르기 전에도 보이므로 「새로 생겼나」로 걸러 내면 안 된다 — 걸러 냈더니 한
        # 매장의 사이즈표가 통째로 빠졌다(2026-09-17 실측).
        for u in urls:
            if u and u not in got and (force or u not in before):
                got.append(u)

    for _ in range(14):
        pick = None
        for el, key in _toggle_candidates(page):
            if key not in seen:
                pick = (el, key)
                break
        if pick is None:
            break
        el, key = pick
        seen.add(key)
        try:
            if not el.is_visible():
                continue
            el.click(timeout=1500)
            page.wait_for_timeout(900)
        except Exception:
            continue
        # ① 누른 것이 사이즈가이드 **창으로 가는 링크**였으면, 그 창의 그림이 곧 표다.
        #    여기서 받아야 한다 — 되돌아간 뒤에 세면 이미 사라지고 없다.
        if page.url != url0:
            take(_img_srcs(page))
        # ② 접혀 있던 칸이 **이미 문서 안에 있던** 경우. 아코디언은 내용을 숨겨 둘 뿐이라
        #    눌러도 그림이 늘지 않는다(2026-09-17 실측: 한 매장의 SIZE CHART 칸이 그랬다).
        #    그래서 누른 것의 바로 옆·부모 칸 안을 따로 본다. 칸이 너무 크면(그림 여덟 장
        #    넘으면) 상품 사진까지 긁게 되므로 쓰지 않는다.
        panel = []
        try:
            panel = el.evaluate("""e => {
              const pick = x => {
                if (!x) return [];
                const g = [...x.querySelectorAll('img')]
                  .map(i => i.currentSrc || i.getAttribute('src')
                          || i.getAttribute('data-src') || i.getAttribute('data-original'))
                  .filter(Boolean);
                return g.length <= 8 ? g : [];
              };
              return [...pick(e.nextElementSibling), ...pick(e.parentElement)];
            }""")
        except Exception:
            panel = []
        if page.url == url0:
            take(panel, force=True)
        # 누른 것이 다른 페이지로 가는 링크였으면 되돌아온다 — 안 그러면 남은 토글도,
        # 지금까지 연 것도 통째로 잃는다.
        if page.url != url0:
            try:
                page.goto(url0, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1200)
            except Exception:
                break
    _scroll_through(page)
    return got


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
            got_t = got_i = got_s = 0
            with (OUT / f"{brand}.jsonl").open("a", encoding="utf-8") as fh:
                for d in todo:
                    try:
                        page.goto(d["source_url"], wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(1500)
                        revealed = open_details(page)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            pass
                        table = read_table(page)
                        # 상세 칸이 실제로 차기를 기다린다 — 안 기다리면 아래 body 폴백이
                        # 메뉴를 긁는다.
                        try:
                            page.wait_for_selector("#prdDetail, .xans-product-detail, .detailArea",
                                                   timeout=6000)
                        except Exception:
                            pass
                        # 「가장 긴 글」로 고르면 안 된다. 상세가 비어 있는 페이지에서는 body 가
                        # 늘 이겨서 내비게이션 메뉴와 「유사한 상품 추천」이 설명글로 저장됐다
                        # (2026-09-05 insilence: 260건 전부 그 꼴이라 소재 태깅이 하나도 안 됐다).
                        # 좁은 선택자부터 보고 충분히 긴 첫 것을 쓴다. body 는 마지막 수단이고,
                        # 그마저도 사이즈·소재 낌새가 있을 때만 받는다.
                        desc = ""
                        for fr in list(page.frames):
                            for sel in ("#prdDetail", ".xans-product-detail", ".detailArea",
                                        "#detail", ".prd-detail"):
                                try:
                                    el = fr.query_selector(sel)
                                except Exception:
                                    continue
                                if not el:
                                    continue
                                t = " ".join((el.inner_text() or "").split())
                                if len(t) >= 120:
                                    desc = t[:8000]
                                    break
                            if desc:
                                break
                        if not desc:
                            try:
                                t = " ".join((page.inner_text("body") or "").split())
                            except Exception:
                                t = ""
                            if re.search(r"총장|가슴|어깨|소매|실측|단면|소재|혼용률|겉감|FABRIC", t, re.I):
                                desc = t[:8000]
                        imgs = []
                        for im in page.query_selector_all("img"):
                            u = im.get_attribute("src") or im.get_attribute("data-src") or ""
                            if u and PHOTO.search(u) and u not in imgs:
                                imgs.append(u)
                        # 눌러서 나타난 그림 — 표가 그림으로 된 매장은 이것이 전부다.
                        # PHOTO 로 거르지 않는다. 사이즈표 그림은 상품 사진과 다른 자리에
                        # 올라가 있는 경우가 많아서, 거르면 정작 필요한 것이 빠진다.
                        size_imgs = [urljoin(page.url, u) for u in revealed
                                     if keep_size_image(u)]
                        size_imgs = list(dict.fromkeys(size_imgs))[:12]
                        fh.write(json.dumps({"brand_slug": brand, "product_no": d.get("product_no"),
                                             "source_url": d["source_url"], "size_table_raw": table,
                                             "description": desc, "images": imgs[:20],
                                             "size_images": size_imgs},
                                            ensure_ascii=False) + "\n")
                        got_t += bool(table)
                        got_i += bool(imgs)
                        got_s += bool(size_imgs)
                    except Exception as e:
                        print(f"    ! {d.get('product_no')} {type(e).__name__}", flush=True)
                    time.sleep(args.delay)
            print(f"  {brand}: {len(todo)}벌 열었다 · 표 {got_t} · 사진 {got_i} · "
                  f"눌러서 나온 그림 {got_s}", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
