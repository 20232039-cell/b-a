import unittest

from PIL import Image, ImageDraw

import photo_bg


class EdgeBgTest(unittest.TestCase):
    def test_flat_gray(self):
        im = Image.new("RGB", (300, 400), (239, 239, 239))
        ImageDraw.Draw(im).rectangle((60, 60, 240, 340), fill=(250, 250, 250))   # 흰 셔츠
        self.assertEqual(photo_bg.edge_bg(im), "efefef")

    def test_uneven_is_none(self):
        im = Image.new("RGB", (300, 400), (240, 240, 240))
        ImageDraw.Draw(im).rectangle((0, 300, 300, 400), fill=(120, 110, 100))   # 바닥 그림자 · 배경 사진
        self.assertIsNone(photo_bg.edge_bg(im))

    def test_transparent_png_is_white(self):
        im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((50, 50, 150, 150), fill=(20, 20, 20, 255))
        self.assertEqual(photo_bg.edge_bg(im), "ffffff")


if __name__ == "__main__":
    unittest.main()
