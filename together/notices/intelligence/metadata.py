"""이미 받은 HTML에서 원문·첨부파일·명시된 게시일을 읽는다."""

import re
import json
from datetime import date
from urllib.parse import urljoin, urlsplit


def _date(value):
    match = re.search(r"((?:19|20)\d{2})(?:[.\-/]|년)\s*(\d{1,2})(?:[.\-/]|월)\s*(\d{1,2})", str(value))
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


BODY_SELECTORS = ('.artclView', '#boardContents', '.board-contents',
                  '.board-view-cont', 'article[itemprop="articleBody"]',
                  '[itemprop="articleBody"]', '.board_view_con', '.view_content')
ATTACHMENT_REGIONS = 'dd.artclInsert, .board-winfo-files, dl.half-box01, div.attachment, .board-view-files'
DATE_LABEL = r'(?:작성|등록|게시)\s*일(?:자)?|(?:publication\s+|published\s+|posted\s+|registration\s+)?date'


def article_region(soup):
    for selector in BODY_SELECTORS:
        body = soup.select_one(selector)
        if body is not None:
            return body, selector
    # 기계공학부의 본문은 class=txt이고 대학공지의 게시일 영역도 txt이다.
    for body in soup.select('div.txt'):
        if not body.find_parent(class_='vtitle-winfo'):
            return body, 'div.txt'
    return None, 'unknown'


def extract_metadata(soup, source_url, requested_url=None):
    body, layout = article_region(soup)
    attachments = {}
    region_link_ids = {id(link) for region in soup.select(ATTACHMENT_REGIONS) for link in region.select('a[href]')}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        in_region = id(link) in region_link_ids
        if not re.search(r"download\.do|downloadRun\.do|download\.(?:asp|php)|\.(?:pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip)(?:[?#]|$)", href, re.I) and not in_region:
            continue
        if "preview" in link.get("class", []):
            continue
        url = urljoin(source_url, href)
        if urlsplit(url).scheme in ("http", "https"):
            name = link.get_text(" ", strip=True)
            image = bool(re.search(r'\.(?:jpe?g|png|gif|webp|bmp|svg|tiff?|heic)(?:[?\s#(]|$)', name+' '+href, re.I))
            attachments[url] = {"url": url, "name": name,
                                "image_excluded_from_download": image,
                                "hash_status": "not_downloaded"}
    publication_time = None
    publication_time_source = None
    for node in soup.select(
        'meta[property="article:published_time"], meta[itemprop="datePublished"], time[itemprop="datePublished"], .artclWriteDate, time[datetime]'
    ):
        # 본문 일정표나 수정일의 time을 게시일로 오인하지 않는다.
        if (body is not None and any(parent is body for parent in node.parents)) or node.get('itemprop') == 'dateModified':
            continue
        value = (
            node.get("datetime")
            or node.get("content")
            or node.get_text(" ", strip=True)
        )
        publication_time = _date(value)
        if publication_time:
            publication_time_source = 'explicit_date_element'
            break
    if publication_time is None:
        for node in soup.find_all("dt"):
            if body is not None and any(parent is body for parent in node.parents):
                continue
            if re.fullmatch(
                DATE_LABEL, node.get_text(strip=True), re.I
            ):
                value = node.find_next_sibling("dd")
                if value:
                    publication_time = _date(value.get_text())
                    if publication_time:
                        publication_time_source = 'explicit_definition_list_label'
                        break
    if publication_time is None:
        # 실제 PNU 상세 페이지의 제목 바로 아래 정보 영역만 읽는다.
        # 본문의 행사일·마감일이나 '게시종료일'은 원래 작성일이 아니다.
        for node in soup.select('.board-view .title ul.detail li, .vtitle-winfo .txt'):
            value = node.get_text(' ', strip=True)
            match = re.match(r'^(?:작성|등록|게시)\s*일(?:자)?\s*[:：]?\s*(.+)$', value)
            if match:
                publication_time = _date(match[1])
                if publication_time:
                    publication_time_source = 'explicit_article_header_label'
                    break
    if publication_time is None:
        for node in soup.select('th, .board-view-info li, .view_info li, .board_info li, .board-view-title li, .board-view-title .board-info > span'):
            if body is not None and any(parent is body for parent in node.parents):
                continue
            text = node.get_text(' ', strip=True)
            if re.fullmatch(r'(?:'+DATE_LABEL+r')\s*[:：]?', text,re.I):
                sibling = node.find_next_sibling()
                text += ' '+(sibling.get_text(' ',strip=True) if sibling else '')
            match = re.match(r'^(?:'+DATE_LABEL+r')\s*[:：]?\s*(.+)$', text,re.I)
            if match and _date(match[1]):
                publication_time, publication_time_source = _date(match[1]), 'explicit_table_or_header_label'
                break
    if publication_time is None:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                value = json.loads(script.get_text())
            except (ValueError, TypeError):
                continue
            queue = value if isinstance(value,list) else [value]
            for item in queue:
                if not isinstance(item,dict):continue
                if isinstance(item.get('@graph'),list):queue.extend(item['@graph'])
                kinds = item.get('@type',[])
                kinds = [kinds] if isinstance(kinds,str) else kinds
                if isinstance(kinds,list) and any(kind in ('Article','NewsArticle','BlogPosting') for kind in kinds) and _date(item.get('datePublished','')):
                    publication_time,publication_time_source = _date(item['datePublished']),'structured_article_datePublished'
                    break
            if publication_time:break
    attachment_region = soup.select_one(ATTACHMENT_REGIONS)
    return {
        "source_url": source_url,
        "requested_url": requested_url or source_url,
        "final_url": source_url,
        "source_host": urlsplit(source_url).hostname,
        "layout": layout,
        "attachment_links": list(attachments.values()),
        "attachment_scan_complete": bool(attachments or attachment_region),
        "inline_image_count": len(body.find_all("img")) if body is not None else 0,
        "table_count": len(body.find_all("table")) if body is not None else 0,
        "publication_time": publication_time,
        "publication_time_is_explicit": publication_time is not None,
        "publication_time_source": publication_time_source,
    }
