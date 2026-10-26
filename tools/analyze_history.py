"""전체 제목·분류 이력을 읽고 품질 점검 결과를 별도 경로에 저장한다."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_intelligence.text import exact_key, family_key, identity, normalize_label, normalize_title


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze(history_path, labels_path, config_path, output):
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    categories = config['CATEGORIES']
    history, bad_history = [], []
    for number, line in enumerate(history_path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            timestamp, title = line.split('$', 1)
            parsed = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            history.append({'row_id': number, 'timestamp': timestamp, 'date': parsed.date().isoformat(),
                            'title': title, 'key': exact_key(title), 'family': family_key(title)})
        except (ValueError, TypeError):
            bad_history.append(number)
    with labels_path.open(encoding='utf-8-sig', newline='') as handle:
        source = list(csv.DictReader(handle))
    labeled = []
    for number, row in enumerate(source, 1):
        title = row.get('제목') or ''
        label = normalize_label(row.get('카테고리', ''), categories)
        labeled.append({'row_id': number, 'title': title, 'original_label': row.get('카테고리', ''),
                        'label': label or '', 'key': exact_key(title), 'family': family_key(title)})
    exact_groups, family_groups = defaultdict(list), defaultdict(list)
    dates = defaultdict(list)
    for row in history:
        exact_groups[row['key']].append(row)
        family_groups[row['family']].append(row)
        dates[row['key']].append(row['timestamp'])
    label_groups = defaultdict(list)
    for row in labeled:
        if row['label']:
            label_groups[row['key']].append(row)
    conflicts = {key: rows for key, rows in label_groups.items() if len({row['label'] for row in rows}) > 1}
    keyword_conflicts, multiple_keywords = [], []
    for row in labeled:
        matched = [(word, category) for word, category in config['KEYWORD_CATEGORIES'].items() if word in row['title']]
        excluded = [word for word in config['EXCLUDE_KEYWORDS'] if word in row['title']]
        if matched and excluded:
            keyword_conflicts.append({'row_id': row['row_id'], 'title': row['title'], 'saved_label': row['label'],
                                      'first_keyword_label': matched[0][1], 'keywords': '|'.join(word for word, _ in matched),
                                      'excludes': '|'.join(excluded)})
        if len({label for _, label in matched}) > 1:
            multiple_keywords.append({'row_id': row['row_id'], 'title': row['title'],
                                      'saved_label': row['label'], 'keyword_labels': '|'.join(sorted({label for _, label in matched}))})
    recurring = []
    for key, rows in family_groups.items():
        years = {year for row in rows for year in identity(row['title'])['years']}
        if len(years) >= 2:
            recurring.extend({'family': key, 'timestamp': row['timestamp'], 'title': row['title']} for row in rows)
    usable = []
    for row in labeled:
        if not row['label'] or row['key'] in conflicts:
            continue
        usable.append(dict(row, timestamp=min(dates.get(row['key'], ['']))))
    summary = {
        'sources': {path.name: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
                    for path in (history_path, labels_path)},
        'history_rows': len(history), 'bad_history_rows': bad_history,
        'history_first': min(row['timestamp'] for row in history), 'history_last': max(row['timestamp'] for row in history),
        'history_months': dict(sorted(Counter(row['timestamp'][:7] for row in history).items())),
        'history_unique_raw': len({row['title'] for row in history}), 'history_unique_keys': len(exact_groups),
        'history_repeated_key_groups': sum(len(rows) > 1 for rows in exact_groups.values()),
        'labeled_rows': len(labeled), 'raw_category_counts': dict(Counter(row['original_label'] for row in labeled)),
        'normalized_category_counts': dict(Counter(row['label'] or '학습대상외' for row in labeled)),
        'invalid_label_rows': sum(not row['label'] for row in labeled),
        'conflicting_key_groups': len(conflicts), 'conflicting_label_rows': sum(len(rows) for rows in conflicts.values()),
        'keyword_exclusion_conflicts': len(keyword_conflicts), 'multiple_keyword_category_matches': len(multiple_keywords),
        'timestamp_join_rows': sum(bool(row['key'] in dates) for row in labeled),
        'recurring_year_family_groups': len({row['family'] for row in recurring}),
        'usable_weak_label_rows': len(usable),
        'missing_historical_fields': ['source_url', 'department', 'body', 'attachments', 'source_publication_time', 'duplicate_gold_label'],
        'grain': {'titles.txt': '저장 시각과 제목 1건', 'sorted_labeled_data.csv': '게시 후 기록한 제목과 당시 분류 1건'},
        'label_quality': '과거 규칙/GPT의 결과이며 검증된 정답이 아님',
    }
    (output / 'profile.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv(output / 'usable_weak_labels.csv', usable, ['row_id', 'title', 'original_label', 'label', 'key', 'family', 'timestamp'])
    write_csv(output / 'label_conflicts.csv', [row for rows in conflicts.values() for row in rows], list(labeled[0]))
    write_csv(output / 'keyword_exclusion_conflicts.csv', keyword_conflicts,
              ['row_id', 'title', 'saved_label', 'first_keyword_label', 'keywords', 'excludes'])
    write_csv(output / 'multiple_keyword_matches.csv', multiple_keywords, ['row_id', 'title', 'saved_label', 'keyword_labels'])
    write_csv(output / 'recurring_years.csv', recurring, ['family', 'timestamp', 'title'])
    rng = random.Random(20261026)
    review = []
    for label in categories:
        pool = [row for row in usable if row['label'] == label]
        review.extend(rng.sample(pool, min(15, len(pool))))
    review.extend(row for row in labeled if row['row_id'] in {item['row_id'] for item in keyword_conflicts[:30]})
    review = list({row['row_id']: row for row in review}.values())
    write_csv(output / 'review_sample.csv', review, ['row_id', 'title', 'original_label', 'label', 'key', 'family', 'timestamp'])
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'env.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.history, args.labels, args.config, args.output)
    print(json.dumps({key: value for key, value in result.items() if key not in ('sources', 'raw_category_counts')}, ensure_ascii=False, indent=2))
