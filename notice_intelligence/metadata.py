"""이미 받은 HTML에서 원문·첨부파일·명시된 게시일을 읽는다."""
import re
from urllib.parse import urljoin, urlsplit


def extract_metadata(soup, source_url):
    attachments = {}
    for link in soup.find_all('a', href=True):
        href = link['href']
        if not re.search(r'download\.do|downloadRun\.do|download\.(?:asp|php)', href, re.I):
            continue
        if 'preview' in link.get('class', []):
            continue
        url = urljoin(source_url, href)
        if urlsplit(url).scheme in ('http', 'https'):
            attachments[url] = {'url': url, 'name': link.get_text(' ', strip=True)}
    publication_time = None
    for node in soup.select('time[datetime], meta[property="article:published_time"], .artclWriteDate'):
        value = node.get('datetime') or node.get('content') or node.get_text(' ', strip=True)
        match = re.search(r'((?:19|20)\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})', value)
        if match:
            publication_time = '-'.join((match[1], match[2].zfill(2), match[3].zfill(2)))
            break
    if publication_time is None:
        for node in soup.find_all('dt'):
            if re.fullmatch(r'작성\s*일|등록\s*일|게시\s*일', node.get_text(strip=True)):
                value = node.find_next_sibling('dd')
                if value:
                    match = re.search(r'((?:19|20)\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})', value.get_text())
                    if match:
                        publication_time = '-'.join((match[1], match[2].zfill(2), match[3].zfill(2)))
                        break
    attachment_region = soup.select_one('dd.artclInsert, .board-winfo-files, dl.half-box01, div.attachment')
    return {'source_url': source_url, 'attachment_links': list(attachments.values()),
            'attachment_scan_complete': bool(attachments or attachment_region),
            'inline_image_count': len(soup.find_all('img')),
            'publication_time': publication_time, 'publication_time_is_explicit': publication_time is not None}
