"""브랜드관 회귀 검사. curl에서 저장한 공개 HTML/JSON 조각, 네트워크·data 쓰기 없음."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import crawl_pages
import crawl_cafe24 as cc
import crawl_shopify as cs
import platforms
from stores import customellow as cw, xtonz, kuho

FIX = Path(__file__).with_name('fixtures') / 'brand_malls'
NOW = '2026-10-22T12:00:00'


def fixture(name):
    return (FIX / name).read_text()


def payload(name):
    return json.loads(fixture(name))


def transport(data):
    # 실제 transport의 undefined는 JSON이 아니다. 문자열 속 단어는 보존되어야 한다.
    return '<script>(window[Symbol.for("ApolloSSRDataTransport")] ??= []).push(' + json.dumps(
        {'rehydrate': {'sample': {'data': data}}, 'events': []}, ensure_ascii=False) + ')</script>'


def response(text='', status=200):
    r = requests.Response()
    r.status_code = status
    r._content = text.encode()
    r.encoding = 'utf-8'
    return r


class BrandMallTest(unittest.TestCase):
    def cw_row(self, data=None):
        data = data or payload('customellow_detail1.json')
        code = data['productDetail']['product']['code']
        return cw.parse_page(transport(data), cw.product_url(code), 'customellow', NOW)

    def test_platform_routes_all_three_to_pages(self):
        for slug in ('customellow', 'kuho', 'xtonz'):
            with self.subTest(slug=slug), patch.object(crawl_pages, 'crawl_one', return_value={}) as crawl:
                platforms.crawl_other('session', slug)
                crawl.assert_called_once_with('session', slug, print)
                self.assertIn(slug, platforms.NOT_CAFE24)
                self.assertIsNotNone(crawl_pages.store(slug))

    def test_apollo_undefined_is_replaced_only_outside_strings(self):
        text = '<script>(window[Symbol.for("ApolloSSRDataTransport")] ??= []).push(' + \
               '{"rehydrate":{"x":{"data":{"text":"undefined in name","value":undefined}}},"events":[]})</script>'
        self.assertEqual(list(cw.apollo_data(text)), [{'text': 'undefined in name', 'value': None}])
        self.assertEqual(list(cw.apollo_data('<script>window.alert("not data")</script>')), [])
        self.assertIsNone(cw.parse_list('<title>로그인이 필요합니다</title>'))

    def test_customellow_saved_lists_have_brand_and_disjoint_pages(self):
        pages = [cw.parse_list(transport(payload(f'customellow_list{i}.json'))) for i in (1, 2)]
        self.assertEqual([len(p[0]) for p in pages], [60, 60])
        self.assertEqual([p[1]['currentPage'] for p in pages], [1, 2])
        self.assertFalse(set(pages[0][0]) & set(pages[1][0]))
        self.assertTrue(all('/Brands/CUSTOMELLOW/Product/' in u for p in pages for u in p[0]))

    def test_customellow_foreign_brand_in_list_fails_closed(self):
        data = payload('customellow_list1.json')
        data['productSearchCodes']['results'][0]['supplierBrandName'] = 'OTHER'
        self.assertIsNone(cw.parse_list(transport(data)))
        data = payload('customellow_list1.json')
        data['brandV2']['supplierBrandCode'] = 'OTHER'
        self.assertIsNone(cw.parse_list(transport(data)))

    def test_customellow_saved_details_and_circumferences(self):
        row = self.cw_row()
        self.assertEqual(row['name'], '블랙 페이크레더 더블니 팬츠')
        self.assertEqual(row['price'], 299000)
        self.assertNotIn('price_listed', row)
        self.assertEqual(row['source_currency'], 'KRW')
        self.assertFalse(row['soldout'])
        self.assertEqual(row['options'], ['M', 'L'])
        self.assertEqual(row['size_table']['_names'], ['M', 'L'])
        self.assertEqual(row['size_table']['허리'], [42.25, 44.25])
        self.assertEqual(row['size_table']['엉덩이'], [55, 57])
        self.assertEqual(row['size_table']['총장'], [109, 111.5])
        self.assertIn('레이온 55% 나일론 45%', row['spec']['소재'])
        self.assertIn('더블니 디자인', row['description'])
        self.assertNotIn('po-editor', row['description'])
        self.assertGreater(len(row['gallery']), 1)
        self.assertEqual(row['source_product_code'], 'CWPAA26551BKX')
        self.assertLess(row['product_no'], 2**53)
        rows = [self.cw_row(payload(f'customellow_detail{i}.json')) for i in (1, 2, 3)]
        self.assertEqual(len({r['product_no'] for r in rows}), 3)

    def test_size_values_are_aligned_by_size_not_position(self):
        p = payload('customellow_detail1.json')['productDetail']['product']
        groups = copy.deepcopy(p['sizeSpecs'])
        groups[1]['sizeSpecs'].reverse()
        self.assertEqual(cw.size_table(groups)['허리'], [42.25, 44.25])
        groups[1]['sizeSpecs'].pop()
        self.assertNotIn('허리', cw.size_table(groups))

    def test_sale_price_keeps_list_price_and_currency_guard(self):
        data = payload('customellow_detail1.json')
        sale = next(p['price'] for p in payload('customellow_list1.json')['productSearchCodes']['results']
                    if p['price']['discountRate'] == 10)
        data['productDetail']['product']['price'] = sale
        row = self.cw_row(data)
        self.assertEqual((row['price'], row['price_listed']), (764100, 849000))
        sale['currencyIso'] = 'USD'
        self.assertIsNone(self.cw_row(data))

    def test_variant_partial_and_full_stockout(self):
        data = payload('customellow_detail1.json')
        p = data['productDetail']['product']
        p['variantOptions'][0]['stock'] = {'stockLevel': 0, 'stockLevelStatus': 'outOfStock'}
        row = self.cw_row(data)
        self.assertFalse(row['soldout'])
        self.assertEqual(row['soldout_options'], ['M'])
        p['variantOptions'][1]['stock'] = {'stockLevel': 0, 'stockLevelStatus': 'outOfStock'}
        row = self.cw_row(data)
        self.assertTrue(row['soldout'])
        self.assertEqual(row['soldout_options'], ['M', 'L'])
        p['variantOptions'][1]['stock'] = {}
        self.assertIsNone(self.cw_row(data))

    def test_customellow_detail_requires_matching_identity_and_brand(self):
        data = payload('customellow_detail1.json')
        p = data['productDetail']['product']
        self.assertIsNone(cw.parse_page(transport(data), cw.product_url('OTHER'), 'customellow', NOW))
        p['supplierBrand']['brandCode'] = 'OTHER'
        self.assertEqual(self.cw_row(data)['_skip'], '다른 브랜드')
        del p['supplierBrand']
        self.assertIsNone(self.cw_row(data))

    def test_customellow_later_page_failure_or_repeat_is_not_empty_success(self):
        for last in (response('', 403), response(transport(payload('customellow_list1.json')))):
            http = Mock()
            http.get.side_effect = [response(fixture('customellow_robots.txt')),
                                    response(transport(payload('customellow_list1.json'))), last]
            self.assertIsNone(cw.list_urls(http, lambda _: None))
            self.assertEqual(http.get.call_count, 3)

    def test_customellow_production_paginates_to_total(self):
        pages = [payload(f'customellow_list{i}.json') for i in (1, 2)]
        for page in pages:
            page['productSearchCodes']['page']['totalCount'] = 120
        http = Mock()
        http.get.side_effect = [response(fixture('customellow_robots.txt'))] + [response(transport(p)) for p in pages]
        urls = cw.list_urls(http, lambda _: None)
        self.assertEqual(len(urls), 120)
        self.assertEqual(http.get.call_count, 3)

    def test_xtonz_saved_lists_only_use_567(self):
        pages = [xtonz.parse_list(fixture(f'xtonz_list{i}.html')) for i in (1, 2)]
        self.assertEqual([len(p[0]) for p in pages], [24, 24])
        self.assertFalse(set(pages[0][0]) & set(pages[1][0]))
        self.assertTrue(all(u.endswith('&cate_no=567') for p in pages for u in p[0]))
        self.assertEqual(pages[0][1], 6)
        html = fixture('xtonz_list1.html').replace('rel="브랜드">XTONZ', 'rel="브랜드">OTHER', 1)
        self.assertEqual(len(xtonz.parse_list(html)[0]), 23)
        self.assertIsNone(xtonz.parse_list('<title>접근 제한</title>'))

    def test_xtonz_saved_details_use_cafe24_prices_options_images(self):
        for i, no, value in ((1, 6863, 97300), (2, 6862, 116100), (3, 6861, 111300)):
            with self.subTest(i=i):
                with patch.object(cc, '_og_too_small', return_value=False):
                    row = xtonz.parse_page(fixture(f'xtonz_detail{i}.html'), xtonz.product_url(no), 'xtonz', NOW)
                self.assertEqual(row['price'], value)
                self.assertGreater(row['price_listed'], value)
                self.assertFalse(row['soldout'])
                self.assertTrue(row['options'])
                self.assertEqual(row['spec']['브랜드'], 'XTONZ')
                self.assertEqual(row['category_nos'], [567])
                self.assertTrue(row['description'])
                self.assertTrue(row['detail_images'])
                self.assertTrue(all('/web/upload/NNEditor/' not in u for u in row['detail_images']))
                self.assertFalse(row['size_table'])
                self.assertIn('size_unavailable', row)
                self.assertIn('material_unavailable', row)

    def test_xtonz_detail_rejects_foreign_brand_and_wrong_category(self):
        html = fixture('xtonz_detail1.html')
        self.assertIsNone(xtonz.parse_page(html, xtonz.product_url(6863).replace('567', '1'), 'xtonz', NOW))
        # 브랜드값만 바꿔도 이름에 XTONZ가 남아 있다는 이유로 통과하면 안 된다.
        html = html.replace('>XTONZ<', '>OTHER<')
        self.assertEqual(xtonz.parse_page(html, xtonz.product_url(6863), 'xtonz', NOW)['_skip'], '다른 브랜드')

    def test_xtonz_failure_and_repeated_page_preserve_state(self):
        for last in (response('', 503), response(fixture('xtonz_list1.html'))):
            http = Mock()
            http.get.side_effect = [response(fixture('xtonz_robots.txt')), response(fixture('xtonz_list1.html')), last]
            self.assertIsNone(xtonz.list_urls(http, lambda _: None))
            self.assertTrue(all('cate_no=567' in call.args[0] for call in http.get.call_args_list[1:]))

    def test_kuho_robots_stops_before_products_and_preserves_snapshot(self):
        http = Mock(delay=1.1)
        http.get.return_value = response(fixture('kuho_robots.txt'))
        logs = []
        with patch.object(crawl_pages.cc, 'PoliteSession', return_value=http), \
                patch.object(cs, 'load_prev', return_value={7: {'name': '기존 상품'}}), \
                patch.object(cs, 'merge_rows') as merge:
            report = crawl_pages.crawl_one(http, 'kuho', logs.append)
        self.assertTrue(report['guard'])
        merge.assert_not_called()
        http.get.assert_called_once_with(kuho.BASE + '/robots.txt', retries=0)
        self.assertTrue(any('robots.txt 수집 금지' in line for line in logs))

    def test_kuho_even_new_robots_permission_needs_verified_parser(self):
        http = Mock()
        http.get.return_value = response('User-agent: *\nAllow: /')
        self.assertIsNone(kuho.list_urls(http, lambda _: None))
        self.assertEqual(http.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
