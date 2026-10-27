"""기존 이메일 본문과 배너 양식을 구성한다."""

import base64
from pathlib import Path
from bs4 import BeautifulSoup
from html import escape
from urllib.parse import urlsplit


def notice_link(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Invalid notice link")
    return f'<a href="{escape(url, quote=True)}">바로가기</a>'


def message_content(title, url, department):
    notice_link(url)
    return f"✔ [{department}] 새로운 공지가 등록되었습니다.\n제목: {title}\n{url}"


def email_content(title, url, content, department, base_dir):
    template = (Path(__file__).parent / "templates/notice_email.html").read_text(
        encoding="utf-8"
    )
    notice_link(url)
    message_text = template.format(
        department=escape(department), title=escape(title), url=escape(url, quote=True)
    )
    soup = BeautifulSoup(message_text, "html.parser")
    url_tag = soup.find("a", href=True)
    if url_tag:
        content_soup = BeautifulSoup(
            f'<blockquote style="margin-left: 20px; border-left: 4px solid #ccc; padding-left: 10px;">{content}</blockquote>',
            "html.parser",
        )
        url_tag.insert_after(content_soup)
    with open(Path(base_dir) / "together.png", "rb") as img_file:
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
        together_link.string = "투게더 바로가기"
        footer.append(together_image)
        footer.append(together_text)
        footer.append(together_link)
    final_email_content = str(soup)
    return final_email_content
