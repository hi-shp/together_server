"""보강으로 실제로 늘어나는 자료와 본문 교체 시 빠지는 정보를 확인한다."""

from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup


def compact(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).lower())


def profile(html):
    soup = BeautifulSoup(html or "", "html.parser")
    tables = set()
    for table in soup.find_all("table"):
        if table.find("table"):
            continue
        rows = table.find_all("tr")
        cells = [compact(cell.get_text(" ", strip=True)) for cell in table.find_all(["td", "th"])]
        if len(rows) >= 2 and sum(bool(cell) for cell in cells) >= 4:
            tables.add("|".join(cells))
    images = set()
    for image in soup.find_all("img", src=True):
        src = image["src"]
        if re.search(r"(?:ico[_-]|icon[_-]|spacer|tracking)", src, re.I):
            continue
        if any(str(image.get(key, "")).isdigit() and int(image[key]) <= 2 for key in ("width", "height")):
            continue
        parts = urlsplit(src)
        images.add(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
    text = soup.get_text(" ", strip=True)
    return {"tables": len(tables), "images": len(images), "broken": text.count("\ufffd") + sum(len(x) for x in re.findall(r"\?{2,}", text))}


def dates(text):
    return {(int(m), int(d)) for m, d in re.findall(r"(?<!\d)(?:20\d{2}[.\-/]\s*)?(\d{1,2})[./월]\s*(\d{1,2})(?!\d)", text) if 1 <= int(m) <= 12 and 1 <= int(d) <= 31}


def facts(text):
    value = unicodedata.normalize("NFKC", text)
    patterns = (r"\b\d{1,2}:\d{2}\b", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"\b0\d{1,2}[- ]\d{3,4}[- ]\d{4}\b", r"\d[\d,]*(?:\.\d+)?\s*(?:만원|원|명|세|학점|시간|호)")
    return {compact(hit) for pattern in patterns for hit in re.findall(pattern, value)}


def compatible(old_text, new_text):
    return dates(old_text) <= dates(new_text) and facts(old_text) <= facts(new_text)


def text_gain(old_text, new_text, a, b):
    old, new = compact(old_text), compact(new_text)
    if not new or not compatible(old_text, new_text):
        return []
    # 본문 교체로 기존 표·사진·핵심 안내가 사라지는 경우에는 자동 처리하지 않는다.
    if (
        b.get("tables", 0) < a.get("tables", 0)
        or b.get("images", 0) < a.get("images", 0)
    ):
        return []
    shingles = lambda value: {value[i:i + 6] for i in range(max(0, len(value) - 5))}
    previous = shingles(old)
    coverage = len(previous & shingles(new)) / len(previous) if previous else 1
    similar = coverage >= 0.85 or SequenceMatcher(None, old, new, autojunk=False).ratio() >= 0.9
    reasons = []
    if similar:
        if b.get("tables", 0) > a.get("tables", 0):
            reasons.append("table_added")
        if b.get("images", 0) > a.get("images", 0):
            reasons.append("image_added")
        if a.get("broken", 0) - b.get("broken", 0) >= 3:
            reasons.append("broken_text_repaired")
    if len(old) < 120 and len(new) >= max(250, len(old) * 3) and coverage >= 0.8:
        reasons.append("missing_body_recovered")
    return reasons


def body_gain(current_html, candidate_html):
    old = BeautifulSoup(current_html or "", "html.parser")
    # 게시용 원문 링크와 이전 보강 구분 문구는 내용 비교에서 제외한다.
    for paragraph in old.find_all("p"):
        if re.match(r"^(?:본문\s*링크|추가\s*원문)\s*:", paragraph.get_text(" ", strip=True)):
            paragraph.decompose()
    old_text = old.get_text(" ", strip=True).replace("본문 링크 :", "").replace("추가 원문:", "").replace("추가 안내", "")
    candidate = BeautifulSoup(candidate_html or "", "html.parser")
    new_text = candidate.get_text(" ", strip=True)
    old_links = {node["href"] for node in old.find_all("a", href=True)}
    new_links = {node["href"] for node in candidate.find_all("a", href=True)}
    if not old_links <= new_links:
        return []
    image_key = lambda src: urlunsplit((*urlsplit(src)[:3], "", ""))
    new_images = {image_key(node["src"]) for node in candidate.find_all("img", src=True)}
    if any(image_key(node["src"]) not in new_images for node in old.find_all("img", src=True) if profile(str(node))["images"]):
        return []
    # 기존 표의 개별 항목이 새 본문에서도 확인되어야 교체할 수 있다.
    new_compact = compact(new_text)
    if any(compact(cell.get_text(" ", strip=True)) not in new_compact for cell in old.select("table td, table th") if len(compact(cell.get_text(" ", strip=True))) >= 3):
        return []
    return text_gain(old_text, new_text, profile(str(old)), profile(candidate_html))
