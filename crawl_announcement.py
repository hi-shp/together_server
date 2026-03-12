import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import urljoin
import re
import time
import urllib3 # 추가
from duplicate_checker import recent_title

# SSL 경고 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

"""
사이트 html 양식에 따라 경우를 나눔
경우 1 : 나머지 모든 공지사항 url
경우 2 : 기계공학부 공지사항
경우 3 : 대학공지
경우 4 : 신규 사이트 템플릿 (2026.03)
"""

class Announcement:
    def __init__(self, title: str, content_html: str, content_text: str, notice_board_name: str, url: str, files: list):
        self.title = title
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
    return ' '.join(title.split())  # 공지사항 제목을 한 줄로 정리

def sanitize_filename(filename):  # 파일 다운로드 시 사용할 수 없는 이름 수정
    return re.sub(r'[\/:*?"<>|]', '_', filename)

# BMP 범위 내 문자만 남기기 위한 필터링 함수 추가
def filter_bmp_characters(text):
    return ''.join(c for c in text if ord(c) <= 0xFFFF)

def get_anns_url(announcementPage, n):  # 각 사이트마다 공지 url 추출
    try:
        # SSL 인증서 오류 해결을 위해 verify=False 유지
        response = requests.get(announcementPage.page_url, verify=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(e, flush=True)
        return [], announcementPage.number  # 오류 발생 시 빈 리스트 반환

    soup = BeautifulSoup(response.text, 'html.parser')

    # 최대 2초 동안 tbody 태그가 나타날 때까지 반복해서 확인
    start_time = time.time()
    table_element = None
    idx = 0
    for _ in range(10):
        table_element = soup.find("tbody")
        if table_element:
            idx = 1
            break  # tbody 태그를 찾으면 즉시 종료
        time.sleep(0.1)  # 0.1초 간격으로 재확인
        response = requests.get(announcementPage.page_url, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

    if idx != 1:
        for _ in range(5):
            table_element = soup.find("tbody")
            if table_element:
                break  # tbody 태그를 찾으면 즉시 종료
            time.sleep(3)  # 0.1초 간격으로 재확인
            response = requests.get(announcementPage.page_url, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

    if table_element:
        rows = table_element.find_all("tr")
    else:
        print("⚠️ tbody 태그를 찾을 수 없습니다.", flush=True)
        rows = []

    announcement_numbers = []
    urls = []

    for row in rows:
        try:
            # 첫 번째 시도: _artclTdNum 클래스의 td 태그 찾기(나머지 모두)
            number_tag = row.find("td", class_="_artclTdNum")
            number_text = number_tag.get_text(strip=True)
        except AttributeError:
            try:
                # 두 번째 시도: number 클래스의 td 태그 찾기(기계과)
                number_tag = row.find("td", class_="number")
                number_text = number_tag.get_text(strip=True).replace("<br>", "").strip()
            except AttributeError:
                try:
                    # 세 번째 시도: num 클래스의 td 태그 찾기(대학공지)
                    number_tag = row.find("td", class_="num")
                    number_text = number_tag.get_text(strip=True).replace("<br>", "").strip()
                except AttributeError:
                    try:
                        # 네 번째 시도: td-num 클래스의 td 태그 찾기 (고분자공학과 등 최신 양식)
                        number_tag = row.find("td", class_="td-num")
                        number_text = number_tag.get_text(strip=True)
                    except AttributeError:
                        # 모든 시도가 실패한 경우
                        continue

        if number_text.isdigit():  # 숫자인 경우만 처리
            announcement_numbers.append(int(number_text))

    if announcement_numbers:
        max_announcement_number = max(announcement_numbers)
        difference = max_announcement_number - announcementPage.number
        print(f'{n}. {announcementPage.department} {max_announcement_number}', flush=True)

        if difference > 0:
            print(f'💡{difference}개의 New Announcment!: {announcementPage.page_url}', flush=True)
        else:
            print(f'새로 추가된 공지사항 없음: {announcementPage.page_url}', flush=True)

        idx = 0
        num_idx = 0

        # yesterday.txt에서 두 번째 줄 읽기
        with open("yesterday.txt", "r", encoding="utf-8") as file:
            lines = file.readlines()
        last_yesterday_title = lines[1].strip() if len(
            lines) >= 2 else None  # 두 번째 줄 가져오기

        # URL 추출
        for row in rows:
            # 첫 번째 시도: 나머지 모두
            number_tag = row.find("td", class_="_artclTdNum")
            if number_tag is None or not number_tag.get_text(strip=True).isdigit():
                try:
                    # 두 번째 시도: 기계과
                    number_tag = row.find("td", class_="number")
                    if number_tag is None or not number_tag.get_text(strip=True).replace("<br>", "").strip().isdigit():
                        try:
                            # 세 번째 시도: 대학공지
                            number_tag = row.find("td", class_="num")
                            if number_tag and number_tag.get_text(strip=True).isdigit():
                                announcement_number = int(number_tag.get_text(strip=True))
                                if announcement_number > announcementPage.number:
                                    title_tag = row.find("td", class_="subject")
                                    if title_tag:
                                        element = title_tag.find('a')
                                        if element:
                                            url = element['href']
                                            urls.append(urljoin(announcementPage.page_url, url))

                            # <img> 태그 포함 경우 (대학공지 특수)
                            elif number_tag and number_tag.find("img"):
                                title_tag = row.find("td", class_="subject")
                                if title_tag:
                                    element = title_tag.find("a")
                                    if element:
                                        title = element.get_text(strip=True)
                                        if title == last_yesterday_title:
                                            idx += 1
                                        if idx == 0:
                                            recent_titles = recent_title()
                                            if title in recent_titles:
                                                lines[1] = title
                                                with open("yesterday.txt", "w", encoding="utf-8") as file:
                                                    file.writelines(lines)
                                                num_idx += 1
                                                print("중복 공지이므로 패스", flush=True)
                                            else:
                                                url = element['href']
                                                urls.append(urljoin(announcementPage.page_url, url))
                                                idx += 1
                                                if num_idx == 0:
                                                    lines[1] = title
                                                    with open("yesterday.txt", "w", encoding="utf-8") as file:
                                                        file.writelines(lines)
                                                    num_idx += 1
                            
                            # 네 번째 시도 (고분자공학과 등 td-num, td-title 사용 사이트)
                            else:
                                number_tag = row.find("td", class_="td-num")
                                if number_tag and number_tag.get_text(strip=True).isdigit():
                                    announcement_number = int(number_tag.get_text(strip=True))
                                    if announcement_number > announcementPage.number:
                                        title_tag = row.find("td", class_="td-title")
                                        if title_tag:
                                            element = title_tag.find('a')
                                            if element:
                                                url = element['href']
                                                urls.append(urljoin(announcementPage.page_url, url))

                        except AttributeError:
                            continue
                    else:
                        # 기계과 URL 처리 로직 (UnboundLocalError 방지 처리)
                        announcement_number = int(number_tag.get_text(strip=True).replace("<br>", "").strip())
                        if announcement_number > announcementPage.number:
                            element = row.find('a', href=True)
                            if element:
                                href_value = element['href']
                                if href_value.startswith("javascript:goDetail("):
                                    detail_id = href_value.split('(')[1].split(')')[0]
                                    
                                    url = None # 초기화
                                    
                                    if "sub01_01.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=hakbunotice&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"
                                    elif "sub01_02.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=gradnotice&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"
                                    elif "sub01_05.php" in announcementPage.page_url:
                                        url = f"{announcementPage.page_url.split('?')[0]}?seq={detail_id}&db=supervision&page=1&perPage=20&SearchPart=BD_SUBJECT&SearchStr=&page_mode=view"
                                    
                                    if url: # url 변수가 생성되었을 때만 추가
                                        urls.append(url)
                except AttributeError:
                    continue
            else:
                # 첫 번째 시도 (나머지 학과들) 로직 유지
                announcement_number = int(number_tag.get_text(strip=True))
                if announcement_number > announcementPage.number:
                    title_tag = row.find("td", class_="_artclTdTitle")
                    if title_tag:
                        element = title_tag.find('a', class_='artclLinkView')
                        if element:
                            url = element['href']
                            urls.append(urljoin(announcementPage.page_url, url))
        return urls[::-1], max_announcement_number
    else:
        print(f'공지사항을 찾을 수 없습니다 : {announcementPage.page_url}', flush=True)
        return [], announcementPage.number


def crawl_ann_partial(url: str) -> Announcement:  # 제목+내용만 부분 추출해서 중복/카테고리 판단
    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(e, flush=True)
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    # 제목 찾기 (우선순위: 일반 -> 기계과 -> 신규양식)
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

    title = filter_bmp_characters(clean_title(title_element.get_text(strip=True))) if title_element else "제목 없음"

    # 본문 찾기 (우선순위: 일반 -> 기계과 -> 대학공지 -> 신규양식)
    content_text_element = soup.find('div', class_="artclView")
    if not content_text_element:
        content_text_element = soup.find('div', id="boardContents")
    if not content_text_element:
        content_text_element = soup.find('div', class_="board-contents clear")
    if not content_text_element:
        content_text_element = soup.find('div', class_="txt")

    content_text = filter_bmp_characters(content_text_element.get_text(strip=True)) if content_text_element else "내용 없음"

    return Announcement(
        title=title,
        url=url,
        notice_board_name="",
        content_html=str(content_text_element) if content_text_element else "",
        content_text=content_text,
        files=[]
    )

def crawl_ann(url: str, category: str) -> Announcement:  # 전부 추출
    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(e, flush=True)
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    base_url = response.url.split('/bbs/')[0]

    # 제목 찾기
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

    title = filter_bmp_characters(clean_title(title_element.get_text(strip=True))) if title_element else "제목 없음"

    # 본문 및 HTML 찾기
    content_text_element = soup.find('div', class_="artclView")
    if not content_text_element:
        content_text_element = soup.find('div', id="boardContents")
    if not content_text_element:
        content_text_element = soup.find('div', class_="board-contents clear")
    if not content_text_element:
        content_text_element = soup.find('div', class_="txt")

    if content_text_element:
        for img_tag in content_text_element.find_all("img"):
            img_url = img_tag.get("src")
            if img_url:
                if img_url.startswith(".."):
                    full_img_url = urljoin(base_url, img_url.replace("..\\", "").replace("../", ""))
                elif not img_url.startswith("http"):
                    full_img_url = urljoin(base_url, img_url)
                else:
                    full_img_url = img_url
                img_tag["src"] = full_img_url
        content_html = filter_bmp_characters(str(content_text_element))
        content_text = filter_bmp_characters(content_text_element.get_text(strip=True))
    else:
        content_html = ""
        content_text = "내용 없음"

    # 첨부파일 다운로드 로직
    files = []
    if category != "해당없음":
        file_extensions_to_exclude = ['.png', '.jpg', '.jpeg', '.gif']
        os.makedirs('downloads', exist_ok=True)

        def download_file(file_url, file_name):
            if not any(file_name.lower().endswith(ext) for ext in file_extensions_to_exclude):
                try:
                    full_file_url = urljoin(base_url, file_url)
                    file_path = os.path.join('downloads', file_name)
                    file_data = requests.get(full_file_url, verify=False).content # 여기도 추가
                    with open(file_path, 'wb') as f:
                        f.write(file_data)
                    files.append(file_path)
                    print(f'파일 다운로드 완료: {file_path}', flush=True)
                except Exception as e:
                    print(f'다운로드 실패: {file_name} ({e})', flush=True)

        # 1. 나머지 공지
        for insert in soup.find_all('dd', class_="artclInsert"):
            for li in insert.find_all("li"):
                link_tag = li.find("a")
                if link_tag and 'download.do' in link_tag["href"]:
                    file_url = link_tag["href"]
                    file_name = sanitize_filename(link_tag.get_text(strip=True))
                    download_file(file_url, file_name)

        # 2. 대학공지
        for li in soup.select('div.board-view-winfo div.board-winfo-files ul.board-view-filelist li'):
            link_tag = li.find("a")
            if link_tag and 'downloadRun.do' in link_tag["href"]:
                file_url = link_tag["href"]
                file_name = sanitize_filename(link_tag.get_text(strip=True).split('(')[0].strip())
                download_file(file_url, file_name)

        # 3. 기계과 공지
        for link_tag in soup.select('dl.half-box01 a.add-file'):
            if link_tag and ('download.asp' in link_tag["href"] or 'download.php' in link_tag["href"]):
                file_url = link_tag["href"]
                raw_text = link_tag.get_text(strip=True)
                file_name_text = raw_text.rsplit(' (', 1)[0] if ' (' in raw_text else raw_text
                file_name = sanitize_filename(file_name_text.strip())
                download_file(file_url, file_name)
        # 4. 신규 템플릿 공지
        for li in soup.select('div.attachment ul li'):
            link_tag = li.find("a", href=True)
            # 미리보기 버튼(class="preview")은 제외하고 다운로드 링크만 필터링
            if link_tag and 'download.do' in link_tag["href"] and not link_tag.has_attr("class"):
                file_url = link_tag["href"]
                # 이미지 태그 텍스트 무시하고 파일명만 가져오기
                file_name = sanitize_filename(link_tag.get_text(strip=True))
                download_file(file_url, file_name)

    return Announcement(
        title=title,
        url=url,
        notice_board_name="",
        content_html=content_html,
        content_text=content_text,
        files=files
    )
