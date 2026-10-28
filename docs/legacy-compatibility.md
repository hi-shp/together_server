# 레거시 함수 전체 대조

기준 커밋 `50d0d258f1d867a39b5ef58898652746ca1f86ee`의 함수·메서드59개를 현재 운영 경로와 대조했습니다. 함수 설명을 제외한 실행문이 같은 항목과 의도적으로 변경·복원한 항목을 구분했습니다. 이는 모든 외부 사이트·미래 입력에서 무오류라는 보증을 뜻하지 않습니다.

| 기존 함수 | 현재 경로 | 대조 결과 |
|---|---|---|
| `main.py:categorize_by_keywords` | `together/application.py` | 실행문 동일. 경로·주석·설명 정리. |
| `main.py:categorize_announcement` | `together/application.py` | 사용자가 요청한 제목·본문 분류 개선. 규칙·로컬 모델·GPT 근거 기록과 API 예산 관리. 기존 키워드 설정 자체는 보존. |
| `main.py:update_csv_with_announcement_numbers` | `together/application.py` | 실행문 동일. 경로·주석·설명 정리. |
| `main.py:main` | `together/application.py` | 초기 날짜/updater/CSV→브라우저→참여자→설문→대학공지 갱신→CSV 재로드 순서 복원. 게시/분류저장/title/쪽지/메일/번호 기록 4개 원본 시나리오와 대조. 전체 실패 시 대학공지 롤백·번호 우회 제거. |
| `crawl_announcement.py:Announcement.__init__` | `together/crawler/announcements.py` | 기존 필드를 유지하고 원제목·첨부·게시일 메타데이터 추가. 기존 생성자 인수 순서 보존. |
| `crawl_announcement.py:AnnouncementPage.__init__` | `together/crawler/announcements.py` | 실행문 동일. 경로·주석·설명 정리. |
| `crawl_announcement.py:clean_title` | `together/crawler/announcements.py` | 실행문 동일. 경로·주석·설명 정리. |
| `crawl_announcement.py:sanitize_filename` | `together/crawler/files.py` | crawler/files.py로 이동. 빈 이름·디렉터리 충돌·경로문자·특수문자·헤더 파일명 처리 보완. |
| `crawl_announcement.py:filter_bmp_characters` | `together/crawler/announcements.py` | 이모지를 일괄 삭제하던 대신 보이지 않는 제어문자만 정리. 사용자가 요청한 특수문자 표시 보존. |
| `crawl_announcement.py:get_anns_url` | `together/crawler/announcements.py` | 원본 대학공지 번호/고정 제목/idx/yesterday 기록 분기 복원. 상대 링크·누락 링크 검증 보완. 운영중단된 학교 페이지는 오류와 구분하고 번호 유지. |
| `crawl_announcement.py:crawl_ann_partial` | `together/crawler/announcements.py` | 제목·본문 선택 양식 유지. HTTP 응답 확인·특수문자 보존·원문 메타데이터 추가. |
| `crawl_announcement.py:crawl_ann` | `together/crawler/announcements.py` | 기존 게시판별 본문·첨부 추출 분기 유지. 상대 이미지 주소 및 파일명/내용 충돌을 보완하고 파일 오류 이유 기록. |
| `duplicate_checker.py:remove_brackets` | `together/notices/history.py` | 실행문 동일. 경로·주석·설명 정리. |
| `duplicate_checker.py:truncate_text` | `together/notices/history.py` | 실행문 동일. 경로·주석·설명 정리. |
| `duplicate_checker.py:calculate_similarity` | `together/notices/history.py` | 실행문 동일. 경로·주석·설명 정리. |
| `duplicate_checker.py:recent_title` | `together/notices/history.py` | 최근10일/최대1000개/최신순/파일을 읽는 시점 유지. 동일 timestamp 해석만 빠르게 처리. |
| `duplicate_checker.py:is_recent_title_duplicate` | `together/notices/history.py` | 최근10일/1000개/상위3후보 유지. 연도·회차·본문 검토 개선. 빈 제목 유사도1 차단. 표시 유사도는 원제목 기준, 정리 점수는 별도 기록. |
| `duplicate_checker.py:save_title` | `together/notices/history.py` | 실행문 동일. 경로·주석·설명 정리. |
| `duplicate_checker.py:save_only_title` | `together/notices/history.py` | 실행문 동일. 경로·주석·설명 정리. |
| `gpt_client.py:answer_gpt` | `together/integrations/openai_client.py` | 사용자가 승인한 새 모델·구조화 응답·비용 집계·보수적 오류 처리. 분류용 본문1000토큰은 레거시 기준 유지. |
| `gpt_client.py:check_title_similarity` | `together/integrations/openai_client.py` | 제목 임계값만으로 확정하던 분기 대신 연도·회차와 후보 인덱스·본문 근거 대조. API 비용 관리 및 고정된 출력 이유. |
| `page_url_manager.py:extract_domain_part` | `together/crawler/pages.py` | 실행문 동일. 경로·주석·설명 정리. |
| `page_url_manager.py:AnnouncementPage.__init__` | `together/crawler/pages.py` | 실제 조회 URL과 CSV 저장 키 분리. URL 변경 후에도 원래 CSV 행·번호를 갱신. |
| `page_url_manager.py:PageUrlManager.__init__` | `together/crawler/pages.py` | 실행문 동일. 경로·주석·설명 정리. |
| `page_url_manager.py:PageUrlManager.load_data` | `together/crawler/pages.py` | 실행문 동일. 경로·주석·설명 정리. |
| `page_url_manager.py:PageUrlManager.reload_data` | `together/crawler/pages.py` | 실행문 동일. 경로·주석·설명 정리. |
| `page_url_manager.py:PageUrlManager.__init_announcement_pages` | `together/crawler/pages.py` | 실행문 동일. 경로·주석·설명 정리. |
| `ready.py:NoticeUpdater.__init__` | `together/crawler/university.py` | 실행문 동일. 경로·주석·설명 정리. |
| `ready.py:NoticeUpdater.get_last_notice_title` | `together/crawler/university.py` | 실행문 동일. 경로·주석·설명 정리. |
| `ready.py:NoticeUpdater.fetch_notice_number` | `together/crawler/university.py` | 원본 제목 일치/숫자 여부/1·2페이지 순서 유지. 개별 HTTP 연결·응답 시간만 명시. |
| `ready.py:NoticeUpdater.update_csv` | `together/crawler/university.py` | 실행문 동일. 경로·주석·설명 정리. |
| `ready.py:NoticeUpdater.fetch_highest_notice_title` | `together/crawler/university.py` | 원본 최신 일반번호와 title 속성 선택 유지. 개별 HTTP 시간 명시. |
| `ready.py:NoticeUpdater.fetch_full_notice_title` | `together/crawler/university.py` | 상대 상세 주소를 urljoin으로 결합. 상세 제목 선택은 원본 유지. |
| `ready.py:NoticeUpdater.update_university_notices` | `together/crawler/university.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.__init__` | `together/plato/service.py` | 환경설정·로그인 순서 유지. 초기화 실패 때 자신이 만든 브라우저 정리와 완료 로그 추가. |
| `selenium_service.py:WriteNoticeService.login_ready` | `together/plato/service.py` | 로그인 준비와 개별 페이지 로드 시간 관리. 전체 사이클 제한과 별개. |
| `selenium_service.py:WriteNoticeService.safe_get` | `together/plato/service.py` | 접속 실패 시 브라우저 재시작·재로그인. 재실행기를 감시가 종료하지 않도록 별도 복원. |
| `selenium_service.py:WriteNoticeService.init_driver` | `together/plato/service.py` | 같은 headless 브라우저 동작과 임시 작업 폴더 분리. 전역 Chrome 종료·타 사용자 임시폴더 삭제 제거. |
| `selenium_service.py:WriteNoticeService.restart_driver` | `together/plato/service.py` | 자신이 만든 프로세스만 정리한 뒤 브라우저 다시 생성. |
| `selenium_service.py:WriteNoticeService.login` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.move_to_course` | `together/plato/service.py` | 강좌 이동 시 로그인 만료 복구. |
| `selenium_service.py:WriteNoticeService.move_to_notice_board` | `together/plato/service.py` | 원래 선택된 게시판 주소를 로그인 확인과 함께 읽음. 저장 전 쓰기 버튼 준비만 한 번 재확인. |
| `selenium_service.py:WriteNoticeService.send_email_process` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.download_survey` | `together/plato/service.py` | 같은 신청 열·학과별 선택과 CSV 파일명 유지. 문자열 ID·빈 값·파일 권한 보호. |
| `selenium_service.py:WriteNoticeService.send_message` | `together/plato/service.py` | 구독 학과·전체 수신자·기존 양식 보존. 체크박스 대신 검증한 서버 양식 전송·학교 응답 인원·중복발송 기록 대조. |
| `selenium_service.py:WriteNoticeService.send_email` | `together/plato/service.py` | 같은 신청자·제목·본문 양식, 사용자가 지정한 하단 바로가기만 변경. 독립 작업자는 이제 요청 순서를 지키며 앞선 미확인 건을 추월하지 않음. |
| `selenium_service.py:WriteNoticeService.write_notices` | `together/plato/service.py` | 같은 게시판·게시 순서 유지. 기존 게시 주소 재사용과 게시 결과 확인으로 재시작 중복 방지. |
| `selenium_service.py:WriteNoticeService.write_notice_in_board` | `together/plato/service.py` | 기존 제목 접두·원문 링크·본문·첨부 유지. 제목/본문 입력·파일 업로드·저장 후 재조회 검증 추가. 저장 재전송 금지. |
| `selenium_service.py:WriteNoticeService.upload_files` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.upload_file` | `together/plato/service.py` | 동일 업로드 기능을 파일 관리자 공통 함수로 이동. 실제 파일명과 완료 상태를 확인. |
| `selenium_service.py:WriteNoticeService.click_with_js` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.remove_stars` | `together/plato/service.py` | 사용자 최신 지시에 따라 첫 무별표 페이지에서 중단하던 조건 제거. 공식 제목 검색 전체 페이지 수집 후 오늘 이전 글의 별표만 제거하고 본문·첨부 목록 대조. |
| `selenium_service.py:WriteNoticeService.update_page_url` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.update_title` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `selenium_service.py:WriteNoticeService.update_participants` | `together/plato/service.py` | 실행문 동일. 경로·주석·설명 정리. |
| `run_main.py:kill_chrome_except_python` | `together/supervisor.py` | 전역 모든 Chrome 종료 대신 이 실행의 자식 프로세스만 정리. 다른 실행·메일 작업자의 브라우저 보호. |
| `run_main.py:cleanup_old_logs` | `together/supervisor.py` | 레거시 7일 보관 유지. 새로 추가했던 파일당8MiB 잘림·총128MiB 조기 삭제 제거. |
| `run_main.py:send_log_to_discord` | `together/supervisor.py` | config.notify(log_path)로 이동. 기존 채널에 원문 파일 업로드. 실제11파일을 Discord에서 다운로드해 서버 해시 일치 확인, 미확인 구간10파일 복구. |
| `run_main.py:run_main` | `together/supervisor.py` | 레거시 무한 반복/10초 재실행/전체 로그 전송과 0~6시 대기 복원. 전체·무출력 사이클 시간 제한 제거. 실패1회로 반복기를 종료하던 검증 감시 제거. |

검증: 자동 검사210개, 원본 main의 신규/중복 대학공지·일반공지·뒤쪽 학과 실패4개 시나리오의 호출·파일 기록 순서 동일, 원본 게시판 분기 후보 대조, 실제학교원문/발송내역/전체로그해시 대조. 실제 받은편지함 UI 확인은 로컬 브라우저 도구 연결 중단으로 아직 미확인입니다.
