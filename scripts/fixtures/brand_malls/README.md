# 브랜드관 회귀 검사 원문 조각

2026-10-22 UTC, `curl -sL -A "Mozilla/5.0"`로 받은 공개 응답에서 추출했다.
전체 원문·응답 헤더·URL·curl 종료코드는 작업 폴더의 `trial/<slug>/`에 있다.
테스트는 이 폴더만으로 오프라인 실행된다.

- `customellow_list1.json`, `customellow_list2.json`: `https://www.kolonmall.com/Brands/CUSTOMELLOW/List/4942` 및 `?page=2`의 Apollo `brandV2`, `productSearchCodes`. 각 60개 결과, 원래 페이지 정보와 브랜드 정보를 유지했다.
- `customellow_detail{1,2,3}.json`: `/Brands/CUSTOMELLOW/Product/CWPAA26551BKX`, `/CWUAW26881GYD`, `/CWUAW26813BKX`의 Apollo `productDetail` 객체. 설명 HTML·옵션 재고·가격·혼용률·실측을 보존했다. 계정 정보와 추천 패널은 포함하지 않았다.
- `xtonz_list1.html`, `xtonz_list2.html`: `https://folixstore.com/product/list.html?cate_no=567` 및 `&page=2`의 실제 상품 목록·페이지 이동 영역.
- `xtonz_detail{1,2,3}.html`: `/product/detail.html?product_no=6863&cate_no=567`, `6862`, `6861`의 상품 정보·사진·옵션·설명·JSON-LD·상품 JS 변수 영역. 공통 Cafe24 파서가 사용하는 원문 조각이다.
- `*_robots.txt`: 각 판매몰의 `/robots.txt` 응답 원문. SSF SHOP은 특정 봇들만 허용하고 일반 수집기를 금지한다. 허용된 봇 이름으로 가장하지 않는다.

`test_brand_malls.py`의 변형 사례는 저장 원문을 복사한 뒤 통화·재고·브랜드·응답 상태만 바꿔 실패와 오염 방지를 검증한다. 실제 표본 6개는 모두 판매 가능이며, 품절과 통화 불일치 사례는 합성 회귀 검사다.
