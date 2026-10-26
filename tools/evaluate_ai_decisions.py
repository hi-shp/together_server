"""운영 게시 없이 고정 표본을 비교한다. API 호출 수를 제한하고 결과만 저장한다."""
import argparse
import csv
from datetime import datetime
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import types
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_intelligence.prompts import category_request, event_request

CATEGORY_IDS = '6 5410 6241 8165 863 874 939 865 871 7888 5151 13402 10682 5592 5289 327 8871 11886 674 10581 7572 11455 6426 7709'.split()
PAIR_IDS = {8, 41, 44, 47, 50, 51, 52, 53, 54, 57, 63, 74}


def legacy_pair_inputs(row):
    entries = sorted(((datetime.fromisoformat(row['left_time']), row['left_title']),
                      (datetime.fromisoformat(row['right_time']), row['right_title'])))
    (older, candidate), (newer, query) = entries
    pattern = r'\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]' if '신입' in candidate or '채용' in candidate else r'\[.*?\]|\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]'
    cleaned = re.sub(pattern, '', candidate).strip()
    return query, cleaned, (newer - older).total_seconds() < 10 * 86400


def main():
    from dotenv import load_dotenv
    from openai import OpenAI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--categories', type=Path, required=True)
    parser.add_argument('--pairs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-calls', type=int, default=64)
    parser.add_argument('--env', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    parser.add_argument('--baseline-ref', default='3c26a9192fa3e37f093e0897e8bf861136f187c2')
    parser.add_argument('--baseline-repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--category-ids', default=' '.join(CATEGORY_IDS))
    parser.add_argument('--pair-ids', default=' '.join(str(value) for value in sorted(PAIR_IDS)))
    args = parser.parse_args()
    load_dotenv(args.env)
    # 현재 어댑터를 과거 모델로 잘못 평가하지 않도록 원본 커밋에서 읽는다.
    baseline = subprocess.run(['git', 'show', f'{args.baseline_ref}:gpt_client.py'],
                              cwd=args.baseline_repo, check=True, capture_output=True, text=True, encoding='utf-8').stdout
    gpt_client = types.ModuleType('legacy_gpt_client_evaluation')
    exec(compile(baseline, 'legacy_gpt_client_evaluation.py', 'exec'), gpt_client.__dict__)
    client = OpenAI(api_key=os.getenv('GPT_API_KEY'), max_retries=0, timeout=30)
    gpt_client.client = client
    config = json.loads((Path(__file__).resolve().parents[1] / 'env.json').read_text(encoding='utf-8'))
    previous = json.loads(args.output.read_text(encoding='utf-8')) if args.output.exists() else {}
    if previous and previous.get('model') != args.model:
        raise ValueError('Evaluation output belongs to another model')
    count = previous.get('api_calls', 0)
    usage = previous.get('usage', [])
    results = previous.get('results', [])
    finished = {(row['task'], str(row['id'])) for row in results}
    legacy_unavailable = previous.get('legacy_unavailable', False)
    baseline_errors = previous.get('baseline_errors', [])

    def old_request(callback):
        nonlocal count, legacy_unavailable
        if legacy_unavailable:
            return 'API_UNAVAILABLE'
        if count >= args.max_calls:
            raise RuntimeError('Evaluation API call cap reached')
        count += 1
        try:
            return callback()
        except Exception as exc:
            body = getattr(exc, 'body', {}) or {}
            baseline_errors.append({'error_type': type(exc).__name__, 'status': getattr(exc, 'status_code', None), 'code': body.get('code')})
            if getattr(exc, 'status_code', None) in (401, 403, 404):
                legacy_unavailable = True
                return 'API_UNAVAILABLE'
            raise

    def modern(request):
        nonlocal count
        if count >= args.max_calls:
            raise RuntimeError('Evaluation API call cap reached')
        count += 1
        response = client.chat.completions.create(model=args.model, max_completion_tokens=450, reasoning_effort='none', **request)
        usage.append({'model': args.model, 'resolved_model': response.model, 'prompt_tokens': response.usage.prompt_tokens, 'completion_tokens': response.usage.completion_tokens})
        return json.loads(response.choices[0].message.content)

    try:
        with args.categories.open(encoding='utf-8-sig', newline='') as handle:
            category_rows = {row['row_id']: row for row in csv.DictReader(handle)}
        for number in args.category_ids.split():
            if ('category', number) in finished:
                continue
            row = category_rows[number]
            title = row['title']
            old = next((category for word, category in config['KEYWORD_CATEGORIES'].items() if word in title), None)
            source = 'keyword'
            if old is None and any(word in title for word in config['EXCLUDE_KEYWORDS']):
                old, source = '해당없음', 'exclude'
            if old is None:
                old, source = old_request(lambda: gpt_client.answer_gpt(title)), 'gpt-3.5-turbo'
            prediction = modern(category_request(title, config['CATEGORIES']))
            results.append({'task': 'category', 'id': number, 'title': title, 'expected': row['reviewed_label'] or '판단보류',
                            'old': old, 'old_method': source, 'new': prediction['category'], 'needs_body': prediction['needs_body'],
                            'reason': prediction['reason']})
            print(f'Category {number} evaluated ({count}/{args.max_calls} calls)', flush=True)
            time.sleep(.3)
        with args.pairs.open(encoding='utf-8-sig', newline='') as handle:
            selected_pair_ids = set(args.pair_ids.split())
            pair_rows = [row for row in csv.DictReader(handle) if row['review_id'] in selected_pair_ids]
        for row in pair_rows:
            if ('pair', row['review_id']) in finished:
                continue
            query, candidate, in_window = legacy_pair_inputs(row)
            score = SequenceMatcher(None, query, candidate).ratio()
            if not in_window:
                old, source = 'outside_recent_window', 'ten_day_window'
            elif score >= .85:
                old, source = 'same_event', 'similarity_085'
            else:
                label = old_request(lambda: gpt_client.check_title_similarity(query, [candidate]))
                old, source = ('API_UNAVAILABLE' if label == 'API_UNAVAILABLE' else 'same_event' if label.strip() == '중복' else 'different_event'), 'gpt-3.5-turbo'
            prediction = modern(event_request({'title': row['left_title'], 'observed_at': row['left_time']},
                                               {'title': row['right_title'], 'observed_at': row['right_time']}))
            results.append({'task': 'pair', 'id': row['review_id'], 'left': row['left_title'], 'right': row['right_title'],
                            'expected': row['reviewed_relation'], 'old': old, 'old_method': source,
                            'new': prediction['relation'], 'reason': prediction['reason'],
                            'baseline_scope': 'pairwise_only_not_full_1000_candidate_history_replay'})
            print(f'Pair {row["review_id"]} evaluated ({count}/{args.max_calls} calls)', flush=True)
            time.sleep(.3)
        status = 'complete'
    except Exception as exc:
        status = 'incomplete'
        print({'error_type': type(exc).__name__, 'status': getattr(exc, 'status_code', None)}, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'model': args.model, 'baseline_ref': args.baseline_ref, 'status': status, 'api_calls': count, 'usage': usage,
                                      'legacy_unavailable': legacy_unavailable, 'baseline_errors': baseline_errors,
                                      'results': results, 'review_provenance': 'assistant_title_only_challenge_set'}, ensure_ascii=False, indent=2), encoding='utf-8')
    print({'status': status, 'api_calls': count, 'completed_cases': len(results)}, flush=True)


if __name__ == '__main__':
    main()
