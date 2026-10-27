import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import urljoin, urlparse
from together.config import HTTP_TIMEOUT
import re
import time
import urllib3
from together.notices.history import recent_title
from together.notices.intelligence.metadata import extract_metadata
from together.notices.intelligence.quality import clean_text
from together.operations.state import record_board_result
from together.operations.events import event
from together.crawler.files import download_attachment, sanitize_filename
from together.config import BASE_DIR
from together.crawler.university_state import pinned_candidate

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

"""
사이트 html 양식에 따라 경우를 나눔
경우 1 : 나머지 모든 공지사항 url
경우 2 : 기계공학부 공지사항
경우 3 : 대학공지
경우 4 : 신규 사이트 템플릿 (2026.03)
"""


class Announcement:
    def __init__(
        self,
        title: str,
        content_html: str,
        content_text: str,
        notice_board_name: str,
        url: str,
        files: list,
        metadata=None,
    ):
        self.title = title
        self.original_title = title
        self.metadata = metadata or {}
        self.url = url
        self.content_html = content_html
        self.content_text = content_text
        self.notice_board_name = notice_board_name
        self.files = files


class AnnouncementPage:
    def __init__(self, page_url: str, default_url: str):
        self.page_url = page_url
        self.default_url = default_url


def clean_title(title):
    return " ".join(title.split())


def filter_bmp_characters(text):
    return clean_text(text)


def get_anns_url(announcementPage, n):
    is_university = "CMS/Board/PopupBoard.do" in announcementPage.page_url
    try:
        # 기존 사이트와의 호환성을 위해 인증서 검증 설정을 유지한다.
        response = requests.get(
            announcementPage.page_url, verify=False, timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        record_board_result(False)
        event(
            "수집",
            f"요청 처리 실패: {type(e).__name__}",
            level="WARN",
            error=type(e).__name__,
        )
        if is_university:
            raise
        return [], announcementPage.number

    soup = BeautifulSoup(response.text, "html.parser")

    # 정적 HTML은 한 번만 받아 파싱한다.
    table_element = soup.find("tbody")
    record_board_result(table_element is not None)

    if table_element:
        rows = table_element.find_all("tr")
    else:
        event("수집", "⚠️ tbody 태그를 찾을 수 없습니다.", level="WARN")
        if is_university:
            raise ValueError("University notice table unavailable")
        rows = []

    announcement_numbers = []
    urls = []

    # 새 공지의 링크가 없으면 저장된 공지 번호를 유지한다.
    for row in rows:
        number = row.select_one("td._artclTdNum, td.number, td.num, td.td-num")
        if (
            number
            and number.get_text(strip=True).isdigit()
            and int(number.get_text(strip=True)) > announcementPage.number
        ):
            link = row.select_one(
                "td._artclTdTitle a[href], td.subject a[href], td.td-title a[href]"
            )
            if link is None and row.select_one("td.number"):
                link = row.select_one("a[href]")
            if link is None or not link.get("href", "").strip():
                event(
                    "수집",
                    f"공지 링크 누락; 번호 유지: {announcementPage.page_url}",
                    level="INFO",
                )
                return [], announcementPage.number

    for row in rows:
        try:
            # 일반 학과 게시판
            number_tag = row.find("td", class_="_artclTdNum")
            number_text = number_tag.get_text(strip=True)
        except AttributeError:
            try:
                # 기계공학부 게시판
                number_tag = row.find("td", class_="number")
                number_text = (
                    number_tag.get_text(strip=True).replace("<br>", "").strip()
                )
            except AttributeError:
                try:
                    # 대학 공지 게시판
                    number_tag = row.find("td", class_="num")
                    number_text = (
                        number_tag.get_text(strip=True).replace("<br>", "").strip()
                    )
                except AttributeError:
                    try:
                        # td-num 형식 게시판
                        number_tag = row.find("td", class_="td-num")
                        number_text = number_tag.get_text(strip=True)
                    except AttributeError:
                        continue

        if number_text.isdigit():
            announcement_numbers.append(int(number_text))

    if announcement_numbers or is_university:
        max_announcement_number = max(
            announcement_numbers, default=announcementPage.number
        )
        difference = max_announcement_number - announcementPage.number
        event(
            "수집",
            f"{n}. {announcementPage.department} {max_announcement_number}",
            level="INFO",
            plain=True,
        )

        if difference > 0:
            event(
                "수집",
                f"💡{difference}개의 New Announcment!: {announcementPage.page_url}",
                plain=True,
            )
        else:
            event(
                "수집",
                f"새로 추가된 공지사항 없음: {announcementPage.page_url}",
                plain=True,
            )

        idx = 0
        num_idx = 0

        # 둘째 줄은 대학 공지의 마지막 확인 제목이다.
        with open("yesterday.txt", "r", encoding="utf-8") as file:
            lines = file.readlines()
        last_yesterday_title = lines[1].strip() if len(lines) >= 2 else None

        for row in rows:
            number_tag = row.find("td", class_="_artclTdNum")
            if number_tag is None or not number_tag.get_text(strip=True).isdigit():
                try:
                    number_tag = row.find("td", class_="number")
                    if (
                        number_tag is None
                        or not number_tag.get_text(strip=True)
                        .replace("<br>", "")
                        .strip()
                        .isdigit()
                    ):
                        try:
                            number_tag = row.find("td", class_="num")
                            if number_tag and number_tag.get_text(strip=True).isdigit():
                                announcement_number = int(
                                    number_tag.get_text(strip=True)
                                )
                                if (
                                    announcement_number > announcementPage.number
                                    or is_university
                                ):
                                    title_tag = row.find("td", class_="subject")
                                    if title_tag:
                                        element = title_tag.find("a")
                                        if element:
                                            url = urljoin(response.url, element["href"])
                                            dates = re.findall(
                                                r"20\d{2}[-.]\d{2}[-.]\d{2}",
                                                row.get_text(" ", strip=True),
                                            )
                                            candidate = (
                                                pinned_candidate(
                                                    BASE_DIR,
                                                    url,
                                                    element.get_text(strip=True),
                                                    (
                                                        dates[-1].replace(".", "-")
                                                        if dates
                                                        else None
                                                    ),
                                                )
                                                if is_university
                                                else url
                                            )
                                            if candidate:
                                                urls.append(candidate)

                            # 일반 글이 상단 고정으로 바뀌어도 제목 이력을 함께 확인한다.
                            elif number_tag and number_tag.find("img"):
                                title_tag = row.find("td", class_="subject")
                                if title_tag:
                                    element = title_tag.find("a", href=True)
                                    if element:
                                        title = element.get_text(strip=True)
                                        if title == last_yesterday_title:
                                            idx += 1
                                        candidate = pinned_candidate(
                                            BASE_DIR,
                                            urljoin(response.url, element["href"]),
                                            title,
                                            (
                                                re.findall(
                                                    r"20\d{2}[-.]\d{2}[-.]\d{2}",
                                                    row.get_text(" ", strip=True),
                                                )
                                                or [None]
                                            )[-1],
                                        )
                                        if candidate:
                                            urls.append(candidate)
                                            event(
                                                "고정 공지",
                                                (
                                                    "미처리 제목 확인"
                                                    if idx == 0
                                                    else "저장된 기준 아래의 미처리 제목 복구"
                                                ),
                                                title=title,
                                            )
                                        if num_idx == 0:
                                            while len(lines) < 2:
                                                lines.append("\n")
                                            lines[1] = title + "\n"
                                            with open(
                                                "yesterday.txt", "w", encoding="utf-8"
                                            ) as file:
                                                file.writelines(lines)
                                            num_idx += 1

                            # td-num 형식 게시판
                            else:
                                number_tag = row.find("td", class_="td-num")
                                if (
                                    number_tag
                                    and number_tag.get_text(strip=True).isdigit()
                                ):
                                    announcement_number = int(
                                        number_tag.get_text(strip=True)
                                    )
                                    if announcement_number > announcementPage.number:
                                        title_tag = row.find("td", class_="td-title")
                                        if title_tag:
                                            element = title_tag.find("a")
                                            if element:
                                                url = element["href"]
                                                urls.append(urljoin(response.url, url))

                        except AttributeError:
                            continue
                    else:
                        # 기계공학부의 goDetail 호출을 상세 주소로 변환한다.
                        announcement_number = int(
                            number_tag.get_text(strip=True).replace("<br>", "").strip()
                        )
                        if announcement_number > announcementPage.number:
                            element = row.find("a", href=True)
                            if element:
                                href_value = element["href"]
                                if href_value.startswith("javascript:goDetail("):
                                    detail_id = (
                                        href_value.split("(")[1]
                                        .split(")")[0]
                                        .strip("'\" ")
                                    )
                                    if not detail_id.isdigit():
                                        continue

                                    url = None

                                    if "sub01_01.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=hakbunotice&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"
                                    elif "sub01_02.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=gradnotice&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"
                                    elif "sub01_05.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=supervision&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"

                                    if url:
                                        urls.append(url)
                except AttributeError:
                    continue
            else:
                # 일반 학과 게시판
                announcement_number = int(number_tag.get_text(strip=True))
                if announcement_number > announcementPage.number:
                    title_tag = row.find("td", class_="_artclTdTitle")
                    if title_tag:
                        element = title_tag.find("a", class_="artclLinkView")
                        if element:
                            url = element["href"]
                            urls.append(urljoin(response.url, url))
        # 상세 링크 추출에 실패하면 공지 번호를 넘기지 않는다.
        urls = list(dict.fromkeys(urls))
        if any(urlparse(url).scheme not in ("http", "https") for url in urls):
            raise ValueError(f"Invalid notice link: {announcementPage.page_url}")
        if (
            max_announcement_number > announcementPage.number
            and not urls
            and not is_university
        ):
            event(
                "수집",
                f"공지 링크 추출 실패; 번호 유지: {announcementPage.page_url}",
                level="WARN",
            )
            return [], announcementPage.number
        event(
            "게시판",
            f"처리 대상 {len(urls)}건 (미완료 이력 포함)",
            count=len(urls),
            source_url=announcementPage.page_url,
            console=False,
        )
        return urls[::-1], max_announcement_number
    else:
        event(
            "수집",
            f"공지사항을 찾을 수 없습니다 : {announcementPage.page_url}",
            level="WARN",
        )
        return [], announcementPage.number


def crawl_ann_partial(url: str) -> Announcement:
    try:
        response = requests.get(url, verify=False, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        event(
            "수집",
            f"요청 처리 실패: {type(e).__name__}",
            level="WARN",
            error=type(e).__name__,
        )
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # 일반 학과, 기계공학부, 신규 양식 순으로 제목을 찾는다.
    title_element = soup.find("h2", class_="artclViewTitle")
    if not title_element:
        title_element = soup.find("h4", class_="vtitle")
    if not title_element:
        dt_elements = soup.find_all("dt")
        for dt in dt_elements:
            if "제목" in dt.get_text():
                title_element = dt.find_next_sibling("dd")
                break
    if not title_element:
        title_div = soup.find("div", class_="title")
        if title_div:
            title_element = title_div.find("strong")

    title = (
        filter_bmp_characters(clean_title(title_element.get_text(strip=True)))
        if title_element
        else "제목 없음"
    )

    # 게시판 양식에 맞는 본문 영역을 찾는다.
    content_text_element = soup.find("div", class_="artclView")
    if not content_text_element:
        content_text_element = soup.find("div", id="boardContents")
    if not content_text_element:
        content_text_element = soup.find("div", class_="board-contents clear")
    if not content_text_element:
        content_text_element = soup.find("div", class_="txt")

    content_text = (
        filter_bmp_characters(content_text_element.get_text(strip=True))
        if content_text_element
        else "내용 없음"
    )

    return Announcement(
        title=title,
        url=url,
        notice_board_name="",
        content_html=str(content_text_element) if content_text_element else "",
        content_text=content_text,
        files=[],
        metadata=extract_metadata(soup, response.url),
    )


def crawl_ann(url: str, category: str) -> Announcement:
    try:
        response = requests.get(url, verify=False, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        event(
            "수집",
            f"요청 처리 실패: {type(e).__name__}",
            level="WARN",
            error=type(e).__name__,
        )
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    base_url = response.url

    title_element = soup.find("h2", class_="artclViewTitle")
    if not title_element:
        title_element = soup.find("h4", class_="vtitle")
    if not title_element:
        dt_elements = soup.find_all("dt")
        for dt in dt_elements:
            if "제목" in dt.get_text():
                title_element = dt.find_next_sibling("dd")
                break
    if not title_element:
        title_div = soup.find("div", class_="title")
        if title_div:
            title_element = title_div.find("strong")

    title = (
        filter_bmp_characters(clean_title(title_element.get_text(strip=True)))
        if title_element
        else "제목 없음"
    )

    content_text_element = soup.find("div", class_="artclView")
    if not content_text_element:
        content_text_element = soup.find("div", id="boardContents")
    if not content_text_element:
        content_text_element = soup.find("div", class_="board-contents clear")
    if not content_text_element:
        content_text_element = soup.find("div", class_="txt")

    if content_text_element:
        for img_tag in content_text_element.find_all("img"):
            img_url = img_tag.get("src")
            if img_url:
                full_img_url = urljoin(base_url, img_url.replace("\\", "/"))
                img_tag["src"] = full_img_url
        content_html = filter_bmp_characters(str(content_text_element))
        content_text = filter_bmp_characters(content_text_element.get_text(strip=True))
    else:
        content_html = ""
        content_text = "내용 없음"

    files = []
    if category != "해당없음":
        file_extensions_to_exclude = [".png", ".jpg", ".jpeg", ".gif"]
        os.makedirs("downloads", exist_ok=True)

        def download_file(file_url, file_name):
            full_file_url = urljoin(base_url, file_url)
            try:
                result = download_attachment(full_file_url, file_name, referer=base_url)
                if result is None:
                    return
                if result["path"] not in files:
                    files.append(result["path"])
                event(
                    "첨부",
                    f"저장 완료: {result['filename']} ({result['bytes']:,}바이트)",
                    source_url=full_file_url,
                    filename=result["filename"],
                    bytes=result["bytes"],
                    sha256=result["sha256"],
                )
            except Exception as exc:
                event(
                    "첨부",
                    f"다운로드 실패: {file_name or '(이름 없음)'} / {type(exc).__name__}",
                    level="ERROR",
                    source_url=full_file_url,
                    filename=file_name,
                    error=type(exc).__name__,
                )

        # 일반 학과 첨부파일
        for insert in soup.find_all("dd", class_="artclInsert"):
            for li in insert.find_all("li"):
                link_tag = li.find("a")
                if link_tag and "download.do" in link_tag["href"]:
                    file_url = link_tag["href"]
                    file_name = sanitize_filename(link_tag.get_text(strip=True))
                    download_file(file_url, file_name)

        # 대학 공지 첨부파일
        for li in soup.select(
            "div.board-view-winfo div.board-winfo-files ul.board-view-filelist li"
        ):
            link_tag = li.find("a")
            if link_tag and "downloadRun.do" in link_tag["href"]:
                file_url = link_tag["href"]
                file_name = sanitize_filename(link_tag.get_text(strip=True))
                download_file(file_url, file_name)

        # 기계공학부 첨부파일
        for link_tag in soup.select("dl.half-box01 a.add-file"):
            if link_tag and (
                "download.asp" in link_tag["href"] or "download.php" in link_tag["href"]
            ):
                file_url = link_tag["href"]
                raw_text = link_tag.get_text(strip=True)
                file_name_text = (
                    raw_text.rsplit(" (", 1)[0] if " (" in raw_text else raw_text
                )
                file_name = sanitize_filename(file_name_text.strip())
                download_file(file_url, file_name)
        # 신규 양식 첨부파일
        for li in soup.select("div.attachment ul li"):
            link_tag = li.find("a", href=True)
            # 미리보기 링크는 다운로드에서 제외한다.
            if (
                link_tag
                and "download.do" in link_tag["href"]
                and not link_tag.has_attr("class")
            ):
                file_url = link_tag["href"]
                file_name = sanitize_filename(link_tag.get_text(strip=True))
                download_file(file_url, file_name)

    return Announcement(
        title=title,
        url=url,
        notice_board_name="",
        content_html=content_html,
        content_text=content_text,
        files=files,
        metadata=extract_metadata(soup, response.url),
    )
