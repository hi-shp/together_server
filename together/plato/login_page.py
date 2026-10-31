"""학교의 이전/현재 로그인 화면을 공통으로 식별한다."""
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

USERNAME_SELECTOR = '#input-username, input[name="username"]'
PASSWORD_SELECTOR = '#input-password, input[name="password"]'


def is_login_page(url, html):
    path = urlsplit(url).path.lower()
    if path == '/login.php' or path.startswith('/login/'):
        return True
    soup = BeautifulSoup(html, 'html.parser')
    return any(
        form.select_one(USERNAME_SELECTOR) is not None
        and form.select_one(PASSWORD_SELECTOR) is not None
        for form in soup.select('form')
    ) or soup.select_one('#input-username') is not None
