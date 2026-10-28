import time

_MODULE_LOAD_STARTED = time.monotonic()

from together.config import notify, notice_category
from together.config import BASE_DIR
import fcntl
import signal
from together.integrations.openai_client import answer_gpt
from together.crawler.announcements import get_anns_url, crawl_ann_partial, crawl_ann
from together.plato.service import WriteNoticeService
from together.notices.history import (
    is_recent_title_duplicate,
    save_title,
    truncate_text,
    save_only_title,
)
from together.crawler.pages import PageUrlManager
from together.notices.history import recent_title
import json
import pandas as pd
import re
import gc
import os
import requests
import csv
from functools import lru_cache
from datetime import datetime
from together.crawler.university import NoticeUpdater
from together.notices.intelligence.publication import known_receipt, remember_receipt
from together.notices.intelligence.routing import category_hint, configured_classifier
from together.notices.intelligence.shadow import (
    observe_notice,
    observe_category,
    finish_cycle,
)
from together.notices.intelligence.enrichment import (
    find_target,
    register_post,
    apply_enrichment,
)
from together.notices.intelligence.quality import (
    assess,
    quarantine,
    review_for,
    display_title,
)
from together.storage.attachments import archive_files
from together.operations.state import update_state, board_summary
from together.operations.events import event, start_cycle, set_notice, clear_notice

from together.notices.intelligence.policy import (
    KEYWORD_CATEGORIES,
    EXCLUDE_KEYWORDS,
    CATEGORIES,
)


def categorize_by_keywords(title, content_text):
    for keyword, category in KEYWORD_CATEGORIES.items():
        if keyword in title:
            return category

    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title + content_text:
            return "해당없음"
    return None


@lru_cache(maxsize=1)
def _saved_categories(path, modified):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}


def saved_category(title):
    path = BASE_DIR / "sorted_labeled_data.csv"
    try:
        return _saved_categories(str(path), path.stat().st_mtime_ns).get(title)
    except OSError:
        return None


def categorize_announcement(title, content_text):
    started = time.monotonic()
    hint = {"category": None, "method": "needs_context"}
    try:
        if os.getenv("NOTICE_CLASSIFIER_MODE", "hybrid") == "legacy":
            hint = {
                "category": categorize_by_keywords(title, content_text),
                "method": "legacy_keyword",
            }
        else:
            hint = category_hint(
                title,
                KEYWORD_CATEGORIES,
                configured_classifier(BASE_DIR),
                body=content_text,
            )
    except Exception as exc:
        event(
            "분류",
            f"로컬 판단 생략: {type(exc).__name__}",
            level="WARN",
            error=type(exc).__name__,
        )
    category = hint["category"]
    method = hint["method"]
    if not category:
        combined_text = f"{title}\n{content_text}"
        truncated_content = truncate_text(combined_text, 1000)
        category = answer_gpt(truncated_content)
        method = "gpt_context_review"
    observe_category(title, category, method)
    prediction = hint.get("prediction") or {}
    explanation = (
        "GPT 본문 검토"
        if method == "gpt_context_review"
        else hint.get("reason") or "제목 규칙·로컬 모델 대조"
    )
    event(
        "분류",
        f"카테고리 분류 결과: {category} ({explanation})",
        plain=True,
        category=category,
        method=method,
        score=prediction.get("score"),
        model=(
            os.getenv("GPT_CATEGORY_MODEL", "gpt-5.4-mini-2026-03-17")
            if method == "gpt_context_review"
            else prediction.get("model_sha256", "local-rules")
        ),
        reason=(
            review_for(truncated_content).get("reason")
            if method == "gpt_context_review"
            else hint.get("reason")
        ),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    return category


def update_csv_with_announcement_numbers(updates, filename: str):
    df = pd.read_csv(f"{filename}")

    for page_url, announcement_number in updates.items():
        df.loc[df["page_url"] == page_url, "number"] = announcement_number

    df["number"] = df["number"].fillna(0).astype(int)
    df.to_csv(filename, index=False)


def fetch_complete_notice(url, category):
    for _ in range(10):
        announcement = crawl_ann(url, category)
        if announcement is not None:
            announcement.title = re.sub(r"【.*?】\s*", "", announcement.title)
            if (
                announcement.title != "제목 없음"
                and announcement.content_text != "내용 없음"
            ):
                return announcement
        time.sleep(1)
    raise RuntimeError(f"공지 본문 추출 실패; 공지 번호 유지: {url}")


def prepare_cycle():
    start_cycle()
    event("사이클", "공지 확인 시작")
    gc.collect()
    today_date = datetime.now().strftime("%Y-%m-%d")
    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
    updater = NoticeUpdater()

    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    for filename in ("email.csv", "message.csv", "알림 신청하기.csv"):
        file_path = os.path.join(DOWNLOADS_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
    page_url_manager = PageUrlManager()

    return today_date, updater, page_url_manager


def run_cycle(writenoticeService, prepared=None):
    today_date, updater, page_url_manager = prepared or prepare_cycle()
    updates = {}
    course_url = "https://dev-plato.pusan.ac.kr/course/view.php?id=157301"

    started = time.monotonic()
    highest_num = writenoticeService.update_participants()
    event(
        "시작",
        f"참여 인원 {highest_num}명 확인",
        elapsed_seconds=round(time.monotonic() - started, 3),
    )

    started = time.monotonic()
    response_count = writenoticeService.download_survey()
    event(
        "시작",
        f"알림 신청서 {response_count}건 확인",
        elapsed_seconds=round(time.monotonic() - started, 3),
    )

    current_datetime = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
    message = f"{current_datetime} (참여: {highest_num}명/설문: {response_count}개)"
    notify(message)

    started = time.monotonic()
    updater.update_university_notices()
    event(
        "시작",
        "대학 공지 기준 확인 완료",
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    page_url_manager = PageUrlManager()
    event("시작", f"게시판 {len(page_url_manager.announcement_pages)}개 준비 완료")
    n = 1
    for announcement_page in page_url_manager.announcement_pages:
        clear_notice()
        notice_category.set(None)
        page_match = re.search(r"(?:[?&])page=(\d+)", announcement_page.page_url)
        page_label = (
            f" [목록 {page_match.group(1)}쪽]"
            if page_match and "CMS/Board/PopupBoard.do" in announcement_page.page_url
            else ""
        )
        event(
            "게시판",
            f"{n}/{len(page_url_manager.announcement_pages)} · {announcement_page.department}{page_label}",
            source_url=announcement_page.page_url,
            board_index=n,
            board_count=len(page_url_manager.announcement_pages),
            console=False,
        )
        ann_urls, latest_announcement_number = get_anns_url(announcement_page, n)
        update_state(BASE_DIR, boards=board_summary())
        department = announcement_page.department
        n += 1
        for url in ann_urls:
            print("", flush=True)
            notice_category.set(None)
            category = None
            set_notice(url, department)
            notice_started = time.monotonic()
            gc.collect()

            for _ in range(10):
                partial_ann = crawl_ann_partial(url)
                if partial_ann is None:
                    time.sleep(1)
                    continue
                partial_ann.title = re.sub(r"【.*?】\s*", "", partial_ann.title)
                if (
                    partial_ann.title != "제목 없음"
                    and partial_ann.content_text != "내용 없음"
                ):
                    break
                time.sleep(1)

            if (
                partial_ann is None
                or partial_ann.title == "제목 없음"
                or partial_ann.content_text == "내용 없음"
            ):
                raise RuntimeError(f"공지 추출 실패; 공지 번호 유지: {url}")

            set_notice(url, department, partial_ann.title)
            event(
                "공지",
                partial_ann.title,
                source_url=url,
                title=partial_ann.title,
                plain=True,
            )

            validation = os.getenv("POST_VALIDATION_MODE", "off") == "enabled"
            if validation:
                result = assess(partial_ann)
                partial_ann.title = result["title"]
                if not result["ok"]:
                    quarantine(partial_ann, result, BASE_DIR)
                    event(
                        "검증",
                        "검토 대상 보관 · 게시 보류",
                        level="WARN",
                        outcome="quarantined",
                    )
                    continue

            observe_notice(partial_ann, department)
            enrichment_target = find_target(partial_ann, department, BASE_DIR)
            duplicate_check = is_recent_title_duplicate(
                partial_ann.title,
                identity_title=getattr(
                    partial_ann, "original_title", partial_ann.title
                ),
                announcement=partial_ann,
                department=department,
            )

            if enrichment_target or duplicate_check != "중복":
                if enrichment_target:
                    category = (
                        enrichment_target["board"]
                        if enrichment_target["board"] in CATEGORIES
                        else "해당없음"
                    )
                    event(
                        "분류",
                        f"카테고리 분류 결과: {category} (기존 게시글 분류)",
                        category=category,
                        plain=True,
                    )
                else:
                    category = categorize_announcement(
                        partial_ann.title, partial_ann.content_text
                    )
                if validation:
                    combined = truncate_text(
                        f"{partial_ann.title}\n{partial_ann.content_text}", 1000
                    )
                    result = assess(partial_ann, review_for(combined))
                    if not result["ok"]:
                        quarantine(partial_ann, result, BASE_DIR)
                        continue
                message = f"{partial_ann.title}\n＞ {category}"
                if not enrichment_target and category != "해당없음":
                    notify(message)
                full_ann = fetch_complete_notice(url, category)
                if validation:
                    full_ann.title = display_title(full_ann.title)
                archive_files(full_ann, BASE_DIR)

                if category == "해당없음" or category not in CATEGORIES:
                    full_ann.notice_board_name = "기타 공지사항"
                else:
                    full_ann.notice_board_name = category

                should_publish = not enrichment_target
                if enrichment_target:
                    outcome = apply_enrichment(
                        writenoticeService,
                        enrichment_target,
                        full_ann,
                        department,
                        BASE_DIR,
                    )
                    if outcome is None and duplicate_check != "중복":
                        category = categorize_announcement(
                            partial_ann.title, partial_ann.content_text
                        )
                        full_ann = fetch_complete_notice(url, category)
                        if validation:
                            full_ann.title = display_title(full_ann.title)
                        archive_files(full_ann, BASE_DIR)
                        full_ann.notice_board_name = (
                            category
                            if category in CATEGORIES and category != "해당없음"
                            else "기타 공지사항"
                        )
                        if category != "해당없음":
                            notify(f"{partial_ann.title}\n＞ {category}")
                        should_publish = True
                if should_publish:
                    event(
                        "게시",
                        f"{full_ann.notice_board_name} 게시판에 저장 요청",
                        console=False,
                    )
                    receipts = writenoticeService.write_notices(
                        course_url, [full_ann], department
                    )
                    if isinstance(receipts, list) and receipts:
                        register_post(
                            writenoticeService,
                            full_ann,
                            department,
                            receipts[0],
                            BASE_DIR,
                        )
                    reused = (
                        isinstance(receipts, list)
                        and receipts
                        and isinstance(receipts[0], dict)
                        and receipts[0].get("reused")
                    )
                    event(
                        "게시",
                        "게시글 작성 완료",
                        plain=True,
                        outcome="reused" if reused else "verified",
                        attachment_count=len(full_ann.files),
                    )
                    if not reused or saved_category(partial_ann.title) is None:
                        save_title(partial_ann.title, category)

            recent_titles = recent_title()
            if "CMS/Board/PopupBoard" in url:
                if "도서관 운영 일정 및 열람실별 이용 안내" in partial_ann.title:
                    event("알림", "쪽지/이메일 패스", outcome="excluded", plain=True)
                    continue

                if partial_ann.title in recent_titles:
                    event("알림", "중복 패스", outcome="deduplicated", plain=True)
                    continue

            if partial_ann.title not in recent_titles:
                save_only_title(partial_ann.title)

            notice_category.set(category or saved_category(partial_ann.title))
            writenoticeService.move_to_course(course_url)
            writenoticeService.send_message(
                partial_ann.title, partial_ann.url, department
            )
            writenoticeService.send_email(
                partial_ann.title, partial_ann.url, partial_ann.content_html, department
            )
            receipt = known_receipt(BASE_DIR, partial_ann.url)
            if receipt:
                receipt["notifications_registered"] = True
                remember_receipt(BASE_DIR, receipt)
            event(
                "공지",
                f"처리 완료 ({time.monotonic()-notice_started:.1f}초)",
                elapsed_seconds=round(time.monotonic() - notice_started, 3),
                outcome="completed",
                console=False,
            )

        # 해당 게시판 처리가 끝난 뒤 마지막 공지 번호를 저장한다.
        if latest_announcement_number > announcement_page.number:
            updates[announcement_page.source_url] = latest_announcement_number
            update_csv_with_announcement_numbers(updates, os.getenv("PAGE_NAME"))
            gc.collect()
            updates = {}

    now = datetime.now()
    clear_notice()
    if 6 <= now.hour < 9:
        event("정리", "\n공지글 수정 작업을 시작합니다.", plain=True)
        writenoticeService.remove_stars(course_url, today_date)

    DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

    files_to_delete = [
        os.path.join(DOWNLOADS_DIR, "email.csv"),
        os.path.join(DOWNLOADS_DIR, "message.csv"),
        os.path.join(DOWNLOADS_DIR, "알림 신청하기.csv"),
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
    event("시작", f"모듈 불러오기 완료 ({time.monotonic()-_MODULE_LOAD_STARTED:.1f}초)")
    os.environ["API_USAGE_CONTEXT"] = "crawler"
    os.environ["TOGETHER_STARTUP_PROGRESS"] = "1"
    (BASE_DIR / ".runtime").mkdir(mode=0o700, exist_ok=True)
    with open(BASE_DIR / ".runtime" / "cycle.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A crawler cycle is already running")
        prepared = prepare_cycle()
        service = WriteNoticeService()
        try:
            run_cycle(service, prepared)
        except Exception as exc:
            event(
                "사이클",
                f"처리 중단: {type(exc).__name__} · 현재 게시판 번호 유지",
                level="ERROR",
                error=type(exc).__name__,
            )
            raise
        finally:
            service.close()
            finish_cycle()
            update_state(BASE_DIR, boards=board_summary())
            clear_notice()
            event(
                "사이클", "브라우저 정리 완료", count=board_summary().get("checked", 0)
            )


def _terminate(signum, frame):
    raise SystemExit(128 + signum)


def cli():
    signal.signal(signal.SIGTERM, _terminate)
    main()
