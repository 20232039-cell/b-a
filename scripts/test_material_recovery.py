"""저장 설명·OCR의 혼용률 복구와 부분 공개 방어. 네트워크·원본 data에는 쓰지 않는다."""
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import product_desc as desc
import tag_items as tag


def group(part, *values):
    return {"p": part, "v": [list(v) for v in values]}


class MaterialRecoveryTest(unittest.TestCase):
    def assertMix(self, text, expected, ocr=False):
        self.assertEqual(desc.mix(text, ocr)[0], expected)

    def test_saved_tabs_keep_remote_material_context(self):
        text = ('DESCRIPTION\nSIZE\nMATERIAL\nSHIPPING & RETURNS\n'
                '레귤러 핏 셔츠\n1 size 가슴 55 어깨 49\nMODEL SIZE\n186cm\n'
                'Cotton 48 polyester 49 polyurethane 3\nCARE\n드라이클리닝')
        self.assertMix(text, [group('', ('코튼', 48), ('폴리에스터', 49), ('스판덱스', 3))])
        self.assertMix('MATERIAL\n제품 설명\n100 genuine leather (cowhide)\nCARE',
                       [group('', ('소가죽', 100))])

    def test_bare_bullet_and_numbered_materials(self):
        self.assertMix('SHELL: POLYESTER 100% MESH SLING BAG / Black ₩207,000 ₩144,900',
                       [group('겉감', ('폴리에스터', 100))])
        self.assertMix('SHELL: POLYESTER 100% MESH: UNKNOWN 95 / Black ₩207,000', [])
        self.assertMix('원단: Cotton 100%',  [group('원단', ('코튼', 100))])
        self.assertMix('SHELL: POLYURETHANE 50% RECYCLED POLYESTER 50% / LINING: POLYESTER',
                       [group('겉감', ('폴리우레탄', 50), ('폴리에스터', 50))])
        self.assertMix('Body: Nylon 100% Lining: Polyester 100% 1. 원단: Polyester 86% Polyurethane 14%', [])
        self.assertMix('Body: Cotton 100% Lining: Polyester 100% 원단: Cotton 100%',
                       [group('겉감', ('코튼', 100)), group('안감', ('폴리에스터', 100))])
        self.assertMix('SLIT CARDIGAN - nylon 55 fine wool 25 baby alpaca 20 - V넥 니트.',
                       [group('', ('나일론', 55), ('울', 25), ('알파카', 20))])
        self.assertMix('FABRIC 1 : NYLON 100% 2 : POLYESTER 100%',
                       [group('원단 1', ('나일론', 100)), group('원단 2', ('폴리에스터', 100))])
        self.assertMix('Material Fabric 1: Cotton 100% / Fabric 2: Polyester 100%',
                       [group('원단 1', ('코튼', 100)), group('원단 2', ('폴리에스터', 100))])
        self.assertMix('FABRIC1: NYLON 100% FABRIC2: POLYESTER 100%',
                       [group('원단 1', ('나일론', 100)), group('원단 2', ('폴리에스터', 100))])
        self.assertMix('(1) ACRYLIC 40% NYLON 30% POLYESTER 30% '
                       '(2) ACRYLIC 50% NYLON 20% POLYESTER 30%',
                       [group('원단 1', ('아크릴', 40), ('나일론', 30), ('폴리에스터', 30)),
                        group('원단 2', ('아크릴', 50), ('나일론', 20), ('폴리에스터', 30))])
        self.assertMix('Cotton 60% Nylon 40% / Cotton 100%',
                       [group('원단 1', ('코튼', 60), ('나일론', 40)), group('원단 2', ('코튼', 100))])

    def test_suffix_parts_and_script_boundary(self):
        # 실제 표본: 코디 상품명 및 사진 OCR 잡음이 완전한 소재 값을 지우지 않는다.
        for tail in ['TOP : CASHMERE BLENDED ROUND WOOL KNIT_Ivory',
                     'A 9, 4 : WO 7,\n4 i, ^ - ee Wwe',
                     'OH Ry Ce Rt De EVE UY GDh 4 St a 9: RY 29. 107']:
            _, _, lost = desc.mix_cands('Fabric: Wool 93% Cashmere 7%\n' + '설명 ' * 40 + '\n' + tail, ocr=True)
            self.assertFalse(lost['parts'], (tail, lost))
        self.assertMix('1.nylon 80 span 20 (겉감) / 2. polyester 83 polyurethane 17 (안감)',
                       [group('겉감', ('나일론', 80), ('스판덱스', 20)),
                        group('안감', ('폴리에스터', 83), ('스판덱스', 17))])
        self.assertMix('레귤러 핏COTTON100%', [group('', ('코튼', 100))])
        self.assertMix('7게이지 코튼 100 원사로 부드러운 촉감 COTTON 100% SIZE GUIDE',
                       [group('', ('코튼', 100))])

    def test_shared_parts_set_and_circled_part_numbers(self):
        self.assertMix('Fabric Shell, Lining: Nylon 100% / Filling: Polyester 100%',
                       [group('겉감', ('나일론', 100)), group('안감', ('나일론', 100)),
                        group('충전재', ('폴리에스터', 100))])
        self.assertMix('겉　감: 100% 폴리에스테르 안감①: 100% 면 안감②: 100% 나일론',
                       [group('겉감', ('폴리에스터', 100)), group('안감 1', ('코튼', 100)),
                        group('안감 2', ('나일론', 100))])
        self.assertMix('FABRIC Shell:(TOP) Cotton 95% Polyuretane 5% '
                       'Contrast:(SKIRT) Nylon 100% / (SCARF) Polyester 70% Rayon 30%',
                       [group('상의', ('코튼', 95), ('스판덱스', 5)), group('스커트', ('나일론', 100)),
                        group('스카프', ('폴리에스터', 70), ('레이온', 30))])

    def test_number_first_specific_leather_and_raw_polyurethane(self):
        self.assertMix('(SHELL1) 100% COW SKIN (SHELL2) 100% GOAT SKIN '
                       '(LINING) 100% PIG SKIN (OUTSOLE) 100% RUBBER',
                       [group('겉감 1', ('소가죽', 100)), group('겉감 2', ('염소가죽', 100)),
                        group('안감', ('돼지가죽', 100)), group('밑창', ('고무', 100))])
        self.assertMix('SHELL 1 : 100% COW LEATHRT SHELL 2 : 100% COW LEATHRT LINING : 100% POLYURETHANE',
                       [group('겉감 1', ('소가죽', 100)), group('겉감 2', ('소가죽', 100)),
                        group('안감', ('폴리우레탄', 100))])
        self.assertMix('1. cotton 57 polyester 40 span 3 / '
                       '2. polyurethane 56 cotton 24 rayon 14 span 6 - 2가지 원단',
                       [group('원단 1', ('코튼', 57), ('폴리에스터', 40), ('스판덱스', 3)),
                        group('원단 2', ('폴리우레탄', 56), ('코튼', 24), ('레이온', 14), ('스판덱스', 6))])

    def test_aliases_abbreviations_and_processed_fiber_names(self):
        for text, expected in [
            ('Acrylic 50% Nylon 20% Alpaca Wool 30%', group('', ('아크릴', 50), ('나일론', 20), ('알파카', 30))),
            ('RECYCLE POLY 50% CASH POLY 25% RAYON 25%', group('', ('폴리에스터', 75), ('레이온', 25))),
            ('Cash 3% Wool 87% Nylon 10%', group('', ('캐시미어', 3), ('울', 87), ('나일론', 10))),
            ('100% CASH POLY', group('', ('폴리에스터', 100))),
            ('COW SPLIT LEATHER 100%', group('', ('소가죽', 100))),
            ('혼용률: SHELL-CU58 R42', group('겉감', ('큐프라', 58), ('레이온', 42))),
            ('혼용율 : C100', group('', ('코튼', 100))),
            ('COMPOSITION: CO72/RY3/PE25', group('', ('코튼', 72), ('레이온', 3), ('폴리에스터', 25))),
        ]:
            with self.subTest(text=text):
                self.assertMix(text, [expected])
        self.assertMix('Brass hardware COW LEATHER 100% HARDWARE : BRASS 100%',
                       [group('', ('소가죽', 100)), group('금속부속', ('황동', 100))])
        self.assertMix('Pendant: UV Acrylic 100% String: Polyester 100%',
                       [group('펜던트', ('UV 아크릴', 100)), group('끈', ('폴리에스터', 100))])

    def test_stored_ocr_keeps_labeled_quotes_and_known_other_fibers(self):
        self.assertMix('Fabric "WOOL 47 NYLON 28 FOX 20\nSPAN 5"\nWash Dry cleaning',
                       [group('', ('울', 47), ('나일론', 28), ('폭스', 20), ('스판덱스', 5))], True)
        self.assertMix('Fabric\nWOOL # 58% NYLON 나일론 31% ACRYLIC 아크릴 7% OTHER FIBERS 기타섬유 4%',
                       [group('', ('울', 58), ('나일론', 31), ('아크릴', 7), ('기타', 4))], True)

    def test_incomplete_unknown_conflicting_and_units_stay_empty(self):
        for text in [
            'Lining: Polyester 100%', '100% Cupro 안감',
            '• 람포 지퍼 • 슬림핏 • 테스트 포켓 • 100% Cupro 안감 • MADE IN KOREA Shipping 기본 배송료는 3000원',
            '[Main1] 파피루스 90 폴리에틸렌 10 [Main2] 소가죽 [Lining] 소가죽',
            'SHELL: COW LEATHER LINING: Polyester 100%',
            'Silver 92.5%', '안감: Rayon 52%', 'Bio Leather 100%',
            'FABRIC: Cotton 80% Unknownfiber 20%', 'FABRIC: Cotton 60% Nylon 30%',
            'Payment information COTTON 100 XS부터', 'COTTON 20\'S', 'NYLON 66 30D',
            'Fabric: Cotton 100% / Fabric: Nylon 100%',
            '※ COTTON 100% 소재의 경우 수축합니다.',
        ]:
            with self.subTest(text=text):
                self.assertFalse(desc.mix(text)[0])

    def test_same_prefix_does_not_discard_tail_or_browser(self):
        row = {'product_no': '1', 'source_url': 'https://example.test/1', 'name': 'Top', 'category': 'Tops'}
        prefix = '고유한 상품 설명입니다. ' * 25
        record = {'description': prefix, 'detail_text': prefix + ' 82% POLYESTER 18% SPANDEX'}
        def latest(path):
            return {'1': record} if path.parent == tag.CRAWL else {}
        with patch.object(tag, 'load_latest', side_effect=latest), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(tag, 'load_paddle', return_value={}), \
             patch.object(tag, 'load_llm_mat', return_value={}):
            self.assertEqual(tag.blend_of_brand('test', [row])[row['source_url']],
                             [group('', ('폴리에스터', 82), ('스판덱스', 18))])

    def test_material_spec_keeps_shoe_parts_and_long_value(self):
        row = {'product_no': '1', 'source_url': 'https://example.test/1', 'name': 'Shoe', 'category': 'Shoes'}
        record = {'spec': {'외피': 'LAMBSKIN SUEDE 100%', '내피': 'PIG SKIN 100%',
                           '인솔': 'LAMBSKIN 100%', 'Fabric Description': ' ' * 450 + 'SHELL: LAMBSKIN 100%'}}
        with patch.object(tag, 'load_latest', side_effect=lambda path: {'1': record} if path.parent == tag.CRAWL else {}), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(tag, 'load_paddle', return_value={}), \
             patch.object(tag, 'load_llm_mat', return_value={}):
            self.assertEqual(tag.blend_of_brand('test', [row])[row['source_url']],
                             [group('겉감', ('양가죽', 100)), group('안감', ('돼지가죽', 100)),
                              group('깔창', ('양가죽', 100))])

    def test_manual_incomplete_rows_and_lining_only_are_blocked(self):
        text = ('브랜드,링크,부위,섬유,퍼센트,왜\n'
                'x,partial,겉감,나일론,,비율 미공개\n'
                'x,partial,안감,큐프라,100,안감\n'
                'x,lining,안감,폴리에스터,100,안감뿐\n'
                'x,valid,겉감,코튼,100,명시\n'
                'x,bad,겉감,울,92.5,부분\n')
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'open', side_effect=lambda **kwargs: io.StringIO(text)), \
             patch.object(tag, 'MANUAL_MAT_RAW', {}):
            self.assertEqual(tag.load_manual_mat(), {'valid': [group('', ('코튼', 100))]})

    def test_shoe_ocr_does_not_drop_explicit_sole_ratio_for_llm_subset(self):
        row = {'product_no': '1', 'source_url': 'https://example.test/1', 'name': 'Winter boots', 'category': 'Shoes'}
        raw = 'fabric\nIH0A - POLYESTER 100%\nIH - POLYESTER 100%\nOFLA - TPR 100%\ncolor BLACK\n' + '제품 설명 ' * 20
        def latest(path):
            return {'1': {'ocr_text': raw}} if path.parent == tag.OCR else {}
        with patch.object(tag, 'load_latest', side_effect=latest), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(tag, 'load_paddle', return_value={}), \
             patch.object(tag, 'load_llm_mat', return_value={row['source_url']: 'POLYESTER 100%'}):
            self.assertNotIn(row['source_url'], tag.blend_of_brand('test', [row]))
        self.assertMix('UPPER: POLYESTER 100% LINING: POLYESTER 100% OUTSOLE: TPR 100%',
                       [group('겉감', ('폴리에스터', 100)), group('안감', ('폴리에스터', 100)), group('밑창', ('TPR', 100))])


if __name__ == '__main__':
    unittest.main()
