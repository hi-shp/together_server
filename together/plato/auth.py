"""PLATO 발송 작업에 사용할 독립된 로그인 세션을 만든다."""

import os
from urllib.parse import urlsplit
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from .notifications import HOST, BASE, COURSE, NotificationError
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
            EC.presence_of_element_located((By.ID, "input-username"))
        ).send_keys(os.environ["PLATO_ID"])
        driver.find_element(By.ID, "input-password").send_keys(os.environ["PLATO_PW"])
        driver.find_element(By.NAME, "loginbutton").click()
        WebDriverWait(driver, 40).until(
            lambda browser: urlsplit(browser.current_url).netloc == HOST
            and not browser.find_elements(By.ID, "input-username")
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
