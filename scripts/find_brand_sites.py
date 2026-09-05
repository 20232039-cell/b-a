#!/usr/bin/env python3
"""국내 브랜드의 공식몰·이메일·인스타를 찾는다.

왜 필요한가: data/musinsa_kept_domestic.csv 에 국내 브랜드 608곳이 있는데 공식몰 링크가
한 곳도 없다. 링크가 없으면 크롤러가 손을 못 댄다. 브랜드 「발견」은 이미 끝나 있고
남은 일은 「연결」이다.

어떻게 찾나: 검색 엔진은 봇을 막으니 도메인을 짐작해서 두드린다. 국내 패션 브랜드는
대개 이름을 그대로 도메인으로 쓴다(insilence.co.kr · junne.com · lecyto.kr).
slug 와 영문명으로 후보를 만들어 .com/.co.kr/.kr/.net 을 돌려 본다.

★ 검증이 핵심이다. 짐작만 하면 남의 사이트를 물어 온다 — kirsh 로 두드렸더니
indianaadoption.com 이 200 을 돌려줬다(2026-09-05). 그래서 두 가지를 같이 본다:
  ① 페이지에 브랜드 이름(영문 또는 한글)이 있는가
  ② 국내 쇼핑몰의 흔적이 있는가(원·₩·장바구니·cafe24·사업자등록번호)
둘 다여야 받아들인다.

덤으로 얻는 것: 하단 사업자정보에 이메일·인스타가 거의 늘 있다(실측 3/3).

    py scripts/find_brand_sites.py                      # 전부(이어서)
    py scripts/find_brand_sites.py --limit 30           # 30곳만
    py scripts/find_brand_sites.py --shard 2/6          # Actions 샤딩
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import socket
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "musinsa_kept_domestic.csv"
OUT = DATA / "brand_contacts.csv"

FIELDS = ["name", "name_en", "slug", "official_url", "email", "instagram",
          "biz_no", "ceo", "shop_platform", "status", "checked_at"]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

EMAIL = re.compile(r"[\w.\-+]+@[\w\-]+\.[A-Za-z]{2,}")
INSTA = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})")
BIZ = re.compile(r"사업자\s*등록\s*번호[^0-9]{0,14}(\d{3}-?\d{2}-?\d{5})")
CEO = re.compile(r"대표(?:자|이사|자명)?\s*[:：]?\s*([가-힣]{2,6})")
# 폰트·라이브러리 버전 문자열이 이메일처럼 생겼다(pretendard@v1.3.9) — 걸러 낸다.
BAD_EMAIL = re.compile(r"@v?\d|\.(png|jpg|jpeg|gif|svg|webp|css|js)$|example\.|sentry|wix|@2x", re.I)
SHOP_SIGN = re.compile(r"장바구니|주문조회|배송|사업자등록번호|₩|cafe24|xans-|ec-base|마이페이지|회원가입", re.I)

TLDS = (".com", ".co.kr", ".kr", ".net", ".shop")


def fetch(url: str, timeout: int = 10) -> tuple[str, str] | tuple[None, None]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
        r = urllib.request.urlopen(req, timeout=timeout, context=CTX)
        raw = r.read(400_000)
        if r.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        return r.geturl(), raw.decode("utf-8", "replace")
    except Exception:
        return None, None


def candidates(slug: str, name_en: str) -> list[str]:
    bases: list[str] = []
    for b in (slug or "", (name_en or "").lower()):
        b = re.sub(r"[^a-z0-9]", "", b.lower())
        if len(b) >= 2 and b not in bases:
            bases.append(b)
    for b in list(bases):
        for suffix in ("official", "store", "shop"):
            if b + suffix not in bases:
                bases.append(b + suffix)
    out = []
    for b in bases[:4]:
        for t in TLDS:
            out.append(f"https://{b}{t}")
    return out


def looks_like_brand(html: str, name: str, name_en: str) -> bool:
    """브랜드 이름과 국내 쇼핑몰 흔적이 둘 다 있어야 그 브랜드의 몰로 본다."""
    if not SHOP_SIGN.search(html):
        return False
    low = html.lower()
    for n in (name_en or "", name or ""):
        n = (n or "").strip()
        if len(n) >= 2 and n.lower() in low:
            return True
    return False


def contacts(html: str) -> dict:
    emails = [e for e in EMAIL.findall(html) if not BAD_EMAIL.search(e)]
    # 회사 도메인 메일을 먼저 — naver/gmail 보다 브랜드가 직접 쓰는 주소다
    emails.sort(key=lambda e: (e.split("@")[-1] in ("naver.com", "gmail.com", "daum.net", "hanmail.net"), len(e)))
    ig = [i for i in INSTA.findall(html) if i.lower() not in ("p", "explore", "accounts", "reel", "reels")]
    biz = BIZ.search(html)
    ceo = CEO.search(html)
    plat = ("cafe24" if re.search(r"cafe24|xans-|ec-base|/exec/front/", html, re.I) else
            "shopify" if "shopify" in html.lower() else
            "imweb" if "imweb" in html.lower() else
            "makeshop" if re.search(r"makeshop|godomall", html, re.I) else "")
    return {"email": emails[0] if emails else "", "instagram": ig[0] if ig else "",
            "biz_no": biz.group(1) if biz else "", "ceo": ceo.group(1) if ceo else "",
            "shop_platform": plat}


def load_done() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    with OUT.open(encoding="utf-8") as f:
        return {r["slug"]: r for r in csv.DictReader(f)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--shard", default="1/1")
    ap.add_argument("--redo", action="store_true", help="이미 못 찾은 것도 다시 시도")
    args = ap.parse_args()

    socket.setdefaulttimeout(12)
    rows = list(csv.DictReader(io.StringIO(Path(args.source).read_text(encoding="utf-8").lstrip("﻿"))))
    k, n = (int(x) for x in args.shard.split("/"))
    rows = [r for i, r in enumerate(rows) if i % n == k - 1]

    done = load_done()
    todo = [r for r in rows if r.get("slug") and (args.redo or r["slug"] not in done
                                                  or not done[r["slug"]].get("official_url"))]
    if args.limit:
        todo = todo[:args.limit]
    print(f"대상 {len(todo)} (전체 {len(rows)} · 이미 {len(done)})", flush=True)

    found = 0
    for i, r in enumerate(todo, 1):
        name, en, slug = r.get("name", ""), r.get("name_en", ""), r["slug"]
        rec = {"name": name, "name_en": en, "slug": slug, "official_url": "", "email": "",
               "instagram": "", "biz_no": "", "ceo": "", "shop_platform": "",
               "status": "못찾음", "checked_at": time.strftime("%Y-%m-%d")}
        for url in candidates(slug, en):
            final, html = fetch(url)
            if not html:
                continue
            if looks_like_brand(html, name, en):
                rec.update(contacts(html))
                rec["official_url"] = final
                rec["status"] = "확인"
                found += 1
                break
            time.sleep(args.delay)
        done[slug] = rec
        time.sleep(args.delay)
        if i % 10 == 0:
            print(f"  {i}/{len(todo)} · 찾음 {found}", flush=True)
            write(done)
    write(done)
    print(f"끝 — 대상 {len(todo)} · 공식몰 찾음 {found} · 누적 {sum(1 for v in done.values() if v.get('official_url'))}", flush=True)


def write(done: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for slug in sorted(done):
            w.writerow({k: done[slug].get(k, "") for k in FIELDS})


if __name__ == "__main__":
    main()
