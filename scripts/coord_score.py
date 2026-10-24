"""코디 조합 채점(2026-11-20) — 옷 두세 벌(아우터 · 상의 · 하의)의 대표 사진을 나란히 붙여 GPT 에게 보여 주고
「같이 입을 만한가」를 1~5점으로 받는다. OpenAI Batch API(반값)로 보낸다.

쓰는 곳: 앱 코디 추천 · 에디터 컬렉션 후보, 나중에 코디 궁합 모델을 학습할 정답 데이터.
조합은 판매 중 옷에서 성별이 맞고 가격대가 크게 어긋나지 않는 것을 무작위로 뽑는다 — 좋은 조합만 고르면
학습 데이터에 「안 어울림」이 없어지므로 일부러 섞는다. 이미 채점한 조합은 건너뛴다.

결과: data/llm/coord_score/<시각>_<이름>.jsonl (+ .summary.json)

  python scripts/coord_score.py --n 200 --model gpt-6.1-sol --name sample
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_DIR = DATA / "llm" / "coord_score"
API = "https://api.openai.com/v1"
ROLE = {"Outerwear": "o", "Tops": "t", "Shirts": "t", "Knitwear": "t", "Pants": "b", "Denim": "b", "Skirts": "b"}
H = 512   # 붙인 그림의 높이

PROMPT = """You are a senior fashion stylist for a Korean fashion app. The image shows one outfit: product photos placed
side by side, left to right in the order of the items listed below (outer → top → bottom). Many photos are worn shots by
models — judge ONLY the listed garment in each photo, not other clothes the model wears.

Decide whether the pieces work together as one outfit someone would actually wear, for the stated gender, in the
season the pieces suggest. Consider color harmony, mood/style consistency, silhouette and proportion balance, formality
match, season match between the pieces, pattern clash, and whether a photo makes the garment unreadable.

Scoring (be strict — an average combination is 3): 5 = editor pick, 4 = good, 3 = passable/unremarkable,
2 = awkward, 1 = clashes. reason: Korean, ≤40 characters, concrete (name colors/shapes), no generic praise.
title: only when score ≥4, a natural Korean title ≤12 characters describing the look concretely; otherwise "".
"""

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["score", "issue", "reason", "title"],
          "properties": {"score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                         "issue": {"type": "string", "enum": ["", "color", "mood", "silhouette", "formality", "season",
                                                               "pattern", "photo", "item"]},
                         "reason": {"type": "string"}, "title": {"type": "string"}}}


def sess():
    import requests
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {os.environ['OPENAI_API_KEY']}"
    return s


def done_keys():
    keys = set()
    for f in OUT_DIR.glob("*.jsonl"):
        for l in f.open(encoding="utf-8"):
            try:
                o = json.loads(l)
            except Exception:
                continue
            if o.get("out"):
                keys.add(o["key"])
    return keys


def pick(n, seed):
    rows = [r for r in csv.DictReader((DATA / "products_full.csv").open(encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("image_url") and r.get("category") in ROLE]
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    by = {}
    for r in rows:
        g = {"MENSWEAR": "M", "WOMENSWEAR": "W"}.get(r.get("gender_target"), "U")
        by.setdefault(ROLE[r["category"]], []).append((g, r))
    done = done_keys()
    out, tries = [], 0
    while len(out) < n and tries < n * 50:
        tries += 1
        g = rng.choice("MW")
        kind = rng.choice(["tb", "tb", "ob", "otb"])
        items = []
        for role in kind:
            cand = [r for gg, r in rng.sample(by[role], min(60, len(by[role]))) if gg in (g, "U")]
            if not cand:
                break
            items.append(rng.choice(cand))
        if len(items) != len(kind) or len({r["brand_slug"] for r in items}) < 1:
            continue
        ps = [float(r.get("price") or 0) for r in items]
        if min(ps) <= 0 or max(ps) / min(ps) > 4:
            continue
        key = "|".join(r["source_url"] for r in items)
        if key in done:
            continue
        desc = []
        for role, r in zip(kind, items):
            t = (tags.get(r["source_url"]) or {}).get("tags") or {}
            desc.append({"role": {"o": "outer", "t": "top", "b": "bottom"}[role], "name": r["name"], "subtype": r.get("subtype"),
                         "color": r.get("representative_color"), "fit": t.get("silhouette") or [], "length": t.get("length") or []})
        out.append({"key": key, "gender": g, "kind": kind, "items": desc, "imgs": [r["image_url"] for r in items]})
        done.add(key)
    return out


def montage(urls, http):
    from PIL import Image
    ims = []
    for u in urls:
        try:
            r = http.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return None
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            ims.append(im.resize((max(1, round(im.width * H / im.height)), H)))
        except Exception:
            return None
    W = sum(i.width for i in ims) + 8 * (len(ims) - 1)
    can = Image.new("RGB", (W, H), "white")
    x = 0
    for i in ims:
        can.paste(i, (x, 0)); x += i.width + 8
    buf = io.BytesIO()
    can.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default="gpt-6.1-sol", help="쉼표로 여럿 — 같은 조합을 모델마다")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--name", default="sample")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=1000)
    ap.add_argument("--hours", type=float, default=4.5)
    ap.add_argument("--resume", default="", help="<시각>_<이름> — 이미 보낸 배치를 거두기만 한다(조합은 입력 파일에서 되살린다)")
    args = ap.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 가 없다"); return 1
    if args.resume:
        return collect(sess(), args.resume, json.loads((OUT_DIR / f"{args.resume}.batch.json").read_text()), None, args.hours)
    import requests
    http = requests.Session()
    looks = pick(args.n, args.seed or int(time.time()))
    print(f"조합 {len(looks)}개 · {dict(Counter(x['kind'] for x in looks))}", flush=True)
    with ThreadPoolExecutor(16) as ex:
        imgs = list(ex.map(lambda x: montage(x["imgs"], http), looks))
    looks = [dict(x, img=b) for x, b in zip(looks, imgs) if b]
    print(f"그림 붙임 {len(looks)}", flush=True)
    api = sess()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")
    jobs = []
    for model in [m.strip() for m in args.model.split(",") if m.strip()]:
        for k in range(0, len(looks), args.chunk):
            part = looks[k:k + args.chunk]
            lines = []
            for i, x in enumerate(part):
                body = {"model": model, "reasoning_effort": args.effort,
                        "response_format": {"type": "json_schema", "json_schema": {"name": "outfit", "strict": True, "schema": SCHEMA}},
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": PROMPT + "\nGender: " + {"M": "men", "W": "women"}[x["gender"]] +
                             "\nItems (left to right):\n" + json.dumps(x["items"], ensure_ascii=False)},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + x["img"], "detail": "low"}}]}]}
                lines.append(json.dumps({"custom_id": f"c{k + i:05d}", "method": "POST", "url": "/v1/chat/completions", "body": body}))
            f = api.post(f"{API}/files", files={"file": ("b.jsonl", "\n".join(lines).encode())}, data={"purpose": "batch"}, timeout=300)
            f.raise_for_status()
            b = api.post(f"{API}/batches", json={"input_file_id": f.json()["id"], "endpoint": "/v1/chat/completions",
                                                 "completion_window": "24h"}, timeout=60)
            b.raise_for_status()
            jobs.append((model, k, b.json()["id"]))
            print(f"{model}: 배치 {b.json()['id']} 보냄 ({len(part)}개)", flush=True)
    (OUT_DIR / f"{tag}_{args.name}.batch.json").write_text(json.dumps(jobs) + "\n", encoding="utf-8")
    with (OUT_DIR / f"{tag}_{args.name}.looks.jsonl").open("w", encoding="utf-8") as fh:
        for x in looks:
            fh.write(json.dumps({k: x[k] for k in ("key", "gender", "kind", "items")}, ensure_ascii=False) + "\n")
    return collect(api, f"{tag}_{args.name}", jobs, looks, args.hours)


def get_json(api, url, tries=8):
    """오래 기다리는 동안 연결이 한 번 끊겨도 판 전체가 죽지 않게(2026-11-20 첫 판이 RemoteDisconnected 로 죽었다)."""
    for i in range(tries):
        try:
            r = api.get(url, timeout=120)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  다시 시도 {i + 1}: {type(e).__name__}", flush=True)
            time.sleep(min(300, 15 * 2 ** i))
    raise RuntimeError(f"{url} 를 못 받았다")


def collect(api, stem0, jobs, looks, hours):
    deadline = time.time() + hours * 3600
    while True:
        st = {bid: get_json(api, f"{API}/batches/{bid}").json() for _, _, bid in jobs}
        if all(s.get("status") in ("completed", "failed", "expired", "cancelled") for s in st.values()):
            break
        if time.time() > deadline:
            print("기다릴 시간이 다 됐다 — 배치는 두고 끝낸다"); return 1
        print("  " + " · ".join(f"{s.get('status')} {(s.get('request_counts') or {}).get('completed')}" for s in st.values()), flush=True)
        time.sleep(60)
    for model, k, bid in jobs:
        s = st[bid]
        lk = looks
        if lk is None:   # 되살리기: 보낸 입력 파일에서 조합 설명을 읽는다
            lk = {}
            for l in get_json(api, f"{API}/files/{s['input_file_id']}/content").text.splitlines():
                q = json.loads(l)
                txt = q["body"]["messages"][0]["content"][0]["text"]
                items = json.loads(txt.split("Items (left to right):\n", 1)[1])
                lk[int(q["custom_id"][1:])] = {"key": "", "gender": "M" if "Gender: men" in txt else "W",
                                               "kind": "".join(i["role"][0] for i in items), "items": items}
        if s.get("status") != "completed":
            print(f"{model} 배치 {bid} {s.get('status')} — {json.dumps(s.get('errors'))[:300]}")
        rows, use, sc = [], Counter(), Counter()
        for fid_key in ("output_file_id", "error_file_id"):
            fid = s.get(fid_key)
            if not fid:
                continue
            for l in get_json(api, f"{API}/files/{fid}/content").text.splitlines():
                o = json.loads(l)
                i = int(o["custom_id"][1:])
                x = lk[i]
                row = {"key": x["key"], "gender": x["gender"], "kind": x["kind"], "items": x["items"], "model": model}
                resp = (o.get("response") or {})
                if resp.get("status_code") == 200:
                    bd = resp["body"]
                    for kk, vv in (bd.get("usage") or {}).items():
                        if isinstance(vv, int):
                            use[kk] += vv
                    try:
                        row["out"] = json.loads(bd["choices"][0]["message"]["content"])
                        sc[row["out"]["score"]] += 1
                    except Exception:
                        row["raw"] = str(bd)[:500]
                else:
                    row["error"] = json.dumps(o.get("error") or resp.get("body"))[:300]
                rows.append(row)
        stem = f"{stem0}_{model}_{k}"
        with (OUT_DIR / f"{stem}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        summ = {"batch": bid, "model": model, "status": s.get("status"), "n": len(rows),
                "ok": sum(1 for r in rows if r.get("out")), "usage": dict(use), "scores": dict(sorted(sc.items()))}
        (OUT_DIR / f"{stem}.summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(summ, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
