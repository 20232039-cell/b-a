"""수기 표를 적용한 뒤 앱 내보내기까지 원본을 보존하는 회귀 검사."""
import contextlib
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import export_app_data as export
import size_from_ocr as size


class ManualPreservationTest(unittest.TestCase):
    def test_short_manual_axis_does_not_truncate_other_sizes(self):
        text = ('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\n'
                'brand,u,S·M,총장,100·102,,,\n'
                'brand,u,S·M,엉덩이,48,,,\n'
                'brand,u,L·M·S,총장,42·41·40,,하의,숏팬츠\n'
                'brand,u,L·M·S,허리,38·36,,하의,숏팬츠\n')
        with patch.object(size, 'MANUAL') as manual:
            manual.exists.return_value = True
            manual.open.return_value = io.StringIO(text)
            ent = size.load_manual()['u']
        app = export.size_entry(ent)
        self.assertEqual(app['size_names'], ['S', 'M'])
        self.assertEqual(app['sizes'], {'총장': [100, 102], '엉덩이': [48]})
        self.assertEqual(app['size_parts'][0]['size_names'], ['L', 'M', 'S'])
        self.assertEqual(app['size_parts'][0]['sizes']['총장'], [42, 41, 40])

    def test_bottom_copy_of_base_table_is_dropped(self):
        text = ('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\n'
                'brand,u,S·M,총장,43·44,,,스커트\n'
                'brand,u,S·M,총장,43·44,,하의,스커트\n')
        with patch.object(size, 'MANUAL') as manual:
            manual.exists.return_value = True
            manual.open.return_value = io.StringIO(text)
            ent = size.load_manual()['u']
        self.assertNotIn('size_parts', export.size_entry(ent))

    def _load(self, text):
        with patch.object(size, 'MANUAL') as manual:
            manual.exists.return_value = True
            manual.open.return_value = io.StringIO('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\n' + text)
            return size.load_manual()['u']

    def test_length_groups_are_joined(self):
        ent = self._load('b,u,30L-01·30L-02,총장,103·104,,,\nb,u,32L-01·32L-02,총장,107·108,,,\n')
        self.assertEqual(ent['size_names'], ['30L-01', '30L-02', '32L-01', '32L-02'])
        self.assertEqual(ent['sizes']['총장'], [103, 104, 107, 108])

    def test_respelled_names_stay_one_group(self):
        ent = self._load('b,u,34·36,총장,100·101,,,\nb,u,34 size·36 size,허리,40·42,,,\n')
        self.assertEqual(ent['size_names'], ['34', '36'])
        self.assertEqual(ent['sizes']['허리'], [40, 42])

    def test_component_tab(self):
        ent = self._load('b,u,S·M,총장,61·62,,,반팔\nb,u,FREE,총장,52.1,,슬리브리스,슬리브리스\n')
        app = export.size_entry(ent, None, {'brand_slug': 'b', 'product_no': '1'})
        self.assertEqual(app['size_parts'][0]['part'], '슬리브리스')
        self.assertEqual(app['t'], '반팔')

    def test_check_flags(self):
        tab = {'size_names': ['S', 'M', 'M'], 'sizes': {'화장': [88, 90, 921], '밑단': [121.4, 124]}}
        self.assertEqual(export.size_chk(tab, [('밑단', 'S')]), {'_names': [2], '화장': [2], '밑단': [0]})

    def test_manual_postprocessing_keeps_names_and_values(self):
        for names in [['SS', 'M'], ['BLACK', 'WHITE'], ['TOP', 'PANTS'], ['?', 'M']]:
            with self.subTest(names=names):
                out = {'u': {'source': 'manual', 'size_names': names,
                             'sizes': {'총장': [153.035, 13], '가슴': [85, 40.75],
                                       '소매길이': [73, 74], '어깨': [44.2, 46.6]}}}
                expected = deepcopy(out)
                rows = {'u': {'brand_slug': 'brand', 'name': 'top', 'category': 'Tops',
                              'options': 'S | M', 'subtype': '반팔'}}
                size.clean_names(out)
                size.names_from_options(out, rows)
                size.repair_names(out, rows)
                size.drop_piece_tables(out)
                size.drop_reversed_labels(out)
                size.unit_guard(out)
                size.sleeve_from_neck(out, rows)
                self.assertEqual(out, expected)
                public = {k: v for k, v in expected['u'].items() if k != 'source'}   # 출처는 공개 게시본에 안 싣는다
                self.assertEqual(export.size_entry(out['u']), public)

    def test_html_and_ocr_export_still_tidy_descending_rows(self):
        for source in ['html', 'ocr']:
            with self.subTest(source=source):
                ent = {'source': source, 'size_names': ['L', 'M', 'S'],
                       'sizes': {'총장': [72, None, 68], '어깨': [46.6, None, 44.2]},
                       'size_parts': [{'part': '하의', 'size_names': ['M', 'S'],
                                       'sizes': {'총장': [42, 40]}}]}
                app = export.size_entry(ent)
                self.assertEqual(app['size_names'], ['S', 'L'])
                self.assertEqual(app['sizes']['어깨'], [44.2, 46.6])
                self.assertEqual(app['size_parts'][0]['size_names'], ['S', 'M'])


if __name__ == '__main__':
    unittest.main()
