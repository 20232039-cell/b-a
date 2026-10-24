"""배치11(56개 미채움 브랜드 중 25개 후보) URL 생존 확인. check_official_urls.py와 동일 로직, 후보 리스트만 다름."""
import csv
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

TIMEOUT = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LayerBrandCheck/1.0)"}
PARKED_MARKERS = [
    "domain is for sale", "buy this domain", "이 도메인은", "도메인 구매",
    "godaddy", "domain parking", "expired", "만료된 도메인",
]

CANDIDATES = [
    ("유라고", "https://www.u-rago.com"),
    ("Kuho", "http://www.kuho.co.kr"),
    ("Hyein Seo", "https://hyeinseo.com"),
    ("Juun.J", "http://www.juunj.com"),
    ("System", "http://www.system.co.kr"),
    ("Blindness", "https://www.blindnessshop.com"),
    ("Lucky Chouette", "https://www.luckychouette.com"),
    ("SJYP", "https://sjyp.kr"),
    ("Mojo.S.Phine", "https://mojosphine.daehyuninside.com"),
    ("Vlas Blomme", "https://vlasblomme.jp"),
    ("Unaffected", "https://unaffected.co.kr"),
    ("More than dope", "http://morethandope.com"),
    ("Sandinista", "https://www.sndnst.com"),
    ("IISE", "https://iise.co.kr"),
    ("Sunday Off Club", "https://sundayoffclub.com"),
    ("Charms", "https://charms.kr"),
    ("Heritagefloss", "https://heritagefloss.com"),
    ("Travel", "https://travelwebsite.kr"),
    ("Suisuee", "https://suisuee.com"),
    ("Suecomma Bonni", "http://www.suecommabonnie.com"),
    ("Critic", "https://criticwear.co.kr"),
    ("Liful", "https://lifulpixel.cafe24.com"),
    ("13Month", "http://shop2.thirteenmonth.cafe24.com"),
    ("포스센스티브", "https://forcesensitive.shop"),
    ("왑웍", "https://wapworks.shop"),
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
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(check_one, n, u) for n, u in CANDIDATES]
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            print(f"[{i}/{len(CANDIDATES)}] {res['verdict']:<16} {res['name']}  {res['title']}")

    results.sort(key=lambda r: (r["verdict"] != "OK", r["name"]))
    with open("data/batch11_check.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "official_url", "final_url", "status_code", "verdict", "title"])
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    main()
