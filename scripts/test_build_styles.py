"""색 형제 후보 생성과 실제 충돌 검사를 함께 검증한다. data/에는 쓰지 않는다."""
import unittest

import build_styles as b


def row(no, name='Everyday Cotton Shirt_Black', **kw):
    return dict(brand_slug='test', product_no=str(no), name=name, source_url=f'https://example.test/{no}',
                category='Shirts', list_price='50000', **kw)


def material(*parts):
    return {'mat': [{'p': p, 'v': values} for p, values in parts]}


def size(keys, **axes):
    return {'size_keys': keys, 'sizes': axes}


class StylesTest(unittest.TestCase):
    def build(self, rows, tags=None, sizes=None):
        return b.build(rows, tags, sizes)[0]

    def grouped(self, rows, tags=None, sizes=None):
        return len({r['style_id'] for r in self.build(rows, tags, sizes)}) == 1

    def test_tail_vocabulary_is_only_used_in_explicit_suffix(self):
        for name, base, color in [
            ('Cropped Hairy Knit - FOREST', 'Cropped Hairy Knit', '그린'),
            ('Everyday Shirt / COAL', 'Everyday Shirt', '차콜'),
            ('Graphic Long Sleeve / L/PURPLE', 'Graphic Long Sleeve', '퍼플'),
            ('NORDIC KNIT (KHAKI+BLACK)', 'NORDIC KNIT', '카키'),
            ('Flower Socks_NAVY IVORY', 'Flower Socks', '네이비'),
            ('Everyday Pants / LIGHT DENIM', 'Everyday Pants', '블루'),
            ('카라 저지-화이트', '카라 저지', '화이트'),
        ]:
            with self.subTest(name=name):
                got = b.split_color(name)
                self.assertEqual(got[0], base)
                self.assertEqual(got[2], color)
        self.assertEqual(b.split_color('FOREST GRAPHIC TEE')[0], 'FOREST GRAPHIC TEE')
        self.assertEqual(b.split_color('Graphic Print FOREST')[1], '')

    def test_store_shade_modifiers_remain_color_suffixes(self):
        for label in ['NATURAL SOAP', 'SHADOW GREEN', 'AUTHENTIC NAVY', 'EVERY BLACK', 'Oat Beige', 'ORGAN BROWN', 'Mild Brown', 'MT']:
            with self.subTest(label=label):
                self.assertEqual(b.split_color('Everyday Cotton Shirt (' + label + ')')[0], 'Everyday Cotton Shirt')

    def test_noncolor_details_survive(self):
        for suffix in ['BLACK RIPSTOP', 'CHARCOAL STRIPE']:
            self.assertEqual(b.split_color('Everyday Shirt_' + suffix)[1], '')
        self.assertIn('WET', b.split_color('Everyday Shirt_WET BLACK')[0])
        self.assertIn('MATT', b.split_color('Everyday Shirt_MATT BLACK')[0])
        self.assertNotEqual(b.loose('Military Belt Black Buckle/Black'), b.loose('Military Belt Silver Buckle/Black'))
        self.assertNotEqual(b.loose('[B품] Everyday Shirt [BL]'), b.loose('Everyday Shirt [BL]'))
        self.assertNotEqual(b.loose('[Artist 협업] Everyday Shirt [BL]'), b.loose('Everyday Shirt [BL]'))
        self.assertNotEqual(b.loose('Everyday Shirt (OLYMPIA)'), b.loose('Everyday Shirt (SUN)'))
        self.assertEqual(b.loose('[10/23 예약발송] Everyday Shirt [BL]'), b.loose('Everyday Shirt [BL]'))
        self.assertEqual(b.loose('[8th restock] Everyday Shirt [BL]'), b.loose('[5th restock] Everyday Shirt [BL]'))
        self.assertEqual(b.loose('[2nd] Everyday Shirt [BL]'), b.loose('Everyday Shirt [BL]'))
        self.assertEqual(b.loose('[Last one] Everyday Shirt [BL]'), b.loose('Everyday Shirt [BL]'))

    def test_named_and_unnamed_outer_material_and_lining(self):
        a, c = row(1), row(2, 'Everyday Cotton Shirt_White')
        tags = {a['source_url']: material(('', [('코튼', 100)])),
                c['source_url']: material(('겉감', [('코튼', 100)]), ('안감', [('폴리에스터', 100)]))}
        self.assertTrue(self.grouped([a, c], tags))
        tags[c['source_url']] = material(('겉감', [('코튼', 70), ('폴리에스터', 30)]))
        self.assertFalse(self.grouped([a, c], tags))
        tags[a['source_url']] = material(('안감', [('레이온', 50), ('폴리에스터', 50)]))
        tags[c['source_url']] = material(('안감', [('레이온', 48), ('폴리에스터', 52)]))
        self.assertTrue(self.grouped([a, c], tags))       # 3%p 이내는 같은 원단(색마다 1~2%p 다르게 적는 매장)
        tags[c['source_url']] = material(('안감', [('레이온', 40), ('폴리에스터', 60)]))
        self.assertFalse(self.grouped([a, c], tags))

    def test_measurement_uses_same_size_and_strict_two_cm(self):
        a, c = row(1), row(2, 'Everyday Cotton Shirt_White')
        sa = size(['S', 'M'], 가슴=[50, 53])
        sizes = {a['source_url']: sa, c['source_url']: size(['M', 'L'], 가슴=[55, 58])}
        self.assertTrue(self.grouped([a, c], sizes=sizes))
        sizes[c['source_url']] = size(['M', 'L'], 가슴=[55.1, 58])
        self.assertFalse(self.grouped([a, c], sizes=sizes))
        sizes[c['source_url']] = size(['L'], 가슴=[90])
        self.assertTrue(self.grouped([a, c], sizes=sizes))

    def test_unknown_cannot_bridge_conflicting_members_and_order_is_stable(self):
        rows = [row(1), row(2, 'Everyday Cotton Shirt_White'), row(3, 'Everyday Cotton Shirt_Navy')]
        tags = {rows[0]['source_url']: material(('', [('코튼', 100)])),
                rows[2]['source_url']: material(('', [('나일론', 100)]))}
        first = {r['product_no']: r['style_id'] for r in self.build(rows, tags)}
        second = {r['product_no']: r['style_id'] for r in self.build(list(reversed(rows)), tags)}
        self.assertEqual(first, second)
        self.assertNotEqual(first['1'], first['3'])

    def test_ocr_axis_shift_is_not_a_measurement_conflict(self):
        a, c = row(1), row(2, 'Everyday Cotton Shirt_White')
        left = size(['FREE'], 총장=[50.8], 어깨=[38.1], 가슴=[52.1], 소매길이=[63.5])
        right = dict(size(['FREE'], 총장=[38.1], 어깨=[52.1], 가슴=[63.5], 소매단=[13.3]), source='ocr')
        sizes = {a['source_url']: left, c['source_url']: right}
        self.assertTrue(self.grouped([a, c], sizes=sizes))
        right['source'] = 'html'
        self.assertFalse(self.grouped([a, c], sizes=sizes))

    def test_sold_out_duplicate_does_not_scatter_selling_siblings(self):
        rows = [row(1, status='SOLD_OUT'), row(2, status='ON_SALE'),
                row(3, 'Everyday Cotton Shirt_White', status='ON_SALE')]
        got = {r['product_no']: r['style_id'] for r in self.build(rows)}
        self.assertEqual(got['2'], got['3'])
        self.assertNotEqual(got['1'], got['2'])

    def test_same_name_pattern_and_option_fit_conflicts(self):
        a, c = row(1, 'Champion Cap'), row(2, 'Champion Cap')
        tags = {a['source_url']: {'tags': {'pattern': ['카모']}}, c['source_url']: {'tags': {'pattern': ['단색']}}}
        self.assertFalse(self.grouped([a, c], tags))
        tags[a['source_url']]['tags']['pattern'] = ['타이다이']
        self.assertFalse(self.grouped([a, c], tags))
        tags[a['source_url']]['tags']['pattern'] = ['멜란지']
        self.assertTrue(self.grouped([a, c], tags))
        a['options'], c['options'] = '오버핏', 'SLIM FIT | REGULAR FIT'
        self.assertFalse(self.grouped([a, c]))

    def test_fit_tags_are_fallback_and_option_fit_has_priority(self):
        a, c = row(1), row(2, 'Everyday Cotton Shirt_White')
        tags = {a['source_url']: {'tags': {'silhouette': ['슬림핏']}},
                c['source_url']: {'tags': {'silhouette': ['오버핏']}}}
        self.assertFalse(self.grouped([a, c], tags))
        a['options'], c['options'] = 'REGULAR FIT', 'REGULAR FIT'
        self.assertTrue(self.grouped([a, c], tags))

    def test_loose_candidates_pass_same_guard(self):
        a, c = row(1, 'PINK Everyday Cotton Shirt'), row(2, 'BLUE Everyday Cotton Shirt')
        tags = {a['source_url']: material(('', [('코튼', 100)])), c['source_url']: material(('', [('나일론', 100)]))}
        self.assertFalse(self.grouped([a, c], tags))
        tags[c['source_url']] = tags[a['source_url']]
        self.assertTrue(self.grouped([a, c], tags))

    def test_overlapping_containers_and_compound_option_color(self):
        a = row(1, 'Everyday Cotton Shirt [3colors]', representative_color='화이트·블랙·네이비')
        c = row(2, 'Everyday Cotton Shirt [4colors]', representative_color='화이트·블랙·네이비·카키')
        self.assertFalse(self.grouped([a, c]))
        a = row(1, 'Everyday Cotton Shirt [8colors]', representative_color='네이비')
        c = row(2, 'Everyday Cotton Shirt_White', representative_color='화이트')
        self.assertFalse(self.grouped([a, c]))  # 옵션 목록이 부족한 컨테이너도 색 SKU에 중복 병합하지 않는다.
        a = row(1, '2 COLOR STITCH Cotton Shirt_Black')
        c = row(2, '2 COLOR STITCH Cotton Shirt_White')
        self.assertTrue(self.grouped([a, c]))

        a, c = row(1, 'Nordic Hoodie Zip Up', options='BLACK+IVORY'), row(2, 'Nordic Hoodie Zip Up', options='KHAKI+BLACK')
        got = self.build([a, c])
        self.assertTrue(self.grouped([a, c]))
        self.assertEqual([r['color'] for r in got], ['블랙', '카키'])
        self.assertEqual([r['colors'] for r in got], ['블랙', '카키'])

    def test_duplicate_raw_color_labels_keep_distinct_shades(self):
        a, c = row(1), row(2)
        self.assertFalse(self.grouped([a, c]))
        a['name'], c['name'] = 'Everyday Cotton Shirt_Melange Grey', 'Everyday Cotton Shirt_Light Melange Grey'
        self.assertTrue(self.grouped([a, c]))
        a['name'], c['name'] = 'Everyday Cotton Shirt', 'Everyday Cotton Shirt_Charcoal'
        a['representative_color'] = '차콜'
        self.assertTrue(self.grouped([a, c]))  # 대표색만 같다는 이유로 분리하지 않는다.
        tags = {r['source_url']: material(('', [('울', 50), ('폴리에스터', 50)])) for r in [a, c]}
        sizes = {r['source_url']: size(['FREE'], 가슴=[60], 총장=[112]) for r in [a, c]}
        self.assertFalse(self.grouped([a, c], tags, sizes))

    def test_brand_and_category_boundaries(self):
        a, c = row(1), row(2)
        c['brand_slug'] = 'other'
        self.assertFalse(self.grouped([a, c]))
        c['brand_slug'], c['category'] = 'test', 'Outerwear'
        self.assertFalse(self.grouped([a, c]))

    def test_condition_set_collab_and_fit_guards(self):
        a = row(1)
        for name, options in [('[B품] Everyday Cotton Shirt_Black', ''),
                              ('리퍼브 Everyday Cotton Shirt_Black', ''),
                              ('Everyday Cotton Shirt SET_Black', ''),
                              ('Artist X Brand Everyday Cotton Shirt_Black', '')]:
            c = row(2, name, options=options)
            ar, cr = self.build([a, c])
            self.assertTrue(b.conflicts(ar, cr))


if __name__ == '__main__':
    unittest.main()
