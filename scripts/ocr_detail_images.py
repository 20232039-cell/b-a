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
from PIL import Image

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


# 매장 원본 서버와 공용 이미지 CDN 을 같은 잣대로 묶을 이유가 없다. 상세 이미지의 상당수는
# 카페24가 수만 매장에 공용으로 쓰는 이미지 서버에서 온다(프리즘웍스 81% · 다이아프바인 99%,
# 2026-09-04 실측). 매장 도메인은 지금 속도 그대로 두고, 공용 CDN 만 조금 빠르게 받는다.
CDN_HOSTS = ("cafe24img.poxo.com", "img.cafe24.com")
CDN_SUFFIX = (".cafe24img.com", ".poxo.com")


def is_cdn(host: str) -> bool:
    return host in CDN_HOSTS or host.endswith(CDN_SUFFIX)


def polite_get(url: str, delay: float, cdn_delay: float | None = None) -> bytes | None:
    host = urlparse(url).netloc
    if cdn_delay is not None and is_cdn(host):
        delay = cdn_delay
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
MIN_W = 1400
# 세로로 길게 이어붙인 상세 띠(kirsh 800x10958 등)까지 2배로 키우면 8.8M → 35M 픽셀이 된다.
# 표가 든 이미지는 대개 2M 픽셀 안쪽이라 거기까지만 키운다.
MAX_PIXELS = 3_500_000


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
        elif w < MIN_W and w * h <= MAX_PIXELS:
            # 작은 이미지는 키운다. 줄이기만 하던 시절, 1000px 짜리 사이즈 표는 값이
            # 통째로 뭉갰다(2026-09-04 diafvine: 1배 `M 405 305 165 % 2` → 2배
            # `M 405 305 185 99 32`). 표의 글자는 8~10px 라 tesseract 가 필요로 하는
            # 높이에 못 미친다 — 사진은 어차피 읽을 게 없으니 손해가 없다.
            im = im.resize((w * 2, h * 2), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def dark_bands(im, min_h=8, min_w=200, thr=120):
    """검정 바탕 가로 띠의 (top, bottom) 목록.

    사이즈 표의 머리줄(라벨이 든 줄)은 매장 절반이 검정 바탕에 흰 글자다. tesseract 는
    어두운 바탕 글자를 못 읽어서, 값(2행·3행)은 멀쩡히 나오는데 라벨만 통째로 잡음이 됐다
    (2026-09-04 diafvine 실측: 값 `M 405 305 185 99 32` 는 정확, 머리줄은 `on ern = oat a`).
    라벨이 없으면 표가 성립하지 않으니 그 브랜드가 통째로 0% 가 된다.
    """
    from PIL import ImageStat
    w, h = im.size
    if w < min_w:
        return []
    means = [ImageStat.Stat(im.crop((0, y, w, y + 1))).mean[0] for y in range(h)]
    runs, start = [], None
    for i, m in enumerate(means):
        if m < thr and start is None:
            start = i
        elif m >= thr and start is not None:
            if i - start >= min_h:
                runs.append((start, i))
            start = None
    if start is not None and h - start >= min_h:
        runs.append((start, h))
    # 사진(검은 옷)이 통째로 어두운 경우를 거른다 — 띠는 얇다
    return [(a, b) for a, b in runs if b - a <= 90][:6]


def ocr_dark_bands(im) -> str:
    """어두운 띠만 잘라 3배로 키우고 반전해서 읽는다. 흰 글자가 검은 글자가 된다."""
    from PIL import Image, ImageOps
    out = []
    from PIL import ImageStat
    for a, b in dark_bands(im):
        # 가로로도 잘라 낸다 — 표 머리줄 양옆의 흰 여백을 같이 뒤집으면 검은 판이 되어
        # tesseract 가 줄 자체를 못 찾는다(2026-09-04: 안 자르면 결과 0자, 자르면 라벨 전부).
        cols = [ImageStat.Stat(im.crop((x, a, x + 1, b))).mean[0] for x in range(im.width)]
        dark_x = [x for x, m in enumerate(cols) if m < 150]
        if not dark_x:
            continue
        x0, x1 = max(0, dark_x[0] - 2), min(im.width, dark_x[-1] + 3)
        if x1 - x0 < 120:
            continue
        band = im.crop((x0, max(0, a - 2), x1, min(im.height, b + 2)))
        band = band.resize((band.width * 3, band.height * 3), Image.LANCZOS)
        band = ImageOps.invert(band)
        # psm 은 한 값으로 고정하면 안 된다 — 같은 띠가 2px 여백 차이로 7 에서는 0자,
        # 11(성긴 글자)에서는 라벨 전부가 나왔다(2026-09-04). 셋을 돌려 가장 긴 것을 쓴다.
        best = ""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as f:
            band.save(f.name)
            for psm in ("7", "6", "11"):
                try:
                    t = subprocess.run(
                        ["tesseract", f.name, "-", "-l", "kor+eng", "--psm", psm],
                        capture_output=True, text=True, timeout=60,
                        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                    ).stdout
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
                if len(t.strip()) > len(best):
                    best = t
        t = " ".join(best.split())
        if len(re.sub(r"[^가-힣A-Za-z0-9]", "", t)) >= 4:
            out.append(t)
    return "\n".join(out)


SIZE_HINT = re.compile(r"총장|어깨|가슴|소매|밑단|허리|허벅지|밑위|암홀|SIZE|실측|단면|길이", re.I)


def ocr_slice(im, top, bot, scale=2, psm="6") -> str:
    """이미지의 한 띠를 잘라 확대해서 읽는다."""
    c = im.crop((0, top, im.width, bot))
    if scale != 1:
        c = c.resize((c.width * scale, c.height * scale), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as f:
        c.save(f.name)
        try:
            return subprocess.run(
                ["tesseract", f.name, "-", "-l", "kor+eng", "--psm", psm],
                capture_output=True, text=True, timeout=90,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            ).stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""


def ocr_tall(im) -> str:
    """세로로 긴 상세 띠 — 성기게 훑고, 표가 있을 만한 곳만 촘촘히 다시 읽는다.

    통짜로 --psm 6 에 넣으면 큰 도식(옷 그림 + 화살표 라벨)과 표가 같은 「균일 블록」에
    들어가 서로를 뭉갠다(2026-09-04 kirsh 800x9521: 통짜 114초에 337자, 표는 유실).
    1500px 띠로 훑으면 「SIZE INFO / 어깨 / 소매 / 가슴 / 총장」까지는 나오지만 값이
    깨지고, 같은 자리를 300px 조각 2배로 다시 읽으면 `총장 가슴 어깨 소매 / 1 46 37`
    까지 정확히 나온다. 그래서 두 단계로 나눈다 — 성긴 훑기는 싸고, 정밀 판독은
    걸린 띠에만 쓴다.
    """
    COARSE, FINE, OVER = 1500, 300, 40
    out = []
    for top in range(0, im.height, COARSE):
        bot = min(im.height, top + COARSE)
        rough = ocr_slice(im, top, bot, scale=1)
        if not SIZE_HINT.search(rough):
            out.append(rough)
            continue
        # 표가 있을 만한 띠 — 촘촘히 다시
        fine = []
        for y in range(top, bot, FINE - OVER):
            fine.append(ocr_slice(im, y, min(bot, y + FINE), scale=2))
        out.append("\n".join(fine))
    return "\n".join(out)


SIZE_TAIL = re.compile(r"총장|어깨|가슴|소매|밑단|허리|밑위|암홀|허벅지|"
                       r"size\s*guide|size\s*info|사이즈\s*정보|실측", re.I)


def _cap(texts: list[str], limit: int = 12000) -> str:
    """앞에서부터 잘라 내면 안 된다 — 사이즈 표는 상세 이미지 맨 끝에 있다.

    6000자에서 앞부분만 남기던 시절 dnsr·easy-no-easy 는 「SIZE GUIDE (CM) SHOULDER
    CHEST SLEEVE LENGTH / OS - 37.5 - 53」이 잘려 나가 「원본에 실측이 없다」로
    세어졌다(사람이 화면으로 짚어 줌, 2026-09-05). 넘치면 표가 있는 쪽을 남긴다."""
    full = "\n".join(texts)
    if len(full) <= limit:
        return full
    tail = None
    for m in SIZE_TAIL.finditer(full):
        tail = m
    if tail is None:
        return full[:limit]
    lo = max(0, tail.start() - limit // 3)
    return full[:limit // 3] + "\n…\n" + full[lo:lo + (limit * 2) // 3]


def ocr_bytes(data: bytes) -> str:
    """tesseract 로 한 장. --psm 6(균일 블록)이 상품 상세의 세로 긴 이미지에 가장 안정적이었다."""
    data = preprocess(data)
    # 세로로 긴 띠는 통짜로 못 읽는다 — 두 단계 훑기로 넘긴다(ocr_tall 주석).
    try:
        from PIL import Image as _I
        import io as _io
        _im = _I.open(_io.BytesIO(data)).convert("L")
        if _im.height >= 2200:
            out = ocr_tall(_im)
            bands = ocr_dark_bands(_im)
            txt = re.sub(r"[ \t]+", " ", out)
            txt = re.sub(r"\n{2,}", "\n", txt).strip()
            lines = [ln for ln in txt.splitlines() if len(re.sub(r"[^가-힣A-Za-z0-9]", "", ln)) >= 2]
            joined = "\n".join(lines)
            if bands:
                joined = bands + "\n" + joined
            words = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", joined)
            return joined if len(words) >= 3 else ""
    except Exception:
        pass
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
    bands = ""
    try:
        from PIL import Image as _I
        import io as _io
        bands = ocr_dark_bands(_I.open(_io.BytesIO(data)).convert("L"))
    except Exception:
        bands = ""
    txt = re.sub(r"[ \t]+", " ", out)
    txt = re.sub(r"\n{2,}", "\n", txt).strip()
    # 한 줄에 글자가 거의 없으면(장식·사진) 버린다
    lines = [ln for ln in txt.splitlines() if len(re.sub(r"[^가-힣A-Za-z0-9]", "", ln)) >= 2]
    joined = "\n".join(lines)
    # 띠(라벨 줄)를 본문 앞에 둔다 — 표 파서는 「라벨 줄 다음에 값 줄」 순서를 본다.
    if bands:
        joined = bands + "\n" + joined
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
                  workers: int = 1, cdn_delay: float | None = None, redo: bool = False) -> dict:
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
    # --redo: 예전에 「앞 3~5장만」 읽고 끝난 상품은 done 에 들어 있어 12장짜리 재시도에서 아예 빠진다.
    # 사이즈가 아직 없고 읽은 그림이 읽을 수 있는 그림보다 적으면 done 에서 빼 다시 읽는다
    # (2026-09-04: 사이즈 없는 옷 875벌 중 617벌이 이 경우였다 — far-from-what 은 11장 중 2장만 읽었다).
    if redo:
        sized_urls = set()
        sp = CRAWL_DIR.parent / "product_sizes.json"
        if sp.exists():
            sized_urls = set(json.loads(sp.read_text(encoding="utf-8")))
        read_n: dict[int, int] = {}
        if main.exists():
            for l in main.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    o = json.loads(l)
                    read_n[o["product_no"]] = len(o.get("images") or [])
        for no, d in latest.items():
            if no not in done or d.get("source_url") in sized_urls:
                continue
            avail = len([u for u in (d.get("detail_images") or []) if not SKIP_NAME.search(u.rsplit("/", 1)[-1])])
            if read_n.get(no, 0) < min(max_images, avail):
                done.discard(no)

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
                data = polite_get(url, delay, cdn_delay)
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
            ocr_text = _cap(texts)
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
    ap.add_argument("--redo", action="store_true", help="이미 읽었지만 그림을 덜 읽은 상품을 다시 읽는다(사이즈 없는 것만)")
    ap.add_argument("--cdn-delay", type=float, default=0.25, help="공용 이미지 CDN(cafe24img) 에만 쓰는 대기")
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
        futs = [ex.submit(process_brand, s, not args.all, args.max_images, args.delay, log, shard, out_dir, select, inner, args.cdn_delay, args.redo) for s in slugs]
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
