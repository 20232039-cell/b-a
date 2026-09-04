"""상세 이미지 OCR — 글 대신 이미지로 설명하는 매장의 소재·사이즈·디테일을 글로 되돌린다.

왜 필요한가: cafe24 상세(#prdDetail)는 대개 이미지다. 27,875건 중 글이 있는 건 일부고
(matin-kim 1,170건 전부 글 0자), 소재·세탁·사이즈 실측은 그 이미지 안에 활자로 박혀 있다.
tesseract(kor+eng) 가 활자로 박힌 한글은 잘 읽는다 — 샘플에서 장당 0.8초, 소재·제조국·사이즈
표까지 나왔다(2026-09-02). 손글씨·사진 위 글자는 못 읽고, 그건 여기서 기대하지 않는다.

무엇을 읽나: crawl/<slug>.jsonl 의 detail_images. 상품마다 앞 MAX_IMAGES 장 — 글이 나오는 장은
앞 세 장에 고르게 퍼져 있었다(위치별 47/87·65/79·42/56, 2026-09-02). 파일명이 배송 안내·
이슈 배너(shipping/issue/notice…)인 것과 20KB 미만(아이콘·구분선)은 건너뛴다 — OCR 대상
3,971건의 상세 이미지 중 「shipping info」 913장은 전부 같은 배송 안내 그림이었다.

결과: crawl/ocr/<slug>.jsonl — {product_no, ocr_text, images: [{url, chars}]}.
상품 단위로 바로 쓰고, 다시 돌리면 이미 있는 상품은 건너뛴다.

예의: 이미지 CDN(ecimg.cafe24img.com 등)이라 상세 페이지보다 부담이 덜하지만 호스트당 0.5초를 둔다.

사용:
    py scripts/ocr_detail_images.py                    # 글이 짧은(<80자) 상품만
    py scripts/ocr_detail_images.py --all              # detail_images 있는 상품 전부
    py scripts/ocr_detail_images.py --brands kirsh --max-images 3 --procs 4
    py scripts/ocr_detail_images.py --brands kirsh --shard 2/6 --out-dir _parts   # 6조각 중 2번째
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
CRAWL_DIR = ROOT / "data" / "crawl"
OCR_DIR = CRAWL_DIR / "ocr"
UA = "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"
MIN_BYTES = 20 * 1024
SKIP_NAME = re.compile(r"shipping|delivery|notice|issue|exchange|refund|return|banner|event|coupon|logo|icon|배송|공지|교환|반품", re.I)
MAX_IMAGES = 5
SHORT_TEXT = 80

_last: dict[str, float] = {}
_lock = threading.Lock()


def polite_get(url: str, delay: float) -> bytes | None:
    host = urlparse(url).netloc
    with _lock:
        wait = delay - (time.monotonic() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200 and r.content and "image" in r.headers.get("Content-Type", "image"):
            return r.content
    except requests.RequestException:
        return None
    return None


MAX_W = 1200


def preprocess(data: bytes) -> bytes:
    """흑백 + 가로 1200px 로 줄인다. tesseract 가 픽셀 수에 비례해 느려지는데, 상세 이미지는
    1800px 짜리도 흔하다 — 줄여도 글자 수는 그대로였다(2026-09-04 실측: 4.3s → 1.5s, 1367자 동일)."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        im = im.convert("L")
        if w > MAX_W:
            im = im.resize((MAX_W, max(1, int(h * MAX_W / w))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def ocr_bytes(data: bytes) -> str:
    """tesseract 로 한 장. --psm 6(균일 블록)이 상품 상세의 세로 긴 이미지에 가장 안정적이었다."""
    data = preprocess(data)
    with tempfile.NamedTemporaryFile(suffix=".img", delete=True) as f:
        f.write(data)
        f.flush()
        try:
            # OMP_THREAD_LIMIT=1 — tesseract 가 코어 수만큼 스레드를 열어서, 프로세스 셋이 4코어에서
            # 서로 밟으면 장당 0.8초가 50초가 됐다(2026-09-02 실측). 한 프로세스 한 스레드로 묶는다.
            out = subprocess.run(
                ["tesseract", f.name, "-", "-l", "kor+eng", "--psm", "6"],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            ).stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
    txt = re.sub(r"[ \t]+", " ", out)
    txt = re.sub(r"\n{2,}", "\n", txt).strip()
    # 한 줄에 글자가 거의 없으면(장식·사진) 버린다
    lines = [ln for ln in txt.splitlines() if len(re.sub(r"[^가-힣A-Za-z0-9]", "", ln)) >= 2]
    joined = "\n".join(lines)
    words = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", joined)
    return joined if len(words) >= 3 else ""


def _has_size_table(text: str) -> bool:
    """size_from_ocr 의 파서로 표가 잡히는지 — 잡히면 남은 그림을 읽을 이유가 없다."""
    try:
        from size_from_ocr import from_ocr
    except Exception:
        return False
    try:
        return len(from_ocr(text)[1]) >= 2
    except Exception:
        return False


def load_latest(path: Path) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        latest[d["product_no"]] = d
    return latest


GARMENTS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim", "Skirts", "Dresses", ""}


def load_categories() -> dict[tuple[str, int], str]:
    import csv
    path = ROOT / "data" / "products_full.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {(r["brand_slug"], int(r["product_no"])): r["category"] for r in csv.DictReader(f)}


# 파일 이름이 말해 주는 것 — 이런 그림은 순서와 무관하게 먼저 읽는다
HINT_NAME = re.compile(r"size|detail|info|spec|measure|fabric|\uc0ac\uc774\uc988|\uc2e4\uce21", re.I)


def process_brand(slug: str, only_short: bool, max_images: int, delay: float, log,
                  shard: tuple[int, int] = (0, 1), out_dir: Path | None = None, select: str = "short",
                  workers: int = 1) -> dict:
    """shard=(k, n): 대상을 product_no 순으로 n등분해 k번째만 본다 — kirsh(1,800건)처럼 큰 브랜드를
    러너 여럿에 나눌 때. out_dir 를 주면 crawl/ocr/<slug>.jsonl 대신 out_dir/<slug>.<k>.jsonl 조각으로 쓴다
    (Actions 의 collect 가 조각을 합친다). 이미 끝난 상품 판단은 항상 crawl/ocr/<slug>.jsonl 기준."""
    src = CRAWL_DIR / f"{slug}.jsonl"
    main = OCR_DIR / f"{slug}.jsonl"
    k, n = shard
    out = (out_dir / f"{slug}.{k}.jsonl") if out_dir else main
    latest = load_latest(src)
    done: set[int] = set()
    if main.exists():
        done = {json.loads(l)["product_no"] for l in main.read_text(encoding="utf-8").splitlines() if l.strip()}
    cats = load_categories() if select == "no-size" else {}
    todo = []
    for no, d in sorted(latest.items(), key=lambda kv: int(kv[0])):
        if no in done or (d.get("price") or 0) <= 1000 or not d.get("detail_images"):
            continue
        if select == "no-size":
            # 사이즈 표 없는 옷만 — 설명 길이와 무관. 사이즈 수집률을 올리는 2차 OCR(사람 결정 2026-09-03)
            if d.get("size_table") or cats.get((slug, int(no)), "") not in GARMENTS:
                continue
        elif only_short and len(d.get("description", "")) >= SHORT_TEXT:
            continue
        todo.append(d)
    todo = todo[k::n]
    want_size = select == "no-size"
    log(f"[{slug}] OCR 대상 {len(todo)} (이미 {len(done)}, 조각 {k + 1}/{n})")
    n_img = n_txt = 0
    counters = {"img": 0, "txt": 0, "done": 0, "early": 0}
    wlock = threading.Lock()

    def one(d: dict) -> str:
            texts, imgs = [], []
            # 소재·디테일·사이즈표는 상세 이미지의 「뒤쪽」에 오는 경우가 많다(사람 지적 2026-09-04).
            # 앞에서 자르면 착용컷만 읽고 정작 필요한 표를 놓친다. 그래서 뒤에서부터 고르되,
            # 파일 이름에 size/detail/info 가 든 그림은 어디에 있든 먼저 읽는다.
            cand = [u for u in d["detail_images"] if not SKIP_NAME.search(u.rsplit("/", 1)[-1])]
            hinted = [u for u in cand if HINT_NAME.search(u)]
            rest = [u for u in cand if u not in hinted]
            picked = hinted[:max_images] + rest[-(max_images - len(hinted[:max_images])):] if max_images > len(hinted[:max_images]) else hinted[:max_images]
            for url in picked:
                data = polite_get(url, delay)
                if not data or len(data) < MIN_BYTES:
                    continue
                with wlock:
                    counters["img"] += 1
                t = ocr_bytes(data)
                imgs.append({"url": url, "chars": len(t)})
                if t:
                    texts.append(t)
                # 사이즈 표를 이미 얻었으면 남은 그림은 읽지 않는다 — 표는 대개 한 장에 다 있는데,
                # 12장을 끝까지 읽느라 시간의 절반을 버리고 있었다(2026-09-04).
                if want_size and texts and _has_size_table("\n".join(texts)):
                    with wlock:
                        counters["early"] += 1
                    break
            ocr_text = "\n".join(texts)[:6000]
            with wlock:
                counters["done"] += 1
                if ocr_text:
                    counters["txt"] += 1
                if counters["done"] % 100 == 0:
                    log(f"[{slug}] … {counters['done']}/{len(todo)} · 글 나온 상품 {counters['txt']} · 표 얻고 조기 종료 {counters['early']}")
            return json.dumps({"brand_slug": slug, "product_no": d["product_no"], "ocr_text": ocr_text, "images": imgs}, ensure_ascii=False)

    with out.open("a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for line in ex.map(one, todo):
                f.write(line + "\n")
                f.flush()
    n_img, n_txt = counters["img"], counters["txt"]
    log(f"[{slug}] 끝 — 상품 {len(todo)} · 이미지 {n_img} · 글 나온 상품 {n_txt} · 조기 종료 {counters['early']}")
    return {"slug": slug, "products": len(todo), "images": n_img, "with_text": n_txt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--all", action="store_true", help="설명 길이와 무관하게 detail_images 있는 상품 전부")
    ap.add_argument("--max-images", type=int, default=MAX_IMAGES)
    ap.add_argument("--procs", type=int, default=3, help="동시에 볼 브랜드 수 (tesseract 가 CPU 를 쓴다 — 코어 수 이하로)")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--shard", default="1/1", help="k/n — 대상을 n등분해 k번째(1부터)만 (Actions 샤딩)")
    ap.add_argument("--out-dir", help="조각 파일을 쓸 폴더 (crawl/ocr/<slug>.jsonl 대신 <slug>.<k>.jsonl)")
    ap.add_argument("--select", default="short", choices=["short", "all", "no-size"], help="short=설명 짧은 것(기본) · all=전부 · no-size=사이즈 표 없는 옷")
    args = ap.parse_args()
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    k, n = (int(x) for x in args.shard.split("/"))
    shard = (k - 1, n)
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    slugs = args.brands or sorted(p.stem for p in CRAWL_DIR.glob("*.jsonl") if not p.name.startswith("_"))
    lock = threading.Lock()

    def log(msg):
        with lock:
            print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.procs)) as ex:
        select = "all" if args.all else args.select
            # 브랜드가 하나면(Actions 는 잡마다 한 브랜드) 그 안에서 상품을 나눠 돌린다 — 러너 4코어를
        # 하나만 쓰고 있었다. 호스트 속도는 polite_get 이 호스트별로 잠가 지킨다.
        inner = args.procs if len(slugs) == 1 else 1
        outer = 1 if len(slugs) == 1 else args.procs
        futs = [ex.submit(process_brand, s, not args.all, args.max_images, args.delay, log, shard, out_dir, select, inner) for s in slugs]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                log(f"예외: {e!r}")
    tot_p = sum(r["products"] for r in results)
    tot_t = sum(r["with_text"] for r in results)
    log(f"전부 끝 — 상품 {tot_p} · 글 나온 상품 {tot_t} · {round(time.time() - started)}s")


if __name__ == "__main__":
    main()
