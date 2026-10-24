"""brands_seed.csv의 official_url 60개가 살아있는지 1차 스크리닝.
크롤링 붙이기 전에 죽은/의심스러운 URL을 걸러낸다. 실제 브랜드 매칭 확인(2차)은 사람이 한다.

사용: py scripts/check_official_urls.py
출력: data/official_url_check.csv (status, verdict, note)
"""
import csv
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SEED_CSV = "data/brands_seed.csv"
OUT_CSV = "data/official_url_check.csv"
TIMEOUT = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LayerBrandCheck/1.0)"}

# 도메인 판매/만료 페이지에서 흔히 보이는 문구 (파킹 도메인 감지용)
PARKED_MARKERS = [
    "domain is for sale", "buy this domain", "이 도메인은", "도메인 구매",
    "godaddy", "domain parking", "expired", "만료된 도메인",
]


def check_one(name, url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        status = resp.status_code
        title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip()[:80] if title_match else ""
        body_lower = resp.text.lower()[:5000]

        if status != 200:
            verdict = "DEAD"
        elif any(m in body_lower for m in PARKED_MARKERS):
            verdict = "PARKED"
        elif len(resp.text) < 500:
            verdict = "SUSPICIOUS_EMPTY"
        else:
            verdict = "OK"

        return {
            "name": name, "official_url": url, "final_url": resp.url,
            "status_code": status, "verdict": verdict, "title": title,
        }
    except requests.exceptions.RequestException as e:
        return {
            "name": name, "official_url": url, "final_url": "",
            "status_code": "", "verdict": "DEAD", "title": type(e).__name__,
        }


def main():
    with open(SEED_CSV, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["official_url"].strip()]

    print(f"검사 대상: {len(rows)}개")

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(check_one, r["name"], r["official_url"].strip()) for r in rows]
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            print(f"[{i}/{len(rows)}] {res['verdict']:<16} {res['name']}")

    results.sort(key=lambda r: (r["verdict"] != "OK", r["name"]))
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "official_url", "final_url", "status_code", "verdict", "title"])
        writer.writeheader()
        writer.writerows(results)

    ok = sum(1 for r in results if r["verdict"] == "OK")
    print(f"\nOK {ok} / DEAD {sum(1 for r in results if r['verdict'] == 'DEAD')} / "
          f"PARKED {sum(1 for r in results if r['verdict'] == 'PARKED')} / "
          f"SUSPICIOUS {sum(1 for r in results if r['verdict'] == 'SUSPICIOUS_EMPTY')}")
    print(f"결과 저장: {OUT_CSV}")


if __name__ == "__main__":
    main()
