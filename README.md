# 투게더 서버

부산대학교의 대학·학과 공지를 모아 PLATO 자율강좌에 올리는 프로그램입니다. 공지를 주제별로 나누고, 신청한 학과의 새 소식을 쪽지와 이메일로 전달합니다.

## 주요 기능

- 대학·학과 공지와 첨부파일 수집
- 제목과 본문을 활용한 분류, 같은 행사와 다른 회차 구분
- 게시 결과 확인과 더 자세한 원문을 활용한 보강
- 신청 학과별 PLATO 쪽지·이메일 발송
- 브라우저 임시 파일, 첨부파일, 로그 정리
- 운영 상태와 API 사용량 기록, 주요 장애 알림

## 실행

Ubuntu와 Python 3.10 이상을 기준으로 구성했습니다. Chrome과 ChromeDriver는 서로 맞는 버전을 사용합니다.

```bash
python3 -m pip install -r requirements.txt
test -f .env || cp .env.example .env
chmod 600 .env
python3 run_main.py
```

[.env.example](.env.example)을 참고해 서버의 `.env`에 PLATO 계정, GPT API 키, ChromeDriver 경로와 Discord 웹훅을 설정합니다. 기존 서버는 현재 값을 그대로 사용하면 됩니다. `requirements.txt`에는 운영 환경에서 확인한 패키지 버전을 정리했습니다.

한 번 실행할 때는 `python3 main.py`를 사용합니다. 실행하면 설정한 강좌에 공지를 게시하고 신청자에게 알림을 보냅니다.

이메일은 전용 작업자가 요청 순서대로 보냅니다. 앞선 발송 내역을 확인한 뒤 다음 요청을 처리하며, 크롤러는 공지 수집과 쪽지 작업을 이어갑니다. 수신자 목록과 발송 기록을 대조합니다. 서버 적용 스크립트가 작업자를 함께 등록하며, 직접 실행하는 환경의 설정은 [운영 안내](docs/operations.md)를 참고하면 됩니다.

## 구성

| 파일 | 역할 |
|---|---|
| `main.py`, `run_main.py` | 기존 실행 명령 |
| `together/application.py`, `together/supervisor.py` | 공지 처리와 반복 실행 |
| `together/crawler/` | 제목·본문·첨부파일과 게시판 관리 |
| `together/plato/` | PLATO 로그인, 게시, 알림과 이메일 양식 |
| `together/storage/` | 첨부 보관과 API 사용량 |
| `together/operations/`, `together/config.py` | 실행 상태와 환경설정 |
| `together/notices/`, `together/integrations/` | 제목 이력, 분류·중복·보강과 GPT 연동 |

`pages.csv`에는 게시판별 마지막 공지 번호, `titles.txt`에는 중복 검사 이력, `sorted_labeled_data.csv`에는 분류 결과를 저장합니다. `yesterday.txt`는 대학 공지의 마지막 확인 제목을 보관합니다. 서버의 기존 파일을 이어서 사용합니다.

## 점검과 적용

```bash
python3 -m unittest discover -s tests -v
python3 tools/check_links.py --csv pages.csv --output link-audit.csv
bash ops/apply_review.sh <검증한 커밋 SHA> --no-start
```

자동 검사는 모의 데이터로 게시·알림 흐름을 확인합니다. 적용 스크립트는 별도 경로에서 검사하고 운영 데이터와 설정을 백업한 뒤 서버 코드를 갱신합니다. `--no-start`는 파일과 설정까지만 적용할 때 사용합니다.

첨부파일 보관 기간은 7일, 기본 보관 상한은 10GB입니다. 브라우저 작업 폴더는 실행별로 만들고 종료할 때 정리합니다. 터미널 전체 로그를 7일간 보관하고, 사이클이 끝나면 Discord에 파일로 보냅니다. 밤 12시부터 오전 6시까지는 다음 실행을 기다립니다. 사이클 전체 시간이나 무출력 시간을 기준으로 종료하는 제한은 없습니다.

터미널은 기존 공지 처리 문구를 유지하며, 중복·분류 결과 뒤에 판단 근거를 표시합니다. 제목 전체를 감싼 대괄호는 비교에서 내용을 남기고, 제목만으로 판단이 어려우면 보관된 본문을 추가로 대조합니다. ‘해당없음’ 공지는 Discord 공지·전송 완료 알림에서 제외합니다. 쪽지와 이메일, PLATO 게시는 기존 설정에 따라 처리합니다. 분류 근거·중복 후보·파일 이름과 해시 등 분석용 기록은 최대 90일·256MiB 범위에서 별도로 보관합니다. `python3 tools/review_logs.py --errors`로 경고와 오류를 모아 볼 수 있습니다. 보관 자료와 조회 방법은 [운영 안내](docs/operations.md#진행-로그와-개선용-자료)에 정리했습니다.

분류·중복·보강의 기준은 [공지 처리 안내](docs/notice-intelligence.md), 게시 검증·전송·장애 감시는 [운영 안내](docs/operations.md), 파일별 역할은 [코드 구성](docs/architecture.md)에 정리했습니다.

## 브랜치

- `main`: 현재 운영 코드
- `legacy/before-crawler-update-2026-10-26`: 수정 전 코드와 기존 커밋 기록
