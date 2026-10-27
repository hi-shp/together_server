import requests
from bs4 import BeautifulSoup
import csv
from urllib.parse import urljoin
from together.config import HTTP_TIMEOUT


class NoticeUpdater:
    def __init__(self, yesterday_file="yesterday.txt", csv_file="pages.csv"):
        self.yesterday_file = yesterday_file
        self.csv_file = csv_file
        self.urls = [
            "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?robot=Y&mgr_seq=3&mode=list&page=1",
            "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do?robot=Y&mgr_seq=3&mode=list&page=2",
        ]

    def get_last_notice_title(self):
        """마지막으로 확인한 대학 공지 제목을 읽는다."""
        with open(self.yesterday_file, "r", encoding="utf-8") as f:
            return f.readline().strip()

    def fetch_notice_number(self, last_title):
        """저장된 제목과 일치하는 대학 공지 번호를 찾는다."""
        for url in self.urls:
            response = requests.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            rows = soup.find_all("tr")

            for row in rows:
                num_tag = row.find("td", class_="num")
                title_tag = row.find("td", class_="subject")

                if num_tag and title_tag:
                    title_link = title_tag.find("a")
                    if title_link:
                        title = title_link.get_text(strip=True)

                        page_num_text = num_tag.get_text(strip=True).strip()

                        if page_num_text.isdigit():
                            page_num = int(page_num_text)

                            if title == last_title:
                                return str(page_num)

        return None

    def update_csv(self, new_number):
        """대학 공지에 해당하는 첫 두 행의 번호를 갱신한다."""
        with open(self.csv_file, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            data = list(reader)

        header = data[0]
        rows = data[1:]

        # CSV의 첫 두 행은 대학 공지 1·2페이지다.
        updated_rows = []
        for i, row in enumerate(rows):
            department, page_url, number = row
            numeber = number.strip()
            if i < 2:
                updated_rows.append(
                    [department, page_url, new_number if new_number else number]
                )
            else:
                updated_rows.append(row)

        with open(self.csv_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(header)
            writer.writerows(updated_rows)
            csvfile.flush()
            csvfile.close()

    def fetch_highest_notice_title(self):
        """대학 공지 목록에서 최신 제목을 찾는다."""
        response = requests.get(self.urls[0], timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        highest_number = -1
        highest_title = None
        highest_url = None

        rows = soup.find_all("tr")

        for row in rows:
            num_tag = row.find("td", class_="num")
            title_tag = row.find("td", class_="subject")

            if num_tag and title_tag:
                title_link = title_tag.find("a")
                if title_link:
                    try:
                        notice_number = int(num_tag.get_text(strip=True))
                        if notice_number > highest_number:
                            highest_number = notice_number

                            highest_title = (
                                title_link.get("title")
                                if title_link.get("title")
                                else title_link.get_text(strip=True)
                            )

                            if highest_title.endswith("..."):
                                highest_url = title_link["href"]

                    except ValueError:
                        continue

        return highest_title

    def fetch_full_notice_title(self, relative_url):
        """상세 페이지에서 공지 제목을 읽는다."""
        base_url = "https://www.pusan.ac.kr/kor/CMS/Board/PopupBoard.do"
        full_url = urljoin(base_url, relative_url)

        response = requests.get(full_url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title_tag = soup.find("h4", class_="vtitle")
        if title_tag:
            return title_tag.get_text(strip=True)

        return None

    def update_university_notices(self):
        """대학 공지 번호와 마지막 확인 제목을 갱신한다."""
        last_title = self.get_last_notice_title()
        new_number = self.fetch_notice_number(last_title)
        self.update_csv(new_number)
        highest_title = self.fetch_highest_notice_title()

        if highest_title:
            # 첫째 줄만 최신 제목으로 바꾸고 나머지 줄은 유지한다.
            with open(self.yesterday_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            lines[0] = highest_title + "\n"

            with open(self.yesterday_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
