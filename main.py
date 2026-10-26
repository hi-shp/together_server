from runtime_config import notify
from runtime_config import BASE_DIR
import fcntl
import signal
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

with open('env.json', 'r', encoding='utf-8') as f:
    config = json.load(f)
KEYWORD_CATEGORIES = config['KEYWORD_CATEGORIES']
EXCLUDE_KEYWORDS = config['EXCLUDE_KEYWORDS']
CATEGORIES = config['CATEGORIES']

def categorize_by_keywords(title, content_text):
    for keyword, category in KEYWORD_CATEGORIES.items():
        if keyword in title:
            return category

    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title + content_text:
            return "해당없음"
    return None

def categorize_announcement(title, content_text):
    category = categorize_by_keywords(title, content_text)
    if category:
        return category
    else:
        # 키워드로 분류되지 않은 공지만 GPT로 분류한다.
        combined_text = f"{title}\n{content_text}"
        truncated_content = truncate_text(combined_text, 1000)
        return answer_gpt(truncated_content)

def update_csv_with_announcement_numbers(updates, filename: str):
    df = pd.read_csv(f'{filename}')

    for page_url, announcement_number in updates.items():
        df.loc[df['page_url'] == page_url, 'number'] = announcement_number

    df['number'] = df['number'].fillna(0).astype(int)
    df.to_csv(filename, index=False)

def run_cycle(writenoticeService):
    gc.collect()
    today_date = datetime.now().strftime("%Y-%m-%d")
    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
    print(current_datetime, flush=True)
    updater = NoticeUpdater()

    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    for filename in ("email.csv", "message.csv", "알림 신청하기.csv"):
        file_path = os.path.join(DOWNLOADS_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
    page_url_manager = PageUrlManager()

    updates = {}
    course_url = "https://dev-plato.pusan.ac.kr/course/view.php?id=157301"

    highest_num = writenoticeService.update_participants()

    response_count = writenoticeService.download_survey()

    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
    message = f"{current_datetime} (참여: {highest_num}명/설문: {response_count}개)"
    notify(message)

    updater.update_university_notices()
    page_url_manager = PageUrlManager()
    n = 1
    for announcement_page in page_url_manager.announcement_pages:
        ann_urls, latest_announcement_number = get_anns_url(announcement_page, n)
        department = announcement_page.department
        n += 1
        for url in ann_urls:

            gc.collect()

            for _ in range(10):
                partial_ann = crawl_ann_partial(url)
                if partial_ann is None:
                    time.sleep(1)
                    continue
                partial_ann.title = re.sub(r'【.*?】\s*', '', partial_ann.title)
                if partial_ann.title != "제목 없음" and partial_ann.content_text != "내용 없음":
                    break
                time.sleep(1)

            if partial_ann is None or partial_ann.title == "제목 없음" or partial_ann.content_text == "내용 없음":
                raise RuntimeError(f'공지 추출 실패; 공지 번호 유지: {url}')

            duplicate_check = is_recent_title_duplicate(partial_ann.title)
            print(f"중복 체크 결과: {duplicate_check} - {partial_ann.title}", flush=True)

            if duplicate_check != "중복":
                category = categorize_announcement(partial_ann.title, partial_ann.content_text)
                print(f"카테고리 분류 결과: {category}", flush=True)
                message = f"{partial_ann.title}\n＞ {category}"
                notify(message)
                for _ in range(10):
                    full_ann = crawl_ann(url, category)
                    if full_ann is None:
                        time.sleep(1)
                        continue
                    full_ann.title = re.sub(r'【.*?】\s*', '', full_ann.title)
                    if full_ann.title != "제목 없음" and full_ann.content_text != "내용 없음":
                        break
                    time.sleep(1)

                if full_ann is None or full_ann.title == "제목 없음" or full_ann.content_text == "내용 없음":
                    raise RuntimeError(f'공지 본문 추출 실패; 공지 번호 유지: {url}')

                if category == "해당없음" or category not in CATEGORIES:
                    full_ann.notice_board_name = "기타 공지사항"
                else:
                    full_ann.notice_board_name = category

                writenoticeService.write_notices(course_url, [full_ann], department)
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

        # 해당 게시판 처리가 끝난 뒤 마지막 공지 번호를 저장한다.
        if latest_announcement_number > announcement_page.number:
            updates[announcement_page.source_url] = latest_announcement_number
            update_csv_with_announcement_numbers(updates, os.getenv('PAGE_NAME'))
            gc.collect()
            updates = {}

    now = datetime.now()
    if 6 <= now.hour < 9:
        print("\n공지글 수정 작업을 시작합니다.", flush=True)
        writenoticeService.remove_stars(course_url, today_date)

    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

    files_to_delete = [
        os.path.join(DOWNLOADS_DIR, "email.csv"),
        os.path.join(DOWNLOADS_DIR, "message.csv"),
        os.path.join(DOWNLOADS_DIR, "알림 신청하기.csv")
    ]

    for file in files_to_delete:
        if os.path.exists(file):
            os.remove(file)

    folder_path = os.path.join("downloads")
    os.makedirs(folder_path, exist_ok=True)
    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)
        if os.path.isfile(file_path):
            os.remove(file_path)


def main():
    os.chdir(BASE_DIR)
    (BASE_DIR / '.runtime').mkdir(mode=0o700, exist_ok=True)
    with open(BASE_DIR / '.runtime' / 'cycle.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A crawler cycle is already running')
        service = WriteNoticeService()
        try:
            run_cycle(service)
        finally:
            service.close()


def _terminate(signum, frame):
    raise SystemExit(128 + signum)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    main()
