#!/usr/bin/env bash
# 데이터 저장소에 커밋 · 푸시. $1 = 메시지, 나머지 = 경로. 다른 판이 먼저 밀어도 받아서 다시 민다.
set +e
msg=$1; shift
for p in "$@"; do
  if [ -e "$p" ]; then git add "$p" || { echo "::error title=커밋::git add 실패: $p"; exit 1; }; fi
done
git diff --cached --quiet && { echo "바뀐 것 없음"; exit 0; }
# set +e 라 커밋이 실패해도 아래 pull/push 가 「밀 것 없음」으로 성공해 버린다 — 여기서 끊는다
git commit -qm "$msg" || { echo "::error title=커밋::git commit 실패"; exit 1; }
for i in 1 2 3 4 5; do
  git pull -q --rebase --autostash origin crawl/cafe24-full-catalog > /tmp/g.log 2>&1 \
    && git push -q origin HEAD:crawl/cafe24-full-catalog >> /tmp/g.log 2>&1 && exit 0
  git rebase --abort 2>/dev/null
  sleep $((i * 15))
done
tail -3 /tmp/g.log | sed 's/^/::error title=푸시::/'
exit 1
