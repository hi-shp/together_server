# Together crawler 운영 수정

## 변경 범위

- Discord 웹훅은 `.env`에서 읽습니다. 기존 `WEBHOOK_URL`은 공통 알림용으로 유지하며 로그·쪽지·이메일 알림은 각각 `DISCORD_LOG_WEBHOOK_URL`, `DISCORD_MESSAGE_WEBHOOK_URL`, `DISCORD_EMAIL_WEBHOOK_URL`을 사용합니다. 미설정이나 전송 실패는 공지 게시를 중단하지 않습니다. POST를 자동 재시도하지 않습니다.
- Chrome마다 전용 프로필·캐시·TMPDIR을 만들고 정상 종료, 오류, 재시작 시 해당 자원만 정리합니다. `/tmp` 공통 폴더의 `chmod`, `sudo rm`, 전체 Chrome `pkill`은 제거했습니다. 임시 경로는 Chromium의 Unix socket 경로 제한 때문에 `/tmp/together-<uid>` 아래의 짧은 경로를 사용합니다.
- `run_main.py`는 같은 Python 실행 환경을 사용하며 중복 실행을 막습니다. 자식 프로세스 그룹만 종료하고, 실행당 2시간 제한을 둡니다. 실패한 실행은 최대 5분까지 재시작 간격을 늘립니다. 직접 `main.py`를 실행해도 중복 공지 실행을 차단합니다.
- 로그는 파일당 약 8 MiB, 합계 128 MiB, 보관 7일로 제한합니다. 전체 출력을 외부 `output.log`에 계속 복제하지 않습니다. systemd 저널 제한 설정도 제공합니다. 프로세스 강제 종료, 전원 장애로 남은 전용 임시 폴더는 일반 `/tmp` 보관 정책 대상입니다.
- HTTP 요청 시간 제한을 추가했습니다. 요청 실패/잘못된 공지 추출 시 번호를 넘기지 않으며, 정적 HTML을 같은 요청으로 15회 반복 조회하던 동작을 제거했습니다. 기존 크롤러의 TLS `verify=False` 설정은 이번 호환성 수정에서 유지했습니다.
- 상세 페이지 리디렉션 후 상대 링크·이미지·첨부파일 주소를 최종 문서 URL 기준으로 해석합니다. 첨부파일의 HTTP 오류도 검사합니다.
- CSV/TXT 파일 구조와 저장 위치, 분류 모델, 공지 게시·쪽지·이메일 처리 순서는 유지합니다. 홈페이지 다운로드 폴더에서는 이 크롤러가 생성하는 3개 CSV만 지웁니다.

## 게시판 점검

`python3 tools/check_links.py --csv pages.csv --insecure --output link-audit.csv`

이 명령은 게시판 GET 요청과 별도 보고서 생성만 수행하며 운영 CSV/TXT와 공지를 변경하지 않습니다. 요청은 1개씩 최소 1초 간격으로 실행하며 연결 오류/403/429가 3회 연속이면 남은 항목을 미점검으로 남깁니다. HTTP 200이라도 게시판 표가 없으면 검토 대상으로 표시합니다. `--insecure`는 기존 크롤러와 동일한 TLS 조건으로 비교할 때만 사용합니다. 생략하면 인증서를 검증합니다. 표가 있다는 검사는 상세 공지 추출·로그인·게시 성공을 보장하지 않습니다.

초기 전체 점검에서 Windows 로컬은 252곳 중 248곳의 게시판 구조를 확인했습니다. 위 주소 매핑 후 문제 4곳을 재점검하여 국어교육과 2곳이 정상임을 확인했습니다. 서버에서 병렬 점검했을 때는 35곳 응답 후 연결 시간 초과 216건·연결 오류 1건이 발생했습니다. 이후 서버에서 공대·국어교육과·대학 본부 3곳은 다시 HTTP 200으로 응답했습니다. 일시적인 경로/접속 제한 가능성이 있어 점검 도구를 순차 요청으로 변경했습니다. 원인은 확정하지 않았으며, 서버 전체 252곳의 지속적인 접근 성공을 보장하지 않습니다.

2026-10-26 공식 학과 메뉴를 확인하여 `url_overrides.json`에 국어교육과 학부 `26185 → 16597`, 일반대학원 `26186 → 16599`를 등록했습니다. 운영 CSV의 원래 URL을 업데이트 키로 유지하므로 누적 공지 번호를 초기화하지 않습니다.

대체 주소를 확정하지 않은 항목:

- 건축학과 취업 `/archi/6181/subview.do`: “메뉴가 존재하지 않습니다”. 현재 공식 메뉴에서 취업 게시판 대체 주소를 확인하지 못했습니다. “진로 및 취업”은 소개 페이지이므로 게시판 대체용으로 사용하지 않습니다.
- 정보의생명공학대학 `/ibe/14669/subview.do`: “사용중지된 싱글 모듈입니다”. 번호를 초기화하거나 다른 학부 공지로 임의 대체하지 않습니다.

## 검증과 적용

서버와 동일한 Ubuntu/Python 3.10 환경에서 실행하세요. 기존 의존성(requests, beautifulsoup4, pandas, python-dotenv, selenium, psutil, tiktoken, openai)을 사용합니다.

```bash
python3 -m unittest discover -s tests -v
python3 tools/smoke_browser.py --driver /home/ubuntu/together/Source/chromedriver
```

테스트는 GPT/Discord 전송이나 로그인/게시를 하지 않습니다. 실제 Chrome smoke test는 data URL만 사용하여 시작·재시작·종료 후 임시 폴더 정리를 확인합니다.

검토한 커밋을 운영에 적용할 때는 그 커밋의 `ops/apply_review.sh`를 받아 실행하세요. 인자는 정확한 40자리 커밋 SHA입니다. 스크립트는 별도 worktree에서 테스트하고, 운영 CSV/TXT와 `.env`를 권한이 제한된 백업 경로에 복사한 뒤 별도 `review-<sha>` 브랜치를 체크아웃합니다. `main`에 커밋하거나 PR을 병합하지 않습니다. 기존 크롤러나 로컬 코드 변경이 있으면 중단합니다. 데이터 체크섬 일치 확인 후 웹훅 값을 숨겨 입력받고 systemd 서비스를 시작합니다. 서비스 시작부터 기존 공지/쪽지/이메일 동작이 재개됩니다.

공개된 기존 Discord 웹훅은 Discord에서 삭제하고 새로 발급해야 합니다. `.env`는 `chmod 600 .env`로 보관하며 저장소에 추가하지 마세요. 이번 변경은 과거 Git 이력을 재작성하지 않습니다. 기존 시크릿을 새 값으로 사용하지 마세요.

상태 확인:

```bash
systemctl status together-crawler.service --no-pager
journalctl -u together-crawler.service -n 30 --no-pager
df -h /
sudo journalctl --disk-usage
find /tmp -maxdepth 1 -type d -name '.org.chromium.Chromium.*' | wc -l
```

롤백: `sudo systemctl stop together-crawler.service` 후 백업 `previous-commit.txt`에 기록된 이전 커밋으로 별도 브랜치를 만들어 돌아가세요. CSV/TXT는 현재 파일을 유지하며, 문제 발생 시 백업 파일과 비교하세요. 이전 코드는 웹훅 하드코딩과 임시 폴더 정리 문제가 있으므로 조사 전 재시작하지 마세요.
