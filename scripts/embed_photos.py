"""상품 사진 임베딩 — 사람 PC(GPU)에서 한 번에 돌리는 판. CPU 에서도 돌지만 느리다.

    python scripts/embed_photos.py --model siglip --all            # 판매중 · 씨앗 브랜드 상품 전부(약 12만 장)
    python scripts/embed_photos.py --model dinov3 --sample data/emb/sample_jobs.json   # 모델 비교용 표본만
    python scripts/embed_photos.py --model fashionclip --all --limit 2000               # 시험 삼아 조금만

결과: data/emb/<model>/part_NNNN.npz — E(float16, 행 순서 = meta) · meta(JSON: [{u: source_url, b, c, t}]).
읽기: for p in sorted(glob("part_*.npz")): z=np.load(p); E=z["E"]; meta=json.loads(str(z["meta"]))
2,000장마다 조각을 저장하므로 끊겨도 같은 명령으로 이어서 돈다. 사진은 data/emb/_imgs/ 에 256px 로 캐시한다.
매장 예의: robots.txt 를 지키고 한 호스트에 동시 1 · 초당 2회를 넘기지 않는다. 무신사 · 29CM 는 열지 않는다.

모델(--model):
  fashionclip  patrickjohncyh/fashion-clip                 (transformers)
  siglip       Marqo/marqo-fashionSigLIP                   (open_clip)      ← 패션 검색 벤치마크에서 fashionclip 보다 좋음
  siglip2      google/siglip2-so400m-patch14-384           (transformers)   ← 범용 큰 판, GPU 권장
  dinov3       facebook/dinov3-vitl16-pretrain-lvd1689m    (transformers)   ← HF 에서 약관 동의 + `huggingface-cli login` 필요
  dinov2       facebook/dinov2-base                        (transformers)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import threading
import time
import urllib.robotparser as robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EMB = DATA / "emb"
IMG = EMB / "_imgs"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
APPAREL = ("tops", "bottoms", "outer", "skirt", "dress", "suiting")
BLOCKED_HOSTS = ("musinsa", "29cm")

MODELS = {
    "fashionclip": ("transformers-clip", "patrickjohncyh/fashion-clip"),
    "siglip": ("open_clip", "hf-hub:Marqo/marqo-fashionSigLIP"),
    "siglip2": ("transformers-siglip", "google/siglip2-so400m-patch14-384"),
    "dinov3": ("transformers-dino", "facebook/dinov3-vitl16-pretrain-lvd1689m"),
    "dinov2": ("transformers-dino", "facebook/dinov2-base"),
}


# ── 예의 바른 내려받기 ──────────────────────────────────────────────────────────
class Polite:
    """호스트마다 robots.txt 를 한 번 읽고, 동시 1 · 요청 간격 0.5초를 지킨다."""

    def __init__(self):
        self.robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.last: dict[str, float] = defaultdict(float)
        self.meta = threading.Lock()

    def host(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def allowed(self, url: str) -> bool:
        h = self.host(url)
        with self.meta:
            if h not in self.robots:
                rp = robotparser.RobotFileParser()
                rp.set_url(h + "/robots.txt")
                try:
                    rp.read()
                    self.robots[h] = rp
                except Exception:
                    self.robots[h] = None
        rp = self.robots[h]
        if rp is None:
            return True
        try:
            return rp.can_fetch(UA["User-Agent"], url)
        except Exception:
            return True

    def get(self, url: str) -> bytes | None:
        if any(b in url for b in BLOCKED_HOSTS) or not self.allowed(url):
            return None
        h = self.host(url)
        with self.locks[h]:
            wait = 0.5 - (time.time() - self.last[h])
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers=UA, timeout=20)
            except Exception:
                return None
            finally:
                self.last[h] = time.time()
        return r.content if r.status_code == 200 else None


def img_path(url: str) -> Path:
    return IMG / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")


def fetch(polite: Polite, url: str) -> bool:
    p = img_path(url)
    if p.exists():
        return True
    raw = polite.get(url)
    if not raw:
        return False
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((256, 256))
        im.save(p, quality=85)
        return True
    except Exception:
        return False


# ── 모델 ────────────────────────────────────────────────────────────────────────
def load_model(name: str, device: str):
    import torch

    kind, repo = MODELS[name]
    if kind == "transformers-clip":
        from transformers import CLIPModel, CLIPProcessor

        m = CLIPModel.from_pretrained(repo).to(device).eval()
        pr = CLIPProcessor.from_pretrained(repo)

        def f(ims):
            o = m.get_image_features(**pr(images=ims, return_tensors="pt").to(device))
            return o if torch.is_tensor(o) else (o.pooler_output if getattr(o, "pooler_output", None) is not None else o[0])

        return f
    if kind == "transformers-siglip":
        from transformers import AutoModel, AutoProcessor

        m = AutoModel.from_pretrained(repo).to(device).eval()
        pr = AutoProcessor.from_pretrained(repo)

        def f(ims):
            o = m.get_image_features(**pr(images=ims, return_tensors="pt").to(device))
            return o if torch.is_tensor(o) else o.pooler_output

        return f
    if kind == "transformers-dino":
        from transformers import AutoImageProcessor, AutoModel

        m = AutoModel.from_pretrained(repo).to(device).eval()
        pr = AutoImageProcessor.from_pretrained(repo)
        return lambda ims: m(**pr(images=ims, return_tensors="pt").to(device)).pooler_output
    if kind == "open_clip":
        import open_clip

        m, _, pre = open_clip.create_model_and_transforms(repo)
        m = m.to(device).eval()
        return lambda ims: m.encode_image(torch.stack([pre(x) for x in ims]).to(device))
    raise SystemExit(f"모르는 모델 {name}")


# ── 대상 고르기 ─────────────────────────────────────────────────────────────────
def all_jobs(limit: int = 0) -> list[dict]:
    seed = {r["slug"] for r in csv.DictReader(open(DATA / "brands_seed.csv", encoding="utf-8-sig"))}
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r["status"] == "ON_SALE" and r["image_url"] and r["brand_slug"] in seed]
    rows.sort(key=lambda r: (0 if r["category_code"] in APPAREL else 1, r["brand_slug"]))
    jobs = [{"u": r["source_url"], "img": r["image_url"], "b": r["brand_slug"], "c": r["category_code"], "t": r["item_type"]} for r in rows]
    return jobs[:limit] if limit else jobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(MODELS))
    ap.add_argument("--all", action="store_true", help="판매중 · 씨앗 브랜드 상품 전부")
    ap.add_argument("--sample", help="표본 목록 JSON([{u,img,b,c,t}]) — 모델 비교용")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=0, help="기본: GPU 64 · CPU 16")
    ap.add_argument("--threads", type=int, default=8, help="사진 내려받기 동시 수(호스트마다는 늘 1)")
    args = ap.parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    batch = args.batch or (64 if device == "cuda" else 16)
    print(f"장치 {device} · 배치 {batch}" + (f" · {torch.cuda.get_device_name(0)}" if device == "cuda" else ""), flush=True)

    out = EMB / args.model
    out.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)
    jobs = json.load(open(args.sample, encoding="utf-8")) if args.sample else all_jobs(args.limit)
    # 결과는 조각(part_NNNN.npz: E + meta)으로 쌓는다 — 한 파일이 GitHub 100MB 상한에 걸리지 않게(12만 × 1152차원이면 280MB).
    parts = sorted(out.glob("part_*.npz"))
    done = {m["u"] for pth in parts for m in json.loads(str(np.load(pth, allow_pickle=False)["meta"]))}
    todo = [j for j in jobs if j["u"] not in done]
    print(f"대상 {len(jobs)} · 이미 함 {len(done)} · 남음 {len(todo)}", flush=True)
    if not todo:
        return 0

    embed = load_model(args.model, device)
    polite = Polite()
    t0 = time.time()
    CH = 2000
    for c0 in range(0, len(todo), CH):
        chunk = todo[c0:c0 + CH]
        with ThreadPoolExecutor(args.threads) as ex:
            ok = list(ex.map(lambda j: fetch(polite, j["img"]), chunk))
        chunk = [j for j, o in zip(chunk, ok) if o]
        Es, metas = [], []
        for i in range(0, len(chunk), batch):
            ims, keep = [], []
            for j in chunk[i:i + batch]:
                try:
                    ims.append(Image.open(img_path(j["img"])).convert("RGB"))
                    keep.append(j)
                except Exception:
                    pass
            if not ims:
                continue
            with torch.no_grad():
                e = embed(ims)
                e = e / e.norm(dim=-1, keepdim=True)
            Es.append(e.float().cpu().numpy().astype(np.float16))
            metas += [{"u": j["u"], "b": j["b"], "c": j["c"], "t": j["t"]} for j in keep]
        if not Es:
            continue
        n = len(parts) + 1
        np.savez(out / f"part_{n:04d}.npz", E=np.concatenate(Es), meta=np.array(json.dumps(metas, ensure_ascii=False)))
        parts.append(out / f"part_{n:04d}.npz")
        done |= {m["u"] for m in metas}
        print(f"저장 {len(done)} / {len(jobs)} · {time.time() - t0:.0f}s", flush=True)
    print(f"끝 — {args.model} {len(done)}장 · {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
