"""samo-ondoh — imweb 으로 본다(platforms.PAGES 주석 — 국외 차단이라 아직 못 열어 봤다). 걷는 길과 한계는 stores/_imweb.py 머리말."""
from stores import _imweb

BASE = "https://www.samoondoh.co.kr"
PAGE_DELAY = 1.0          # 매장당 초당 한 번(eenk 와 같다)
list_urls, parse_page = _imweb.bind(BASE)
