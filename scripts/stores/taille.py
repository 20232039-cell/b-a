"""taille — imweb. 걷는 길과 한계는 stores/_imweb.py 머리말."""
from stores import _imweb

BASE = "https://taille.kr"
PAGE_DELAY = 1.0          # 매장당 초당 한 번(카페24 수집기와 같다)
list_urls, parse_page = _imweb.bind(BASE)
