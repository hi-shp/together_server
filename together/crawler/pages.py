import pandas as pd
from dotenv import load_dotenv
import os
import json
from pathlib import Path


with open(Path(__file__).with_name("url_overrides.json"), encoding="utf-8") as handle:
    URL_OVERRIDES = json.load(handle)


def extract_domain_part(url, domain):
    domain_index = url.find(domain)
    if domain_index == -1:
        return url

    path_start_index = url.find("/", domain_index + len(domain))
    if path_start_index == -1:
        return url
    else:
        return url[:path_start_index]


class AnnouncementPage:
    def __init__(self, department, page_url, default_url, number=0) -> None:
        self.department = department
        self.source_url = (
            page_url  # 주소가 바뀌어도 CSV 저장 키와 누적 번호는 유지한다.
        )
        self.page_url = URL_OVERRIDES.get(page_url, page_url)
        self.default_url = default_url
        self.number = number


class PageUrlManager:
    def __init__(self):
        """CSV에서 게시판 목록을 불러온다."""
        self.load_data()

    def load_data(self):
        """CSV의 학과명, 주소, 마지막 공지 번호를 읽는다."""
        load_dotenv()
        filename = os.getenv("PAGE_NAME")
        df = pd.read_csv(f"{filename}")
        self.announcement_pages = []
        self.__init_announcement_pages(df)

    def reload_data(self):
        """변경된 CSV를 다시 읽는다."""
        self.load_data()

    def __init_announcement_pages(self, data):
        for _, row in data.iterrows():
            department = (
                row["department"]
                if "department" in row and pd.notna(row["department"])
                else ""
            )
            page_url = row["page_url"]
            number_str = (
                str(row["number"])
                if "number" in row and pd.notna(row["number"])
                else "0"
            )
            number = int(float(number_str))

            self.announcement_pages.append(
                AnnouncementPage(
                    department=department,
                    page_url=page_url,
                    default_url=extract_domain_part(page_url, "pusan.ac.kr"),
                    number=number,
                )
            )
