"""PLATO 발송 작업에 사용할 독립된 로그인 세션을 만든다."""

import os
from urllib.parse import urlsplit
import requests
from .login_page import USERNAME_SELECTOR, PASSWORD_SELECTOR
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from .notifications import (
    HOST,
    BASE,
    COURSE,
    NotificationError,
    NotificationAuthenticationError,
)
from .browser_runtime import (
    new_workspace,
    cleanup_workspace,
    driver_children,
    stop_children,
)


def login_session():
    session = requests.Session()
    driver = None
    service = None
    workspace = new_workspace()
    try:
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"
        for argument in (
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--blink-settings=imagesEnabled=false",
            "--window-size=1280,720",
            "--lang=ko",
            "--user-data-dir=" + str(workspace / "profile"),
        ):
            options.add_argument(argument)
        if os.getenv("USER_AGENT"):
            options.add_argument("--user-agent=" + os.environ["USER_AGENT"])
        environment = os.environ.copy()
        environment.update({key: str(workspace) for key in ("TMPDIR", "TMP", "TEMP")})
        service = Service(os.environ["CHROME_DRIVER_PATH"], env=environment)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(45)
        driver.get("https://" + HOST + "/")
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, USERNAME_SELECTOR))
        ).send_keys(os.environ["PLATO_ID"])
        driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR).send_keys(os.environ["PLATO_PW"])
        driver.find_element(By.NAME, "loginbutton").click()
        WebDriverWait(driver, 40).until(
            lambda browser: urlsplit(browser.current_url).netloc == HOST
            and not browser.find_elements(By.CSS_SELECTOR, USERNAME_SELECTOR)
        )
        for cookie in driver.get_cookies():
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=HOST,
                path=cookie.get("path", "/"),
            )
        session.headers["User-Agent"] = driver.execute_script(
            "return navigator.userAgent"
        )
        with session.get(
            BASE + "email_log.php?id=" + COURSE, timeout=(5, 30)
        ) as response:
            if (
                response.status_code != 200
                or urlsplit(response.url).netloc != HOST
                or urlsplit(response.url).path != "/local/ubmessage/email_log.php"
            ):
                raise NotificationError("Notification login failed")
        return session
    except BaseException:
        session.close()
        raise
    finally:
        children = driver_children(service)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if service:
            try:
                service.stop()
            except Exception:
                pass
        stop_children(children)
        cleanup_workspace(workspace)


def http_login_session():
    """실제 로그인 양식으로 접속하고 계정 세션을 반환한다."""
    from bs4 import BeautifulSoup, SoupStrainer
    from urllib.parse import urljoin

    session = requests.Session()
    session.headers["User-Agent"] = os.getenv("USER_AGENT") or "Mozilla/5.0"
    root = "https://" + HOST + "/"
    try:
        with session.get(root, timeout=(5, 30)) as response:
            response.raise_for_status()
            soup = BeautifulSoup(
                response.text, "html.parser", parse_only=SoupStrainer("form")
            )
            forms = [
                form
                for form in soup.find_all("form")
                if form.select_one('input[name="username"]')
                and form.select_one('input[name="password"]')
            ]
            if len(forms) != 1:
                raise NotificationAuthenticationError("Login form unavailable")
            form = forms[0]
            action = urljoin(response.url, form.get("action", ""))
            if (
                action != root + "login/index.php"
                or form.get("method", "").lower() != "post"
            ):
                raise NotificationAuthenticationError("Login form scope mismatch")
            data = {
                node["name"]: node.get("value", "")
                for node in form.select('input[type="hidden"][name]')
            }
            submit = form.select_one('input[name="loginbutton"]')
            if submit is not None:
                data["loginbutton"] = submit.get("value", "")
            data.update(
                username=os.environ["PLATO_ID"], password=os.environ["PLATO_PW"]
            )
        with session.post(
            action,
            data=data,
            headers={"Referer": root, "Origin": root.rstrip("/")},
            timeout=(5, 30),
        ) as response:
            response.raise_for_status()
            if (
                urlsplit(response.url).netloc != HOST
                or "logout.php" not in response.text
            ):
                raise NotificationAuthenticationError("Notification login failed")
        return session
    except BaseException:
        session.close()
        raise
