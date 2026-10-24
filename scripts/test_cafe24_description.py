"""긴 상품간략설명을 안내보다 먼저 모으는지 검사한다."""
import unittest
from unittest.mock import patch

import crawl_cafe24 as crawl


class Cafe24DescriptionTest(unittest.TestCase):
    def parse(self, brief, details):
        html = ('<title>워크 재킷</title><script>var product_price = "128000";</script>'
                '<div class="xans-product-detail"><table class="xans-product-detaildesign">'
                '<tr><th>상품간략설명</th><td>' + brief + '</td></tr></table></div>'
                '<div id="prdDetail">' + details + '</div>')
        return crawl.parse_detail(html, 'https://example.com/product/jacket/1450/',
                                  crawl.Shop('arto', 'https://example.com', ''))

    def test_long_brief_starts_description_before_fabric_and_care(self):
        brief = ('탄탄한 트윌 코튼 소재의 세미 오버핏 워크 재킷입니다. ' * 12).strip()
        got = self.parse(brief, '<p>MODEL SIZE: HEIGHT 166cm</p>'
                        '<h3>Fabric &amp; Care</h3><p>COTTON 100%</p>'
                        '<p>전문 드라이클리닝을 권장합니다.</p>')
        self.assertNotIn('상품간략설명', got['spec'])
        self.assertTrue(got['description'].startswith(brief))
        self.assertTrue(got['detail_text'].startswith(brief))
        self.assertIn('COTTON 100%', got['description'])

    def test_brief_survives_long_detail_limit(self):
        brief = '소뿔 단추와 패치 포켓으로 완성한 세미 오버핏 재킷입니다.'
        got = self.parse(brief, '<p>' + 'Fabric &amp; Care COTTON 100% ' * 180 + '</p>')
        self.assertTrue(got['description'].startswith(brief))
        self.assertEqual(len(got['description']), 4000)

    def test_repeated_brief_is_collected_once(self):
        brief = '바이오 워싱으로 부드러운 터치감을 살린 코튼 재킷입니다.'
        got = self.parse(brief, '<p>' + brief + '</p><p>COTTON 100%</p>')
        self.assertEqual(got['description'].count(brief), 1)
        self.assertIn('COTTON 100%', got['description'])

    def test_policy_brief_is_still_excluded(self):
        got = self.parse('교환 및 반품 안내 상품 수령 후 7일 이내 접수해 주세요.',
                         '<p>부드러운 코튼 소재와 소뿔 단추를 사용했습니다.</p>')
        self.assertNotIn('7일', got['description'])
        self.assertIn('소뿔 단추', got['description'])

    def test_commented_template_does_not_enter_description(self):
        got = self.parse('부드러운 코튼 소재의 니트 가디건입니다.',
                         '<!-- <p>세미 오버핏 봄버 자켓 워시드 코튼 소재</p> -->'
                         '<p>자개 단추와 골지 디테일의 가디건입니다.</p>')
        for key in ('description', 'detail_text'):
            self.assertNotIn('봄버', got[key])
            self.assertIn('가디건', got[key])

    def test_json_ld_spacing_uses_matching_body_inside_other_text(self):
        # JSON-LD의 <br> 경계가 사라져도 본문의 같은 글에서만 빈칸을 되살린다.
        import json
        ref = ('면 혼방 이중 메쉬 원단 사용 내부에 면 혼방 배색 소매 및 몸판 밑단 스트링 디테일로 다양한 연출 가능 후드 스트링 조절 가능 '
               '전면 지퍼 포켓과 후면의 절개선이 특징이며 여유 있는 오버핏 실루엣으로 제작되었습니다.')
        glued = ref.replace('사용 내부', '사용내부').replace('배색 소매', '배색소매').replace('가능 후드', '가능후드')
        html = ('<title>메쉬 후드</title><script>var product_price="100000";</script>'
                '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'name': '메쉬 후드',
                'description': glued}, ensure_ascii=False) + '</script><div id="prdDetail">상품 정보 ' + ref + '</div>')
        with patch.object(crawl, '_big_slot_ok', return_value=False):
            got = crawl.parse_detail(html, 'https://example.com/product/hood/12/', crawl.Shop('test', 'https://example.com', ''))
        self.assertEqual(got['description_source'], 'json-ld')
        self.assertIn('사용 내부', got['description'])
        self.assertIn('가능 후드', got['description'])


if __name__ == '__main__':
    unittest.main()
