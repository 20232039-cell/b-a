"""주간 수집 묶음 나누기(weekly_plan) 회귀 검사 — 2026-11-11.

2026-11-09 판은 상품 수로 무게를 달아 묶음이 「고르게」 나뉘었는데도 걸린 시간이 77분 ~ 330분으로 벌어졌다.
무게는 지난 판에 실제로 걸린 시간이어야 하고, 지난 판에 끊긴 매장은 맨 앞에 서야 한다. 숫자는 지어낸 것이다.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import weekly_plan as plan


def line(slug, date, sec=None, **more):
    return {"date": date, "slug": slug, **({"sec": sec} if sec is not None else {}), **more}


class EstimateTest(unittest.TestCase):
    def test_measured_time_beats_product_count(self):
        counts = {"slow-small": 800, "fast-big": 3000}
        log = [line("slow-small", "2026-11-09", 11594), line("fast-big", "2026-11-09", 5000)]
        est, starved = plan.estimate(counts, log, set())
        self.assertGreater(est["slow-small"], est["fast-big"])      # 상품은 적어도 오래 걸리는 매장이 더 무겁다
        self.assertEqual(starved, set())

    def test_takes_the_longest_of_recent_runs(self):
        # 주중에 손으로 돌린 짧은 판(값 갱신이 거의 없다)에 속지 않는다. 네 번째로 오래된 줄은 안 본다.
        log = [line("a", "2026-10-12", 9000), line("a", "2026-10-19", 6128), line("a", "2026-10-22", 2222),
               line("a", "2026-10-26", 4997)]
        est, _ = plan.estimate({"a": 100}, log, set())
        self.assertEqual(est["a"], 6128)

    def test_brand_missing_from_last_full_run_is_starved(self):
        counts = {f"b{i}": 100 for i in range(10)}
        log = [line(b, "2026-10-26", 1000) for b in counts]
        log += [line(b, "2026-11-09", 1000) for b in counts if b != "b3"]      # b3 은 러너가 꺼져 줄을 못 남겼다
        log += [line("b5", "2026-11-10", 50)]                                  # 손으로 한 곳만 돌린 판은 전체 판이 아니다
        self.assertEqual(plan.last_full_run(log), "2026-11-09")
        est, starved = plan.estimate(counts, log, set())
        self.assertEqual(starved, {"b3"})
        self.assertEqual(est["b3"], 1000 * plan.STARVED_FACTOR)

    def test_cut_line_is_starved_even_without_time(self):
        log = [line("a", "2026-11-09", 3000), line("b", "2026-11-09", 3000),
               line("a", "2026-11-16", 17000, cut={"값 갱신": 400}),
               line("b", "2026-11-16", cut={"시작": 900}, guard="시간 마감 — 시작 못 함")]
        est, starved = plan.estimate({"a": 1000, "b": 900}, log, set())
        self.assertEqual(starved, {"a", "b"})
        self.assertEqual(est["a"], 17000 * plan.STARVED_FACTOR)
        self.assertEqual(est["b"], 3000 * plan.STARVED_FACTOR)      # 시작도 못 한 판에는 시간이 없다 — 그 앞 판의 시간으로

    def test_other_platform_without_old_lines_is_not_starved(self):
        # 다른 틀 매장은 2026-11-11 전에는 기록을 안 남겼다 — 옛 줄만 있다고 굶은 것이 아니다.
        counts = {f"b{i}": 100 for i in range(5)} | {"shopify": 300}
        log = [line(b, "2026-11-09", 400) for b in counts if b != "shopify"] + [line("shopify", "2026-09-12", 2)]
        _, starved = plan.estimate(counts, log, {"shopify"})
        self.assertEqual(starved, set())
        # 기록을 남기기 시작한 뒤에 빠지면 굶은 것이다
        log += [line("shopify", "2026-11-16", 900, other=True)] + [line(b, "2026-11-23", 400) for b in counts if b != "shopify"]
        _, starved = plan.estimate(counts, log, {"shopify"})
        self.assertEqual(starved, {"shopify"})

    def test_unmeasured_brand_uses_median_time_per_product(self):
        counts = {"a": 100, "b": 100, "c": 100, "new": 50, "new-other": 50}
        log = [line("a", "2026-11-09", 200), line("b", "2026-11-09", 400), line("c", "2026-11-09", 900)]
        est, _ = plan.estimate(counts, log, {"new-other"})
        self.assertEqual(est["new"], 50 * 4.0)               # 중앙값 4초/벌
        self.assertEqual(est["new-other"], 50 * 4.0)         # 기록이 있는 판에서는 다른 틀이라고 더 곱하지 않는다

    def test_first_run_without_any_log_keeps_old_weights(self):
        est, starved = plan.estimate({"cafe": 100, "shopify": 100}, [], {"shopify"})
        self.assertEqual((est["cafe"], est["shopify"]), (100, 100 * plan.OTHER_WEIGHT))
        self.assertEqual(starved, set())

    def test_dead_brand_weighs_nothing(self):
        log = [line("gone", "2026-10-26", 5000), line("a", "2026-11-09", 100)]
        est, starved = plan.estimate({"gone": 700, "a": 10}, log, set(), dead={"gone"})
        self.assertEqual(est["gone"], 0)
        self.assertNotIn("gone", starved)


class SplitTest(unittest.TestCase):
    def test_every_brand_lands_in_exactly_one_bucket(self):
        est = {f"b{i:02d}": float(1000 + 37 * i) for i in range(50)}
        groups = plan.split(est, set(), 7, 6)
        flat = [b for g in groups for b in g]
        self.assertEqual(sorted(flat), sorted(est))           # 한 매장은 한 묶음에만 — 매장당 초당 1회
        self.assertEqual(len(groups), 7)
        self.assertEqual(groups, plan.split(est, set(), 7, 6))  # 같은 입력이면 같은 묶음

    def test_long_brands_are_spread_out(self):
        est = {"long1": 15000.0, "long2": 14000.0, "long3": 13000.0, **{f"s{i}": 500.0 for i in range(30)}}
        groups = plan.split(est, set(), 3, 6)
        self.assertEqual(sorted(g[0] for g in groups), ["long1", "long2", "long3"])   # 묶음마다 하나씩, 맨 앞에
        sums = [sum(est[b] for b in g) for g in groups]
        self.assertLess(max(sums) - min(sums), 2001)

    def test_starved_brand_starts_first(self):
        est = {"big": 9000.0, "starved": 800.0, "mid": 3000.0}
        (only,) = plan.split(est, {"starved"}, 1, 6)
        self.assertEqual(only, ["starved", "big", "mid"])

    def test_fewer_brands_than_buckets(self):
        self.assertEqual(plan.split({"a": 1.0, "b": 2.0}, set(), 20, 6), [["b"], ["a"]])


class MainTest(unittest.TestCase):
    def run_main(self, argv, log_lines):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            (data / "crawl").mkdir()
            (data / "products_full.csv").write_text(
                "brand_slug,name\n" + "a,x\n" * 30 + "b,x\n" * 20 + "gone,x\n" * 40, encoding="utf-8-sig")
            for b, n in {"a": 30, "b": 20, "gone": 40, "held": 25}.items():      # held 는 CSV 에 없다 — 줄 수로 센다
                (data / "crawl" / f"{b}.jsonl").write_text('{"product_no": 1}\n' * n, encoding="utf-8")
            (data / "crawl" / "_failures_a.jsonl").write_text("{}\n", encoding="utf-8")
            (data / "crawl" / "_weekly_log.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in log_lines) + "깨진 줄\n", encoding="utf-8")
            (data / "brands_seed.csv").write_text(
                "slug,official_url\na,https://a.example\nb,https://b.example\nheld,https://held.example\ngone,\n",
                encoding="utf-8-sig")
            out = data / "gh_output"
            env = {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(data / "summary.md")}
            with patch.object(plan, "DATA", data), patch.dict(os.environ, env), \
                    patch("sys.argv", ["weekly_plan.py", *argv]), patch("builtins.print") as printed:
                plan.main()
            text = out.read_text(encoding="utf-8")
            summary = (data / "summary.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("matrix=") and text.count("\n") == 1)     # $GITHUB_OUTPUT 에 한 줄
        said = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)
        return json.loads(text[len("matrix="):])["include"], said, summary

    def test_matrix_shape_and_weights(self):
        log = [line("a", "2026-11-09", 600), line("b", "2026-11-09", 16000)]
        inc, said, summary = self.run_main(["--buckets", "2", "--hours", "5"], log)
        self.assertEqual([sorted(x) for x in inc], [["brands", "bucket", "load"]] * 2)
        self.assertEqual([x["bucket"] for x in inc], [1, 2])
        self.assertEqual(inc[0]["brands"].split()[0], "b")           # 가장 오래 걸리는 매장이 첫 묶음 맨 앞
        self.assertEqual(inc[0]["load"], round(16000 / 60))
        everyone = " ".join(x["brands"] for x in inc).split()
        self.assertEqual(sorted(everyone), ["a", "b", "gone", "held"])
        self.assertIn("::warning title=혼자서도 마감에 걸릴 매장::", said)   # b 16000초 ≥ 5시간의 80%
        self.assertIn("b 267분", said)
        self.assertIn("| 묶음 |", summary)

    def test_brands_filter(self):
        inc, said, _ = self.run_main(["--brands", "a held", "--buckets", "20"], [line("a", "2026-11-09", 600)])
        self.assertEqual(sorted(" ".join(x["brands"] for x in inc).split()), ["a", "held"])
        self.assertNotIn("::warning", said)


if __name__ == '__main__':
    unittest.main()
