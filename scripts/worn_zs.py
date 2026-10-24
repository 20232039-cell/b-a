"""대표 사진이 착용컷인지 — 이미 계산해 둔 사진 임베딩(Marqo fashionSigLIP, data/emb/siglip)에 글 프롬프트를 대어 본 점수.

왜 있는가(사진 대조 QA 2026-10-27): 착용컷 표시 wn 은 옷 분할 모델(worn_photo.py · SegFormer) 넓이로만 정했다. 그 모델은
  · 얼굴 · 팔 없이 허리 아래만 찍은 바지 컷(다리가 바지에 덮여 「다리」 칸이 비고 「바지」 칸만 찬다), 목 · 얼굴 클로즈업에 꽂은
    헤어핀처럼 몸 일부만 나온 컷을 놓치고(눈 대조: 분할 판정 0 · 이 점수 ≥0.7 표본 72장 중 70장이 착용컷),
  · 흰 배경 위 모자 · 장갑 · 가방 · 납작하게 놓은 옷을 윗옷 · 원피스 · 머리카락으로 칠해 착용컷이라 했다(분할 판정 1 · 이 점수 <0.1
    표본 44장 중 43장이 제품만 찍은 컷).
글-사진 모델은 「사람이 입었나 · 물건만 놓였나」를 사진 전체로 본다. 두 판정을 export_app_data.worn 이 합친다(규칙은 그쪽에).

무엇을 — data/emb/siglip/part_*.npz 의 임베딩(판매중 상품 대표 사진, embed_photos.py)에 착용 프롬프트 10개 · 제품컷 프롬프트 10개를
대어 softmax 로 착용 쪽 확률 합을 낸다. 사진을 다시 받지 않는다(임베딩만 쓴다 — 사진 서버에 요청 0).
한계: 구판 임베딩은 사진 주소가 없어 변경을 확인할 수 없다. 새 임베딩의 사진 주소는 함께 내보내 낡은 점수를 거른다(2026-10-28).

결과: data/worn_zs.json — {"v", "model", "prompts": {"worn": [...], "prod": [...]}, "items": {source_url: 점수(0~1)}}.

사용: python scripts/worn_zs.py
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "worn_zs.json"
MODEL = "hf-hub:Marqo/marqo-fashionSigLIP"   # embed_photos.py 의 siglip 과 같은 모델이어야 글 · 사진 벡터가 한 공간에 있다

# 착용 쪽에는 몸 일부만 나온 컷(다리 · 몸통 · 발 · 머리 · 목 · 손)을 일부러 넣는다 — 분할 모델이 놓치는 자리다.
PROMPTS = {
    "worn": ["a photo of a person wearing clothes", "a model wearing an outfit", "a fashion model posing",
             "a cropped photo of a person's legs wearing pants", "a close-up of a model's torso wearing a shirt",
             "a person's feet wearing shoes", "a person wearing a hat", "a model holding a bag",
             "a person wearing jewelry on the neck", "a person's hand wearing a ring"],
    "prod": ["a product photo of clothing laid flat on a plain background", "a garment on a hanger",
             "a product photo of a bag on a white background", "a product photo of shoes on a white background",
             "a product photo of jewelry on a plain background", "a still life photo of an accessory",
             "a product photo of a hat on a white background", "a mannequin wearing clothes", "a folded garment",
             "clothes photographed alone without a person"],
}


def load_embeddings() -> tuple[np.ndarray, list[dict]]:
    E, U = [], []
    for p in sorted(glob.glob(str(DATA / "emb" / "siglip" / "part_*.npz"))):
        z = np.load(p, allow_pickle=False)
        E.append(z["E"].astype(np.float32))
        U += json.loads(str(z["meta"]))
    E = np.concatenate(E) if E else np.zeros((0, 768), np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-8
    return E, U


def worn_prob(E: np.ndarray, Tw: np.ndarray, Tp: np.ndarray, scale: float) -> np.ndarray:
    """착용 프롬프트들의 softmax 확률 합 — 프롬프트 수가 같아 기준선은 0.5."""
    s = np.concatenate([E @ Tw.T, E @ Tp.T], 1) * scale
    s -= s.max(1, keepdims=True)
    e = np.exp(s)
    return (e[:, :Tw.shape[0]].sum(1) / e.sum(1)).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    import open_clip
    import torch
    E, U = load_embeddings()
    if not len(U):
        raise SystemExit("사진 임베딩이 없어 점수를 만들지 않는다")
    model, _, _ = open_clip.create_model_and_transforms(MODEL)
    tok = open_clip.get_tokenizer(MODEL)
    model.eval()
    with torch.no_grad():
        T = {k: model.encode_text(tok(v)).float() for k, v in PROMPTS.items()}
        scale = float(model.logit_scale.exp().item())
    T = {k: (t / t.norm(dim=-1, keepdim=True)).numpy() for k, t in T.items()}
    p = worn_prob(E, T["worn"], T["prod"], scale)
    args.out.write_text(json.dumps({"v": date.today().isoformat(), "model": MODEL, "prompts": PROMPTS,
                               "images": {m["u"]: m["img"] for m in U if m.get("img")},
                               "items": {m["u"]: round(float(x), 3) for m, x in zip(U, p)}},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(U)}장 → {args.out}")


if __name__ == "__main__":
    main()
