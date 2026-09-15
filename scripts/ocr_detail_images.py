"""상세 이미지 OCR — 글 대신 이미지로 설명하는 매장의 소재·사이즈·디테일을 글로 되돌린다.

왜 필요한가: cafe24 상세(#prdDetail)는 대개 이미지다. 27,875건 중 글이 있는 건 일부고
(matin-kim 1,170건 전부 글 0자), 소재·세탁·사이즈 실측은 그 이미지 안에 활자로 박혀 있다.
tesseract(kor+eng) 가 활자로 박힌 한글은 잘 읽는다 — 샘플에서 장당 0.8초, 소재·제조국·사이즈
표까지 나왔다(2026-09-02). 손글씨·사진 위 글자는 못 읽고, 그건 여기서 기대하지 않는다.

무엇을 읽나: crawl/<slug>.jsonl 의 detail_images(없으면 gallery). 상품마다 앞 MAX_IMAGES 장 — 글이 나오는 장은
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
import collections
import functools
import io
import base64
import math
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlparse

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CRAWL_DIR = ROOT / "data" / "crawl"
OCR_DIR = CRAWL_DIR / "ocr"
UA = "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"
MIN_BYTES = 20 * 1024
# 안내문 그림은 읽어도 소용없다. 다만 「logo」·「icon」은 상품 이름에도 들어간다 —
# 수집기에서 고친 것과 같은 함정이다(2026-09-05). the-coldest-moment 의 되찾은 그림
# 「상세_TCM mini logo pocket work jacket(BLACK).jpg」이 여기서 다시 버려져, 그림을
# 되찾아 놓고도 27벌을 못 읽었다. 짧은 이름에서 구분자에 붙은 낱말만 자산으로 본다.
SKIP_NAME = re.compile(r"shipping|delivery|notice|issue|exchange|refund|return|banner|event|coupon"
                       r"|배송|공지|교환|반품", re.I)
_ASSET_WORD = re.compile(r"(?:^|[/_.\-])(?:ico|icon|btn|logo|blank|spacer|badge|arrow)s?"
                         r"(?:[0-9]|[/_.\-]|$)", re.I)
_SIZE_WORD = re.compile(r"size|chart|guide|measure|사이즈|실측|치수", re.I)


# 파이썬 str.splitlines() 는 U+0085(NEL)·U+2028·U+2029 도 줄바꿈으로 센다. json.dumps 는
# ensure_ascii=False 면 그것들을 그대로 흘리므로, 그런 글자가 든 줄은 **읽는 쪽에서
# 조각나고 통째로 버려진다** — 아무 소리 없이. 2026-09-15 에 wiggle-wiggle 에서 잡았다:
# 매장이 준 그림 주소에 잘못 디코드된 한글 자모가 섞여 0x85 바이트가 U+0085 로 들어갔고,
# 상품 한 벌이 조각 셋으로 흩어져 사라졌다. 쓸 때 escape 해 둔다 — 읽는 쪽 서른두 자리를
# 전부 고치는 것보다 한 자리를 막는 쪽이 확실하다.
_LINE_SEPS = str.maketrans({"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"})


def jsonl_line(obj) -> str:
    """JSONL 한 줄 — 줄바꿈으로 세어지는 글자를 escape 해서 낸다."""
    return json.dumps(obj, ensure_ascii=False).translate(_LINE_SEPS)


def skip_image(url: str) -> bool:
    name = re.sub(r"\?.*$", "", url).rsplit("/", 1)[-1]
    if SKIP_NAME.search(name):
        return True
    stem = re.sub(r"\.[a-z0-9]{2,4}$", "", name, flags=re.I)
    if _SIZE_WORD.search(stem):
        return False
    return len(stem) <= 24 and bool(_ASSET_WORD.search(stem))
MAX_IMAGES = 5
# 고른 그림에서 이만큼도 안 나오면 그림을 더 본다(글자 수).
LOW_YIELD = 80
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


# 카페24는 사진 한 장을 tiny·small·medium·big 네 칸에 나눠 두는데, 매장에 따라 큰 칸이
# 아예 없다. 크롤이 목록에서 받은 주소를 무조건 `/big/` 으로 올려 적어 왔기 때문에
# (crawl_cafe24._big), 그런 매장은 상세 그림이 통째로 404 였다 — 판독기가 한 장도 못 받는다.
#   xlim      /big/ 404 → /small/  851x1000
#   matin-kim /big/ 404 → /medium/ 1200x1800
#   stu       /big/ 404 → /small/  1280x1920
# 작은 칸이라고 작은 그림이 아니다. 매장이 원본을 넣어 둔 칸이 어디냐의 문제라서, 내려가도
# 판독에 쓸 만한 크기가 나온다(표본 70장 전부 되살아났다, 2026-09-08).
# 받아 보고 실패했을 때만 한 칸씩 내려 본다 — 되는 길에는 요청이 늘지 않는다.
_BIG_SLOT = re.compile(r"/web/product/(extra/)?big/")


def size_fallbacks(url: str) -> list[str]:
    if not _BIG_SLOT.search(url):
        return []
    return [_BIG_SLOT.sub(lambda m: f"/web/product/{m.group(1) or ''}{slot}/", url)
            for slot in ("medium", "small", "tiny")]


def _get_once(url: str, delay: float, cdn_delay: float | None) -> bytes | None:
    # 주소가 아니라 그림 자체가 글로 박혀 있는 경우 — 「data:image/jpeg;base64,...」.
    # 매장이 상세컷을 HTML 안에 넣어 두면 detail_images 에 이 꼴로 들어온다. requests 에
    # 넘기면 예외로 죽어 그 상품은 글을 한 자도 못 얻는다(2026-09-15, 판매중 옷 194벌이
    # 그래서 사이즈가 비어 있었다 — plac 111·munn 74). 받아 올 것 없이 그 자리에서 푼다.
    if url.startswith("data:"):
        head, _, body = url.partition(",")
        if not body or "image" not in head:
            return None
        try:
            return base64.b64decode(body) if "base64" in head else unquote_to_bytes(body)
        except Exception:
            return None
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


def polite_get(url: str, delay: float, cdn_delay: float | None = None) -> bytes | None:
    data = _get_once(url, delay, cdn_delay)
    if data is not None:
        return data
    for alt in size_fallbacks(url):
        data = _get_once(alt, delay, cdn_delay)
        if data is not None:
            return data
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


def dark_bands(im, min_h=8, min_w=200, thr=150):
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
    # 양옆 여백을 빼고 가운데만 잰다. 표 머리줄은 검은 판이지만 좌우에 흰 여백이 남아
    # 줄 전체 평균이 문턱을 넘나들면서 한 띠가 둘로 쪼개졌다(2026-09-05 diafvine 1048:
    # 536~572 한 줄이 (535,547)·(558,572) 로 갈려 라벨을 통째로 놓쳤다).
    x0, x1 = int(w * 0.15), int(w * 0.85)
    means = [ImageStat.Stat(im.crop((x0, y, x1, y + 1))).mean[0] for y in range(h)]
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


LABEL_WORD = re.compile(r"총장|총 ?기장|어깨|가슴|소매|밑단|허리|밑위|암홀|허벅지|엉덩이|"
                        r"화장|기장|둘레|목|품|길이|shoulder|chest|sleeve|length|waist|"
                        r"hip|thigh|hem|rise|bust", re.I)
CELL_NUM = re.compile(r"\d{1,4}(?:[.,]\d{1,2})?|[-–—~]")


def _tsv_rows(im, psm: str = "6") -> list[list[tuple[int, int, int, int, str]]]:
    """tesseract 의 tsv 로 낱말 상자를 받아 줄 단위로 묶는다."""
    import csv as _csv
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as f:
        im.save(f.name)
        try:
            out = subprocess.run(
                ["tesseract", f.name, "stdout", "-l", "kor+eng", "--psm", psm, "tsv"],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            ).stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
    rows: dict[tuple, list] = {}
    for r in _csv.DictReader(io.StringIO(out), delimiter="\t", quoting=_csv.QUOTE_NONE):
        try:
            if int(r["level"]) != 5:
                continue
            t = (r["text"] or "").strip()
            if not t:
                continue
            k = (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))
            rows.setdefault(k, []).append(
                (int(r["left"]), int(r["top"]), int(r["width"]), int(r["height"]), t))
        except (KeyError, ValueError, TypeError):
            continue
    return [sorted(v, key=lambda x: x[0]) for _, v in sorted(rows.items())]


def _cell_crop(src, box, band, ratio: float, cut: float = 0.0):
    """머리줄의 낱말 하나를 자른다 — 세로는 줄 전체 높이를 쓴다.

    낱말 상자 높이만 믿으면 안 된다. 「a어깨」처럼 굵은 알파벳이 붙으면 tesseract 가
    한글의 윗부분을 상자 밖으로 흘려서, 그 상자만 잘라 읽으면 `ani` 가 나온다
    (2026-09-05 diafvine). 줄의 위·아래 끝까지 넉넉히 잘라야 「어깨」가 나온다.
    """
    from PIL import Image
    L, W = int(box[0] * ratio), int(box[2] * ratio)
    if cut:
        L, W = L + int(W * cut), W - int(W * cut)
    T, B = int(band[0] * ratio), int(band[1] * ratio)
    padx, pady = max(3, W // 12), max(4, (B - T) // 3)
    c = src.crop((max(0, L - padx), max(0, T - pady),
                  min(src.width, L + W + padx), min(src.height, B + pady)))
    if c.width < 8 or c.height < 8:
        return None
    k = max(2, min(6, 140 // max(1, c.height)))
    return c.resize((c.width * k, c.height * k), Image.LANCZOS)


def _cell_text(src, box, band, ratio: float, alt=None) -> str:
    """낱말 하나만 크게 키워 읽는다.

    왜 낱말마다 따로 읽나: 라벨이 「a어깨 b가슴 c밑단」처럼 굵은 알파벳과 붙어 있으면
    tesseract 가 줄 단위로는 `aon bas cet` 같은 잡음을 낸다. 같은 글자를 상자 하나씩
    떼어 psm 7 로 읽으면 「어깨·가슴·밑단·소매기장·총장」이 전부 나온다
    (2026-09-05 diafvine 실측). 값 줄은 멀쩡한데 라벨만 잡음이라 표가 통째로 버려졌다.
    원본과 줄인 것 중 어느 쪽이 읽히는지는 매장마다 달라서 둘 다 본다.
    """
    best = ""
    # 「a어깨」의 굵은 알파벳이 한글에 붙어 뭉개진다 — 왼쪽 조금을 떼어 낸 것도 본다.
    tries = [(src, ratio, 0.0), (alt, 1.0, 0.0), (src, ratio, 0.28), (alt, 1.0, 0.28)]
    for img, r, cut in tries:
        if img is None:
            continue
        c = _cell_crop(img, box, band, r, cut)
        if c is None:
            continue
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as f:
            c.save(f.name)
            for psm in ("7", "8"):
                try:
                    t = subprocess.run(
                        ["tesseract", f.name, "-", "-l", "kor+eng", "--psm", psm],
                        capture_output=True, text=True, timeout=30,
                        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                    ).stdout
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
                t = re.sub(r"^[^가-힣A-Za-z]+", "", " ".join(t.split()))
                # 라벨이 보이면 바로 채택 — 길이로만 고르면 잡음이 이긴다
                if LABEL_WORD.search(t):
                    return t
                if len(t) > len(best):
                    best = t
    return best


def _is_value_row(toks) -> bool:
    if len(toks) < 3:
        return False
    num = sum(1 for t in toks if CELL_NUM.fullmatch(t[4]))
    # 첫 칸은 사이즈 이름(S·M·L·95)이라 숫자가 아닐 수 있다
    return num >= 2 and num >= len(toks) - 1


# 머리줄을 이어 붙인 글자에서 찾아 쓸 치수 낱말. 사이저의 어휘(size_labels.json)를
# 그대로 쓴다 — 여기서 목록을 새로 적으면 둘이 갈라진다. 못 읽어 오면 최소한만 쓴다.
def _label_words() -> list[str]:
    out: set[str] = set()
    try:
        import json as _j
        from pathlib import Path as _P
        d = _j.loads((_P(__file__).resolve().parents[1] / "data" / "size_labels.json")
                     .read_text(encoding="utf-8"))
        for k, v in d.items():
            if k.startswith("_") or not isinstance(v, list):
                continue                      # _comment·_ranges_cm 같은 메모 칸은 라벨이 아니다
            out.add(str(k))
            out.update(str(a) for a in v)
    except Exception:
        pass
    out |= {"총장", "총길이", "기장", "어깨", "어깨너비", "어깨단면", "가슴", "가슴단면",
            "허리", "허리단면", "밑단", "밑단단면", "소매", "소매길이", "화장", "암홀",
            "엉덩이", "엉덩이단면", "허벅지", "허벅지단면", "밑위", "팔기장", "팔통"}
    # 머리줄을 공백 없이 이어 붙인 글자에서 찾을 것이므로, 공백·기호 없는 한글 낱말만 쓴다.
    # 긴 것부터 봐야 「가슴단면」이 「가슴」에 먼저 먹히지 않는다.
    words = {w for w in (x.strip() for x in out)
             if 2 <= len(w) <= 8 and re.fullmatch(r"[가-힣]+", w)}
    return sorted(words, key=len, reverse=True)


_LABEL_WORDS = _label_words()


def grid_from_value_rows(rows) -> str:
    """숫자 줄로 열을 먼저 세우고, 그 위 글자들을 열에 나눠 담아 머리줄을 짓는다.

    예전 방식은 「값 줄 바로 위 한 줄이 머리줄이고 토큰 수가 값 줄과 똑같아야 한다」였다.
    한글 표에서는 그 가정이 자주 깨진다 — 머리줄이 두세 줄로 나뉘고(라벨 아래 「(Reglan)」),
    tesseract 가 한글을 글자 단위로 끊어 토큰 수가 안 맞는다. 그러면 표 전체를 버렸다.

    실제로 걸린 표(사람이 앱에서 「프리사이즈로 뜬다」고 짚어 찾음, 2026-09-13):
        (cm)      총장    어깨너비      가슴단면   소매길이
                         (Reglan)             (Reglan)
        48 SIZE   66     78.7        55.9     78.7
        50 SIZE   67.9   81.3        58.4     81.3
    평문으로 읽으면 머리줄 순서가 엉키는데(「어깨너비 소매길이 / 초장 ... / [슴단면」),
    숫자 줄의 x 좌표는 256·424·604·784 로 또렷하다. 그래서 숫자가 기준이 되어야 한다.

    머리줄을 못 읽어도 값은 살린다 — 라벨 자리는 「-」로 비워 둔다. 뒤 파서가 라벨 없는
    칸을 버리므로, 틀린 라벨이 붙는 일은 없다(없는 치수보다 틀린 치수가 나쁘다).
    """
    vr = [i for i, r in enumerate(rows) if _is_value_row(r)]
    if not vr:
        return ""
    first = vr[0]
    # 붙어 있는 값 줄만 한 표로 본다 — 사이에 두 줄 넘게 비면 다른 표다
    body, prev = [], None
    for i in vr:
        if prev is not None and i - prev > 3:
            break
        body.append(rows[i])
        prev = i
    if not body:
        return ""

    def centers(toks):
        return [(t[0] + t[2] / 2.0) for t in toks]

    base = max(body, key=len)
    xs = centers(base)
    if len(xs) < 3:
        return ""
    gaps = sorted(b - a for a, b in zip(xs, xs[1:]))
    if not gaps:
        return ""
    med = gaps[len(gaps) // 2]
    # 사이즈 이름 칸은 「48」「SIZE」처럼 두 토막으로 읽히기도 한다. 가까운 것끼리 묶어
    # 한 열로 본다 — 안 묶으면 최소 간격이 그 둘 사이로 잡혀 허용 오차가 지나치게 좁아지고,
    # 머리줄 글자가 열 밖으로 밀려 잘린다(「어깨너비」가 「깨너비」가 됐다).
    groups: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] < med * 0.35:
            groups[-1].append(x)
        else:
            groups.append([x])
    anchors = [sum(g) / len(g) for g in groups]
    if len(anchors) < 3:
        return ""
    tol = max(20.0, med * 0.5)

    def assign(toks, skip_num=False):
        cols: list[list[tuple[int, int, str]]] = [[] for _ in anchors]
        for (l, t, w, h, txt) in toks:
            if skip_num and CELL_NUM.fullmatch(txt):
                continue
            cx = l + w / 2.0
            k = min(range(len(anchors)), key=lambda j: abs(anchors[j] - cx))
            if abs(anchors[k] - cx) <= tol:
                cols[k].append((t, l, txt))
        return cols

    # 글자마다 top 이 1~2픽셀씩 다르다. 그대로 (top, left) 로 세우면 같은 줄 글자가
    # 뒤섞인다 — 「소매길이」가 「매소길이」로, 「가슴다며」가 「다며슴가」로 나왔다.
    # 줄 높이만큼의 띠로 먼저 묶고, 띠 안에서 왼쪽부터 읽는다.
    _hs = sorted(t[3] for r in rows[:first] for t in r) or [20]
    band_h = max(8, _hs[len(_hs) // 2])

    def _read_order(x):
        return (x[0] // band_h, x[1])

    head = assign([tok for r in rows[:first] for tok in r], skip_num=True)
    labs = []
    for c in head:
        c.sort(key=_read_order)
        raw = re.sub(r"[\s()\[\]]", "", "".join(x[2] for x in c))
        # 이어 붙인 글자가 깨져 있을 수 있다(「가슴다며즘단면」). 아는 낱말이 통째로 들어
        # 있을 때만 그 낱말을 쓴다 — 편집거리로 짐작하지 않는다. 못 고르면 「-」로 비운다.
        hit = ""
        for w in _LABEL_WORDS:
            if w in raw and len(w) > len(hit):
                hit = w
        labs.append(hit or "-")
    if sum(1 for l in labs if l != "-") < 2:
        return ""
    # 라벨을 못 읽은 칸은 **값까지 함께** 버린다. 라벨 자리만 「-」로 비워 두면 뒤 파서가
    # 그 칸만 건너뛰고 값은 그대로 세어, 남은 값이 한 칸씩 밀린다 — 실제로 가슴단면 값
    # 55.9 가 소매길이로 붙었다. 없는 치수보다 틀린 치수가 나쁘다.
    # 첫 칸(사이즈 이름)은 라벨이 없어도 지킨다.
    keep = [0] + [i for i, l in enumerate(labs) if i > 0 and l != "-"]
    if len(keep) < 3:
        return ""
    out = [" ".join(labs[i] if i > 0 else "-" for i in keep)]
    for r in body:
        cols = assign(r)
        line = []
        for i in keep:
            c = sorted(cols[i], key=_read_order)
            # 한 칸 안의 토막은 **붙여서** 낸다 — 공백으로 이으면 뒤 파서가 「48 SIZE」를
            # 두 칸으로 보고 머리줄과 칸 수가 어긋난다(값이 한 칸씩 밀린다).
            line.append("".join(x[2] for x in c) or "-")
        out.append(" ".join(line))
    return "\n".join(out)


def ocr_cell_grid(small, src=None, ratio: float = 1.0, max_tables: int = 3) -> str:
    """값 줄이 보이면 그 바로 위 줄을 낱말 하나씩 다시 읽어 「라벨 줄 + 값 줄」을 짜 준다."""
    rows = _tsv_rows(small)
    if not rows:
        return ""
    out, used = [], 0
    i = 0
    while i < len(rows) and used < max_tables:
        toks = rows[i]
        if not _is_value_row(toks):
            i += 1
            continue
        head = None
        for j in range(i - 1, max(-1, i - 3), -1):
            h = rows[j]
            if len(h) < 2:
                continue
            # 칸 수는 딱 맞아야 한다. 하나만 어긋나도 라벨이 밀려 들어간다
            # (2026-09-05 diafvine 1054: 머리줄 4칸 · 값 5칸 → 밑단 22.5·총장 53 처럼
            # 한 칸씩 밀린 값이 나왔다. 없는 치수보다 틀린 치수가 나쁘다).
            if _is_value_row(h) or len(h) != len(toks):
                break
            head = h
            break
        if head is None:
            i += 1
            continue
        band = (min(b[1] for b in head), max(b[1] + b[3] for b in head))
        labs = [_cell_text(src if src is not None else small, b, band, ratio, alt=small)
                for b in head]
        if sum(1 for l in labs if LABEL_WORD.search(l)) < 2:
            i += 1
            continue
        block = [" ".join(l or "-" for l in labs)]
        n = len(toks)
        gap = 0
        while i < len(rows) and gap <= 2:
            if _is_value_row(rows[i]) and len(rows[i]) == n:
                block.append(" ".join(t[4] for t in rows[i]))
                gap = 0
            else:
                gap += 1
            i += 1
        out.append("\n".join(block))
        used += 1
    if not out:
        # 엄격한 길(머리줄 한 줄 · 토큰 수 일치)이 못 짚으면 숫자 좌표로 열을 세워 본다.
        # 한글 표는 머리줄이 두세 줄로 나뉘고 글자 단위로 끊겨 토큰 수가 자주 안 맞는다.
        return grid_from_value_rows(rows)
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


VALUE_LINE = re.compile(r"(?:(?<!\d)\d{1,3}(?:[.,]\d)?(?!\d)[^\dA-Za-z가-힣]{0,4}){3,}")


def add_cell_grid(joined: str, data: bytes, orig: bytes) -> str:
    """값 줄이 보이는데 라벨이 잡음이면, 머리줄을 낱말 단위로 다시 읽어 앞에 붙인다.

    tsv 한 번 + 머리줄 낱말 수만큼 tesseract 를 더 부르므로(장당 3~4초) 표가 실제로
    보이는 장에서만 돈다. 결과는 앞에 붙이기만 하고 원문은 그대로 둔다 — 파서가
    출처를 골라 쓰므로 이미 되던 것을 망칠 수 없다.
    """
    if not any(VALUE_LINE.search(ln) for ln in joined.splitlines()):
        return joined
    try:
        from PIL import Image as _I
        import io as _io
        small = _I.open(_io.BytesIO(data)).convert("L")
        src = _I.open(_io.BytesIO(orig)).convert("L")
        ratio = src.width / max(1, small.width)
        grid = ocr_cell_grid(small, src, ratio)
    except Exception:
        return joined
    return (grid + "\n" + joined) if grid else joined


def ocr_bytes(data: bytes) -> str:
    """tesseract 로 한 장. --psm 6(균일 블록)이 상품 상세의 세로 긴 이미지에 가장 안정적이었다."""
    orig = data
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
            if len(words) < 3:
                return ""
            return add_cell_grid(joined, data, orig)
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
    if len(words) < 3:
        return ""
    return add_cell_grid(joined, data, orig)


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


def load_rows() -> list[dict]:
    """products_full.csv 를 줄 그대로 — 매장 옵션(사이즈 선택지)을 보려고 읽는다."""
    import csv
    path = ROOT / "data" / "products_full.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@functools.lru_cache(maxsize=1)
def load_categories() -> dict[tuple[str, int], str]:
    # 브랜드마다 부르는데 12만 줄짜리 csv 를 매번 다시 읽고 있었다. 잡 하나가 브랜드
    # 하나였을 땐 티가 안 났지만, --plan 은 200곳을 한 판에 세어 200번 읽는다.
    import csv
    path = ROOT / "data" / "products_full.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {(r["brand_slug"], int(r["product_no"])): r["category"] for r in csv.DictReader(f)}


# 파일 이름이 말해 주는 것 — 이런 그림은 순서와 무관하게 먼저 읽는다
HINT_NAME = re.compile(r"size|detail|info|spec|measure|fabric|\uc0ac\uc774\uc988|\uc2e4\uce21", re.I)


def images_of(d: dict) -> list[str]:
    """읽을 그림 — 상세 그림이 없으면 갤러리를 읽는다.

    상세 그림이 한 장도 없는데 갤러리에는 사진이 있는 상품이 7,852벌이다(2026-09-06).
    매장이 상세 설명을 「추가 이미지」 자리에 올린 경우인데, 우리는 상세 그림만 읽어서
    그 글을 통째로 못 봤다. 그중 판매중인 옷이면서 아직 빈 곳이 있는 것이 330벌이다.

    무작위로 열어 재 봤다 — 갤러리에서 나온 글자 수:
        sinoon  Authentic Logo Hoodie        644자
        glowny  HUGGING BIKINI SKIRT       1,386자
        learve  퍼티그 라운지 팬츠              479자
        the-museum-visitor "WE ARE BUDDY" FLOWER KNIT  1,737자
        sinoon  Round Neck Sleeveless Knit  2,159자
        coor    메리노 울 크루넥 스웨터           0자 (갤러리가 한 장뿐)
    열두 벌을 재서 일곱 벌(58%)에 80자 넘는 글이 있었다.

    상세 그림이 있으면 지금처럼 그것만 읽는다 — 갤러리는 대개 착장컷이라 글이 없고,
    괜히 예산만 먹는다. 없을 때만 대신 본다.
    """
    return list(d.get("detail_images") or []) or list(d.get("gallery") or [])


def process_brand(slug: str, only_short: bool, max_images: int, delay: float, log,
                  shard: tuple[int, int] = (0, 1), out_dir: Path | None = None, select: str = "short",
                  workers: int = 1, cdn_delay: float | None = None, redo: bool = False,
                  plan_only: bool = False) -> dict:
    """shard=(k, n): 대상을 product_no 순으로 n등분해 k번째만 본다 — kirsh(1,800건)처럼 큰 브랜드를
    러너 여럿에 나눌 때. out_dir 를 주면 crawl/ocr/<slug>.jsonl 대신 out_dir/<slug>.<k>.jsonl 조각으로 쓴다
    (Actions 의 collect 가 조각을 합친다). 이미 끝난 상품 판단은 항상 crawl/ocr/<slug>.jsonl 기준.

    plan_only=True 면 그림을 한 장도 받지 않고 「무엇을 읽을지」만 세어 돌려준다. Actions 의
    plan 잡이 조각 수를 정할 때 쓴다 — 예전엔 plan 이 워크플로 안에 제 잣대를 따로 들고 있었고,
    그 잣대가 판독기와 어긋나 갤러리만 있는 매장 3,557벌이 통째로 일정에 못 올랐다(2026-09-15).
    잣대가 한 벌뿐이면 다시 어긋날 수 없다."""
    src = CRAWL_DIR / f"{slug}.jsonl"
    main = OCR_DIR / f"{slug}.jsonl"
    k, n = shard
    out = (out_dir / f"{slug}.{k}.jsonl") if out_dir else main
    latest = load_latest(src)
    done: set[int] = set()
    if main.exists():
        done = {json.loads(l)["product_no"] for l in main.read_text(encoding="utf-8").splitlines() if l.strip()}
    cats = load_categories() if select in ("no-size", "ocr", "gaps", "bad-size") else {}
    # select=ocr: 사이즈를 「그림에서」 읽어 둔 옷을 다시 읽는다. 머리줄을 낱말 단위로
    # 읽게 바꾼 뒤 kirsh 10440 은 라벨이 한 칸씩 밀려 있던 것이 바로잡혔다(밑위 49cm·
    # 허벅지 25.5cm → 밑위 25.5·허벅지 33.8, 2026-09-05). 빠진 것뿐 아니라 틀린 것도 있다.
    # select=gaps: 사이즈뿐 아니라 소재·색·디테일이 빈 옷도 함께 읽는다(사람 결정 2026-09-05).
    # 토글을 열어도 글이 없는 매장이 많고, 그 내용이 상세 그림 안에 적혀 있다.
    # select=bad-size: 사이즈 표가 「사이즈가 커지는데 값이 작아지는」 옷만 다시 읽는다.
    # 감사기가 55벌을 들고 있는데 그 가운데 46벌이 그림에서 읽은 것이다 — 숫자만 보고는
    # 어느 칸이 틀렸는지 못 가리지만(총장 66·78·70), 그림을 다시 읽으면 원본이 나온다.
    # 사람 지적(2026-09-07): 「70 78 이건 OCR 오류 아니야?」 — 출처를 세어 보니 그랬다.
    bad_urls: set[str] = set()
    if select == "bad-size":
        sp4 = CRAWL_DIR.parent / "product_sizes.json"
        if sp4.exists():
            _ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]

            def _rank(x: str):
                x = (x or "").upper().strip()
                if x in _ORDER:
                    return _ORDER.index(x)
                m = re.fullmatch(r"0*(\d{1,3})", x)
                return 100 + int(m.group(1)) if m else None

            for u, e in json.loads(sp4.read_text(encoding="utf-8")).items():
                names = [str(x).strip() for x in (e.get("size_names") or [])]
                if len(names) < 2:
                    continue
                rs = [_rank(x) for x in names]
                if any(r is None for r in rs) or rs != sorted(rs) or len(set(rs)) != len(rs):
                    continue
                for vals in (e.get("sizes") or {}).values():
                    v = [x for x in vals if x is not None]
                    if len(v) == len(names) and any(b < a - 1.0 for a, b in zip(v, v[1:])):
                        bad_urls.add(u)
                        break

    # select=thin-table: 표에서 읽어 낸 칸 수가 매장이 파는 사이즈 수보다 적은 옷.
    # 「표가 한 칸뿐」이라 앱에서 프리사이즈처럼 뜨는데, 정작 매장 옵션에는 S·M·L 이 있다
    # (2,426벌. 사람 지적 2026-09-13: 「실제로 사이즈표가 있는데도 프리사이즈로 뜬다」).
    # 사이즈가 「있긴 있어서」 no-size·gaps 어느 갈래에도 안 걸렸다. 그 가운데 2,313벌은
    # 상세 그림이 있으니, 사이즈별 실측이 그림 안에 적혀 있을 자리다.
    # 옵션에서 색을 걸러 내고 사이즈로 읽히는 것만 센다 — 옵션은 색과 사이즈가 섞여 온다
    # (「BURGUNDY | M」·「DARK BROWN | 01_S | 02_M」).
    thin_urls: set[str] = set()
    if select == "thin-table":
        # 매장 옵션에서 사이즈 이름만 끊어 내는 일은 사이저에 정본이 있다
        # (_size_option_names — 색 칸이 섞여 오는 것, 「[품절]」 표시, 「1(XS)」 꼴까지 본다).
        # 여기서 정규식을 다시 짜면 둘이 갈라진다.
        sp5 = CRAWL_DIR.parent / "product_sizes.json"
        try:
            from size_from_ocr import _size_option_names
        except Exception:
            _size_option_names = None
        _sizes5 = json.loads(sp5.read_text(encoding="utf-8")) if sp5.exists() else {}
        if _size_option_names is not None:
            for _r in load_rows():
                if _r.get("status") != "ON_SALE":
                    continue
                _e = _sizes5.get(_r.get("source_url") or "")
                if not _e:
                    continue
                _cols = max((len(v) for v in (_e.get("sizes") or {}).values()
                             if isinstance(v, list)), default=0)
                if _cols < 1:
                    continue
                _opt = _size_option_names([o.strip() for o in (_r.get("options") or "").split("|") if o.strip()])
                if len(_opt) > _cols:
                    thin_urls.add(_r["source_url"])

    gap_urls: set[str] = set()
    if select == "gaps":
        root = CRAWL_DIR.parent
        sp3 = root / "product_sizes.json"
        tp3 = root / "product_tags_full.json"
        have_size = set(json.loads(sp3.read_text(encoding="utf-8"))) if sp3.exists() else set()
        tg = json.loads(tp3.read_text(encoding="utf-8")) if tp3.exists() else {}
        for u, p in tg.items():
            t = (p or {}).get("tags") or {}
            if (u not in have_size or not t.get("material") or not t.get("color")
                    or not (t.get("design_element") or t.get("construction") or t.get("hardware"))):
                gap_urls.add(u)

    # 옛 6,000자 상한에 잘려 나간 기록 — 그때는 그림별 글을 남기지 않아서 되살릴 길이
    # 그림을 다시 읽는 것뿐이다(지금 상한은 12,000자). 잘린 자리가 글 끝이라, 상세 그림
    # 맨 뒤에 오는 사이즈 표·소재·케어가 통째로 날아간 상품이 있다.
    capped_nos: set[int] = set()
    if select == "capped" and main.exists():
        for l in main.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            o = json.loads(l)
            if 5995 <= len(o.get("ocr_text") or "") <= 6005 and not o.get("imgs"):
                capped_nos.add(o["product_no"])

    ocr_sized: set[str] = set()
    if select in ("ocr", "all"):
        sp2 = CRAWL_DIR.parent / "product_sizes.json"
        if sp2.exists():
            for u, e in json.loads(sp2.read_text(encoding="utf-8")).items():
                if str((e or {}).get("source", "")).startswith("ocr"):
                    ocr_sized.add(u)
    # bad-size 는 「이미 읽은」 상품만 고른다 — 값이 거꾸로인 표는 애초에 우리가 그림에서
    # 읽어 넣은 것이니 전부 done 안에 있다. 그래서 --redo 없이 돌리면 대상이 58 → 5 로
    # 주저앉는다(2026-09-07 실측: noirer 20 · easy-no-easy 9 · frizmworks 7 이 전부 0 이 됐다).
    # 이 갈래는 다시 읽기가 목적이므로 redo 를 켜고 시작한다.
    if select in ("bad-size", "capped", "thin-table"):
        redo = True

    # 매장 공용 안내 그림은 읽어도 소용없다 — 결제 아이콘·저작권 안내·교환반품 규정이
    # 상품마다 붙어 상세 그림의 45%(101,405장 중 46,032장)를 차지한다. 그것들이 「앞
    # 12장」 예산을 먹어 정작 사이즈 표가 안 읽혔다(2026-09-05: kirsh 에서 글자가 가장
    # 많은 그림이 「NOTICE 배송안내」였다). 한 브랜드 안에서 열 상품 넘게, 그리고 전체의
    # 20% 넘게 쓰는 그림은 상품 그림이 아니다 — 색만 다른 형제도 그렇게 많지 않다.
    use: collections.Counter = collections.Counter()
    for d in latest.values():
        for u in set(images_of(d)):
            use[u] += 1
    floor = max(10, len(latest) * 0.2)
    shared = {u for u, c in use.items() if c >= floor}

    # --redo: 예전에 「앞 3~5장만」 읽고 끝난 상품은 done 에 들어 있어 12장짜리 재시도에서 아예 빠진다.
    # 사이즈가 아직 없고 읽은 그림이 읽을 수 있는 그림보다 적으면 done 에서 빼 다시 읽는다
    # (2026-09-04: 사이즈 없는 옷 875벌 중 617벌이 이 경우였다 — far-from-what 은 11장 중 2장만 읽었다).
    if redo:
        sized_urls = set()
        sp = CRAWL_DIR.parent / "product_sizes.json"
        if sp.exists():
            sized_urls = set(json.loads(sp.read_text(encoding="utf-8")))
        read_n: dict[int, int] = {}
        text_len: dict[int, int] = {}
        if main.exists():
            for l in main.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    o = json.loads(l)
                    read_n[o["product_no"]] = len(o.get("images") or [])
                    text_len[o["product_no"]] = len(o.get("ocr_text") or "")
        for no, d in latest.items():
            if no not in done:
                continue
            # 옛 6000자 상한에 잘린 기록은 무엇보다 먼저 본다 — 사이즈가 이미 있어도.
            # 아래의 「사이즈 있으면 건너뜀」이 먼저 오는 바람에 이 검사에 아예 닿지 못했고,
            # 어젯밤 재판독이 성공으로 끝났는데도 956벌이 그대로 남았다(2026-09-06).
            # 그 중 237벌은 사이즈만 있고 소재·색·디테일이 잘린 채였다(siyazu 126 ·
            # rough-side 28 · dnsr 15). 사이즈 표는 상세 그림 맨 끝에 있어 앞에서 자르면
            # 통째로 날아간다 — 그 뒤에 오는 소재·케어 글도 함께 날아갔다.
            # 대상은 여전히 select 의 목록(gaps 면 빈 축이 있는 상품)이 정한다.
            if 5995 <= text_len.get(no, 0) <= 6005:
                done.discard(no)
                continue
            if select not in ("ocr", "bad-size") and d.get("source_url") in sized_urls:
                # select=all 에서도 「그림에서 읽은」 사이즈는 다시 읽는다 — 판독기가 바뀌면
                # 같은 그림에서 다른 값이 나온다. HTML 로 얻은 사이즈는 건드릴 까닭이 없다
                # (2026-09-05: 되찾은 그림 읽기와 판독기 재판독을 한 판에 돌리려고).
                if not (select == "all" and d.get("source_url") in ocr_sized):
                    continue
            if select == "ocr" and d.get("source_url") not in ocr_sized:
                continue
            # 사이즈 없는 옷만 고르는 판(no-size)에서는 장 수를 따지지 않는다 — 읽는 방법이
            # 바뀌면(2026-09-05 머리줄 낱말 단위 판독) 같은 그림에서 새 글이 나온다.
            if select in ("no-size", "ocr", "gaps", "bad-size", "thin-table") or (select == "all" and d.get("source_url") in ocr_sized):
                done.discard(no)
                continue
            avail = len([u for u in images_of(d)
                         if u not in shared and not skip_image(u)])
            if read_n.get(no, 0) < min(max_images, avail):
                done.discard(no)

    todo = []
    for no, d in sorted(latest.items(), key=lambda kv: int(kv[0])):
        if no in done or (d.get("price") or 0) <= 1000 or not images_of(d):
            continue
        if select == "no-size":
            # 사이즈 표 없는 옷만 — 설명 길이와 무관. 사이즈 수집률을 올리는 2차 OCR(사람 결정 2026-09-03)
            if d.get("size_table") or cats.get((slug, int(no)), "") not in GARMENTS:
                continue
        elif select == "ocr":
            if d.get("source_url") not in ocr_sized or cats.get((slug, int(no)), "") not in GARMENTS:
                continue
        elif select == "gaps":
            # 사이즈·소재·색·디테일 가운데 하나라도 빈 옷. 그림 안에 적혀 있는 경우가 많다.
            if d.get("source_url") not in gap_urls or cats.get((slug, int(no)), "") not in GARMENTS:
                continue
        elif select == "bad-size":
            # 사이즈가 커지는데 값이 작아지는 표 — 그림을 다시 읽어 원본 숫자를 본다
            if d.get("source_url") not in bad_urls:
                continue
        elif select == "capped":
            if no not in capped_nos:
                continue
        elif select == "thin-table":
            if d.get("source_url") not in thin_urls:
                continue
        elif only_short and len(d.get("description", "")) >= SHORT_TEXT:
            continue
        todo.append(d)
    todo = todo[k::n]
    if plan_only:
        imgs = sum(min(max_images, len([u for u in images_of(d)
                                        if u not in shared and not skip_image(u)]))
                   for d in todo)
        return {"brand": slug, "products": len(todo), "images": imgs, "with_text": 0}
    # 표를 얻으면 남은 그림을 안 읽고 멈추는 갈래. 사이즈**만** 노리는 판에서만 켠다.
    # gaps 는 사이즈·소재·색·디테일 가운데 빈 것을 채우러 가는 판인데 여기 끼어 있었다 —
    # 소재를 채우러 가 놓고 사이즈 표를 보는 순간 멈춰, 뒤에 오는 소재·케어 글을 못 읽었다.
    # 상세 그림은 대개 「착장 → 표 → 소재·세탁」 차례라 그 뒤가 통째로 날아간다
    # (사람 지적 2026-09-13: 「OCR 돌릴거면 안에 디테일이나 소재 같은것도 같이 수집해」).
    want_size = select in ("no-size", "ocr", "bad-size", "capped", "thin-table")
    log(f"[{slug}] OCR 대상 {len(todo)} (이미 {len(done)}, 조각 {k + 1}/{n})")
    n_img = n_txt = 0
    counters = {"img": 0, "txt": 0, "done": 0, "early": 0}
    wlock = threading.Lock()

    def one(d: dict) -> str:
            texts, imgs = [], []
            # 소재·디테일·사이즈표는 상세 이미지의 「뒤쪽」에 오는 경우가 많다(사람 지적 2026-09-04).
            # 앞에서 자르면 착용컷만 읽고 정작 필요한 표를 놓친다. 그래서 뒤에서부터 고르되,
            # 파일 이름에 size/detail/info 가 든 그림은 어디에 있든 먼저 읽는다.
            cand = [u for u in images_of(d)
                    if u not in shared and not skip_image(u)]
            hinted = [u for u in cand if HINT_NAME.search(u)]
            rest = [u for u in cand if u not in hinted]
            # 힌트가 붙은 그림을 먼저, 나머지는 뒤에서부터. 상한에 닿았는데 글자가 거의 안
            # 나왔으면 상한을 두 배까지 늘려 더 본다 — 뒤 여덟 장이 전부 착용컷이고 표는
            # 앞쪽에 있는 매장이 있다(espionage 는 그림 23장 중 뒤 8장만 읽고 42자를 건졌다,
            # 2026-09-06). 잘 나오는 상품에는 아무 값도 더 안 든다.
            order = hinted + rest[::-1]
            budget, read = max_images, 0
            for url in order:
                if read >= budget:
                    break
                data = polite_get(url, delay, cdn_delay)
                if not data or len(data) < MIN_BYTES:
                    continue
                with wlock:
                    counters["img"] += 1
                t = ocr_bytes(data)
                imgs.append({"url": url, "chars": len(t), "text": t})
                if t:
                    texts.append(t)
                read += 1
                # 사이즈 표를 이미 얻었으면 남은 그림은 읽지 않는다 — 표는 대개 한 장에 다 있는데,
                # 12장을 끝까지 읽느라 시간의 절반을 버리고 있었다(2026-09-04).
                if want_size and texts and _has_size_table("\n".join(texts)):
                    with wlock:
                        counters["early"] += 1
                    break
                if read >= budget and budget < max_images * 2 and sum(map(len, texts)) < LOW_YIELD:
                    budget = max_images * 2
                    with wlock:
                        counters["more"] = counters.get("more", 0) + 1
            ocr_text = _cap(texts)
            # 상한에 걸려 잘린 기록만 그림별 글을 함께 남긴다 — 그래야 나중에 상한을
            # 올리거나 자르는 자리를 바꿀 때 그림을 다시 내려받지 않아도 된다.
            # 예전 6,000자 상한에 잘린 956벌이 딱 그래서 되살릴 길이 없었다(2026-09-06).
            # 지금 상한(12,000자)에 걸리는 것은 19,407건 중 57건(0.3%)뿐이라 값이 싸다.
            if len("\n".join(texts)) <= len(ocr_text):
                imgs = [{k: v for k, v in im.items() if k != "text"} for im in imgs]
            with wlock:
                counters["done"] += 1
                if ocr_text:
                    counters["txt"] += 1
                if counters["done"] % 100 == 0:
                    log(f"[{slug}] … {counters['done']}/{len(todo)} · 글 나온 상품 {counters['txt']} · 표 얻고 조기 종료 {counters['early']}")
            return jsonl_line({"brand_slug": slug, "product_no": d["product_no"],
                               "ocr_text": ocr_text, "images": imgs})

    with out.open("a", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for line in ex.map(one, todo):
                f.write(line + "\n")
                f.flush()
    n_img, n_txt = counters["img"], counters["txt"]
    log(f"[{slug}] 끝 — 상품 {len(todo)} · 이미지 {n_img} · 글 나온 상품 {n_txt} · 조기 종료 {counters['early']}")
    return {"slug": slug, "products": len(todo), "images": n_img, "with_text": n_txt}


def _plan_one(slug: str, only_short: bool, max_images: int, select: str,
              cdn_delay: float | None, redo: bool) -> dict:
    """--plan 의 일꾼. 프로세스로 돌리므로 바깥 변수를 붙잡지 않는다(log 가 닫힘이라 못 절인다)."""
    return process_brand(slug, only_short, max_images, 0.0,
                         lambda m: None, (0, 1), None, select, 1, cdn_delay, redo, True)


def plan(slugs: list[str], args, log) -> None:
    """Actions 의 조각 나누기 — 판독기와 **같은 코드**로 대상을 세어 matrix 를 낸다.

    그림을 한 장도 받지 않는다(plan_only). 예전엔 이 잣대가 ocr.yml 안에 베껴져 있었고,
    베낀 쪽이 낡아 세 번 사고가 났다. 마지막은 「상세 그림이 없으면 갤러리를 읽는다」를
    판독기만 알고 plan 은 몰라, 읽을 수 있는 3,557벌이 통째로 일정에 못 오른 일이다(2026-09-15).

    matrix 가 비면 죽는다 — 「읽을 것이 없다」와 「잣대가 고장 났다」는 밖에서 구별이 안 되고,
    지금까지 우리를 물어 온 쪽은 늘 뒤엣것이었다. 정말로 빌 수 있는 판이면 --allow-empty 를 준다.
    """
    select = "all" if args.all else args.select
    counts: dict[str, int] = {}
    # 스레드가 아니라 프로세스로 센다 — 하는 일이 거의 전부 json 파싱이라 GIL 에 막혀,
    # 384곳을 --procs 4 스레드로 세는 데 18분이 걸렸다. plan 은 OCR 전체를 막는 자리다.
    # 프로세스로 바꾸니 같은 입력에 5분 20초.
    ex_cls = ProcessPoolExecutor if args.procs > 1 else ThreadPoolExecutor
    with ex_cls(max_workers=max(1, args.procs)) as ex:
        futs = {ex.submit(_plan_one, s, not args.all, args.max_images, select,
                          args.cdn_delay, args.redo): s
                for s in slugs}
        failed = 0
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                failed += 1
                log(f"예외 [{futs[fut]}]: {e!r}")
                traceback.print_exception(e)
                continue
            if r["images"]:
                counts[r["brand"]] = r["images"]
    if failed:
        raise SystemExit(f"브랜드 {failed}곳이 일정 짜기에서 죽었다 — 위 자취를 볼 것")

    # GitHub 의 matrix 상한은 256잡이다. 넘기면 ocr 잡이 **통째로** 안 뜬다 — 오늘 고친
    # 「초록인데 아무 일도 안 함」과 같은 꼴이다(전수 gaps 를 재니 322잡이 나왔다).
    # 먼저 조각을 굵게 해 상한 안에 넣어 보고, 그래도 안 되면 그림이 많은 매장부터 넣는다.
    # 남은 매장은 조용히 사라지지 않는다 — 로그와 job summary 에 이름을 다 적고, 대상을
    # 고르는 잣대가 상태를 보므로(gaps·no-size 는 「아직 빈 것」) 다음 판이 집어 간다.
    per = max(1, args.per_shard)

    def build(per_shard):
        out = []
        for b in sorted(counts):
            n = max(1, min(args.max_shards, math.ceil(counts[b] / per_shard)))
            for k in range(1, n + 1):
                out.append({"brand": b, "shard": k, "shards": n})
        return out

    inc = build(per)
    while len(inc) > args.max_jobs and per < 10 ** 6:
        per = int(per * 1.5) + 1
        inc = build(per)
    if per != args.per_shard:
        log(f"조각 크기를 {args.per_shard} → {per}장으로 키웠다 (matrix 상한 {args.max_jobs}잡)")
    left = []
    if len(inc) > args.max_jobs:
        keep, used = [], 0
        for b in sorted(counts, key=lambda x: -counts[x]):
            n = max(1, min(args.max_shards, math.ceil(counts[b] / per)))
            if used + n > args.max_jobs:
                left.append(b)
                continue
            used += n
            keep += [{"brand": b, "shard": k, "shards": n} for k in range(1, n + 1)]
        inc = sorted(keep, key=lambda e: (e["brand"], e["shard"]))
        log(f"!! matrix 상한 {args.max_jobs}잡에 걸려 {len(left)}곳을 이번 판에서 뺐다 — "
            f"다음 판이 집어 간다: {' '.join(sorted(left))}")
    log(f"잡 {len(inc)} · 브랜드 {len({e['brand'] for e in inc})} · 그림 {sum(counts.values())}")
    for b in sorted(counts, key=lambda x: -counts[x])[:10]:
        log(f"  {b} {counts[b]}장")
    sm = os.environ.get("GITHUB_STEP_SUMMARY")
    if sm:
        with open(sm, "a", encoding="utf-8") as f:
            f.write(f"### 일정 — 잡 {len(inc)} · 브랜드 {len({e['brand'] for e in inc})} "
                    f"· 그림 {sum(counts.values())} · 조각 {per}장\n")
            if left:
                f.write(f"**다음 판으로 미룬 매장 {len(left)}곳**: {' '.join(sorted(left))}\n")
    if not inc and not args.allow_empty:
        raise SystemExit(
            "읽을 것이 하나도 없다 — 잡을 만들지 않고 죽는다.\n"
            "정말 다 읽은 것이면 --allow-empty 를, 아니면 잣대(select·brands)를 볼 것.")
    out = os.environ.get("GITHUB_OUTPUT")
    payload = json.dumps({"include": inc}, ensure_ascii=False)
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"matrix={payload}\n")
    print(payload, flush=True)


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
    ap.add_argument("--plan", action="store_true",
                    help="그림을 받지 않고 「무엇을 읽을지」만 세어 Actions matrix(JSON)를 낸다")
    ap.add_argument("--per-shard", type=int, default=250, help="--plan: 조각 하나가 맡을 그림 수 목표")
    ap.add_argument("--max-shards", type=int, default=12, help="--plan: 브랜드당 조각 수 상한")
    ap.add_argument("--max-jobs", type=int, default=256,
                    help="--plan: matrix 잡 수 상한 (GitHub 은 256잡을 넘기면 잡을 아예 안 만든다)")
    ap.add_argument("--allow-empty", action="store_true", help="--plan: 대상이 0이어도 죽지 않는다")
    ap.add_argument("--select", default="short", choices=["short", "all", "no-size", "ocr", "gaps", "bad-size", "capped", "thin-table"], help="short=설명 짧은 것(기본) · all=전부 · no-size=사이즈 표 없는 옷 · ocr=사이즈를 그림에서 읽은 옷 다시 · gaps=사이즈·소재·색·디테일 중 하나라도 빈 옷 · bad-size=사이즈가 커지는데 값이 작아지는 표만 다시 · capped=옛 6,000자 상한에 잘린 기록만 다시 · thin-table=표 칸 수가 매장 사이즈 수보다 적은 옷")
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

    if args.plan:
        # 일정 로그는 stderr 로 — stdout 은 matrix JSON 만 나와야 파이프로 바로 받는다.
        def plog(msg):
            with lock:
                print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)

        plan(slugs, args, plog)
        return

    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.procs)) as ex:
        select = "all" if args.all else args.select
            # 브랜드가 하나면(Actions 는 잡마다 한 브랜드) 그 안에서 상품을 나눠 돌린다 — 러너 4코어를
        # 하나만 쓰고 있었다. 호스트 속도는 polite_get 이 호스트별로 잠가 지킨다.
        inner = args.procs if len(slugs) == 1 else 1
        outer = 1 if len(slugs) == 1 else args.procs
        futs = [ex.submit(process_brand, s, not args.all, args.max_images, args.delay, log, shard, out_dir, select, inner, args.cdn_delay, args.redo) for s in slugs]
        failed = 0
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                failed += 1
                log(f"예외: {e!r}")
                traceback.print_exception(e)
    tot_p = sum(r["products"] for r in results)
    tot_t = sum(r["with_text"] for r in results)
    log(f"전부 끝 — 상품 {tot_p} · 글 나온 상품 {tot_t} · {round(time.time() - started)}s")
    # 브랜드가 통째로 예외로 죽었는데 잡이 초록으로 끝나면, 판이 아무것도 안 하고
    # 성공했다고 보고한다. 실제로 두 번 겪었다 — 2026-09-13 의 capped 판은 잡 30개가
    # 전부 「성공」했는데 한 벌도 안 읽었다(shared 를 만들기 전에 읽는 NameError 였다).
    # 자국이 남는 것은 로그 한 줄뿐이라 아무도 안 본다. 죽으면 잡도 빨갛게 죽는다.
    if failed:
        raise SystemExit(f"브랜드 {failed}곳이 예외로 죽었다 — 위 자취를 볼 것")


if __name__ == "__main__":
    main()
