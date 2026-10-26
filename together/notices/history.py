import os
import re
from datetime import datetime, timedelta
import tiktoken
from together.integrations.openai_client import check_title_similarity
from difflib import SequenceMatcher
import csv
import heapq
from collections import defaultdict, deque
from notice_intelligence.text import occurrence_conflict
from notice_intelligence.events import Notice, compare_notices

_TITLE_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}')


def _parse_title_timestamp(value):
    # 저장 형식이 일치할 때만 빠르게 해석하고, 그 밖에는 기존 방식을 쓴다.
    if _TITLE_TIMESTAMP.fullmatch(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def remove_brackets(text):
    # 채용 제목은 대괄호 안의 구분 정보를 남긴다.
    if "신입" in text or "채용" in text:
        text = re.sub(r'\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]', '', text).strip()
    else:
        text = re.sub(r'\[.*?\]|\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]', '', text).strip()

    return text


def truncate_text(text, max_tokens):
    encoding = tiktoken.get_encoding("cl100k_base")

    tokens = encoding.encode(text)

    truncated_tokens = tokens[:max_tokens]

    truncated_text = encoding.decode(truncated_tokens)

    return truncated_text


def calculate_similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _top_title_similarities(new_title, titles):
    best = []
    for index, title in enumerate(titles):
        matcher = SequenceMatcher(None, new_title, title)
        if len(best) == 3:
            # 상위 3개에 들 수 없는 경우만 정밀 계산을 생략한다.
            floor = best[0][0]
            if matcher.real_quick_ratio() < floor or matcher.quick_ratio() < floor:
                continue
        # 점수가 같으면 기존 목록에서 먼저 나온 제목을 우선한다.
        item = (matcher.ratio(), -index, title)
        if len(best) < 3:
            heapq.heappush(best, item)
        elif item > best[0]:
            heapq.heapreplace(best, item)
    return [(score, title) for score, _, title in sorted(best, reverse=True)]

def recent_title():
    filename='titles.txt'
    recent_titles = []
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            for line in lines:
                saved_datetime, saved_title = line.strip().split('$')
                saved_date = _parse_title_timestamp(saved_datetime)
                if saved_date > datetime.now() - timedelta(days=10):
                    recent_titles.append((saved_date, saved_title))
    recent_titles.sort(reverse=True, key=lambda x: x[0])
    recent_titles = [title for _, title in recent_titles[:1000]]
    return recent_titles

def is_recent_title_duplicate(new_title, filename='titles.txt', *, identity_title=None):
    recent_titles = []
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            for line in lines:
                saved_datetime, saved_title = line.strip().split('$')
                saved_date = _parse_title_timestamp(saved_datetime)
                if saved_date > datetime.now() - timedelta(days=10):
                    recent_titles.append((saved_date, saved_title))

    # 최근 10일 이내 제목 중 최대 1,000개를 비교한다.
    recent_titles.sort(reverse=True, key=lambda x: x[0])
    recent_entries = recent_titles[:1000]
    mode = os.getenv('NOTICE_DUPLICATE_MODE') or 'hybrid'
    original_title = identity_title or new_title
    if mode != 'legacy':
        recent_entries = [(date, title) for date, title in recent_entries if not occurrence_conflict(original_title, title)]
    recent_titles = [title for _, title in recent_entries]

    cleaned_titles = [remove_brackets(title).strip() for title in recent_titles]

    query_title = new_title if mode == 'legacy' else remove_brackets(new_title).strip()
    title_similarities = _top_title_similarities(query_title, cleaned_titles)

    if len(title_similarities) == 0:
        return '중복 아님'

    highest_similarity = title_similarities[0][0]

    if mode == 'legacy' and highest_similarity >= 0.85:
        print(f"유사도: {highest_similarity:.5f}", flush=True)
        return '중복'
    print(f"유사도: {highest_similarity:.5f}", flush=True)

    if mode == 'legacy':
        return check_title_similarity(new_title, [title for _, title in title_similarities[:3]])
    originals = defaultdict(deque)
    for date, title in recent_entries:
        originals[remove_brackets(title).strip()].append((date, title))
    candidates = [originals[cleaned].popleft() for _, cleaned in title_similarities[:3]]
    current = Notice(original_title, datetime.now().isoformat())
    for date, title in candidates:
        result = compare_notices(current, Notice(title, date.isoformat()))
        if result['relation'] == 'same_event':
            return '중복'
    return check_title_similarity(original_title, [title for _, title in candidates])


def save_title(title, category, filename='sorted_labeled_data.csv'):
    with open(filename, 'a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file, quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerow([title, category])


def save_only_title(title):
    with open('titles.txt', 'a', encoding='utf-8') as file:
        file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}${title}\n")
