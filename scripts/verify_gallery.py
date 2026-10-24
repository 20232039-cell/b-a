"""갤러리 주소가 실제로 열리는지 브랜드별로 확인한다.

크롤러는 상품 썸네일(/web/product/extra/small/…)을 찾아 주소를 /big/ 으로 바꿔 저장한다.
그런데 그 크기를 두지 않는 매장이 있어 만들어낸 주소가 404 로 죽는다 — frizmworks 표본
6개 중 1개가 그랬다(2026-09-04). 앱이 사진 여러 장을 띄우려면 이 구멍을 먼저 막아야 한다.

브랜드마다 몇 장을 찍어 /big/ → /medium/ → /small/ 순으로 열리는 크기를 찾고,
브랜드별로 쓸 크기를 data/gallery_size.json 에 남긴다. 사진 CDN 설정은 매장 단위라
표본으로 브랜드 전체를 판단한다 — 표본이 갈리는 브랜드는 mixed 로 적어 둔다.

사용: py scripts/verify_gallery.py [--per-brand 3] [--brands a,b]
"""
from __future__ import annotations
import argparse, json, random, re, time
from collections import defaultdict
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +size-check)"}
SIZES = ("big", "medium", "small")


def variants(url: str) -> dict[str, str]:
    """/web/product/extra/big/… 의 크기 자리만 바꾼 주소들."""
    m = re.search(r"/web/product/(extra/)?(big|medium|small|tiny)/", url)
    if not m:
        return {}
    return {s: url[:m.start()] + f"/web/product/{m.group(1) or ''}{s}/" + url[m.end():] for s in SIZES}


def head(url: str, timeout=15) -> int:
    if url.startswith("//"):
        url = "https:" + url
    for k in range(2):
        try:
            return requests.head(url, headers=UA, timeout=timeout, allow_redirects=True).status_code
        except Exception:
            time.sleep(1.5 * (k + 1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-brand", type=int, default=3)
    ap.add_argument("--brands", default="")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()
    want = {b for b in args.brands.split(",") if b}
    rnd = random.Random(20260904)

    out, summary = {}, []
    for p in sorted(CRAWL.glob("*.jsonl")):
        brand = p.stem
        if want and brand not in want:
            continue
        pool = []
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            for u in (d.get("gallery") or []):
                if variants(u):
                    pool.append(u)
        if not pool:
            continue
        rnd.shuffle(pool)
        votes = defaultdict(int)
        checked = 0
        for u in pool[:args.per_brand]:
            v = variants(u)
            for s in SIZES:
                code = head(v[s])
                time.sleep(args.delay)
                if code == 200:
                    votes[s] += 1
                    break
            else:
                votes["none"] += 1
            checked += 1
        best = max(votes, key=lambda k: votes[k]) if votes else "none"
        mixed = len([k for k in votes if k != "none"]) > 1
        out[brand] = {"use": best, "mixed": mixed, "checked": checked, "votes": dict(votes)}
        summary.append((brand, best, mixed, dict(votes)))
        print(f"  {brand:20s} {best:7s} {'섞임' if mixed else '    '} {dict(votes)}", flush=True)

    (DATA / "gallery_size.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bad = [b for b, s, _, _ in summary if s != "big"]
    print(f"\n브랜드 {len(summary)}개 · /big/ 이 안 되는 곳 {len(bad)}개: {bad}")


if __name__ == "__main__":
    main()
