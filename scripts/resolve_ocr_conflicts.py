"""rebase 도중 부딪힌 `data/crawl/ocr/*.jsonl` 을 **내용으로** 합친다.

왜 필요한가. collect 는 수확을 밀기 전에 원격 위로 rebase 하는데, 같은 매장을 읽은 판이
둘 겹치면 그 매장의 jsonl 이 부딪힌다. 생성물(product_sizes.json 등)은 「저쪽 것을 받고
다시 만들면 그만」이라 이미 자동으로 풀지만, 수확은 덮으면 안 되니 rebase 를 접었다.
그래서 **판 하나가 통째로 날아갔다** — 2026-09-17 판 43(조각 78개 · 두 시간)이 푸시
단계에서 세 번 다 실패하고 끝났다. 그 사이 판 44 가 같은 매장을 밀었기 때문이다.

덮을 필요가 없다. 이 파일은 한 줄이 한 상품이고, 합치는 규칙은 collect 가 조각을 모을 때
쓰는 것과 같다 — **그림을 더 읽었거나 글이 더 나온 쪽이 이긴다.** 양쪽을 다 읽어
상품번호로 합치면 어느 쪽 수확도 잃지 않는다.

`git rebase` 도중에 부르면 부딪힌 파일만 골라 합치고 `git add` 까지 한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

OCR_PATH = re.compile(r"^data/crawl/ocr/[^/]+\.jsonl$")


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _load(blob: str) -> dict:
    out = {}
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "product_no" in d:
            out[d["product_no"]] = d
    return out


def _rank(d: dict) -> tuple[int, int]:
    return len(d.get("images") or []), len(d.get("ocr_text") or "")


def main() -> int:
    conflicted = [f for f in _run("git", "diff", "--name-only", "--diff-filter=U").split()
                  if OCR_PATH.match(f)]
    if not conflicted:
        return 0
    for f in conflicted:
        ours = _load(_run("git", "show", f":2:{f}"))
        theirs = _load(_run("git", "show", f":3:{f}"))
        merged = dict(theirs)
        kept = 0
        for no, d in ours.items():
            old = merged.get(no)
            if old is None or _rank(d) > _rank(old):
                merged[no] = d
                kept += 1
        with open(f, "w", encoding="utf-8") as fh:
            for d in merged.values():
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        subprocess.run(["git", "add", f], check=True)
        print(f"  {f} — 저쪽 {len(theirs)} · 우리 {len(ours)} → {len(merged)}벌"
              f"(우리 것이 이긴 상품 {kept})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
