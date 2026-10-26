#!/usr/bin/env bash
# Usage: bash ops/apply_review.sh <verified-40-character-commit-sha>
# Run as ubuntu. The exact code is tested in a separate worktree before switching.
set -euo pipefail
revision=${1:?검증한 40자리 커밋 SHA를 입력하세요}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo '잘못된 SHA'; exit 1; }
cd /home/ubuntu/together/Source
[[ $(git rev-parse --show-toplevel) == "$PWD" ]] || exit 1
python3 - <<'PY'
import os
from pathlib import Path
import psutil
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        args = proc.info['cmdline'] or []
        if 'python' in (proc.info['name'] or '').lower() and any(Path(a).name in ('run_main.py', 'main.py') for a in args):
            raise SystemExit('실행 중인 크롤러를 먼저 정상 종료하세요. 변경하지 않았습니다.')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        continue
PY
[[ -z $(git diff --cached --name-only) ]] || { echo '스테이징된 변경이 있어 중단합니다.'; exit 1; }
while IFS= read -r path; do
    case "$path" in pages.csv|sorted_labeled_data.csv|titles.txt|yesterday.txt) ;;
        *) echo "로컬 코드 변경을 먼저 확인하세요: $path"; exit 1 ;;
    esac
done < <(git diff --name-only)
git fetch origin fix/crawler-resource-safety
git cat-file -e "$revision^{commit}"
[[ $(git rev-parse FETCH_HEAD) == "$revision" ]] || { echo '브랜치가 바뀌었습니다. 최신 PR을 확인하세요.'; exit 1; }
check_dir=$(mktemp -d /home/ubuntu/together-review.XXXXXXXX)
git worktree add --detach "$check_dir" "$revision"
echo "검증 경로: $check_dir"
(cd "$check_dir" && python3 -m unittest discover -s tests -q &&
    python3 tools/smoke_browser.py --driver /home/ubuntu/together/Source/chromedriver)
umask 077
backup_dir=/home/ubuntu/together-backups/$(date +%Y%m%d_%H%M%S)
mkdir -p "$backup_dir"
git rev-parse HEAD > "$backup_dir/previous-commit.txt"
for file in .env pages.csv sorted_labeled_data.csv titles.txt yesterday.txt; do
    [[ ! -e "$file" ]] || cp -a -- "$file" "$backup_dir/"
done
sha256sum pages.csv sorted_labeled_data.csv titles.txt yesterday.txt > "$backup_dir/state.sha256"
branch="review-${revision:0:12}"
if git show-ref --verify --quiet "refs/heads/$branch"; then
    [[ $(git rev-parse "$branch") == "$revision" ]] || exit 1
    git switch "$branch"
else
    git switch -c "$branch" "$revision"
fi
sha256sum --check "$backup_dir/state.sha256"
python3 ops/configure_webhooks.py --from-ref "$(cat "$backup_dir/previous-commit.txt")"
sudo install -d -m 755 /etc/systemd/journald.conf.d
sudo install -m 644 ops/90-together-limits.conf /etc/systemd/journald.conf.d/90-together-limits.conf
sudo systemctl restart systemd-journald
# Age out leftovers from power loss/SIGKILL. Normal cycles are capped at 2h.
printf 'e /tmp/together-%s 0700 ubuntu ubuntu 1d\n' "$(id -u ubuntu)" > "$backup_dir/together-tmpfiles.conf"
sudo install -m 644 "$backup_dir/together-tmpfiles.conf" /etc/tmpfiles.d/together-crawler.conf
sudo systemctl enable --now systemd-tmpfiles-clean.timer
sudo install -m 644 ops/together-crawler.service /etc/systemd/system/together-crawler.service
sudo systemctl daemon-reload
sudo systemctl enable --now together-crawler.service
systemctl is-active together-crawler.service
df -h /
echo "적용 완료. 데이터/.env 백업: $backup_dir"
echo "검증 worktree는 추후 git worktree remove 명령으로 정리할 수 있습니다: $check_dir"
