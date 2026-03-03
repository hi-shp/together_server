from gpt_client import answer_gpt
from crawl_announcement import get_anns_url, crawl_ann_partial, crawl_ann
from selenium_service import WriteNoticeService
from duplicate_checker import is_recent_title_duplicate, save_title, truncate_text, save_only_title
from page_url_manager import PageUrlManager
from duplicate_checker import recent_title
import json
import pandas as pd
import re
import gc
import os
import time
import requests
from datetime import datetime
from ready import NoticeUpdater
import glob
import shutil

# JSON 파일 불러오기
with open('env.json', 'r', encoding='utf-8') as f:
    config = json.load(f)
KEYWORD_CATEGORIES = config['KEYWORD_CATEGORIES']
EXCLUDE_KEYWORDS = config['EXCLUDE_KEYWORDS']
CATEGORIES = config['CATEGORIES']

def categorize_by_keywords(title, content_text):
    # 제목과 내용에서 키워드를 검색하여 게시판을 직접 분류. 매핑된 카테고리가 있는 경우 해당 카테고리로 분류, 아니면 None 반환
    for keyword, category in KEYWORD_CATEGORIES.items():
        if keyword in title:  # + content_text
            return category

    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title + content_text:
            return "해당없음"
    return None

def categorize_announcement(title, content_text):
    # 먼저 키워드를 기반으로 카테고리 분류 시도
    category = categorize_by_keywords(title, content_text)
    if category:
        return category
    else:
        # 사전 정의된 키워드로 분류되지 않으면 ChatGPT를 통해 카테고리 분류
        combined_text = f"{title}\n{content_text}"
        truncated_content = truncate_text(combined_text, 1000)  # 토큰 수 제한(1000토큰 = 약 500~700글자)
        return answer_gpt(truncated_content)

def update_csv_with_announcement_numbers(updates, filename: str):
    df = pd.read_csv(f'{filename}')

    # 각 URL에 대한 공지 번호 업데이트
    for page_url, announcement_number in updates.items():
        df.loc[df['page_url'] == page_url, 'number'] = announcement_number

    # CSV 파일 다시 쓰기
    df['number'] = df['number'].fillna(0).astype(int)
    df.to_csv(filename, index=False)

def main():
    gc.collect()
    #print("모듈 로드 완료", flush=True)
    today_date = datetime.now().strftime("%Y-%m-%d") # 오늘 날짜 가져오기
    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S") # 현재 날짜와 시간(년-월-일 / 시:분:초)
    print(current_datetime, flush=True)
    updater = NoticeUpdater()

    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
    for filename in os.listdir(DOWNLOADS_DIR):
        file_path = os.path.join(DOWNLOADS_DIR, filename)
        if os.path.isfile(file_path):  # 파일만 삭제
            os.remove(file_path)
    announcements = []
    page_url_manager = PageUrlManager()
    #print("데이터 초기화 완료", flush=True)

    updates = {}  # 업데이트할 URL과 공지 번호 저장
    writenoticeService = WriteNoticeService()
    course_url = "https://plato.pusan.ac.kr/course/view.php?id=157301&lang=ko"  # 실제 course_url 사용
    #print("Selenium 클래스 로드 완료", flush=True)

    highest_num = writenoticeService.update_participants()
    #print(f"참여자 수 업데이트 완료 ({highest_num}명)", flush=True)

    response_count = writenoticeService.download_survey()
    #print(f"설문 다운로드 완료 ({response_count}명)", flush=True)

    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
    message = f"{current_datetime} (참여: {highest_num}명/설문: {response_count}개)"
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')
    requests.post(WEBHOOK_URL, json={"content": message})

    updater.update_university_notices()
    page_url_manager = PageUrlManager()
    #print("", flush=True)
    n = 1
    for announcement_page in page_url_manager.announcement_pages:
        ann_urls, latest_announcement_number = get_anns_url(announcement_page, n)  # 각 페이지에서 공지사항 URL 가져오기
        department = announcement_page.department
        n += 1
        for url in ann_urls:

            # 메모리 누수를 방지하기 위해 주기적으로 가비지 컬렉션 실행
            gc.collect()

            # 제목+내용만 부분적으로 크롤링
            for _ in range(10):
                partial_ann = crawl_ann_partial(url)
                partial_ann.title = re.sub(r'【.*?】\s*', '', partial_ann.title)
                if partial_ann.title != "제목 없음" and partial_ann.content_text != "내용 없음":
                    break  # 유효한 데이터가 생겼으므로 종료
                time.sleep(1)

            # 제목 중복 체크
            duplicate_check = is_recent_title_duplicate(partial_ann.title)
            print(f"중복 체크 결과: {duplicate_check} - {partial_ann.title}", flush=True)

            if duplicate_check != "중복":
                # 키워드 기반 카테고리 분류 시도
                category = categorize_announcement(partial_ann.title, partial_ann.content_text)
                print(f"카테고리 분류 결과: {category}", flush=True)
                message = f"{partial_ann.title}\n＞ {category}"
                requests.post(WEBHOOK_URL, json={"content": message})
                # 전체 공지사항 크롤링
                for _ in range(10):
                    full_ann = crawl_ann(url, category)
                    full_ann.title = re.sub(r'【.*?】\s*', '', full_ann.title)
                    if full_ann.title != "제목 없음" and full_ann.content_text != "내용 없음":
                        break
                    time.sleep(1)

                if category == "해당없음" or category not in CATEGORIES:
                    full_ann.notice_board_name = "기타 공지사항"
                else:
                    full_ann.notice_board_name = category

                announcements.append(full_ann)
                writenoticeService.write_notices(course_url, [full_ann], department)  # 공지사항 작성
                print(f"게시글 작성 완료", flush=True)
                save_title(partial_ann.title, category)

            recent_titles = recent_title()
            if "CMS/Board/PopupBoard" in url:
                if "도서관 운영 일정 및 열람실별 이용 안내" in partial_ann.title:
                    print("쪽지/이메일 패스", flush=True)
                    continue

                if partial_ann.title in recent_titles:
                    print("중복 패스", flush=True)
                    continue

            if partial_ann.title not in recent_titles:
                save_only_title(partial_ann.title)

            writenoticeService.move_to_course(course_url)
            writenoticeService.send_message(partial_ann.title, partial_ann.url, department)
            writenoticeService.send_email(partial_ann.title, partial_ann.url, partial_ann.content_html, department)
            print("", flush=True)

        # URL의 최신 공지 번호를 업데이트 딕셔너리에 저장
        if latest_announcement_number > announcement_page.number:
            updates[announcement_page.page_url] = latest_announcement_number
            update_csv_with_announcement_numbers(updates, os.getenv('PAGE_NAME'))
            gc.collect()
            updates = {}

    now = datetime.now()
    if 6 <= now.hour < 7:
        print("\n공지글 수정 작업을 시작합니다.", flush=True)
        writenoticeService.remove_stars(course_url, today_date)
    os.system("pkill -f chrome")

    # 다운로드 폴더 경로 수정
    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

    files_to_delete = [
        os.path.join(DOWNLOADS_DIR, "email.csv"),
        os.path.join(DOWNLOADS_DIR, "message.csv"),
        os.path.join(DOWNLOADS_DIR, "알림 신청하기.csv")
    ]

    for file in files_to_delete:
        if os.path.exists(file):
            os.remove(file)

    # 다운로드 폴더 내 모든 파일 삭제 (폴더 유지)
    folder_path = os.path.join("downloads")
    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)
        if os.path.isfile(file_path):
            os.remove(file_path)

    # chrome,selenium 캐시 파일 삭제
    for path in glob.glob("/tmp/.org.chromium.Chromium.*") + glob.glob("/tmp/.com.google.Chrome.*") + glob.glob("/dev/shm/.com.google.Chrome.*"):
        try: shutil.rmtree(path)
        except (NotADirectoryError, PermissionError): os.chmod(path, 0o777); os.remove(path)
        except Exception: os.system(f"sudo rm -rf {path}")

if __name__ == "__main__":
    main()
