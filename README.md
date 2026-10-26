# 투게더 서버 (Together Server)

부산대학교에 흩어진 공지사항을 모아 PLATO 자율강좌에 게시하는 서버입니다. 투게더의 공지 수집·분류·알림 기능을 담당합니다.

## 주요 기능

- 대학·학과별 공지 수집 및 첨부파일 다운로드
- 키워드와 GPT를 이용한 분류 및 중복 검사
- PLATO 공지 게시, 새 공지 표시 및 지난 공지 정리
- 신청 학과에 따른 PLATO 쪽지·이메일 알림
- 참여자 수 갱신 및 Discord 운영 알림

## 구성

| 파일 | 역할 |
|---|---|
| `main.py` | 수집·분류·게시 작업 실행 |
| `run_main.py` | 반복 실행, 중복 실행 방지, 로그 관리 |
| `crawl_announcement.py` | 공지 목록·본문·첨부파일 수집 |
| `selenium_service.py` | PLATO 로그인·게시·알림 처리 |
| `duplicate_checker.py`, `gpt_client.py` | 제목 중복 검사 및 분류 |
| `page_url_manager.py`, `ready.py` | 게시판 주소와 마지막 공지 번호 관리 |
| `runtime_config.py`, `browser_runtime.py` | 환경설정, Discord 알림, 브라우저 자원 관리 |

`pages.csv`는 게시판별 마지막 공지 번호, `titles.txt`는 중복 검사 이력, `sorted_labeled_data.csv`는 분류 결과를 저장합니다. `yesterday.txt`는 대학 공지의 마지막 확인 제목을 저장합니다.

## 실행

Ubuntu와 Python 3.10 이상을 사용합니다. Chrome과 ChromeDriver의 버전을 맞춰 설치한 뒤 의존성을 준비합니다.

```bash
python3 -m pip install requests beautifulsoup4 pandas python-dotenv selenium psutil tiktoken openai
test -f .env || cp .env.example .env
chmod 600 .env
```

[.env.example](.env.example)을 참고해 PLATO 계정, GPT API 키, ChromeDriver 경로와 게시판 CSV 경로를 설정합니다. Discord 웹훅도 `.env`에서 읽으며, 비워 두면 해당 알림을 생략합니다. 계정·키·웹훅 값과 신청자 정보는 저장소에 올리지 않습니다.

```bash
python3 run_main.py
```

한 번만 실행할 때는 `python3 main.py`를 사용합니다. 실행 시 설정된 PLATO 강좌에 공지를 게시하고 신청자에게 쪽지·이메일을 보냅니다.

## 점검 및 운영

```bash
python3 -m unittest discover -s tests -v
python3 tools/check_links.py --csv pages.csv --output link-audit.csv
```

테스트는 외부 발송 없이 수행합니다. 링크 점검은 운영 CSV/TXT를 변경하지 않습니다. Chrome만 확인하려면 `tools/smoke_browser.py --driver <ChromeDriver 경로>`를 사용합니다.

로그는 파일당 약 8 MiB, 합계 128 MiB, 7일 보관으로 제한합니다. 브라우저 임시 파일은 실행별로 분리하고 종료 시 정리합니다. 변경된 게시판 주소는 `url_overrides.json`에서 관리하며 CSV의 누적 번호는 유지합니다.

기존 서버에 적용할 때는 `bash ops/apply_review.sh <검토한 커밋 SHA>`를 사용합니다. 별도 경로에서 검사하고 데이터와 `.env`를 백업한 뒤 서버의 `main`을 해당 커밋까지 갱신합니다. 기존 웹훅 값은 서버 안에서 그대로 이전합니다.

파일과 설정만 적용하고 실행은 나중에 하려면 마지막에 `--no-start`를 붙입니다.

## 브랜치

- `main`: 최신 코드. 이후 수정 사항도 이 브랜치에 반영합니다.
- `legacy/before-crawler-update-2026-10-26`: 크롤러 수정 전 코드와 기존 커밋 기록.
