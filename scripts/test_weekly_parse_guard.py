"""날짜 글을 값으로 읽다 죽는 일 · 상품 하나의 파싱 예외가 매장 수집을 끝내는 일의 회귀 검사.

2026-11-09 주간 수집에서 nasty-fancy-club 이 `could not convert string to float: '2020.02.21'` 로
통째로 실패했다 — 판매가 자리에 발매일을 적은 룩북 상품 하나 때문이었다. 공식 페이지의 필드 꼴만 줄여 쓴다.
"""
import contextlib
import io
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import crawl_cafe24 as cc
import weekly_update as weekly


def ld_price(price):
    node = {"@type": "Product", "name": "상품",
            "offers": {"@type": "Offer", "priceCurrency": "KRW", "price": price,
                       "availability": "https://schema.org/InStock"}}
    return '<script type="application/ld+json">' + json.dumps(node) + '</script>'


class DateLikePriceTest(unittest.TestCase):
    URL = 'https://example.com/product/detail.html?product_no=213'

    def parse(self, body, price="0"):
        html = f'<title>상품</title><script>var product_price = "{price}";</script>' + body
        return cc.parse_detail(html, self.URL, cc.Shop('example', 'https://example.com', ''))

    def test_date_in_json_ld_price_is_not_a_price(self):
        # 고치기 전에는 여기서 ValueError 로 죽었다.
        for text in ['2020.02.21', '2020.02.21.', '20.02.21', '2020-02-21', 'COMING SOON', '']:
            with self.subTest(price=text):
                self.assertIsNone(self.parse(ld_price(text))['price'])

    def test_numeric_json_ld_price_is_still_read(self):
        for raw in ['45000', '45000.00', '45000.', 45000, 45000.0]:
            with self.subTest(price=raw):
                self.assertEqual(self.parse(ld_price(raw))['price'], 45000)

    def test_date_in_sale_slot_is_not_a_sale_price(self):
        # 「2020.02.21」의 앞머리 「2020.02」가 2,020원 할인가로 읽히면 안 된다.
        got = self.parse('<span id="span_product_price_sale">2020.02.21</span>'
                         '<script>var product_sale_price = \'2020.02.21\';</script>'
                         '<meta property="product:sale_price:amount" content="2020.02.21">', price="45000")
        self.assertEqual(got['price'], 45000)
        self.assertNotIn('price_sale', got)
        # 소수점 하나짜리 값은 그대로 읽는다.
        got = self.parse('<span id="span_product_price_sale">22,500.00 KRW</span>', price="45000")
        self.assertEqual(got['price_sale'], 22500)

    def test_weekly_fetch_drops_the_lookbook_page_instead_of_crashing(self):
        shop = cc.Shop('example', 'https://example.com', '')
        page = ('<title>20 S/S Collection</title><script>var product_price = "0";'
                "var product_name = '20 S/S Collection';</script>" + ld_price('2020.02.21')).replace(
                    '"name": "상품"', '"name": "20 S/S Collection"')
        http = Mock()
        http.get.return_value = Mock(status_code=200, url=self.URL, text=page + ' ' * 2000)
        with patch.object(shop, 'allowed', return_value=True):
            self.assertEqual(weekly.fetch_one(http, shop, 213, self.URL), ('no-price', None))


class WeeklyParseErrorTest(unittest.TestCase):
    """파서가 상품 하나에서 죽어도 매장의 나머지는 저장한다. 다만 조용히 넘기지 않는다."""

    def run_brand(self, known, listed, broken):
        shop = cc.Shop('example', 'https://example.com', '')
        url = lambda no: f'https://example.com/product/detail.html?product_no={no}'  # noqa: E731
        rows = {no: {'product_no': no, 'price': 45000, 'soldout': False, 'source_url': url(no),
                     'crawled_at': '2026-10-01T10:00:00', 'price_seen_at': '2026-10-01T10:00:00'} for no in known}
        shop.product_urls.update({no: url(no) for no in listed})

        def parse(html_text, page_url, shop, http=None):
            no = cc.product_no_of(page_url)
            if no in broken:
                raise ValueError(f"could not convert string to float: '2020.02.21' ({no})")
            return {'product_no': no, 'name': f'Cotton Shirt {no}', 'price': 47000, 'soldout': False,
                    'source_url': page_url, 'options': ['S', 'M']}

        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(weekly, 'load_rows', return_value=rows))
            save = stack.enter_context(patch.object(weekly, 'save_rows'))
            stack.enter_context(patch.object(weekly, 'today', return_value='2026-11-11'))
            for name in ['load_robots', 'load_categories', 'enumerate_by_sitemap', 'crawl_category_lists']:
                stack.enter_context(patch.object(cc, name))
            stack.enter_context(patch.object(cc, 'parse_detail', side_effect=parse))
            stack.enter_context(patch.object(shop, 'allowed', return_value=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            http = Mock()
            http.get.side_effect = lambda u, **_: Mock(status_code=200, url=u, text='<title>EXAMPLE</title>' + ' ' * 2000)
            try:
                report = weekly.update_brand(http, shop, lambda _: None, False)
            except Exception as e:  # 호출한 쪽이 예외 꼴을 본다
                return e, save, out.getvalue()
        return report, save, out.getvalue()

    def test_one_broken_product_does_not_kill_the_brand(self):
        report, save, printed = self.run_brand(known=[1, 2], listed=[1, 2, 3, 4], broken={3})
        self.assertIsInstance(report, dict)
        saved = save.call_args.args[1]
        self.assertEqual(sorted(saved), [1, 2, 4])                  # 죽은 3번만 빠지고 신상 4번은 들어왔다
        self.assertEqual(saved[1]['price'], 47000)                  # 아는 상품의 값 갱신도 끝까지 갔다
        self.assertEqual(report['new'], 1)
        # 삼키지 않았다 — 건수 · 상품 번호 · 호출 자리가 남는다.
        self.assertEqual(report['parse_errors'], [3])
        self.assertEqual(report['reasons'].get('parse-error'), 1)
        self.assertEqual(report['failed'], 1)
        self.assertIn('Traceback', printed)
        self.assertIn("could not convert string to float: '2020.02.21'", printed)
        self.assertIn('::warning title=상세 파싱 예외::[example] 1건', printed)

    def test_broken_known_product_keeps_its_old_row(self):
        report, save, _ = self.run_brand(known=[1, 2], listed=[1, 2], broken={2})
        saved = save.call_args.args[1]
        self.assertEqual(saved[2]['price'], 45000)                  # 못 읽은 상품은 예전 행 그대로
        self.assertFalse(saved[2].get('soldout') or saved[2].get('delisted') or saved[2].get('missing_weeks'))
        self.assertEqual(saved[1]['price'], 47000)
        self.assertEqual(report['parse_errors'], [2])

    def test_broken_parser_still_fails_the_brand(self):
        # 상세마다 죽으면 상품 탓이 아니라 파서 고장이다 — 예전처럼 매장을 예외로 끝내 main 이 1로 나가게 한다.
        listed = list(range(1, 31))
        error, save, _ = self.run_brand(known=[], listed=listed, broken=set(listed))
        self.assertIsInstance(error, RuntimeError)
        self.assertIn('파서 고장', str(error))
        save.assert_not_called()

    def test_scattered_errors_in_a_large_brand_do_not_stop_it(self):
        listed = list(range(1, 101))
        report, save, _ = self.run_brand(known=[], listed=listed, broken={10, 20, 30, 40, 50, 60, 70})
        self.assertIsInstance(report, dict)
        self.assertEqual(len(report['parse_errors']), 7)
        self.assertEqual(len(save.call_args.args[1]), 93)


if __name__ == '__main__':
    unittest.main()
