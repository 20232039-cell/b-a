"""작은 가짜 값으로 통계 JSON 저장을 검사한다. 학습하지 않고 data/에도 쓰지 않는다."""
import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

import photo_fill as photo
import size_from_ocr as size


class GeneratedStatsTest(unittest.TestCase):
    def read_report(self, path):
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("NaN", text)
        report = json.loads(text)
        stamp = datetime.fromisoformat(report["generated_at"])
        self.assertEqual(stamp.utcoffset(), timedelta(0))
        return report

    def test_photo_metrics_write(self):
        correct = np.array([True, False, True, True])
        confidence = np.array([0.8, 0.95, 0.79, 0.9])
        axes = {"소매": photo.validation_metrics(correct, confidence >= photo.MIN_P)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo_fill_metrics.json"
            photo.write_metrics(axes, path)
            report = self.read_report(path)
        self.assertEqual(report["confidence_threshold"], 0.8)
        self.assertEqual(report["axes"]["소매"], {
            "sample_count": 4, "accuracy": 0.75,
            "high_confidence_count": 3, "high_confidence_fraction": 0.75,
            "high_confidence_accuracy": 2 / 3,
        })

    def test_photo_metrics_without_confident_samples(self):
        axes = {"넥라인": photo.validation_metrics(np.array([True, False]), np.array([False, False]))}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo_fill_metrics.json"
            photo.write_metrics(axes, path)
            metrics = self.read_report(path)["axes"]["넥라인"]
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["high_confidence_fraction"], 0)
        self.assertIsNone(metrics["high_confidence_accuracy"])

    def test_fit_report_collects_holdout_metrics(self):
        axes = {}
        # crc32 분할은 그대로 쓰되 학습과 확률 출력만 작은 가짜 값으로 대체한다.
        held = next(f"b{i}" for i in range(100) if photo.zlib.crc32(f"b{i}".encode()) % 10 == 0)
        trained = next(f"b{i}" for i in range(100) if photo.zlib.crc32(f"b{i}".encode()) % 10 != 0)
        probability = np.array([[0.8, 0.2], [0.6, 0.4]])
        with patch.object(photo, "MIN_CLASS", 1), \
             patch.object(photo, "train", return_value=(np.zeros((1, 2)), np.zeros(2))) as train, \
             patch.object(photo, "softmax", return_value=probability):
            photo.fit_report("소매", np.zeros((4, 1)), ["a", "b", "a", "b"],
                             [held, held, trained, trained], axes)
        self.assertEqual(train.call_count, 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo_fill_metrics.json"
            photo.write_metrics(axes, path)
            metrics = self.read_report(path)["axes"]["소매"]
        self.assertEqual(metrics["sample_count"], 2)
        self.assertEqual(metrics["training_samples"], 2)
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["high_confidence_fraction"], 0.5)
        self.assertEqual(metrics["high_confidence_count"], 1)
        self.assertEqual(metrics["high_confidence_accuracy"], 1)
        self.assertEqual(metrics["status"], "ok")

    def test_photo_metrics_without_validation(self):
        axes = {}
        # 브랜드 하나라 검증을 못 뗀다. 학습 함수는 가짜 반환값으로 대체한다.
        with patch.object(photo, "MIN_CLASS", 1), patch.object(photo, "train", return_value=(None, None)):
            photo.fit_report("큰 품목", np.zeros((2, 1)), ["팬츠", "셔츠"], ["brand", "brand"], axes)
            photo.fit_report("소매", np.zeros((1, 1)), ["반팔"], ["brand"], axes)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo_fill_metrics.json"
            photo.write_metrics(axes, path)
            metrics = self.read_report(path)["axes"]
        self.assertEqual(metrics["큰 품목"]["status"], "no_holdout")
        self.assertEqual(metrics["소매"]["status"], "insufficient_classes")
        for entry in metrics.values():
            self.assertEqual(entry["sample_count"], 0)
            self.assertIsNone(entry["accuracy"])
            self.assertIsNone(entry["high_confidence_fraction"])
            self.assertIsNone(entry["high_confidence_accuracy"])

    def test_no_embeddings_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            pred_path = Path(tmp) / "photo_pred.json"
            metrics_path = Path(tmp) / "photo_fill_metrics.json"
            writer = photo.write_metrics
            with patch.object(photo, "load_emb", return_value=(np.zeros((0, 1)), {})), \
                 patch.object(photo, "OUT", pred_path), \
                 patch.object(photo, "write_metrics", side_effect=lambda axes, **kw: writer(axes, metrics_path, **kw)), \
                 patch("sys.argv", ["photo_fill.py", "--dry-run"]):
                self.assertEqual(photo.main(), 0)
                self.assertFalse(pred_path.exists())
                self.assertFalse(metrics_path.exists())
                with patch("sys.argv", ["photo_fill.py"]):
                    self.assertEqual(photo.main(), 0)
                report = self.read_report(metrics_path)
                self.assertEqual(report["status"], "no_embeddings")
                self.assertEqual(report["axes"], {})

    def test_size_stats_write_and_empty_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "size_stats.json"
            size.write_stats(8, 5, Counter({"같음": 3, "차례로": 1, "모름": 1}), path)
            report = self.read_report(path)
            self.assertEqual(report["total_products"], 8)
            self.assertEqual(report["products_with_sizes"], 5)
            self.assertEqual(report["coverage"], 5 / 8)
            self.assertEqual(report["size_keys_products"], 5)
            self.assertEqual(report["size_keys"], {"같음": 3, "차례로": 1, "모름": 1})
            size.write_stats(0, 0, Counter(), path)
            report = self.read_report(path)
            self.assertIsNone(report["coverage"])
            self.assertEqual(report["size_keys_products"], 0)
            self.assertEqual(report["size_keys"], {})


if __name__ == "__main__":
    unittest.main()
