#!/usr/bin/env python3
"""내보낸 조각을 슈퍼베이스 Storage 공개 버킷에 올린다.

사람 결정(2026-09-20): 앱 데이터는 **따로 올린다.** 데이터 저장소의 app/ 폴더도,
layer-web 저장소 직접도 아니다 — 그러면 데이터 갱신이 앱 배포에 묶인다. 코드를 한 줄도
안 고쳤는데 매일 새 배포가 나가고, 배포를 미루면 데이터가 낡는다.

지켜야 할 둘(사람이 댄 것):
  ① 평문 JSON 으로 올린다. 미리 압축하지 않는다 — 앞의 클라우드플레어가 brotli 로
     줄여 준다. 미리 gzip 해서 올리면 그 위에 brotli 가 한 번 더 걸려 오히려 늘었다
     (728,949 → 728,959 바이트, 실측 2026-09-20).
  ② 한 파일이 50MB 를 넘으면 안 된다(버킷 상한). 지금은 제일 큰 조각이 gzip 2MB
     안쪽이라 걸릴 게 없지만, 설명이나 상세를 한 파일로 합치면 걸린다 — 그래서 여기서 막는다.

    py scripts/upload_supabase.py --dir /tmp/app --prefix ""
"""
from __future__ import annotations
import argparse, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

MAX_BYTES = 50 * 1024 * 1024


def one(sess, base: str, bucket: str, key: str, path: Path, root: Path) -> tuple[str, int, str]:
    rel = str(path.relative_to(root)).replace(os.sep, "/")
    size = path.stat().st_size
    if size > MAX_BYTES:
        return rel, size, f"너무 크다 ({size/1048576:.0f}MB > 50MB) — 쪼개야 한다"
    url = f"{base}/storage/v1/object/{bucket}/{rel}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Content-Encoding 은 안 붙인다. 슈퍼베이스가 어차피 안 전해 주고, 앞의
        # 클라우드플레어가 알아서 brotli 로 줄여 준다(실측 2026-09-20).
        "Cache-Control": "public, max-age=300",
        "x-upsert": "true",
    }
    body = path.read_bytes()
    for attempt in range(3):
        try:
            r = sess.post(url, headers=headers, data=body, timeout=120)
            if r.status_code in (200, 201):
                return rel, size, ""
            if r.status_code == 409:      # 이미 있다 — 덮어쓴다
                r = sess.put(url, headers=headers, data=body, timeout=120)
                if r.status_code in (200, 201):
                    return rel, size, ""
            err = f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(2 ** attempt)
    return rel, size, err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="내보낸 폴더")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    bucket = os.environ.get("SUPABASE_BUCKET") or "layer"
    # 비밀값 이름이 저장소마다 다를 수 있다 — 먼저 찾은 것을 쓰고 **어느 이름이었는지** 찍는다.
    # 값은 절대 안 찍는다. 첫 판이 「SUPABASE_SERVICE_KEY: (빈칸)」으로 죽었는데,
    # 어느 이름이 비었는지 말해 주지 않으면 사람이 또 짐작해야 한다.
    NAMES = ("SUPABASE_SERVICE_KEY", "SUPABASE_KEY", "LAYER", "layer", "SUPABASE_SERVICE_ROLE_KEY")
    key = used = ""
    for n in NAMES:
        if (os.environ.get(n) or "").strip():
            key, used = os.environ[n].strip(), n
            break
    if not base or not key:
        print("열쇠를 못 찾았다. 본 이름: " + ", ".join(
            f"{n}={'있음' if (os.environ.get(n) or '').strip() else '빈칸'}" for n in NAMES),
            file=sys.stderr)
        print(f"SUPABASE_URL={'있음' if base else '빈칸'}", file=sys.stderr)
        return 2
    print(f"열쇠는 `{used}` 에서 읽었다 (길이 {len(key)}자)")

    root = Path(args.dir)
    files = sorted(p for p in root.rglob("*.json") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    big = [p for p in files if p.stat().st_size > MAX_BYTES]
    print(f"올릴 것 {len(files):,}개 · 합계 {total/1048576:.1f} MB · 버킷 {bucket}")
    if big:
        print("50MB 넘는 파일:", ", ".join(p.name for p in big), file=sys.stderr)
    if args.dry:
        print("(--dry 라 안 올렸다)")
        return 0

    sess = requests.Session()

    # ── 없어진 조각을 쓸어낸다 ─────────────────────────────────────────────
    # 올리기만 하고 지우지 않아서, 내보내기에서 빠진 파일이 버킷에 그대로 남았다.
    # 앱이 `descs/99-is.json` 을 다시 받아 「프리오더 마감: 2021/8/23」이 도로 떴다
    # (앱 쪽 지적 2026-09-20). 도장의 파일 목록에는 없는데 파일은 살아 있었다.
    #
    # 지우는 일이라 빗장을 셋 건다:
    #   ① **우리가 만드는 자리만** 본다 — 다른 것이 같은 버킷에 있어도 안 건드린다
    #   ② 이번 내보내기가 온전할 때만 — 파일이 100개도 안 되면 뭔가 잘못된 판이다
    #   ③ 지우기 전에 **무엇을 지우는지 전부 찍는다**
    MINE = ("catalog.json", "search.json", "index/", "brands/", "descs/")
    if len(files) >= 100:
        want = {str(p.relative_to(root)).replace("\\", "/") for p in files}
        have, stale = [], []
        for pre in ("index", "brands", "descs", ""):
            r = sess.post(f"{base}/storage/v1/object/list/{bucket}",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"prefix": pre, "limit": 5000}, timeout=60)
            if r.status_code != 200:
                print(f"버킷 목록 못 읽음({pre or '/'}): {r.status_code} — 쓸어내기 건너뛴다",
                      file=sys.stderr)
                have = None
                break
            for o in r.json():
                rel = f"{pre}/{o['name']}" if pre else o["name"]
                if o.get("id") and rel.endswith(".json"):
                    have.append(rel)
        if have is not None:
            stale = [h for h in have
                     if h not in want and any(h == m or h.startswith(m) for m in MINE)]
            if stale:
                print(f"버킷에만 남은 조각 {len(stale)}개 — 지운다: " + ", ".join(sorted(stale)[:20]))
                if not args.dry:
                    d = sess.delete(f"{base}/storage/v1/object/{bucket}",
                                    headers={"Authorization": f"Bearer {key}",
                                             "Content-Type": "application/json"},
                                    json={"prefixes": stale}, timeout=120)
                    print(f"   지우기 {d.status_code}")
            else:
                print("버킷에만 남은 조각 없음")

    ok = fail = 0
    errs = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for rel, size, err in ex.map(lambda p: one(sess, base, bucket, key, p, root), files):
            if err:
                fail += 1
                if len(errs) < 10:
                    errs.append(f"{rel}: {err}")
            else:
                ok += 1
            if (ok + fail) % 200 == 0:
                print(f"  {ok + fail}/{len(files)}", flush=True)
    print(f"올렸다 {ok:,} · 실패 {fail:,}")
    for e in errs:
        print("   ", e, file=sys.stderr)
    if fail:
        return 1
    print(f"\n공개 주소: {base}/storage/v1/object/public/{bucket}/catalog.json.gz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
