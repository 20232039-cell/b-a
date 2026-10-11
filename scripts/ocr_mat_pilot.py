"""혼용률을 더 잘 읽는 판독기로 다시 읽어 보는 시험 — PaddleOCR(한글 모델).

왜 있는가(2026-10-11, 사람 「깃액션으로 가능해?」 → 「해 봐」): 판매중 옷 가운데 혼용률이 빈 7,849벌 중 5,007벌은 상세 그림을
tesseract 로 읽었는데 「%」가 하나도 안 나왔다. till-i-die · roem 은 혼용률이 그림 속에 있는데 tesseract 결과가 깨진 글자뿐이다.
언어 모델 토큰 없이 Actions 러너에서 더 나은 판독기로 다시 읽으면 얼마나 채워지는지 잰다.

무엇을 — 그 상품들 가운데 --brands 매장에서 --limit 벌. 상품마다 tesseract 가 글자를 가장 많이 찾은 그림(없으면 앞 그림) 4장을
PaddleOCR 로 읽고, 혼용률 해석기(product_desc.mix_cands · mix_pick, 그림 글 모드)에 그대로 넣는다. 결과는 data/ocr_mat_pilot.json —
{"summary": {...}, "items": {source_url: {"mat", "status", "text"}}}. 이 시험은 혼용률을 싣지 않는다(재기만 한다).

사용: python scripts/ocr_mat_pilot.py --brands till-i-die,roem --limit 200
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "ocr_mat_pilot.json"
sys.path.insert(0, str(ROOT / "scripts"))
import product_desc  # noqa: E402
import tag_items as ti  # noqa: E402

GARMENT = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
PCT = re.compile(r"\d{1,3}\s*[%％]")


def pick_images(o: dict, n: int = 4) -> list[str]:
    imgs = [i for i in (o.get("images") or []) if i.get("url")]
    withtext = sorted([i for i in imgs if (i.get("chars") or 0) > 0], key=lambda i: -(i.get("chars") or 0))
    chosen = [i["url"] for i in withtext[:n]]
    for i in imgs:
        if len(chosen) >= n:
            break
        if i["url"] not in chosen:
            chosen.append(i["url"])
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", default="till-i-die,roem")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--images", type=int, default=4)
    args = ap.parse_args()
    brands = [b.strip() for b in args.brands.split(",") if b.strip()]

    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("category") in GARMENT and r["brand_slug"] in brands
            and not (tags.get(r["source_url"]) or {}).get("mat")]
    targets = []
    for b in brands:
        o = ti.load_latest(ti.CRAWL / "ocr" / f"{b}.jsonl")
        for r in rows:
            if r["brand_slug"] != b:
                continue
            rec = o.get(str(r["product_no"])) or {}
            if PCT.search(rec.get("ocr_text") or ""):
                continue          # tesseract 가 % 는 찾았다 — 해석기 몫이라 이 시험에서 뺀다
            urls = pick_images(rec, args.images)
            if urls:
                targets.append((r, urls))
    per = max(1, args.limit // max(1, len(brands)))
    sel = []
    for b in brands:
        sel += [t for t in targets if t[0]["brand_slug"] == b][:per]
    print(f"대상 {len(targets)}벌 중 {len(sel)}벌 시험", flush=True)

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(lang="korean", use_angle_cls=False, show_log=False)

    items: dict[str, dict] = {}
    st = Counter()
    t0 = time.time()
    for k, (r, urls) in enumerate(sel, 1):
        lines: list[str] = []
        for u in urls:
            try:
                im = Image.open(io.BytesIO(requests.get(u, headers=UA, timeout=25).content)).convert("RGB")
            except Exception:
                continue
            # 긴 상세 그림은 세로로 잘라 읽는다(판독기가 긴 그림을 줄여 글자가 뭉개진다)
            w, h = im.size
            if w > 1000:
                im = im.resize((1000, int(h * 1000 / w)))
                w, h = im.size
            for top in range(0, h, 1200):
                part = im.crop((0, top, w, min(h, top + 1200)))
                import numpy as np
                res = ocr.ocr(np.array(part), cls=False) or []
                for page in res:
                    for box in page or []:
                        try:
                            lines.append(str(box[1][0]))
                        except Exception:
                            pass
        text = "\n".join(lines)
        got, _, lp = product_desc.mix_cands(text, ocr=True)
        m, why = product_desc.mix_pick(got) if got else ([], "none")
        has_pct = bool(PCT.search(text))
        status = "읽음" if m else ("%는 있음" if has_pct else "% 없음")
        st[(r["brand_slug"], status)] += 1
        items[r["source_url"]] = {"brand": r["brand_slug"], "name": r["name"], "status": status, "mat": m,
                                  "text": text[:1500]}
        if k % 5 == 0:
            print(f"  {k}/{len(sel)} · {time.time() - t0:.0f}초 · {dict(st)}", flush=True)
    summary = {"n": len(sel), "by": {f"{b}|{s}": n for (b, s), n in st.items()},
               "read": sum(n for (_, s), n in st.items() if s == "읽음"), "secs": round(time.time() - t0)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    OUT.write_text(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
