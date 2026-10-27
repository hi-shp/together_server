from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
import requests
from typing import List
from together.crawler.announcements import Announcement
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode
import time
import pandas as pd
from selenium.common.exceptions import NoAlertPresentException
from selenium.common.exceptions import TimeoutException
import base64
import re
from html import escape
from threading import Thread
from together.config import notify
from together.plato.browser_runtime import (
    new_workspace,
    cleanup_workspace,
    driver_children,
    stop_children,
)
from together.notices.intelligence.post_editor import (
    validate_article_url,
    PostEditError,
    upload_file_in_manager,
    set_post_fields,
)
from together.notices.intelligence.quality import (
    display_title,
    PostVerificationError,
    sanitize_html,
)
from together.notices.intelligence.publication import (
    known_receipt,
    remember_receipt,
    verify_publication,
)
from together.config import BASE_DIR
from together.operations.events import event
from together.plato.content import message_content
from together.plato.notifications import PlatoNotificationTransport
from together.plato.content import email_content
from together.storage.email_outbox import EmailOutbox


class WriteNoticeService:
    def __init__(self):
        load_dotenv()

        self.window_size = os.getenv("WINDOW_SIZE")
        self.user_agent = os.getenv("USER_AGENT")
        self.chrome_driver_path = os.getenv("CHROME_DRIVER_PATH")
        self.plato_id = os.getenv("PLATO_ID")
        self.plato_pw = os.getenv("PLATO_PW")

        self.driver = None
        self._workspace = None
        try:
            started = time.monotonic()
            self.init_driver()
            self._startup_progress("브라우저 시작 완료", started)
            started = time.monotonic()
            self.safe_get("https://dev-plato.pusan.ac.kr/")
            self._startup_progress("PLATO 접속 완료", started)
            started = time.monotonic()
            self.login(self.plato_id, self.plato_pw)
            self._startup_progress("PLATO 로그인 완료", started)
        except BaseException:
            self.close()
            raise

    def _startup_progress(self, message, started=None):
        if os.getenv("TOGETHER_STARTUP_PROGRESS") == "1":
            suffix = (
                f" ({time.monotonic()-started:.1f}초)" if started is not None else ""
            )
            event("시작", message + suffix)

    def login_ready(self, timeout=30):
        self.driver.set_page_load_timeout(timeout)
        self.safe_get("https://dev-plato.pusan.ac.kr/")

    def safe_get(self, url, login_required=False):
        for attempt in range(3):
            try:
                self.driver.get(url)
                return
            except Exception:
                if attempt == 2:
                    raise
                event("PLATO", "⭐접속 에러 발생. 브라우저 재시작", level="INFO")
                time.sleep(2)
                self.restart_driver()
                if login_required:
                    self.login_ready()
                    self.login(self.plato_id, self.plato_pw)

    def init_driver(self):
        self._workspace = new_workspace()
        options = webdriver.ChromeOptions()
        options.add_argument(f'--user-data-dir={self._workspace / "profile"}')
        options.add_argument(f'--disk-cache-dir={self._workspace / "cache"}')
        options.add_argument("--headless=new")
        options.add_argument("no-sandbox")
        options.add_argument(f"window-size={self.window_size}")
        options.add_argument("disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-angle")
        options.add_argument("--disable-3d-apis")
        options.add_argument("lang=ko_KR")
        options.add_argument("intl.accept_languages=ko")
        options.add_argument(f"user-agent={self.user_agent}")
        options.add_argument("--disable-features=Crashpad")
        options.add_argument("--disable-crashpad")
        options.add_argument("--disable-breakpad")
        options.add_argument("--noerrdialogs")
        options.add_argument("--disable-in-process-stack-traces")
        options.add_argument("--disable-features=AudioServiceOutOfProcess")
        options.add_argument("--disable-audio-output")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")

        driver_env = os.environ.copy()
        driver_env.update(
            {key: str(self._workspace) for key in ("TMPDIR", "TMP", "TEMP")}
        )
        self._service = Service(self.chrome_driver_path, env=driver_env)
        try:
            self.driver = webdriver.Chrome(service=self._service, options=options)
            self.driver.set_page_load_timeout(45)
        except BaseException:
            self.close()
            raise

    def close(self):
        transport = getattr(self, "_notification_client", None)
        if transport is not None:
            transport.close()
            self._notification_client = None
        service = getattr(self, "_service", None)
        children = driver_children(service)
        try:
            if self.driver is not None:
                self.driver.quit()
        except Exception as exc:
            event("PLATO", f"Browser quit: {type(exc).__name__}", level="INFO")
        finally:
            self.driver = None
            service = getattr(self, "_service", None)
            if service is not None:
                try:
                    service.stop()
                except Exception as exc:
                    event("PLATO", f"Driver stop: {type(exc).__name__}", level="INFO")
            stop_children(children)
            cleanup_workspace(self._workspace)
            self._workspace = None

    def restart_driver(self):
        self.close()
        self.init_driver()

    def login(self, id: str, pw: str):
        username_input = self.driver.find_element(By.ID, "input-username")
        username_input.send_keys(id)

        password_input = self.driver.find_element(By.ID, "input-password")
        password_input.send_keys(pw)

        submit = self.driver.find_element(By.NAME, "loginbutton")
        submit.click()

    def move_to_course(self, course_url: str):
        self.safe_get(course_url)

    def move_to_notice_board(self, notice_board_name: str):
        notice_board_link = self.driver.find_element(
            By.XPATH,
            f'//a[span[contains(@class, "instancename") and contains(text(), "{notice_board_name}")]]',
        )
        notice_board_link.click()

    def send_email_process(self):
        try:
            send_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(), '이메일 전송')]")
                )
            )
            self.driver.execute_script("arguments[0].click();", send_button)
            time.sleep(1)
        except Exception as e:
            self.driver.quit()

    def download_survey(self):
        self.safe_get(
            "https://dev-plato.pusan.ac.kr/mod/feedback/view.php?id=2007210&lang=ko",
            login_required=True,
        )

        response_count_element = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "feedback_info_value"))
        )
        response_count = response_count_element.text

        response_view_button = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.LINK_TEXT, "응답 보기"))
        )
        response_view_button.click()

        download_button = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(text(), "다운로드")]')
            )
        )
        download_button.click()
        download_path = os.path.join(
            os.path.expanduser("~"), "Downloads", "알림 신청하기.csv"
        )
        # 설문 CSV 다운로드를 최대 10초 기다린다.
        wait_time = 0
        while not os.path.exists(download_path) and wait_time < 10:
            time.sleep(0.1)
            wait_time += 0.1

        if os.path.exists(download_path):
            df = pd.read_csv(download_path, encoding="utf-8-sig", dtype={"학번": str})

            df_message = df[
                ["이름", "학번", "(선택/복수 가능) 학과", "(선택/복수 가능) 알림 설정"]
            ].copy()
            df_message["Plato 쪽지 여부"] = df_message[
                "(선택/복수 가능) 알림 설정"
            ].apply(lambda x: "Plato 쪽지" in str(x))
            download_path = os.path.join(
                os.path.expanduser("~"), "Downloads", "message.csv"
            )
            df_message.to_csv(download_path, index=False, encoding="utf-8-sig")
            os.chmod(download_path, 0o600)

            df_email = df[
                [
                    "이름",
                    "(선택/복수 가능) 학과",
                    "(선택/복수 가능) 알림 설정",
                    "이메일 주소",
                ]
            ].copy()
            df_email["Email 여부"] = df_email["(선택/복수 가능) 알림 설정"].apply(
                lambda x: "이메일" in str(x)
            )
            download_path = os.path.join(
                os.path.expanduser("~"), "Downloads", "email.csv"
            )
            df_email.to_csv(download_path, index=False, encoding="utf-8-sig")
            os.chmod(download_path, 0o600)

        return response_count

    def _notification(self):
        if getattr(self, "_notification_client", None) is None:
            self._notification_client = PlatoNotificationTransport(
                self.driver, BASE_DIR
            )
        return self._notification_client

    def _recipients(self, kind, department):
        filename = "message.csv" if kind == "message" else "email.csv"
        df = pd.read_csv(
            os.path.join(os.path.expanduser("~"), "Downloads", filename), dtype=str
        ).fillna("")
        selected = "Plato 쪽지" if kind == "message" else "이메일"
        df = df[df["(선택/복수 가능) 알림 설정"].str.contains(selected, regex=False)]
        df = df[
            df["(선택/복수 가능) 학과"].apply(
                lambda value: department in value.split("\n")
            )
        ]
        if kind == "message":
            return [
                {"name": row["이름"].strip(), "student_id": row["학번"].strip()}
                for _, row in df.iterrows()
            ]
        return [
            {"name": row["이름"].strip(), "email": row["이메일 주소"].strip()}
            for _, row in df.iterrows()
        ]

    def send_message(self, title, url, department):
        if os.getenv("PLATO_NOTIFICATION_TRANSPORT", "http") == "ui":
            return self._send_message_ui(title, url, department)
        recipients = self._recipients("message", department)
        if not recipients:
            return
        body = message_content(title, url, department)
        result = self._notification().send(
            "message", recipients, "", body, f"{department}|{url}|{title}"
        )
        count = result["sent"] + result["deduplicated"]
        event(
            "쪽지",
            f"전송 확인 {count}명 (새 전송 {result['sent']}명, 이력 재사용 {result['deduplicated']}명)",
            count=count,
            sent=result["sent"],
            deduplicated=result["deduplicated"],
            missing=result.get("missing", 0),
        )
        if result["sent"]:
            notify(
                f"{department}/Plato 쪽지 전송 완료 ({count}명) -{title}",
                channel="DISCORD_MESSAGE_WEBHOOK_URL",
            )
        return result

    def send_email(self, title, url, content, department):
        if os.getenv("PLATO_NOTIFICATION_TRANSPORT", "http") == "ui":
            return self._send_email_ui(title, url, content, department)
        recipients = self._recipients("email", department)
        if not recipients:
            return
        subject = f"✔ [{department}] {title}"
        body = email_content(title, url, content, department, BASE_DIR)
        if os.getenv("PLATO_EMAIL_MODE", "direct") == "queue":
            _, ids, _ = self._notification().validated_recipients("email", recipients)
            result = EmailOutbox(BASE_DIR).enqueue(
                {
                    "title": title,
                    "url": url,
                    "department": department,
                    "subject": subject,
                    "body": body,
                    "recipients": recipients,
                    "expected_ids": ids,
                    "accepted_recipients": len(ids),
                    "event_key": f"{department}|{url}|{title}",
                }
            )
            count = result["recipients"]
            event(
                "이메일",
                f"발송 요청 등록 {count}명 · 별도 작업자가 발송 내역 확인",
                count=count,
                status=result["state"],
            )
            if result["new"]:
                notify(
                    f"{department}/이메일 전송 완료 ({count}명) -{title}",
                    channel="DISCORD_EMAIL_WEBHOOK_URL",
                )
            return result
        result = self._notification().send(
            "email", recipients, subject, body, f"{department}|{url}|{title}"
        )
        count = result["sent"] + result["deduplicated"]
        event(
            "이메일",
            f"발송 확인 {count}명",
            count=count,
            sent=result["sent"],
            deduplicated=result["deduplicated"],
        )
        if result["sent"]:
            notify(
                f"{department}/이메일 전송 완료 ({count}명) -{title}",
                channel="DISCORD_EMAIL_WEBHOOK_URL",
            )
        return result

    def _send_message_ui(self, title, url, department):
        self.safe_get(
            "https://dev-plato.pusan.ac.kr/local/ubmessage/send.php?id=157301&lang=ko",
            login_required=True,
        )

        download_path = os.path.join(
            os.path.expanduser("~"), "Downloads", "message.csv"
        )
        df_messages = pd.read_csv(
            download_path, dtype={"학번": str}, encoding="utf-8-sig"
        )

        if "(선택/복수 가능) 학과" in df_messages.columns:
            df_messages.rename(
                columns={"(선택/복수 가능) 학과": "department"}, inplace=True
            )

        # 복수 선택한 학과는 줄바꿈으로 구분한다.
        df_messages["department"] = (
            df_messages["department"]
            .fillna("")
            .astype(str)
            .apply(lambda x: x.split("\n"))
        )

        selected_users = df_messages[
            df_messages["(선택/복수 가능) 알림 설정"]
            .astype(str)
            .str.contains("Plato 쪽지", na=False)
        ]

        filtered_users = selected_users[
            selected_users["department"].apply(
                lambda dept_list: department in dept_list
            )
        ]

        student_ids = filtered_users["학번"].tolist()
        student_names = filtered_users["이름"].tolist()

        if student_ids:
            # 이름과 학번이 모두 일치하는 신청자를 선택한다.
            for student_id, student_name in zip(student_ids, student_names):
                try:
                    user_label_xpath = (
                        f"//label[contains(text(), '{student_name} ({student_id})')]"
                    )

                    user_label = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, user_label_xpath))
                    )

                    checkbox_id = user_label.get_attribute("for")

                    checkbox_xpath = f"//input[@id='{checkbox_id}']"
                    checkbox = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, checkbox_xpath))
                    )

                    checkbox.click()

                except Exception as e:
                    pass

            message_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//textarea[@name='message']")
                )
            )

            message_text = message_content(title, url, department)

            message_input.send_keys(message_text)

            send_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(), '쪽지 전송')]")
                )
            )
            send_button.click()
            try:
                WebDriverWait(self.driver, 5).until(EC.alert_is_present())
                alert = self.driver.switch_to.alert
                alert.accept()
            except NoAlertPresentException:
                event("PLATO", "Alert 없음", level="DEBUG")

            message = f"{department}/Plato 쪽지 전송 완료 ({len(student_ids)}명)"
            discord_message = (
                f"{department}/Plato 쪽지 전송 완료 ({len(student_ids)}명) -{title}"
            )
            event("PLATO", message, level="INFO")
            notify(discord_message, channel="DISCORD_MESSAGE_WEBHOOK_URL")

        self.safe_get(
            "https://dev-plato.pusan.ac.kr/course/view.php?id=157301&lang=ko",
            login_required=True,
        )

    def _send_email_ui(self, title, url, content, department):
        self.safe_get(
            "https://dev-plato.pusan.ac.kr/local/ubmessage/email_send.php?id=157301&lang=ko",
            login_required=True,
        )

        download_path = os.path.join(os.path.expanduser("~"), "Downloads", "email.csv")
        df_email = pd.read_csv(download_path, encoding="utf-8-sig")

        if "(선택/복수 가능) 학과" in df_email.columns:
            df_email.rename(
                columns={"(선택/복수 가능) 학과": "department"}, inplace=True
            )

        # 복수 선택한 학과는 줄바꿈으로 구분한다.
        df_email["department"] = (
            df_email["department"].fillna("").astype(str).apply(lambda x: x.split("\n"))
        )

        selected_users = df_email[
            df_email["(선택/복수 가능) 알림 설정"]
            .astype(str)
            .str.contains("이메일", na=False)
        ]

        filtered_users = selected_users[
            selected_users["department"].apply(
                lambda dept_list: department in dept_list
            )
        ]

        student_names = filtered_users["이름"].tolist()
        email_addresses = filtered_users["이메일 주소"].tolist()

        if email_addresses:
            # 이름과 이메일이 모두 일치하는 신청자를 선택한다.
            for student_name, email in zip(student_names, email_addresses):
                try:
                    user_label_xpath = (
                        f"//label[contains(@title, '{student_name} ({email})')]"
                    )

                    user_label = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, user_label_xpath))
                    )

                    checkbox_id = user_label.get_attribute("for")

                    checkbox_xpath = f"//input[@id='{checkbox_id}']"
                    checkbox = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, checkbox_xpath))
                    )

                    checkbox.click()

                except Exception as e:
                    pass

            subject_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "subject"))
            )

            subject_text = f"✔ [{department}] {title}"
            subject_input.send_keys(subject_text)

            message_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "id_messageeditable"))
            )

            message_text = f"""
            <!DOCTYPE html>
            <html lang="ko">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>2026학년도 1학기 [투게더] 부산대학교 모든 공지사항</title>
            </head>
            <body>
                <div class="container">
                    <div class="content">
                        <p>[{department}] 새로운 공지가 등록되었습니다.</p>
                        <br>
                        <p><strong>제목: {title}</strong></p>
                        <p><a href="{url}">{url}</a></p>
                        <br>
                        <br>
                        <br>
                    </div>
                </div>
            </body>
            </html>
            """

            soup = BeautifulSoup(message_text, "html.parser")

            url_tag = soup.find("a", href=True)

            url_tag = soup.find("a", href=True)

            if url_tag:
                content_soup = BeautifulSoup(
                    f'<blockquote style="margin-left: 20px; border-left: 4px solid #ccc; padding-left: 10px;">{content}</blockquote>',
                    "html.parser",
                )

                url_tag.insert_after(content_soup)

            with open("/home/ubuntu/together/Source/together.png", "rb") as img_file:
                encoded_string = base64.b64encode(img_file.read()).decode("utf-8")

            image_base64 = f"data:image/png;base64,{encoded_string}"

            footer = soup.find("div", class_="container")
            if footer:
                together_image = soup.new_tag(
                    "img",
                    src=image_base64,
                    alt="TOGETHER 배너",
                    width="350",
                    height="51",
                    style="max-width: 100%; height: auto; user-select: auto !important;",
                    class_="img-responsive atto_image_button_text-bottom",
                )

                together_text = soup.new_tag("p", style="font-size: 12px; color: #777;")
                together_text.string = (
                    "부산대학교의 모든 공지사항을 한곳에서 편리하게, 자율강좌 투게더"
                )

                together_link = soup.new_tag(
                    "a",
                    href="https://dev-plato.pusan.ac.kr/course/view.php?id=157301",
                    style="font-size: 12px; color: #007bff; display: block;",
                )
                together_link.string = (
                    "https://dev-plato.pusan.ac.kr/course/view.php?id=157301"
                )

                footer.append(together_image)
                footer.append(together_text)
                footer.append(together_link)

            final_email_content = str(soup)

            # 편집기에 HTML을 넣고 변경 이벤트를 전달한다.
            self.driver.execute_script(
                """
                arguments[0].innerHTML = arguments[1];
                arguments[0].focus();
                arguments[0].dispatchEvent(new Event('focus', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                arguments[0].blur();
            """,
                message_input,
                final_email_content,
            )

            # 전송 처리는 별도 스레드에서 실행하고 완료를 기다린다.
            email_thread = Thread(target=self.send_email_process)
            email_thread.start()
            email_thread.join()
            message = f"{department}/이메일 전송 완료 ({len(email_addresses)}명)"
            discord_message = (
                f"{department}/이메일 전송 완료 ({len(email_addresses)}명) -{title}"
            )
            event("PLATO", message, level="INFO")
            notify(discord_message, channel="DISCORD_EMAIL_WEBHOOK_URL")

        self.safe_get(
            "https://dev-plato.pusan.ac.kr/course/view.php?id=157301&lang=ko",
            login_required=True,
        )

    def write_notices(
        self, course_url: str, announcements: List[Announcement], department
    ):
        self.safe_get(course_url, login_required=True)
        receipts = []
        for announcement in announcements:
            self.move_to_notice_board(announcement.notice_board_name)
            receipt = self.write_notice_in_board(
                announcement.title,
                announcement.url,
                announcement.content_html,
                announcement.files,
                announcement.notice_board_name,
                department,
            )
            receipts.append(receipt)

            self.safe_get(course_url, login_required=True)
        return receipts

    def write_notice_in_board(
        self,
        subject: str,
        url: str,
        content: str,
        files: List[str],
        notice_board_name: str,
        department: str,
    ):

        department_cleaned = re.sub(r"\([^)]*\)", "", department).replace(" ", "")

        if notice_board_name in ["기타 공지사항", "장학금"]:
            subject = f"[{department_cleaned}] {subject}"
        else:
            subject = f"⭐{subject}"

        subject = display_title(subject)
        content_with_link = '<p>본문 링크 : <a href="{0}">{0}</a></p>'.format(
            escape(url, quote=True)
        ) + sanitize_html(content, url)
        soup = BeautifulSoup(content_with_link, "html.parser")
        for img in soup.find_all("img"):
            img["class"] = img.get("class", []) + [
                "img-responsive",
                "atto_image_button_text-bottom",
            ]
        content_with_styles = str(soup)
        validation = os.getenv("POST_VALIDATION_MODE", "off") == "enabled"
        if validation:
            previous = known_receipt(BASE_DIR, url)
            if previous:
                if previous.get("state") == "verified":
                    return dict(previous, reused=True)
                return verify_publication(
                    self, previous, content_with_styles, files, BASE_DIR
                )

        # 공지가 많을 때 화면 밖으로 밀린 쓰기 버튼을 찾는다.
        write_button = WebDriverWait(self.driver, 60).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    '//a[contains(@class, "btn btn-primary") and contains(text(), "쓰기")]',
                )
            )
        )

        self.driver.execute_script("arguments[0].scrollIntoView(true);", write_button)
        write_button.click()

        input_subject = self.driver.find_element(By.NAME, "subject")
        set_post_fields(self.driver, subject, content_with_styles)

        """
        # "장학금" 게시판이 아닌 경우에만 공지 글로 설정 (체크박스 클릭)
        if notice_board_name != "장학금":
            notice_checkbox = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "id_notice"))
            )
            if not notice_checkbox.is_selected():
                notice_checkbox.click()
        """

        if files and notice_board_name != "기타 공지사항":
            self.upload_files(files)

            try:
                WebDriverWait(self.driver, 2).until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, 'input[type="file"]')
                    )
                )
            except:
                event(
                    "PLATO",
                    "⚠️ 파일 입력 필드가 사라지지 않았지만 저장 버튼을 클릭합니다.",
                    level="INFO",
                )

        receipt = {
            "source_url": url,
            "subject": subject,
            "board": notice_board_name,
            "state": "submitting",
        }
        if validation:
            remember_receipt(BASE_DIR, receipt)
        self.click_with_js('input[type="submit"].btn-primary')

        WebDriverWait(self.driver, 45).until(EC.staleness_of(input_subject))
        try:
            validate_article_url(self.driver.current_url)
            receipt.update(article_url=self.driver.current_url, state="created")
            if validation:
                remember_receipt(BASE_DIR, receipt)
                return verify_publication(
                    self, receipt, content_with_styles, files, BASE_DIR
                )
            return receipt
        except PostEditError:
            # 게시 주소가 확인되지 않으면 자동 수정 대상으로 등록하지 않는다.
            if validation:
                raise PostVerificationError(
                    "Published article address needs inspection"
                ) from None
            return None

    def upload_files(self, files: List[str]):
        for file_path in files:
            self.upload_file(file_path)

    def upload_file(self, file_path: str):
        upload_file_in_manager(self.driver, file_path)

    def click_with_js(self, css_selector: str):
        element = WebDriverWait(self.driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        self.driver.execute_script("arguments[0].click();", element)

    def remove_stars(self, course_url: str, today_date: str):
        board_names = [
            "교육/특강/프로그램",
            "[공모전] 공학/IT/SW",
            "[공모전] 아이디어/기획",
            "[공모전] 미술/디자인/건축",
            "[공모전] 문학/수기/에세이",
            "[공모전] 기타",
            "봉사활동",
            "서포터즈",
            "취업 정보",
        ]

        for board_name in board_names:
            self.safe_get(course_url, login_required=True)

            board_link_element = self.driver.find_element(
                By.XPATH,
                f'//a[span[contains(@class, "instancename") and contains(text(), "{board_name}")]]',
            )
            board_url = board_link_element.get_attribute("href")
            board_link_element.click()

            event("PLATO", f"\n현재 작업 중인 게시판: {board_name}", level="INFO")

            page = 1
            while True:
                current_url = self.update_page_url(board_url, page)
                self.driver.get(current_url)

                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "tbody tr"))
                    )

                    rows = self.driver.find_elements(By.CSS_SELECTOR, "tbody tr")
                    found_star = False
                    found_older_post = False

                    # 수정한 공지를 다시 읽지 않도록 마지막 확인 위치를 저장한다.
                    last_checked_index = len(rows) - 1

                    while last_checked_index > -1:
                        try:
                            row = rows[last_checked_index]

                            try:
                                title_element = row.find_element(
                                    By.CSS_SELECTOR, "td:nth-child(3) a"
                                )
                                notice_url = title_element.get_attribute("href")
                                date_selector = "td:nth-child(5)"
                            except Exception:
                                title_element = row.find_element(
                                    By.CSS_SELECTOR, "td:nth-child(4) a"
                                )
                                notice_url = title_element.get_attribute("href")
                                date_selector = "td:nth-child(6)"

                            title_text = title_element.text.strip()
                            date_element = row.find_element(
                                By.CSS_SELECTOR, date_selector
                            )
                            date_text = date_element.text.strip()

                            if date_text == today_date:
                                last_checked_index -= 1
                                continue

                            found_older_post = True

                            if title_text.startswith("⭐"):
                                new_title = title_text[1:].strip()
                                found_star = True

                                self.driver.get(notice_url)
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located(
                                        (By.XPATH, "//a[contains(@class, 'modify')]")
                                    )
                                )

                                modify_button = self.driver.find_element(
                                    By.XPATH, "//a[contains(@class, 'modify')]"
                                )
                                modify_link = modify_button.get_attribute("href")
                                modify_link = modify_link.replace("&amp;", "&")

                                self.driver.get(modify_link)
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located((By.NAME, "subject"))
                                )

                                self.update_title(new_title)
                                event("PLATO", f"수정 완료: {new_title}", level="INFO")

                                self.driver.get(current_url)
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located(
                                        (By.CSS_SELECTOR, "tbody tr")
                                    )
                                )

                                # 목록을 다시 읽은 뒤 다음 위치부터 확인한다.
                                rows = self.driver.find_elements(
                                    By.CSS_SELECTOR, "tbody tr"
                                )
                                last_checked_index -= 1
                                continue

                        except Exception as e:
                            event(
                                "PLATO", f"오류 발생: {type(e).__name__}", level="WARN"
                            )

                        last_checked_index -= 1

                    if found_older_post and not found_star:
                        if board_name == "취업 정보":
                            event("PLATO", "모든 게시판 수정 완료", level="INFO")
                        else:
                            event(
                                "PLATO",
                                f"{board_name} 게시판 수정 완료 → 다음 게시판으로 이동",
                                level="INFO",
                            )
                        break

                    page += 1

                except Exception as e:
                    event("PLATO", f"오류 발생: {type(e).__name__}", level="WARN")
                    break

    def update_page_url(self, base_url: str, page: int) -> str:
        parsed_url = urlparse(base_url)
        query_params = parse_qs(parsed_url.query)
        query_params["page"] = [str(page)]
        new_query = urlencode(query_params, doseq=True)
        return f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{new_query}"

    def update_title(self, new_title: str):
        input_subject = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.NAME, "subject"))
        )
        input_subject.clear()
        input_subject.send_keys(new_title)

        self.click_with_js('input[type="submit"].btn-primary')

    def update_participants(self):

        self.driver.get("https://dev-plato.pusan.ac.kr/user/users.php?id=157301")

        html = self.driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        first_cell = soup.find("td", class_="cell c1 number text-center column-number")
        if first_cell:
            highest_number = int(first_cell.text.strip())
        else:
            return

        formatted_number = "{:,}".format(highest_number)

        self.driver.get(
            "https://dev-plato.pusan.ac.kr/course/editsection.php?id=1814053&sr"
        )

        content_div = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "id_summary_editoreditable"))
        )

        original_content = content_div.get_attribute("innerHTML")

        if "명이 함께하고 있습니다." in original_content:
            old_text = (
                original_content.split("현재 ")[1]
                .split("명이 함께하고 있습니다.")[0]
                .strip()
            )
            new_content = original_content.replace(old_text, formatted_number)

            self.driver.execute_script(
                """
                arguments[0].innerHTML = arguments[1];
                arguments[0].focus();
                arguments[0].dispatchEvent(new Event('focus', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                arguments[0].blur();
            """,
                content_div,
                new_content,
            )

            save_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.NAME, "submitbutton"))
            )
            save_button.click()

        return formatted_number
