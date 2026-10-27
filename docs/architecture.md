# 코드 구성

실행 명령과 데이터 위치를 유지하면서 역할별로 코드를 모았습니다.

```text
main.py / run_main.py          실행 진입점
together/
  application.py              한 차례 공지 처리
  supervisor.py               반복 실행, 자식 프로세스와 로그 관리
  config.py                   환경변수, 기본 경로와 Discord 연동
  crawler/
    announcements.py          공지 제목·본문·첨부 수집
    pages.py                  게시판 목록과 마지막 확인 번호
    university.py             대학 공지의 기준 제목·번호
  plato/
    service.py                로그인, 게시, 설문과 참여 인원 처리
    browser_runtime.py        브라우저 임시 폴더와 종료 처리
    notifications.py          인증된 발송 요청과 수신자별 전송 이력
    auth.py                   이메일 발송 전용 로그인 세션
    content.py                기존 이메일 본문 구성
    templates/                이메일 HTML 양식
  notices/
    history.py                제목 이력과 중복 검사 연결
    intelligence/             분류·중복·보강·게시 검증
      policy.py               카테고리·키워드 기준
  storage/
    attachments.py            첨부파일 보관
    api_usage.py              토큰·비용 기록과 요청 재사용
    email_outbox.py           이메일 대기열과 완료 기록
  operations/
    state.py                  실행 상태 기록
    email_worker.py           크롤러와 독립된 이메일 발송
  integrations/openai_client.py  GPT 분류·중복 판단 연결
ops/                          서버 적용과 서비스 설정
tools/                        진단·분석 명령
tests/                        동작, 호환성과 발송 양식 검사
docs/                         운영 안내
```

루트의 Python 파일은 `main.py`와 `run_main.py` 두 개입니다. 실제 구현과 분석 도구는 `together/`의 담당 모듈을 직접 사용합니다. `python3 main.py`, `python3 run_main.py`와 systemd 실행 경로를 그대로 사용할 수 있습니다.

`.env`, `pages.csv`, `sorted_labeled_data.csv`, `titles.txt`, `yesterday.txt`와 배너 이미지는 기존 루트 경로를 사용합니다. 실행 이력·첨부 보관·분류 모델은 `.runtime/`에 저장합니다. 설정 모듈의 기준 경로는 저장소 루트입니다.

기존 `env.json`의 카테고리·키워드·제외 기준은 `notices/intelligence/policy.py`에 모았습니다. 분류 실행과 GPT 응답 형식, 분석 도구가 같은 기준을 읽습니다. 기존 값과 순서는 해시로 대조합니다. 키워드 표는 활동 형식과 로컬 모델의 결과를 교차 확인하는 자료로 사용하며, 중복 판별은 행사·연도·회차 등의 별도 기준으로 처리합니다.

공지 처리는 `application.py`가 순서를 조정하고, 수집·분류·게시·전송은 각 담당 모듈에 맡깁니다. 반복 실행과 자원 정리는 `supervisor.py`가 관리합니다. 이메일 HTML은 원본 양식을 별도 파일로 두며, 원본에서 만든 기준 자료와 전체 HTML을 대조합니다. 쪽지 본문과 터미널·Discord의 전송 완료 문구도 동일한 검사에 포함합니다.

이메일은 SQLite 대기열에 저장한 뒤 전용 작업자가 처리합니다. 게시용 브라우저와 로그인 세션을 분리해 이메일 응답을 기다리는 동안에도 다음 공지를 처리할 수 있습니다. 요청과 수신자별 완료 이력을 함께 기록해 재시작 때 확인하며, 완료된 요청의 본문과 수신자 정보는 대기열에서 비웁니다.

시작 단계에서는 모듈 불러오기, 브라우저 시작, PLATO 접속·로그인, 참여 인원, 알림 신청자와 대학 공지 기준 확인이 완료될 때 걸린 시간을 출력합니다. 기존 진행 로그는 터미널과 로그 파일에 바로 기록합니다.
