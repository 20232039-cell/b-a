"""품절·공개 할인 회귀 검사. 공식 페이지의 필드 꼴만 줄여 쓴다."""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import crawl_cafe24 as cc
import weekly_update as weekly


def ld_script(node):
    return '<script type="application/ld+json">' + json.dumps(node) + '</script>'


def variant(availability):
    return {"@type": "Product", "offers": {"availability": "https://schema.org/" + availability}}


class Cafe24StockPriceTest(unittest.TestCase):
    def parse(self, body, price=45000):
        html = f'<title>상품</title><script>var product_price = "{price}";</script>' + body
        return cc.parse_detail(html, 'https://example.com/product/detail.html?product_no=213',
                               cc.Shop('example', 'https://example.com', ''))

    def test_zero_stock_overrides_false_icon_in_saved_rows(self):
        row = {'soldout': False, 'stock': 'all0', 'options': ['M', 'L']}
        self.assertTrue(cc.is_soldout(row))

    def test_all_marked_options_override_false_icon_and_old_headers(self):
        for options, dead in [(['S', 'M'], ['S', 'M']),
                              (['SIZE', '- [필수] 선택 -', 'S [품절] (+1원)', 'M [품절]'], [])]:
            with self.subTest(options=options):
                self.assertTrue(cc.is_soldout({'soldout': False, 'options': options, 'soldout_options': dead}))

    def test_live_or_missing_options_are_not_proof_of_soldout(self):
        for row in [{'options': []}, {'options': ['SIZE']},
                    {'options': ['S', 'M'], 'soldout_options': ['M']},
                    {'stock': 'some', 'options': ['S', 'M']}]:
            with self.subTest(row=row):
                self.assertFalse(cc.is_soldout({'soldout': False, **row}))

    def test_legacy_icon_policy_is_preserved(self):
        row = {'soldout': True, 'options': ['S', 'M'], 'soldout_options': ['M']}
        self.assertFalse(cc.is_soldout({**row, 'brand_slug': 'kijun'}))
        self.assertTrue(cc.is_soldout({**row, 'brand_slug': 'o-oi'}))
        self.assertFalse(cc.is_soldout({'soldout': True, 'options': ['S', 'M']}))

    def test_known_live_stock_is_not_overruled_by_json_ld(self):
        row = {'soldout': True, 'stock': 'some', 'availability': 'OutOfStock',
               'options': ['S', 'M'], 'soldout_options': ['M']}
        self.assertFalse(cc.is_soldout(row))

    def test_parser_sets_raw_flag_for_weekly_stock_tracking(self):
        rows = {size: {'use_stock': True, 'stock_number': 0, 'option_value': size} for size in ['M', 'L']}
        value = json.dumps(json.dumps(rows))[1:-1]
        got = self.parse("<script>var is_soldout_icon = 'F'; var option_stock_data = '" + value + "';</script>")
        self.assertTrue(got['soldout'])
        self.assertTrue(cc.is_soldout(got))
        cc.carry_over({'price': 45000, 'soldout': False}, got, '2026-10-26T10:00:00')
        self.assertEqual(got['stock_log'][-1], ['2026-10-26', '품절'])

    def test_all_dropdown_options_soldout_without_icon(self):
        got = self.parse('<select name="option1"><option>S [품절]</option><option>M [품절]</option></select>')
        self.assertTrue(got['soldout'])
        self.assertTrue(cc.is_soldout(got))

    def test_product_group_all_variants_soldout_is_saved(self):
        node = {'@type': 'ProductGroup', 'hasVariant': [variant('OutOfStock'), variant('SoldOut')]}
        got = self.parse(ld_script(node) + '<select name="option1"><option>S</option><option>M</option></select>')
        self.assertEqual(got['availability'], 'OutOfStock')
        self.assertTrue(got['soldout'])
        self.assertTrue(cc.is_soldout(got))

    def test_partial_stock_and_unknown_variants_do_not_become_full_soldout(self):
        for other, expected in [(variant('InStock'), 'InStock'), (variant('PreOrder'), 'InStock'),
                                ({'@type': 'Product'}, None), ('/variant/2', None)]:
            with self.subTest(other=other):
                node = {'@type': 'ProductGroup', 'hasVariant': [variant('OutOfStock'), other]}
                self.assertEqual(cc.json_ld_availability(ld_script(node)), expected)
                self.assertFalse(self.parse(ld_script(node))['soldout'])

    def test_offer_list_is_not_decided_by_first_soldout_offer(self):
        node = {'@type': 'Product', 'offers': [variant('OutOfStock')['offers'], variant('InStock')['offers']]}
        self.assertEqual(cc.json_ld_availability(ld_script(node)), 'InStock')
        self.assertFalse(self.parse(ld_script(node))['soldout'])

    def test_graph_and_unrelated_products(self):
        node = {'@graph': [{'@type': 'WebSite'}, variant('InStock'), variant('OutOfStock')]}
        self.assertEqual(cc.json_ld_availability(ld_script(node)), 'InStock')
        self.assertEqual(cc.json_ld_availability(ld_script({'@graph': [variant('OutOfStock')]})), 'OutOfStock')
        for html in ['', '<script type="application/ld+json">broken</script>', ld_script({'@type': 'WebSite'})]:
            self.assertIsNone(cc.json_ld_availability(html))

    def test_public_half_price_without_js_and_meta(self):
        got = self.parse('<span id="span_product_price_sale">22,500 KRW <span>(22,500 KRW 할인)</span></span>')
        self.assertEqual(got['price_sale'], 22500)
        self.assertEqual(cc.price_pair(got), (45000, 22500, 22500))

    def test_public_sale_survives_member_optimum_price(self):
        got = self.parse('''<span id="span_product_price_sale">22,500 KRW</span>
            <span id="span_optimum_discount_price">19,125 KRW <span>15% OFF (회원)</span></span>
            <script>var product_sale_price = 19125;</script>''')
        self.assertEqual(got['price_sale'], 22500)

    def test_member_only_discount_is_not_a_sale(self):
        for amount in ['38,250', '38250']:
            with self.subTest(amount=amount):
                got = self.parse(f'''<span id="span_optimum_discount_price">{amount} KRW (회원) 15% OFF</span>
                    <script>var product_sale_price = 38250;</script>
                    <meta property="product:sale_price:amount" content="38250">''')
                self.assertNotIn('price_sale', got)

    def test_hidden_member_and_invalid_public_sale_rows_are_ignored(self):
        for body in ['<div class="displaynone"><span id="span_product_price_sale">22,500 KRW</span></div>',
                     '<span style="display: none" id="span_product_price_sale">22,500 KRW</span>',
                     '<table><tr><th>회원가</th><td id="span_product_price_sale">22,500 KRW</td></tr></table>',
                     '<span id="span_product_price_sale">50% OFF</span>',
                     '<span id="span_product_price_sale">45,000 KRW</span>']:
            with self.subTest(body=body):
                self.assertNotIn('price_sale', self.parse(body))

    def test_existing_js_and_meta_public_prices_are_kept(self):
        for body in ['<script>var product_sale_price = 22500;</script>',
                     '<meta property="product:sale_price:amount" content="22500">']:
            self.assertEqual(self.parse(body)['price_sale'], 22500)


class WeeklyRestockTest(unittest.TestCase):
    def test_relisted_delisted_product_is_checked_before_restoring(self):
        for listed, result, restored in [(True, 'ok', True), (True, 'http', False), (False, 'ok', False)]:
            with self.subTest(listed=listed, result=result), ExitStack() as stack:
                previous = {'product_no': 325, 'price': 178000, 'soldout': True, 'delisted': True,
                            'source_url': 'https://example.com/product/old-name/325/', 'options': ['S', 'M']}
                rows = {325: previous}
                shop = cc.Shop('junne', 'https://example.com', '')
                if listed:
                    shop.product_urls[325] = previous['source_url']
                stack.enter_context(patch.object(weekly, 'load_rows', return_value=rows))
                save = stack.enter_context(patch.object(weekly, 'save_rows'))
                stack.enter_context(patch.object(weekly, 'today', return_value='2026-10-26'))
                for name in ['load_robots', 'load_categories', 'enumerate_by_sitemap', 'crawl_category_lists']:
                    stack.enter_context(patch.object(cc, name))
                fresh = {**previous, 'soldout': False}
                fresh.pop('delisted')
                fetch = stack.enter_context(patch.object(weekly, 'fetch_one', return_value=(result, fresh if result == 'ok' else None)))
                http = Mock()
                http.get.return_value = Mock(status_code=200, url=shop.base, text='<title>JUNNE</title>')
                weekly.update_brand(http, shop, lambda _: None, False)
                self.assertEqual(fetch.call_count, int(listed))
                saved = save.call_args.args[1][325]
                self.assertEqual(not saved.get('delisted', False), restored)
                if restored:
                    self.assertFalse(saved['soldout'])


if __name__ == '__main__':
    unittest.main()
