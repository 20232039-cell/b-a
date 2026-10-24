"""대표 사진 회귀: 네트워크·data 쓰기 없이 실제 실패 형태와 부분 응답을 재현한다."""
import io
import unittest
from collections import Counter
from unittest.mock import Mock, patch

import requests
from PIL import Image
from bs4 import BeautifulSoup

import crawl_cafe24 as cc
from stores import rolarola

BASE = 'https://example.com'
OG = BASE + '/web/product/big/swatch.jpg'
PHOTO = BASE + '/web/product/tiny/photo.gif'


def soup(body):
    return BeautifulSoup(body, 'lxml')


def response(width=900, height=1200, status=200, headers=None):
    stream = io.BytesIO()
    Image.new('RGB', (width, height), 'white').save(stream, format='JPEG')
    body = stream.getvalue()
    r = Mock(status_code=status, headers=headers if headers is not None else {'Content-Length': str(len(body))})
    r.iter_content.side_effect = lambda chunk_size: (body[i:i+chunk_size] for i in range(0, len(body), chunk_size))
    return r


class ProductImageTests(unittest.TestCase):
    def parse(self, body, http=None):
        html = '<title>Jacket</title><script>var product_price="100000";</script>' + body
        with patch.object(cc, '_big_slot_ok', return_value=True):
            return cc.parse_detail(html, BASE+'/product/jacket/4837/', cc.Shop('test', BASE, ''), http=http)

    def test_space_unicode_and_existing_escapes(self):
        for url in ('/web/product/big/A B%20C%2f한글.jpg', '//cdn.test/A B%20C%2f한글.jpg',
                    'https://cdn.test/A B%20C%2f한글.jpg?name=A B&token=a%2Bb+c%26d#A B'):
            with self.subTest(url=url):
                got = cc._fix_url(url, BASE)
                self.assertNotIn(' ', got)
                self.assertNotIn('%2520', got)
                self.assertNotIn('%252f', got)
                self.assertIn('%2f', got)
                self.assertEqual(cc._fix_url(got, BASE), got)
        self.assertEqual(cc._fix_url('/a b?x=a%26b&y=1+2', BASE), BASE+'/a%20b?x=a%26b&y=1+2')
        self.assertEqual(cc._fix_url('', BASE), '')

    def test_html_and_url_dimensions_need_no_request(self):
        http = Mock()
        for body, url in [
            (f'<meta property="og:image" content="{OG}"><meta property="og:image:width" content="50">', OG),
            ('', OG+'?width=50&height=50'), ('', BASE+'/thumb_50x50.jpg')]:
            self.assertTrue(cc._og_too_small(soup(body), url, BASE, http))
        http.get.assert_not_called()

    def test_og_dimensions_belong_to_the_matching_image(self):
        other = BASE+'/other.jpg'
        body = (f'<meta property="og:image" content="{OG}"><meta property="og:image:width" content="900">'
                '<meta property="og:image:height" content="1200">'
                f'<meta property="og:image" content="{other}"><meta property="og:image:width" content="50">')
        http = Mock()
        self.assertFalse(cc._og_too_small(soup(body), OG, BASE, http))
        http.get.assert_not_called()

    def test_unknown_size_makes_no_request(self):
        # 크기 표시가 없는 OG 는 요청 없이 그대로 둔다(상품마다 그림을 더 받지 않는다)
        http = Mock()
        self.assertFalse(cc._og_too_small(soup(''), OG, BASE, http))
        http.get.assert_not_called()

    def test_color_img_og_uses_real_tiny_without_request(self):
        http = Mock()
        body = (f'<meta property="og:image" content="{OG}">'
                f'<div class="xans-product-image"><img src="{PHOTO}"></div>'
                f'<div class="xans-product-image color-img"><img src="{OG}"></div>')
        row = self.parse(body, http)
        self.assertEqual(row['image_url'], PHOTO)
        self.assertNotIn(OG, row['gallery'])
        http.get.assert_not_called()

    def test_rejected_og_never_reappears_as_main_or_big_gallery(self):
        small = OG.replace('/big/', '/small/')
        body = (f'<meta property="og:image" content="{OG}"><meta property="og:image:width" content="50">'
                f'<div class="xans-product-image"><img src="{OG}"><img src="{small}"></div>')
        http = Mock()
        row = self.parse(body, http)
        self.assertEqual(row['image_url'], small)
        self.assertNotIn(OG, row['gallery'])
        http.get.assert_not_called()

    def test_rejected_og_fallback_skips_related_product_before_main_photo(self):
        body = (f'<meta property="og:image" content="{OG}">'
                f'<div class="recommend"><img src="{BASE}/web/product/big/other.jpg"></div>'
                f'<div class="xans-product-image"><img src="{PHOTO}"></div>'
                f'<div class="color-img"><img src="{OG}"></div>')
        http = Mock()
        row = self.parse(body, http)
        self.assertEqual(row['image_url'], PHOTO)
        self.assertFalse(any('other.jpg' in u for u in row['gallery']))
        http.get.assert_not_called()

    def test_shared_logo_falls_back_to_thumbimage_nonstandard_skin(self):
        body = (f'<meta property="og:image" content="{BASE}/RSCSNSLOGO.jpg">'
                f'<img class="ThumbImage" src="{PHOTO}">')
        http = Mock()
        self.assertEqual(self.parse(body, http)['image_url'], PHOTO)
        http.get.assert_not_called()

    def test_regular_og_is_preserved(self):
        body = f'<meta property="og:image" content="{OG}"><img class="ThumbImage" src="{PHOTO}">'
        http = Mock()
        self.assertEqual(self.parse(body, http)['image_url'], OG)
        http.get.assert_not_called()

    def test_existing_brand_wide_shared_image_rule(self):
        shared = BASE+'/campaign.jpg'
        rows = [{'image_url': shared, 'gallery': [f'{BASE}/{i}.jpg']} for i in range(10)]
        uses = Counter(r['image_url'] for r in rows)
        self.assertEqual([cc._representative_image(r, uses) for r in rows], [f'{BASE}/{i}.jpg' for i in range(10)])
        self.assertEqual(cc._representative_image({'image_url': shared, 'gallery': []}, uses), '')
        # 2~6개 색상 상품이 실제 대표컷을 나눠 쓰는 기존 규칙도 보존한다.
        self.assertEqual(cc._representative_image({'image_url': shared}, Counter({shared: 6})), shared)
        self.assertEqual(cc._representative_image({'image_url': shared}, Counter({shared: 1})), shared)

    def test_rolarola_first_visible_slide_wins_over_mainimg_and_addimg(self):
        html = ('<form name="prdFrm"><input name="product_name" value="Jacket">'
                '<input name="total_prc" value="100000"></form><div data-pno="2966"></div>'
                '<!-- <img id="mainImg" src="https://cdn.test/comment.jpg"> -->'
                '<img id="mainImg" src="https://cdn.test/old.jpg">'
                '<img src="https://cdn.test/outside.jpg#addimg">'
                '<ul id="product_add_image_list"><li><img src="https://cdn.test/first.jpg#addimg"></li>'
                '<li><img src="https://cdn.test/second.jpg#addimg"></li></ul>'
                '<div class="detail_info"><img src="https://cdn.test/detail.jpg"></div>')
        row = rolarola.parse_page(html, rolarola.BASE+'/shop/detail.php?pno='+'A'*32, 'rolarola', 'now')
        self.assertEqual(row['image_url'], 'https://cdn.test/first.jpg')
        self.assertEqual(row['gallery'][:2], ['https://cdn.test/first.jpg', 'https://cdn.test/second.jpg'])
        self.assertNotIn('https://cdn.test/detail.jpg', row['gallery'])
        self.assertNotIn('https://cdn.test/old.jpg', row['gallery'])

    def test_polite_session_streaming_206_returns_without_retry(self):
        http = cc.PoliteSession(0)
        r = Mock(status_code=206)
        with patch.object(http.s, 'get', return_value=r) as get:
            self.assertIs(http.get(OG, retries=0, headers={'Range':'bytes=0-8191'}, stream=True), r)
            self.assertTrue(get.call_args.kwargs['stream'])
            get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
