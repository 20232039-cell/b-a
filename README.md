# b-a — LAYER 수집 일꾼

GitHub Actions 워크플로와 수집 스크립트만 두는 저장소다. **데이터는 여기 없다.**
상품 데이터·어휘 사전·수집 결과는 비공개 저장소 `20232039-cell/layer-brand-agent` 에 있고,
워크플로가 돌 때마다 `work/` 로 받아 와서 그 안에서 작업한 뒤 되돌려 보낸다.

공개 저장소라 Actions 실행 시간이 무료다. 비공개 저장소에서 돌리면 월 2,000분 한도에 걸린다
(2026-09-04 실제로 걸려서 나눴다).

## 준비

이 저장소 Settings → Secrets and variables → Actions 에 **`DATA_TOKEN`** 을 넣는다.
`20232039-cell/layer-brand-agent` 의 **Contents: Read and write** 권한을 가진 파인그레인 토큰이면 된다.

## 워크플로

| 이름 | 하는 일 | 언제 |
|---|---|---|
| `refetch-detail` | 고른 상품의 상세를 다시 열어 사이즈 표·설명을 채운다 | 손으로 |
| `ocr-detail-images` | 상세 이미지에서 글자를 읽는다(tesseract kor+eng) | 손으로 |
| `weekly-update` | 신상·품절·재입고·삭제만 훑는다 | 매주 월 03:00 KST |

셋 다 `.github/actions/data-repo` 로 데이터 저장소를 받고, 잡 안의 모든 명령은 `work/` 에서 돈다.
스크립트는 `ROOT = 파일의 부모의 부모` 로 `data/` 를 찾으므로 `work/scripts` 에 얹으면 `work/data` 를 본다.

## 규칙

- 매장 서버에 **호스트당 1초**. 한 브랜드는 반드시 한 묶음(러너)에서만 돈다 —
  묶음이 갈리면 러너마다 따로 세션을 들어 같은 서버에 초당 5~10회가 나간다(2026-09-04 실측).
- 무신사는 자동 수집하지 않는다(약관).
- 자세한 규칙과 결정 기록은 비공개 저장소의 `AGENTS.md` 에 있다.
