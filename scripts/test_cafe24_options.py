"""본품·추가상품 옵션 회귀 검사. 네트워크와 data/는 쓰지 않는다."""
import json
import unittest

import crawl_cafe24 as crawl


def buttons(values, attrs=''):
    items = ''.join(f'<li option_value="{v}" title="{v}">{v}</li>' for v in values)
    return f'<ul option_title="Size" {attrs}>{items}</ul>'


def stock_script(name, rows):
    value = json.dumps(json.dumps(rows))[1:-1]
    return f"<script>var {name} = '{value}';</script>"


class Cafe24OptionsTest(unittest.TestCase):
    def parse(self, body, slug='hatching-room'):
        html = '<title>가방</title><script>var product_price = "148000";</script>' + body
        return crawl.parse_detail(html, 'https://example.com/product/detail.html?product_no=1471',
                                  crawl.Shop(slug, 'https://example.com', ''))

    def test_addproduct_buttons_do_not_become_bag_sizes(self):
        main = buttons(['Free'], 'product_type="product_option"')
        extra = buttons(['M', 'L'], 'ec-dev-id="addproduct_option_id_1378_1"')
        got = self.parse(main + extra)
        self.assertEqual(got['options'], ['Free'])
        self.assertEqual(got['soldout_options'], [])

    def test_addproduct_markers_without_wrapper(self):
        for attr in ('id', 'name', 'ec-dev-id', 'ec-dev-name', 'product_type', 'product_option_area'):
            with self.subTest(attr=attr):
                got = self.parse(buttons(['Free']) + buttons(['M', 'L'], f'{attr}="addproduct_option_1378"'))
                self.assertEqual(got['options'], ['Free'])

    def test_addproduct_wrappers_with_generic_option_ids(self):
        extra = '<select id="product_option_id2" name="option2"><option>M [품절]</option></select>'
        for attrs in ('class="xans-product-addproduct"', 'class="xans-product-addproduct-option"',
                      'class="productSet additional"', 'module="product_addproduct"',
                      'id="addproduct_1378"'):
            with self.subTest(attrs=attrs):
                got = self.parse(buttons(['Free']) + f'<div {attrs}><div>{extra}</div></div>')
                self.assertEqual(got['options'], ['Free'])
                self.assertEqual(got['soldout_options'], [])

    def test_addproduct_select_does_not_hide_main_buttons(self):
        extra = '<select id="addproduct_option_id_1378_1" name="option2"><option>M</option></select>'
        self.assertEqual(self.parse(extra + buttons(['Free']))['options'], ['Free'])

    def test_only_addproduct_options_leave_main_empty(self):
        got = self.parse(buttons(['M', 'L'], 'product_type="addproduct_option"'))
        self.assertEqual(got['options'], [])
        self.assertEqual(got['soldout_options'], [])

    def test_other_shops_keep_existing_option_reading(self):
        body = buttons(['44', '46', '카멜']) + buttons(['블랙', '차콜'], 'product_type="addproduct_option"')
        self.assertEqual(self.parse(body, slug='lmood')['options'], ['44', '46', '카멜', '블랙', '차콜'])

    def test_main_dropdown_values_and_soldout_are_kept(self):
        main = '''<select name="option1"><option>- [필수] 선택 -</option>
        <option>S</option><option>M [품절]</option></select>'''
        got = self.parse(main + buttons(['L'], 'product_type="addproduct_option"'))
        self.assertEqual(got['options'], ['S', 'M'])
        self.assertEqual(got['soldout_options'], ['M'])

    def test_main_buttons_keep_color_order_and_disabled_size(self):
        main = '''<div class="xans-product-additional">
        <ul option_title="Color"><li option_value="BLUE">BLUE</li></ul>
        <ul option_title="Size"><li option_value="S" class="ec-product-disabled" title="S">S</li>
        <li option_value="M" title="M">M</li></ul></div>'''
        got = self.parse(main)
        self.assertEqual(got['options'], ['S', 'M', 'BLUE'])
        self.assertEqual(got['soldout_options'], ['S'])

    def test_hidden_main_stock_is_kept_and_prefixed_stock_is_ignored(self):
        extra = stock_script('addproduct_option_stock_data', {
            'extra': {'option_value': 'L', 'use_stock': True, 'stock_number': 10}})
        main = stock_script('option_stock_data', {
            'main': {'option_value_orginal': ['BLUE', 'S'], 'is_display': 'F',
                     'use_stock': True, 'stock_number': 0},
            'other': {'option_value': 'M [품절]', 'use_stock': True, 'stock_number': 0}})
        self.assertEqual(crawl.option_values_all(extra + main), ['BLUE', 'S', 'M'])
        self.assertEqual(crawl.stock_state(extra + main), 'all0')
        self.assertIsNone(crawl.option_values_all(extra))
        self.assertIsNone(crawl.stock_state(extra))

    def test_missing_or_malformed_stock_has_no_options(self):
        for html in ('', "var option_stock_data = 'broken';", stock_script('option_stock_data', []),
                     stock_script('option_stock_data', {'bad': None})):
            with self.subTest(html=html):
                self.assertIsNone(crawl.option_values_all(html))
                self.assertIsNone(crawl.stock_state(html))

    def test_desc_unisex_declaration(self):
        # badblood SIZE TIP — 상품 전체 선언은 받고, 사이즈 하나만의 말은 안 받는다
        self.assertTrue(crawl.DESC_UNISEX.search('SIZE TIP * 본 상품은 유니섹스 입니다. MODEL INFO'))
        self.assertTrue(crawl.DESC_UNISEX.search('SIZE TIP * 본 상품은 유니섹스입니다.'))
        self.assertIsNone(crawl.DESC_UNISEX.search('본 상품의 XL사이즈는 유니섹스입니다'))
        self.assertIsNone(crawl.DESC_UNISEX.search('유니섹스 무드의 니트'))
        for t in ('유니섹스 아이템입니다', '남여공용 착용', '남녀 구분 없이 착용 가능합니다', '젠더리스 아이템으로 제작',
                  '남녀 모두에게 어우러지는 머플러'):
            self.assertTrue(crawl.DESC_UNISEX.search(t), t)
        for t in ('유니섹스 아이템과 매치', '여성 모델 착용', '남녀 모두 좋아하는 색'):
            self.assertIsNone(crawl.DESC_UNISEX.search(t), t)


if __name__ == '__main__':
    unittest.main()
