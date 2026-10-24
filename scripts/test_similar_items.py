"""집업 후보 제한을 실제 일반·축별 목록 생성 경로에서 검사한다. data/에는 쓰지 않는다."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import similar_items as s


class ZipCompatibilityTest(unittest.TestCase):
    def test_explicit_half_overrides_broad_full_type(self):
        for name in ("하프집업", "하프 집업", "HALF ZIP SWEAT", "Half-Zip Knit",
                     "quarter_zip top", "쿼터 집업", "1/4 zip fleece"):
            with self.subTest(name=name):
                self.assertEqual(s.zip_kind("후드집업", name), 2)
        self.assertEqual(s.zip_kind("하프집업", "제품"), 2)

    def test_full_and_unknown_evidence(self):
        for ft in ("집업", "후드집업", "니트집업"):
            self.assertEqual(s.zip_kind(ft, "two way zip"), 1)
        for ft, name in (("니트", "하프 슬리브"), ("맨투맨", "half moon"),
                         ("니트", "지퍼 포켓 니트"), ("", "")):
            self.assertEqual(s.zip_kind(ft, name), 0)

    def test_generation_filters_both_directions_and_all_axes(self):
        # 같은 브랜드·성별 제한도 함께 유지되는지 확인한다.
        defs = [
            ("a", "집업", "full", "MENSWEAR"),
            ("b", "후드집업", "full", "UNISEX"),
            ("c", "집업", "HALF-ZIP fleece", "UNISEX"),
            ("d", "하프집업", "product", "UNISEX"),
            ("e", "맨투맨", "unknown", "UNISEX"),
            ("f", "집업", "full", "WOMENSWEAR"),
            ("a", "집업", "other full", "MENSWEAR"),
        ]
        rows = [{"source_url": f"https://example.test/{i}", "brand_slug": b,
                 "product_no": str(i), "name": name, "item_type": ft, "gender_target": gd,
                 "status": "ON_SALE", "category_code": "tops", "price": "50000",
                 "representative_color": "블랙"} for i, (b, ft, name, gd) in enumerate(defs)]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            data = Path(folder)
            with (data / "products_full.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            (data / "brands_seed.csv").write_text("slug\n" + "\n".join(sorted({r["brand_slug"] for r in rows})))
            tags = {r["source_url"]: {"tags": {"material": ["코튼"], "silhouette": ["레귤러핏"],
                                                      "pattern": ["단색"]}} for r in rows}
            (data / "product_tags_full.json").write_text(json.dumps(tags))
            (data / "vocab_aliases.json").write_text('{"_groups": {}}')
            out = data / "similar_items.test.json"
            with patch.object(s, "DATA", data), \
                 patch.object(s, "brand_matrix", side_effect=lambda rows, tags, brands: np.full((len(brands), len(brands)), .4)), \
                 patch.object(s, "pick_emb_dir", return_value="siglip"), \
                 patch.object(s, "load_emb", return_value=(np.zeros((0, 768), np.float32), {})), \
                 patch.object(sys, "argv", ["similar_items.py", "--out", str(out)]):
                self.assertEqual(s.main(), 0)
            result = json.loads(out.read_text())
            axes = json.loads((data / "similar_axes.test.json").read_text())
            index = result["index"]
            neighbors = lambda a: {index[i] for i in result["items"][a][::2]}
            self.assertEqual(neighbors("a-0"), {"b-1", "e-4"})
            self.assertEqual(neighbors("c-2"), {"d-3", "e-4"})
            self.assertIn("c-2", neighbors("e-4"))
            self.assertIn("b-1", neighbors("e-4"))
            for key in ("cp", "s", "m"):
                got = {index[i] for i in axes["items"]["a-0"][key][::2]}
                self.assertEqual(got, {"b-1", "e-4"})
            self.assertIn("zip_compatibility", result["config"])


if __name__ == "__main__":
    unittest.main()
