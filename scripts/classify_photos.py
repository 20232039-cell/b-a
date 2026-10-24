"""상세그림·갤러리를 「룩 재료」와 「버릴 것」으로 가른다.

왜 필요한가(2026-09-04): 룩 매칭 시험 6건 중 3건이 kirsh 캠페인 배너를 착용샷으로
집어서 깨졌다. 상세그림 칸은 설명 영역의 모든 이미지를 담는다 — 착용샷·상품컷뿐 아니라
캠페인 배너, 배송 안내표, 사이즈표 그림, 세탁 주의까지. 사이즈표 그림은 OCR 재료라
수집을 끊을 수 없으니, 받아 두고 여기서 갈라낸다.

가르는 기준 두 개 — 둘 다 사진을 열지 않고 판단한다:
  1) 여러 상품에 쓰인 사진 = 매장 공용물. 상세그림 136,970장 중 10벌 이상에 쓰인 것이
     48,478장(35%)이다. dunst 는 28,380장이 서로 다른 것 12개뿐(전부 공용 안내물).
     ★ 2~9벌 공용은 버리지 않는다 — 색만 다른 형제와 「같은 룩」이 그 경우이고,
     같은 사진이 두 상품에 걸린 262건이 룩 매칭의 유일한 확실한 근거다.
  2) 파일 이름이 안내물인 것 — shipping_info, sms_btn, count_up, plus/minus 등.

그런데 이 두 기준으로는 「그 시즌 캠페인 배너」를 못 막는다 — kirsh 는 매장 공용
안내(TEST2.jpg 399벌·ISSUE.jpg 359벌)는 잡히지만 컬렉션 배너는 2~4벌에만 쓰여
문턱을 빠져나간다(2~4벌 공용이 515장). 룩 시험 6건 중 3건이 그래서 깨졌다.

세 번째 기준은 사진에서 읽힌 글자 수다. OCR 기록에 사진마다 글자 수가 남아 있고
(images: [{url, chars}]), 22,865장의 분포가 「0자 36%」와 「60자 이상 61%」로 갈리며
1~19자 구간이 0.1% 밖에 없다. 즉 사진이냐 글 박힌 이미지냐가 이 숫자로 거의 결정된다.
20자를 문턱으로 쓴다 — 배너·안내물·사이즈표 그림이 다 여기 걸린다(사이즈표는 OCR
재료로는 계속 쓰고 룩에서만 뺀다).

네 번째 기준은 사진의 모양이다. 이어붙인 띠는 아주 길고(0.35 미만), 가로로 넓은 것은
배너다(1.5+). 다만 세로 비율로 착용샷을 가릴 수는 없다 — 세로로 긴 팬츠 누끼도 0.6 이라
kirsh 를 그 기준으로 골라 보니 다섯 장이 전부 누끼컷이었다. 착용샷 판별은 픽셀을
봐야 하고(배경이 순백인가), 그건 이 스크립트가 하지 않는다. 크기는 파일 앞 32KB만 받아도 읽히므로
(Range 요청) 사진을 다 내려받지 않는다. --measure 로 켠다.

사용: py scripts/classify_photos.py [--shared-max 10] [--report]
      py scripts/classify_photos.py --measure --brands kirsh,insilence --per-product 4
결과: data/look_photos.json — 상품마다 룩 재료로 쓸 수 있는 사진 주소
      data/photo_shape.json — 재어 본 사진의 가로·세로 (--measure)
"""
from __future__ import annotations
import argparse, json, re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"

# 파일 이름만 봐도 상품 사진이 아닌 것들 — 이름 조사에서 실제로 나온 것만 넣는다
BOILER = re.compile(
    r"(shipping|delivery|sms_btn|count_up|count_down|plus\.|minus\.|btn_|icon|logo"
    r"|banner|notice|guide|washing|caution|refund|exchange|newsize|size_?top"
    r"|kakaotalk|talk_photo|test\d|issue\.)", re.I)

OCR_DIR = CRAWL / "ocr"
TEXT_MAX = 20        # 이만큼 글자가 읽힌 사진은 사진이 아니라 글이다


def ocr_chars() -> dict[str, int]:
    """사진마다 OCR 로 읽힌 글자 수 — 있으면 배너·안내물을 이걸로 가장 정확히 가른다."""
    out: dict[str, int] = {}
    if not OCR_DIR.exists():
        return out
    for p in OCR_DIR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            for im in (d.get("images") or []):
                u = im.get("url")
                if u:
                    out[u] = max(out.get(u, 0), im.get("chars") or 0)
    return out


def read_size(url: str, sess) -> tuple[int, int] | None:
    """앞 32KB 만 받아 가로·세로를 읽는다 — 사진을 다 내려받지 않는다."""
    if url.startswith("//"):
        url = "https:" + url
    try:
        r = sess.get(url, headers={"Range": "bytes=0-32767",
                                   "User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2)"},
                     timeout=15)
        if r.status_code not in (200, 206) or not r.content:
            return None
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(r.content))
        return im.size            # 헤더만으로 알 수 있다
    except Exception:
        return None


def shape_of(w: int, h: int) -> str:
    a = w / h if h else 0
    if a >= 1.5:
        return "배너"          # 가로로 넓다
    if a < 0.35:
        return "이어붙인 띠"    # frizmworks 830x11620
    if 0.55 <= a <= 0.95:
        # 「착용샷」이라 부르면 안 된다 — 세로로 긴 팬츠 누끼도 0.6 이다. kirsh 를 이 기준으로
        # 골라 봤더니 다섯 장이 전부 누끼컷이었다(2026-09-04). 착용샷 판별은 픽셀을 봐야 한다.
        return "세로 사진"
    return "그 외"             # 정사각 누끼·디테일컷


def measure(brands: list[str], per_product: int, delay: float, max_products: int = 0):
    import time, requests
    shapes: dict[str, list] = {}
    fp = DATA / "photo_shape.json"
    if fp.exists():
        shapes = json.loads(fp.read_text(encoding="utf-8"))
    look = json.loads((DATA / "look_photos.json").read_text(encoding="utf-8"))
    sess = requests.Session()
    for brand in brands:
        todo = [(u, v["photos"][:per_product]) for u, v in look.items()
                if v["brand_slug"] == brand]
        if max_products:
            import random
            random.Random(7).shuffle(todo)
            todo = todo[:max_products]
        cnt = Counter()
        for _, photos in todo:
            for ph in photos:
                if ph in shapes:
                    cnt[shapes[ph][2]] += 1
                    continue
                wh = read_size(ph, sess)
                time.sleep(delay)
                if not wh:
                    continue
                shapes[ph] = [wh[0], wh[1], shape_of(*wh)]
                cnt[shapes[ph][2]] += 1
        print(f"  {brand}: {dict(cnt)}", flush=True)
        fp.write_text(json.dumps(shapes, ensure_ascii=False), encoding="utf-8")
    print(f"재어 둔 사진 {len(shapes):,}장 → {fp}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true", help="사진 크기를 재어 모양으로 가른다")
    ap.add_argument("--brands", default="")
    ap.add_argument("--per-product", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.12)
    ap.add_argument("--max-products", type=int, default=0, help="브랜드마다 표본 상품 수 (0=전부)")
    ap.add_argument("--shared-max", type=int, default=10,
                    help="이만큼 이상의 상품에 쓰인 사진은 매장 공용물로 버린다")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.measure:
        measure([b for b in args.brands.split(",") if b], args.per_product, args.delay, args.max_products)
        return

    out: dict[str, dict] = {}
    rep = []
    chars = ocr_chars()
    if chars:
        print(f"OCR 글자 수를 아는 사진 {len(chars):,}장")
    for p in sorted(CRAWL.glob("*.jsonl")):
        brand = p.stem
        recs = []
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                recs.append(json.loads(l))
        if not recs:
            continue
        use: Counter = Counter()
        for d in recs:
            for u in set((d.get("detail_images") or []) + (d.get("gallery") or [])):
                use[u] += 1
        kept = shared = boiler = texty = 0
        withphoto = 0
        for d in recs:
            u0 = d.get("source_url")
            if not u0:
                continue
            photos, drop_s, drop_b, drop_t = [], 0, 0, 0
            seen = set()
            for u in (d.get("gallery") or []) + (d.get("detail_images") or []):
                if u in seen:
                    continue
                seen.add(u)
                if use[u] >= args.shared_max:
                    drop_s += 1
                    continue
                if BOILER.search(u.rsplit("/", 1)[-1]):
                    drop_b += 1
                    continue
                if chars.get(u, 0) >= TEXT_MAX:
                    drop_t += 1
                    continue
                photos.append(u)
            shared += drop_s
            boiler += drop_b
            texty += drop_t
            kept += len(photos)
            if photos:
                withphoto += 1
            out[u0] = {"brand_slug": brand, "photos": photos,
                       "dropped_shared": drop_s, "dropped_boiler": drop_b,
                       "dropped_texty": drop_t}
        rep.append((brand, len(recs), withphoto, kept, shared, boiler, texty))

    (DATA / "look_photos.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tk = sum(r[3] for r in rep); ts = sum(r[4] for r in rep)
    tb = sum(r[5] for r in rep); tt = sum(r[6] for r in rep)
    print(f"상품 {len(out):,} · 룩 재료로 남긴 사진 {tk:,} · 버림 — 공용 {ts:,} · 안내물 {tb:,} · 글 박힌 것 {tt:,}"
          f" → {DATA / 'look_photos.json'}")
    if args.report:
        print(f"\n{'브랜드':18s}{'상품':>6}{'사진있는상품':>12}{'남긴사진':>9}{'공용':>7}{'안내물':>7}{'글':>7}")
        for brand, n, wp, k, s, b, t in sorted(rep, key=lambda r: -(r[4] + r[6])):
            if n < 30:
                continue
            print(f"{brand:18s}{n:6d}{wp:12d}{k:9d}{s:7d}{b:7d}{t:7d}")


if __name__ == "__main__":
    main()
