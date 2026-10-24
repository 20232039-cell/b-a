"""매장이 아직 **살아 있는지** 상품 페이지로 확인한다 — 상태 코드 말고 내용으로.

왜 만들었나(2026-09-18, 사람이 화면으로 짚어 줬다). till-i-die 는 우리가 `tillidie.co.kr` 를
긁고 있는데 실제 매장은 `tilldie.co.kr` 다(i 하나 차이). 옛 주소는 **200 을 돌려준다** —
118,003자짜리 페이지가 멀쩡히 오는데 「실루엣·코튼·총기장·어깨단면」이 한 번도 안 나온다.
껍데기다. 주소 점검표에도 `200 · OK` 로 찍혀 있어 상태 코드로는 절대 못 잡는다.
그렇게 1,573벌이 통째로 옛 데이터로 남아 있었다.

**성공의 잣대를 바꾼다.** 페이지를 받았느냐가 아니라 **상품 글이 왔느냐**다 — 푸터(배송·반품·
개인정보·쿠폰)를 줄 단위로 걷어 낸 뒤 소재·디테일·색·치수 가운데 하나라도 남아야 한다.
사이즈뿐 아니라 가격·품절·신상까지 같은 문제를 안고 있으므로 매장 단위로 먼저 가른다.

매장마다 상품 세 벌을 받아 셋 중 하나로 가른다:
    살아 있다      알맹이가 나온다
    껍데기         200 인데 알맹이가 없다 ← 주소가 바뀌었거나 매장이 닫혔다. 사람이 봐야 한다
    글이 그림에만  알맹이는 없지만 상세 그림이 여럿이다 — 매장 설계다. OCR 몫
"""
from __future__ import annotations
import argparse, collections, csv, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CRAWL = ROOT / "data" / "crawl"
OUT = ROOT / "data" / "_store_health.csv"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
       "Accept-Language": "ko,en;q=0.8"}

FOOT = re.compile(r"교환|반품|환불|배송|결제|무통장|카드사|입금|계좌|개인정보|이용약관|사업자|"
                  r"통신판매|고객센터|문의|쿠폰|적립|마일리지|회원|로그인|장바구니|위시|후기|"
                  r"공지|이벤트|품절|재입고|RETURN|EXCHANGE|SHIPPING|DELIVERY|PRIVACY|TERMS|CS|Q&A", re.I)
MEAT = re.compile(r"코튼|면\s|울|워싱|데님|린넨|폴리|나일론|아크릴|레이온|비스코스|캐시미어|스판|혼방|"
                  r"cotton|wool|linen|nylon|poly|acryl|rayon|viscose|cashmere|denim|"
                  r"핏|실루엣|절개|스티치|봉제|안감|밑단|소매|카라|넥|포켓|지퍼|단추|"
                  r"총장|어깨|가슴|허리|기장", re.I)


def look(url: str) -> dict:
    try:
        r = requests.get(url, headers=HDR, timeout=25)
    except Exception as e:
        return {"code": 0, "err": type(e).__name__, "meat": 0, "imgs": 0, "bytes": 0}
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", x).strip() for x in soup.get_text("\n").splitlines()]
    meat = [x for x in lines if len(x) >= 15 and not FOOT.search(x) and MEAT.search(x)]
    imgs = soup.select("#prdDetail img, .xans-product-detail img, #detail img, .detailArea img")
    return {"code": r.status_code, "err": "", "meat": len(meat), "imgs": len(imgs),
            "bytes": len(r.text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", default="", help="공백으로 나눈 slug. 비우면 전부")
    ap.add_argument("--per", type=int, default=3, help="매장마다 볼 상품 수")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    want = set(args.brands.split()) if args.brands.strip() else None
    jobs = []
    for p in sorted(CRAWL.glob("*.jsonl")):
        b = p.name[:-6]
        if b.startswith("_") or (want and b not in want):
            continue
        urls = []
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            try:
                d = json.loads(l)
            except Exception:
                continue
            if d.get("source_url"):
                urls.append(d["source_url"])
            if len(urls) >= args.per:
                break
        if urls:
            jobs.append((b, urls))
    print(f"매장 {len(jobs)}곳 · 매장마다 {args.per}벌", flush=True)

    rows, tally = [], collections.Counter()

    def one(job):
        b, urls = job
        res = [look(u) for u in urls]
        meat = sum(1 for r in res if r["meat"])
        imgs = max((r["imgs"] for r in res), default=0)
        code = max((r["code"] for r in res), default=0)
        if code == 0:
            v = "못 받음"
        elif meat:
            v = "살아 있다"
        elif imgs >= 3:
            v = "글이 그림에만"
        else:
            v = "**껍데기**"
        return {"brand_slug": b, "verdict": v, "code": code, "meat_pages": meat,
                "detail_imgs": imgs, "bytes": max((r["bytes"] for r in res), default=0),
                "sample": urls[0]}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(one, jobs):
            rows.append(r)
            tally[r["verdict"]] += 1
            if r["verdict"] != "살아 있다":
                print(f"  {r['brand_slug']:22s} {r['verdict']:12s} "
                      f"code {r['code']} · 알맹이쪽 {r['meat_pages']}/{args.per} · "
                      f"상세그림 {r['detail_imgs']} · {r['sample'][:70]}", flush=True)
            time.sleep(0.05)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["verdict"], x["brand_slug"])):
            w.writerow(r)
    print("\n== 갈래별")
    for k, v in tally.most_common():
        print(f"  {v:4d}  {k}")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
