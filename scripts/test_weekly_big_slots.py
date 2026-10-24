"""큰 사진 칸(/big/)을 다시 묻지 않기 — 2026-11-11.

parse_detail 은 갤러리 사진마다 큰 칸이 있는지 매장 서버에 묻는다(cc._big_slot_ok). 주간 갱신이 아는 상품을 다시 읽을 때
지난 판에 확인해 저장한 주소는 다시 묻지 않아야 한다 — 이것 때문에 dunst · sinoon 이 혼자서 5시간 반을 넘겼다.
매장 서버에는 가지 않는다(requests.get 을 흉내로 바꿔 몇 번 묻는지만 센다).
"""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawl_cafe24 as cc
import weekly_update as weekly

URL = 'https://example.com/product/detail.html?product_no=1'


def page(names):
    imgs = ''.join(f'<img src="//example.com/web/product/small/202611/{n}.jpg">' for n in names)
    return (f'<title>상품</title><script>var product_price = "45000";</script>'
            f'<meta property="og:image" content="https://example.com/web/product/big/202611/main.jpg">'
            f'<div class="xans-product-image"><img src="//example.com/web/product/big/202611/main.jpg"></div>'
            f'<div class="xans-product-addimage">{imgs}</div>')


class BigSlotSeedTest(unittest.TestCase):
    def setUp(self):
        saved = dict(cc._BIG_OK)
        cc._BIG_OK.clear()
        self.addCleanup(lambda: (cc._BIG_OK.clear(), cc._BIG_OK.update(saved)))
        self.asked = []

        def get(url, **kw):
            self.asked.append(url)
            return Mock(status_code=200, headers={'Content-Type': 'image/jpeg'})
        self.enterContext(patch.object(cc.requests, 'get', side_effect=get))

    def parse(self, names):
        return cc.parse_detail(page(names), URL, cc.Shop('example', 'https://example.com', ''))

    def test_known_big_urls_are_not_asked_again(self):
        first = self.parse(['a', 'b', 'c'])
        self.assertEqual(len(self.asked), 3)                         # 처음엔 사진마다 한 번씩 묻는다
        self.assertTrue(all('/web/product/big/' in u for u in first['gallery']), first['gallery'])
        row = {**first, 'crawled_at': '2026-11-09T06:00:00'}
        cc._BIG_OK.clear(); self.asked.clear()                        # 다음 주 — 새 러너라 기억이 없다
        self.assertEqual(weekly.seed_big_slots({1: row}), 3)
        again = self.parse(['a', 'b', 'c'])
        self.assertEqual(self.asked, [])                             # 지난 판에 확인한 주소는 다시 묻지 않는다
        self.assertEqual(again['gallery'], first['gallery'])

    def test_new_photo_is_still_asked(self):
        row = {**self.parse(['a', 'b']), 'crawled_at': '2026-11-09T06:00:00'}
        cc._BIG_OK.clear(); self.asked.clear()
        weekly.seed_big_slots({1: row})
        self.parse(['a', 'b', 'new'])
        self.assertEqual(self.asked, ['https://example.com/web/product/big/202611/new.jpg'])

    def test_rows_read_before_the_check_existed_are_not_trusted(self):
        row = {**self.parse(['a', 'b']), 'crawled_at': '2026-10-26T06:00:00'}
        cc._BIG_OK.clear(); self.asked.clear()
        self.assertEqual(weekly.seed_big_slots({1: row, 2: {'gallery': None}, 3: {}}), 0)
        self.parse(['a', 'b'])
        self.assertEqual(len(self.asked), 2)

    def test_small_urls_kept_last_time_are_asked_again(self):
        # 큰 칸이 없어 작은 주소로 남긴 사진은 답을 심지 않는다 — 다음 판에 다시 묻는다(생겼을 수 있다).
        row = {'crawled_at': '2026-11-09T06:00:00',
               'gallery': ['https://example.com/web/product/small/202611/a.jpg']}
        self.assertEqual(weekly.seed_big_slots({1: row}), 0)
        self.parse(['a'])
        self.assertEqual(len(self.asked), 1)

    def test_seed_does_not_overwrite_a_fresh_answer(self):
        cc._BIG_OK['https://example.com/web/product/big/202611/a.jpg'] = False     # 이번 판에 물어 「없다」를 받았다
        weekly.seed_big_slots({1: {'crawled_at': '2026-11-09T06:00:00',
                                   'gallery': ['https://example.com/web/product/big/202611/a.jpg']}})
        self.assertFalse(cc._BIG_OK['https://example.com/web/product/big/202611/a.jpg'])


if __name__ == '__main__':
    unittest.main()
