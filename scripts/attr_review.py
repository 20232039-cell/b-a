"""상품 속성 사진 판독 — 표본(2026-11-16).

판매 중 상품을 품목별로 고르게 뽑아, 대표 사진과 갤러리 사진 두 장을 Claude 에게 보여 주고
지금 데이터의 품목 · 대표색 · 무늬 · 핏 · 소매 · 넥라인 · 기장 · 디테일이 맞는지 판정받는다.
틀린 칸은 고칠 값을 받는다. Message Batches 로 보내 값은 반값이다.

이 판은 표본(기본 500벌)이다. 결과로 칸마다 틀린 비율 · 벌당 비용을 재고, 쓸 만하면 전체로 늘린다.
결과는 data/llm/attr_review/<날짜>_<이름>.jsonl, 요약은 같은 이름의 .summary.json.

  python scripts/attr_review.py --n 500 --max-usd 30
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_DIR = DATA / "llm" / "attr_review"

MODEL = "claude-opus-5-5"
# 배치 단가(달러 / 100만 토큰) — 공식 가격의 반값. Opus 5.5: 입력 $4 · 출력 $20.
PRICE = {"claude-opus-5-5": (2.0, 10.0), "claude-sonnet-5-5": (1.0, 5.0), "claude-haiku-5-5": (0.05, 0.25)}
AXES = ["silhouette", "sleeve_length", "neckline", "length", "pattern"]
GARMENTS = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Skirts", "Denim", "Dresses"}
TALL_MAX = 1.8
IMG_SIDE = 768


def load_csv():
    with (DATA / "products_full.csv").open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def vocab(rows, tags):
    """칸마다 고를 수 있는 값 — 지금 데이터에 30번 넘게 나온 값만(새 낱말을 지어내지 않게)."""
    v = {}
    sub = Counter(r["subtype"] for r in rows if r.get("subtype"))
    v["subtype"] = sorted(s for s, n in sub.items() if n >= 10)
    col = Counter(r["representative_color"] for r in rows if r.get("representative_color"))
    v["color"] = sorted(s for s, n in col.items() if n >= 30)
    for ax in AXES + ["design_element"]:
        c = Counter(x for e in tags.values() for x in (e.get("tags") or {}).get(ax) or [])
        v[ax] = sorted(s for s, n in c.items() if n >= 30)
    return v


def gallery_shapes():
    shp = {}
    for p in sorted((DATA / "gallery_clean").glob("*.jsonl")):
        for l in p.open(encoding="utf-8"):
            try:
                o = json.loads(l)
            except Exception:
                continue
            shp[o.get("img")] = o
    return shp


def crawl_gallery():
    g = {}
    for p in sorted((DATA / "crawl").glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for l in p.open(encoding="utf-8"):
            try:
                d = json.loads(l)
            except Exception:
                continue
            if d.get("source_url") and d.get("gallery"):
                g[d["source_url"]] = d["gallery"]
    return g


def pick(rows, tags, n, seed):
    """판매 중 옷 · 태그 있음 · 사진 있음. 품목(category)마다 고르게, 브랜드당 3벌까지."""
    rng = random.Random(seed)
    pool = [r for r in rows if r.get("status") == "ON_SALE" and r.get("image_url") and r["source_url"] in tags
            and r.get("category") in GARMENTS]   # 소품 · 가방 · 신발은 핏 · 소매 · 넥라인 칸이 없어 이번 판에서 뺀다
    rng.shuffle(pool)
    by = defaultdict(list)
    for r in pool:
        by[r.get("category") or "?"].append(r)
    cats = sorted(by)
    per = max(1, n // len(cats))
    out, brand = [], Counter()
    for c in cats:   # 1차: 품목마다 같은 몫
        k = 0
        for r in by[c]:
            if k >= per:
                break
            if brand[r["brand_slug"]] >= 3:
                continue
            out.append(r); brand[r["brand_slug"]] += 1; k += 1
    seen = {r["source_url"] for r in out}
    for r in pool:   # 2차: 모자라면 채움
        if len(out) >= n:
            break
        if r["source_url"] not in seen and brand[r["brand_slug"]] < 3:
            out.append(r); seen.add(r["source_url"]); brand[r["brand_slug"]] += 1
    return out[:n]


def fetch_img(url, sess):
    from PIL import Image
    try:
        resp = sess.get(url, timeout=20)
        if resp.status_code != 200:
            return None
        im = Image.open(io.BytesIO(resp.content))
        im = im.convert("RGB")
        w, h = im.size
        if min(w, h) < 150:
            return None
        s = IMG_SIDE / max(w, h)
        if s < 1:
            im = im.resize((round(w * s), round(h * s)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def images_for(r, gal, shp, sess):
    urls = [r["image_url"]]
    for u in gal.get(r["source_url"]) or []:
        if len(urls) >= 3:
            break
        if u in urls:
            continue
        o = shp.get(u) or {}
        if o.get("st") in (404, 410):
            continue
        w, h = o.get("w") or 0, o.get("h") or 0
        if w and (h > w * TALL_MAX or w > h * 1.6):
            continue
        urls.append(u)
    out = []
    for u in urls:
        b = fetch_img(u, sess)
        if b:
            out.append(b)
    return out


SYSTEM = """너는 한국 패션 플랫폼의 상품 데이터 검수자다. 상품 사진과 지금 데이터에 적힌 속성을 받는다.
사진을 보고 각 칸이 맞는지 판정한다.

- 첫 사진이 대표 사진이고, 나머지는 같은 상품의 다른 사진이다. 모델이 다른 옷과 같이 입고 있으면 이 상품(상품명 기준)만 본다.
- verdict: "ok"(맞음) · "wrong"(틀림 — fix 에 고칠 값) · "missing"(빈 칸인데 사진으로 분명히 보임 — fix 에 값) · "unsure"(사진으로 판단 불가).
- 사진으로 확실할 때만 wrong/missing 을 쓴다. 애매하면 unsure. 의견이 아니라 사실(보이는 것)만.
- fix 는 반드시 아래 값 목록에 있는 말만 쓴다. 목록에 맞는 말이 없으면 unsure.
- 그 상품에 해당하지 않는 칸(예: 바지의 넥라인)은 지금 값이 비어 있으면 ok.
- note 는 틀린 칸이 있을 때만 한 줄(40자 안쪽)로 근거를 적는다.

값 목록:
"""


def schema():
    one = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["ok", "wrong", "missing", "unsure"]},
                                             "fix": {"type": "array", "items": {"type": "string"}}},
           "required": ["verdict", "fix"], "additionalProperties": False}
    props = {k: one for k in ["subtype", "color"] + AXES}
    props["details_missing"] = {"type": "array", "items": {"type": "string"}}
    props["details_wrong"] = {"type": "array", "items": {"type": "string"}}
    props["photo"] = {"type": "string", "enum": ["ok", "not_this_product", "unclear"]}
    props["note"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def current(r, tags):
    t = (tags.get(r["source_url"]) or {}).get("tags") or {}
    cur = {"상품명": r["name"], "브랜드": r["brand_slug"], "성별": r.get("gender_target"),
           "subtype(품목)": r.get("subtype") or "", "color(대표색)": r.get("representative_color") or ""}
    for ax in AXES:
        cur[ax] = t.get(ax) or []
    cur["design_element(디테일)"] = t.get("design_element") or []
    return cur


def build(args):
    rows = load_csv()
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    voc = vocab(rows, tags)
    if args.all:
        # 전체 판(2026-11-20): 판매 중 옷 전부를 정해진 순서로, 이미 판독해 둔 것은 건너뛰고 앞에서 n 벌.
        done = set()
        for f in OUT_DIR.glob("*.jsonl"):
            for l in f.open(encoding="utf-8"):
                try:
                    o = json.loads(l)
                except Exception:
                    continue
                if o.get("status") == "succeeded" and o.get("out"):
                    done.add(o.get("u"))
        pool = sorted((r for r in rows if r.get("status") == "ON_SALE" and r.get("image_url") and r["source_url"] in tags
                       and r.get("category") in GARMENTS and r["source_url"] not in done),
                      # 크레딧이 전체를 못 덮으므로(Opus 벌당 약 $0.009) 빈 칸이 많은 옷부터 — 채울 것이 많은 데서 효과가 크다
                      key=lambda r: (-sum(1 for ax in AXES if not ((tags.get(r["source_url"]) or {}).get("tags") or {}).get(ax)),
                                     r["brand_slug"], r["source_url"]))
        print(f"판매 중 옷 중 남은 것 {len(pool)}벌 (이미 판독 {len(done)})", flush=True)
        sample = pool[:args.n]
    else:
        sample = pick(rows, tags, args.n, args.seed)
    print(f"이번 판 {len(sample)}벌 · 품목 {dict(Counter(r.get('category') for r in sample))}", flush=True)
    gal, shp = crawl_gallery(), gallery_shapes()
    import requests
    from concurrent.futures import ThreadPoolExecutor
    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0"
    system = SYSTEM + json.dumps(voc, ensure_ascii=False)
    reqs, meta = [], {}
    # 사진 받기는 여러 갈래로(한 갈래로는 500벌에 20분 — 전체 판에선 너무 느리다)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        got = list(ex.map(lambda r: images_for(r, gal, shp, sess), sample))
    for i, (r, imgs) in enumerate(zip(sample, got)):
        if not imgs:
            continue
        cid = f"a{i:05d}"
        meta[cid] = {"u": r["source_url"], "cur": current(r, tags), "n_img": len(imgs)}
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}} for b in imgs]
        content.append({"type": "text", "text": "지금 데이터:\n" + json.dumps(meta[cid]["cur"], ensure_ascii=False)})
        reqs.append({"custom_id": cid, "params": {
            "model": args.model, "max_tokens": 6000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {"effort": args.effort, "format": {"type": "json_schema", "schema": schema()}},
            "messages": [{"role": "user", "content": content}]}})
    print(f"사진 받음 {len(reqs)}/{len(sample)}", flush=True)
    return reqs, meta, voc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261116)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-usd", type=float, default=30.0)
    ap.add_argument("--name", default="sample")
    ap.add_argument("--hours", type=float, default=5.0, help="배치를 기다릴 시간 상한")
    ap.add_argument("--all", action="store_true", help="표본 대신 판매 중 옷 전부에서 아직 안 한 것 n 벌")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--chunk", type=int, default=800, help="배치 하나에 담을 요청 수(256MB 한도)")
    args = ap.parse_args()

    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):   # 사진 20분을 받고 나서야 키가 없다고 죽지 않게(2026-11-16 첫 판)
        print("ANTHROPIC_API_KEY 가 없다 — 저장소 Settings → Secrets and variables → Actions 에 넣는다"); return 1
    import anthropic
    client = anthropic.Anthropic()
    reqs, meta, voc = build(args)
    if not reqs:
        print("보낼 것이 없다"); return 1

    # 모델 여럿(쉼표)이면 같은 표본 · 같은 사진으로 모델마다 배치를 따로 보낸다 — 정확도와 단가를 나란히 잰다.
    models = [m.strip() for m in args.model.split(",") if m.strip()]
    # 비용 어림: 앞 5개의 입력 토큰을 세고, 출력은 벌당 1,500으로 잡는다.
    toks = []
    for q in reqs[:5]:
        p = q["params"]
        c = client.messages.count_tokens(model=models[0], system=p["system"], messages=p["messages"])
        toks.append(c.input_tokens)
    # 앞서 같은 모델로 재 둔 실제 벌당 비용이 있으면 그것(×1.2)으로 어림한다 — 출력 1,500 토큰 가정은 Opus 실측(약 490)의
    # 세 배라 8,000벌 판을 $187 로 부풀려 보내지도 못하고 멈췄다(2026-11-20).
    seen = defaultdict(list)
    for f in OUT_DIR.glob("*.summary.json"):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if o.get("usd_per_item"):
            seen[o.get("model")].append(o["usd_per_item"])
    est = 0.0
    for m in models:
        pin, pout = PRICE.get(m, PRICE[MODEL])
        if seen.get(m):
            e = len(reqs) * sorted(seen[m])[len(seen[m]) // 2] * 1.2
        else:
            e = len(reqs) * (sum(toks) / len(toks) * pin + 1500 * pout) / 1e6
        print(f"{m}: 요청 {len(reqs)}개 · 입력 어림 {sum(toks) // len(toks)} 토큰/벌 · 비용 어림 ${e:.2f}", flush=True)
        est += e
    print(f"합계 어림 ${est:.2f} (상한 ${args.max_usd})", flush=True)
    if est > args.max_usd:
        print("어림 비용이 상한을 넘어 보내지 않는다"); return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    run_tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")   # 같은 날 여러 판이 서로 덮어쓰지 않게
    batches = defaultdict(list)   # 모델 → 배치 id 들(요청 chunk 개씩 나눠 보낸다)
    for m in models:
        stem = f"{run_tag}_{args.name}" + (f"_{m}" if len(models) > 1 else "")
        for k in range(0, len(reqs), args.chunk):
            rq = [{"custom_id": q["custom_id"], "params": {**q["params"], "model": m}} for q in reqs[k:k + args.chunk]]
            batch = client.messages.batches.create(requests=rq)
            batches[m].append(batch.id)
            print(f"{m}: 배치 {batch.id} 보냄 ({len(rq)}개)", flush=True)
        (OUT_DIR / f"{stem}.batch.json").write_text(json.dumps({"batches": batches[m], "model": m, "effort": args.effort,
                                                                 "n": len(reqs)}, ensure_ascii=False) + "\n", encoding="utf-8")
    deadline = time.time() + args.hours * 3600
    while True:
        st = {b: client.messages.batches.retrieve(b) for bs in batches.values() for b in bs}
        if all(b.processing_status == "ended" for b in st.values()):
            break
        if time.time() > deadline:
            print("기다릴 시간이 다 됐다 — 배치는 그대로 두고 끝낸다(나중에 거둔다)"); return 1
        print(f"  끝 {sum(b.request_counts.succeeded for b in st.values())} · 처리 중 {sum(b.request_counts.processing for b in st.values())}", flush=True)
        time.sleep(60)

    for m, bids in batches.items():
        pin, pout = PRICE.get(m, PRICE[MODEL])
        stem = f"{run_tag}_{args.name}" + (f"_{m}" if len(models) > 1 else "")
        n_in = n_out = n_cr = 0
        stat = defaultdict(Counter)
        out = []
        for res in (x for bid in bids for x in client.messages.batches.results(bid)):
            mt = meta.get(res.custom_id) or {}
            row = {"id": res.custom_id, "u": mt.get("u"), "cur": mt.get("cur"), "n_img": mt.get("n_img"), "status": res.result.type}
            if res.result.type == "succeeded":
                msg = res.result.message
                u = msg.usage
                n_in += u.input_tokens + (u.cache_creation_input_tokens or 0)
                n_cr += u.cache_read_input_tokens or 0
                n_out += u.output_tokens
                row["stop"] = msg.stop_reason
                txt = next((x.text for x in msg.content if x.type == "text"), "")
                try:
                    row["out"] = json.loads(txt)
                except Exception:
                    row["raw"] = txt[:2000]
                for k, v in (row.get("out") or {}).items():
                    if isinstance(v, dict):
                        bad = [x for x in v.get("fix") or [] if x not in voc.get(k, [])]
                        if bad:
                            v["off_vocab"] = bad
                        stat[k][v.get("verdict")] += 1
            else:
                err = getattr(res.result, "error", None)
                row["error"] = str(getattr(err, "error", err))[:300]
            out.append(row)
        with (OUT_DIR / f"{stem}.jsonl").open("w", encoding="utf-8") as fh:
            for row in out:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        cost = (n_in * pin + n_cr * pin * 0.1 + n_out * pout) / 1e6
        summ = {"batches": bids, "model": m, "effort": args.effort, "n": len(out),
                "succeeded": sum(1 for r in out if r["status"] == "succeeded"),
                "tokens": {"input": n_in, "cache_read": n_cr, "output": n_out}, "usd": round(cost, 2),
                "usd_per_item": round(cost / max(1, len(out)), 4),
                "verdicts": {k: dict(v) for k, v in stat.items()}}
        (OUT_DIR / f"{stem}.summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(summ, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
