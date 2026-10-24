"""브랜드관 공통 읽기. 차단·실패를 빈 목록으로 바꾸지 않는다."""
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import crawl_cafe24 as cc


def get(http, url, log=print):
    r = http.get(url, retries=0)
    if r is None or r.status_code != 200:
        log(f"  브랜드관 요청 실패: {getattr(r, 'status_code', None)} {url}")
        return None
    return r


def allowed(http, base, url, log=print):
    """매 판 robots를 확인한다. robots조차 못 읽으면 상태를 보존한다."""
    r = get(http, urljoin(base, '/robots.txt'), log)
    if r is None:
        return False
    robots = RobotFileParser()
    robots.parse(r.text.splitlines())
    if not robots.can_fetch(cc.UA, url):
        log(f"  robots.txt 수집 금지 (UA={cc.UA}): {url}")
        return False
    return True
