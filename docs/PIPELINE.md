# 수집 순서 — 매장 들이기부터 앱까지

정본은 코드다. 이 문서는 흩어진 차례를 한 장에 모은 지도이고, 칸마다 그 결정이 적힌 파일을 적는다.
차례를 바꾸면 여기와 그 파일을 같이 고친다.

## 1. 매장 들이기

1. **씨앗** — 데이터 저장소 `data/brands_seed.csv` 한 줄(slug · 성별 · 공식몰 주소). 사람이 성별을 판정하고,
   목록에서 뺀 매장은 씨앗에서 지운다. 공식몰 주소는 **원화를 파는 한국몰**이어야 한다(해외몰은 달러라
   build-csv 가 통째로 뺀다 — egnarts 2026-09-27).
2. **틀 가르기** — `scripts/platforms.py`
   - 카페24: 기본(`crawl_cafe24.py`).
   - Shopify(`SHOPIFY`) · 식스샵(`SIXSHOP`): 틀 전용 수집기.
   - 그 밖(`PAGES` — 고도몰·위사·메이크샵·직접 만든 사이트): 매장별 해석기 `scripts/stores/<slug>.py` 와
     구동부 `crawl_pages.py`. 해석기 규약은 `crawl_pages.py` 머리말. 시험은 `stores/_try.py`.
   - 편집숍은 **자체 상품만** 걷는다(사람 결정 2026-09-28 — khakis · insane-garage).
3. **첫 수집** — 워크플로 `crawl-brand`(`crawl.yml`, 손으로). 새 매장은 `platforms.NEW_HOLD` 에 넣어
   **사람이 보기 전까지 앱에 안 나가게** 한다.

## 2. 상품 한 벌에서 정보를 얻는 차례

**사이즈표** — 사람이 정한 차례(2026-09-18, `size_from_ocr.py` 합치기 부분). 앞에서 얻으면 뒤는 안 본다.

| 차례 | 어디서 | 누가 거두나 |
|---|---|---|
| ① | 상품 페이지 **서버 HTML** 의 표·글 | `crawl_cafe24.parse_detail` (표 → 글 꼴) |
| ② | **토글·아코디언을 펼친** HTML — 글은 이미 HTML 안에 있다 | 같은 곳(`.accordion-*` · `#prdInfo` 등 그릇 목록) |
| ②' | 자바스크립트가 그리는 표 | `browser_collect.py`(`browser.yml`) |
| ③ | **버튼이 여는 창** — 카페24 사이즈가이드(`/product/sizeguide.html`) · 크리마 핏 위젯 | `fetch_sizeguide.py`(`sizeguide.yml`, 매일) · `fetch_cremafit.py` |
| ④ | 그래도 없으면 **사진** — 상세 그림·사이즈가이드 그림 OCR(tesseract) | `ocr_detail_images.py`(`ocr.yml`) |
| ⑤ | 사람이 옮겨 적은 값 | `data/manual_sizes.csv` — 무엇보다 앞선다 |
| ⑥ | 색만 다른 형제의 표 물려받기 | `size_from_ocr.py` (출처 `sibling`) |

값은 어디서 왔든 라벨 범위(`size_labels.json`)를 벗어나면 뺀다. 둘레로 적힌 값은 반으로 나눠 단면으로
맞추고, 둘레인지 단면인지 가릴 수 없으면 비운다(**틀린 값보다 빈 값**).

**설명·소재** — 같은 결: HTML 글이 있으면 그것으로 끝, 없으면 그림에서 읽은 글(`detail_from_ocr.py`)에서
소재·취급·설명 문장만 골라 뽑는다(사람 지시 2026-09-05 「html에 있으면 그걸로 끝. 없으면 이미지」).
앱에 나가는 설명은 `product_desc.py` 가 씻는다(배송·교환 공지 · 주문 상자 · 틀 조각 · 반복 문구).

**채우기 한 바퀴** — `fill-gaps`(`fill.yml`): ① 상세 다시 열기(`refetch`, select=gaps) → ② 그림 읽기(`ocr`).
①에서 채워진 상품은 ②에서 저절로 빠진다. 자동 일정은 꺼 두고 필요할 때 손으로 띄운다(사람 2026-09-26).

## 3. 생성물 다시 만들기 (이 차례를 지킨다)

`detail_from_ocr.py` → `crawl_cafe24.py --build-csv` → `size_from_ocr.py` → `tag_items.py`

- `size_from_ocr` 는 `products_full.csv` 를 읽으므로 **build-csv 가 먼저**다(순서를 틀려 한 번 헛돌았다, 2026-09-07).
- 생성물(`products_full.csv` · `product_sizes.json` · `product_tags_full.json`)은 원본의 함수라 CI 가 만든다.
  로컬에서 만들어 밀지 않는다.

## 4. 늘 도는 것

| 워크플로 | 하는 일 | 언제 |
|---|---|---|
| `weekly-update` | 신상 · 품절 · 재입고 · 삭제 | 매주 월 03:00 KST |
| `sizeguide-images` | ③ 사이즈가이드 창 그림 | 매일 |
| `store-health` | 매장이 살아 있는지 **내용으로** 확인 | 매주 월 |
| `crawl-brand` · `refetch-detail` · `ocr-detail-images` · `fill-gaps` · `browser-collect` | 위 단계들 | 손으로 |
| `publish-app-data` | 앱 조각을 버킷에 올린다 | 손으로 — 사람이 정할 때만 |

## 5. 검증 — 앱에 내보내기 전

1. **1차 전처리(Claude)** — 수집 뒤 칸별 채움률을 매장마다 세고, 이상한 값을 먼저 걸러 고친다.
2. **전처리 검증(Codex)** — 매장마다 몇 벌씩 표본을 뽑아(상품 링크만, **사진은 올리지 않는다**)
   layer-web PR #14(중계 전용 · 병합 금지) 댓글에서 코덱스에게 맡긴다. 코덱스는 페이지와 우리 값을
   칸마다 대 보고 **같은 꼴로 되풀이되는 틀림**을 짚는다. 결과는 댓글로 받는다.
   국내 IP 가 필요한 일(국외 차단 매장)만 사람 PC 의 코덱스 중계(`relay/inbox/`)로 보낸다.
3. **가르기(Claude)** — 짚은 것을 「버그」와 「설계」로 가른다. 버그는 고치고 **고치기 전후를 전 매장에
   대 본다**(바뀐 수 · 줄어든 것 표본). 설계와 판단이 필요한 것만 사람에게 묻는다.
4. **앱 공개(사람)** — `NEW_HOLD` 에서 빼는 것은 사람이 수치를 보고 정한다.

## 6. 늘 지키는 것

- 매장 서버에 호스트당 1초 안팎. robots.txt 가 막은 길(예: 재고 API)은 부르지 않는다.
- 무신사는 자동 수집하지 않는다(약관).
- 할인가: 카페24 는 지금 정가만 쓴다(사람 결정 2026-09-28 — 나중에 「할인가 + 정가 + 확인 날짜」로 바꾼다).
- 사진은 저장하지 않는다 — 주소만 둔다.
