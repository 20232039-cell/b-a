"""해석기 시험 — 데이터를 쓰지 않고 몇 벌만 읽어 칸이 얼마나 찼는지 본다.

    python scripts/stores/_try.py concepts1one            # 목록 + 앞·중간·끝에서 고른 12벌
    python scripts/stores/_try.py concepts1one --n 30 --dump out.jsonl
    python scripts/stores/_try.py concepts1one --url https://…   # 한 벌만
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import crawl_cafe24 as cc          # noqa: E402
import crawl_shopify as cs         # noqa: E402
import crawl_pages                 # noqa: E402

KEYS = ["name", "price", "image_url", "gallery", "description", "detail_text", "size_table",
        "detail_images", "options", "soldout_options", "category_names"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--url")
    ap.add_argument("--dump")
    a = ap.parse_args()
    mod = crawl_pages.store(a.slug)
    http = cc.PoliteSession(delay=getattr(mod, "PAGE_DELAY", crawl_pages.PAGE_DELAY))
    now = datetime.now(cc.KST).strftime("%Y-%m-%dT%H:%M:%S")
    if hasattr(mod, "fetch_rows") and not a.url:
        rows = mod.fetch_rows(http, a.slug, now, print) or []
        print(f"fetch_rows: {len(rows)}벌")
        rows = rows[: a.n] if len(rows) <= a.n else [rows[i * len(rows) // a.n] for i in range(a.n)]
    else:
        if a.url:
            urls = [a.url]
        else:
            urls = mod.list_urls(http, print)
            if urls is None:
                sys.exit("list_urls → None")
            print(f"list_urls: {len(urls)}개")
            if len(urls) > a.n:
                urls = [urls[i * len(urls) // a.n] for i in range(a.n)]
        rows = []
        for u in urls:
            r = cs.get_patient(http, u, print)
            if r is None or r.status_code != 200:
                print(f"  받기 실패 {getattr(r, 'status_code', None)} {u}")
                continue
            d = mod.parse_page(r.text, u, a.slug, now, http)
            if d is None:
                print(f"  해석 None {u}")
                continue
            if d.get("_skip"):
                print(f"  건너뜀 {d['_skip']} {u}")
                continue
            rows.append(d)
    n = len(rows)
    print(f"\n읽은 벌 {n}")
    for k in KEYS:
        print(f"  {k:16s} {sum(1 for d in rows if d.get(k))}/{n}")
    print(f"  soldout          {sum(1 for d in rows if d.get('soldout'))}/{n}")
    for d in rows[:3]:
        v = {k: (x[:300] if isinstance(x, str) else x) for k, x in d.items()}
        print(json.dumps(v, ensure_ascii=False)[:2500])
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
