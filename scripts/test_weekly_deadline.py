"""주간 갱신 마감 시각(--hours) 회귀 검사 — 2026-11-11.

2026-11-09 판(run 42526643058)에서 묶음 10 · 16 이 잡 상한 330분에 걸려 dunst · sinoon 이 본 것을 통째로 잃었다.
마감이 지나면 (1) 시작 전 매장은 매장에 가지 않고 파일을 그대로 두고, (2) 보던 매장은 본 데까지 저장하고,
(3) 끝난 매장의 기록은 바로 남고, (4) 마감을 모르는 수집기가 안 끝나도 스크립트는 제때 0 으로 끝나야 한다.
매장 서버에는 가지 않는다(목록 · 상세는 전부 흉내).
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import crawl_cafe24 as cc
import weekly_update as weekly

HERE = Path(__file__).resolve().parent


class DeadlineClockTest(unittest.TestCase):
    def tearDown(self):
        weekly.set_deadline(None)

    def test_no_deadline_by_default(self):
        for hours in (None, 0, 0.0, -1):
            weekly.set_deadline(hours)
            self.assertFalse(weekly.time_up())

    def test_deadline_passes(self):
        weekly.set_deadline(1)
        self.assertFalse(weekly.time_up())
        with patch.object(weekly.time, 'monotonic', return_value=time.monotonic() + 3601):
            self.assertTrue(weekly.time_up())


class UpdateBrandDeadlineTest(unittest.TestCase):
    """update_brand — 값 갱신 다섯 벌 가운데 두 벌을 본 뒤 마감이 온다."""

    def run_brand(self, time_up, rows, listed, details=None):
        """rows: 아는 행 · listed: 이번 목록 · details: 상세가 돌려줄 행(없으면 아는 행에 값만 99000 으로)."""
        shop = cc.Shop('example', 'https://example.com', '')
        shop.product_urls.update(listed)
        seen = []

        def fetch(http, shop_, no, url, titles=None):
            seen.append(no)
            return 'ok', dict(details[no]) if details else {**rows[no], 'price': 99000}

        with ExitStack() as stack:
            stack.enter_context(patch.object(weekly, 'load_rows', return_value=rows))
            save = stack.enter_context(patch.object(weekly, 'save_rows'))
            stack.enter_context(patch.object(weekly, 'today', return_value='2026-11-11'))
            stack.enter_context(patch.object(weekly, 'time_up', side_effect=time_up))
            stack.enter_context(patch.object(weekly, 'fetch_one', side_effect=fetch))
            for name in ['load_robots', 'load_categories', 'enumerate_by_sitemap', 'crawl_category_lists']:
                stack.enter_context(patch.object(cc, name))
            http = Mock()
            http.get.return_value = Mock(status_code=200, url=shop.base, text='<title>EXAMPLE</title>')
            rep = weekly.update_brand(http, shop, lambda _: None, False)
        return rep, seen, save, http

    @staticmethod
    def stale_rows():
        # 값 본 날이 오래된 순: 5 → 4 → 3 → 2 → 1
        return {no: {'product_no': no, 'price': 50000, 'soldout': False, 'options': ['M'],
                     'source_url': f'https://example.com/product/detail.html?product_no={no}',
                     'price_seen_at': f'2026-10-{20 - no:02d}'} for no in (1, 2, 3, 4, 5)}

    def test_skipped_before_start_touches_nothing(self):
        rows = self.stale_rows()
        rep, seen, save, http = self.run_brand(lambda: True, rows, {no: r['source_url'] for no, r in rows.items()})
        self.assertEqual(seen, [])
        http.get.assert_not_called()           # 매장에 한 번도 안 간다
        save.assert_not_called()               # 파일도 그대로
        self.assertFalse(rep['ok'])
        self.assertIn('시간 마감', rep['guard'])
        self.assertTrue(rep['cut'])

    def test_cut_midway_saves_what_was_checked(self):
        rows = self.stale_rows()
        listed = {no: r['source_url'] for no, r in rows.items()}
        calls = {'n': 0}

        def time_up():
            # 시작 전 확인 1번 + 값 갱신 루프에서 두 벌을 볼 때까지는 시간이 남아 있다
            calls['n'] += 1
            return calls['n'] > 3

        rep, seen, save, _ = self.run_brand(time_up, rows, listed)
        self.assertEqual(seen, [5, 4])                         # 오래 묵은 것부터 두 벌
        self.assertEqual(rep['checked'], 2)
        self.assertEqual(rep['cut'], {'값 갱신': 3})
        self.assertIn('시간 마감', rep['guard'])
        saved = save.call_args.args[1]
        self.assertEqual(sorted(saved), [1, 2, 3, 4, 5])       # 한 벌도 안 잃는다
        self.assertEqual([saved[no]['price'] for no in (5, 4)], [99000, 99000])
        for no in (1, 2, 3):                                   # 못 본 것은 지난 판 그대로 — 다음 판의 맨 앞에 선다
            self.assertEqual(saved[no]['price'], 50000)
            self.assertEqual(saved[no]['price_seen_at'], f'2026-10-{20 - no:02d}')
            self.assertEqual(saved[no]['last_seen'], '2026-11-11')

    def test_new_products_come_before_the_cut(self):
        rows = self.stale_rows()
        listed = {no: r['source_url'] for no, r in rows.items()}
        listed[9] = 'https://example.com/product/detail.html?product_no=9'
        details = {**rows, 9: {'product_no': 9, 'price': 1000, 'options': ['M'], 'source_url': listed[9]}}
        calls = {'n': 0}

        def time_up():
            calls['n'] += 1
            return calls['n'] > 2          # 시작 전 1번 + 신상 한 벌

        rep, seen, save, _ = self.run_brand(time_up, rows, listed, details)
        self.assertEqual(seen, [9])                            # 신상이 값 갱신보다 먼저다
        self.assertEqual(rep['new'], 1)
        self.assertEqual(rep['cut'], {'값 갱신': 5})
        self.assertEqual(save.call_args.args[1][9]['first_seen'], '2026-11-11')

    def test_no_deadline_checks_everything(self):
        rows = self.stale_rows()
        rep, seen, save, _ = self.run_brand(lambda: False, rows, {no: r['source_url'] for no, r in rows.items()})
        self.assertEqual(seen, [5, 4, 3, 2, 1])
        self.assertNotIn('cut', rep)
        self.assertEqual(rep['guard'], '')
        save.assert_called_once()


DRIVER = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    tmp = Path(sys.argv[2])
    import crawl_cafe24 as cc
    import platforms
    import weekly_update as weekly

    cc.BRANDS_CSV = tmp / "brands_seed.csv"
    weekly.CRAWL_DIR = tmp
    weekly.LOG_PATH = tmp / "_weekly_log.jsonl"
    weekly.GRACE_SEC = 3
    platforms.NOT_CAFE24 = {"slow-other"}

    def crawl_other(http, slug, log=print):
        time.sleep(60)          # 마감을 모르는 수집기 — 한참 더 돈다
        return {"slug": slug, "listed": 1}

    def update_brand(http, shop, log, first_week):
        if weekly.time_up():
            return {"date": "2026-11-11", "slug": shop.slug, "ok": False, "guard": "시간 마감 — 시작 못 함", "cut": {"시작": 0}}
        time.sleep(3.5 if shop.slug == "slow-c" else 0.2)     # slow-c 는 마감(2초)을 넘겨 끝난다
        return {"date": "2026-11-11", "slug": shop.slug, "ok": True, "guard": "", "listed": 3, "new": 1, "sec": 0}

    platforms.crawl_other = crawl_other
    weekly.update_brand = update_brand
    sys.argv = ["weekly_update.py", "--brands", "slow-other", "fast-a", "fast-b", "slow-c", "late-d",
                "--workers", "2", "--no-csv", "--hours", sys.argv[3]]
    weekly.main()
''')


class MainDeadlineTest(unittest.TestCase):
    """main — 일꾼 둘, 마감 2초 + 기다림 3초. slow-other(다른 틀)가 한 자리를 60초 붙잡는다. 나머지 자리로
    fast-a · fast-b 가 끝나고, slow-c 가 마감을 넘겨 끝난 뒤 차례가 온 late-d 는 시작하지 않는다."""

    def run_main(self, hours):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "brands_seed.csv").write_text(
                "slug,official_url,gender\n" + "".join(f"{b},https://{b}.example,\n" for b in ("fast-a", "fast-b", "slow-c", "late-d")),
                encoding="utf-8-sig")
            (tmp / "driver.py").write_text(DRIVER, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
            t0 = time.time()
            proc = subprocess.run([sys.executable, str(tmp / "driver.py"), str(HERE), str(tmp), str(hours)],
                                  capture_output=True, text=True, timeout=50, env=env)
            took = time.time() - t0
            log = tmp / "_weekly_log.jsonl"
            lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return proc, took, lines

    def test_stuck_collector_does_not_hold_the_run(self):
        proc, took, lines = self.run_main(2 / 3600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)   # 마감은 실패가 아니다
        self.assertLess(took, 30)                          # 60초짜리 수집기를 기다리지 않는다
        by = {r["slug"]: r for r in lines}
        self.assertEqual(sorted(by), ["fast-a", "fast-b", "late-d", "slow-c"])   # 끝난 매장의 기록은 남는다
        self.assertTrue(all(by[b]["ok"] for b in ("fast-a", "fast-b", "slow-c")))
        self.assertIn("시작 못 함", by["late-d"]["guard"])
        warn = [l for l in proc.stdout.splitlines() if l.startswith("::warning title=수집 시간 마감::")]
        self.assertEqual(len(warn), 1, proc.stdout)
        late = warn[0].rsplit(": ", 1)[1].split(", ")
        self.assertEqual(late, ["late-d", "slow-other"])   # 시작 못 한 것 · 안 끝난 것만 알린다

    def test_no_deadline_waits_for_everything(self):
        with patch.object(sys.modules[__name__], "DRIVER", DRIVER.replace("time.sleep(60)", "time.sleep(1)")):
            proc, took, lines = self.run_main(0)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        by = {r["slug"]: r for r in lines}
        self.assertEqual(sorted(by), ["fast-a", "fast-b", "late-d", "slow-c", "slow-other"])
        self.assertTrue(by["slow-other"]["other"])
        self.assertIn("sec", by["slow-other"])             # 다른 틀 매장도 걸린 시간을 남긴다(plan 의 무게)
        self.assertNotIn("::warning", proc.stdout)


if __name__ == '__main__':
    unittest.main()
