"""승인된 학습 예산 안에서 검토 작업을 접수하고 결과를 보관한다.

운영 크롤러와 별도 디렉터리에서만 사용한다. API 전송 전 비용을 예약하고,
접수 응답이 불확실하면 작업 목록으로 대조한다. 자동 재접수하지 않는다.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.learning.budget import TrainingBudget


def write_private(path, data):
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def submit(client, root, plan, budget):
    source = root / plan['file']
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != plan['sha256']:
        raise ValueError('Batch input changed after cost reservation planning')
    state_path = root / ('job-' + plan['model'] + '.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {'sha256':digest, 'phase':'prepared'}
    if state['sha256'] != digest:
        raise ValueError('Existing job belongs to another input; preserve it')
    if state.get('batch_id'):
        return state
    if state['phase'] == 'create_unknown':
        matches = [job for job in client.batches.list(limit=100)
                   if (job.metadata or {}).get('together_input_sha') == digest]
        if len(matches) != 1:
            raise RuntimeError('Batch acceptance is unconfirmed; no duplicate request sent')
        state.update(batch_id=matches[0].id, phase='accepted')
        write_private(state_path, state)
        return state
    for reservation in plan['reservations']:
        budget.reserve(reservation['key'], reservation['maximum_usd'])
    write_private(state_path, state)
    if not state.get('input_file_id'):
        with source.open('rb') as handle:
            uploaded = client.files.create(file=handle, purpose='batch')
        state['input_file_id'] = uploaded.id
        write_private(state_path, state)
    # 접수 호출 직전의 상태를 먼저 보관한다. 연결이 끊겨도 중복 접수하지 않는다.
    state['phase'] = 'create_unknown'
    write_private(state_path, state)
    job = client.batches.create(input_file_id=state['input_file_id'], endpoint='/v1/chat/completions',
                                completion_window='24h', metadata={'together_input_sha':digest, 'purpose':'notice_review_20261030'})
    state.update(batch_id=job.id, phase='accepted')
    write_private(state_path, state)
    return state


def collect(client, root, plan, budget, state):
    job = client.batches.retrieve(state['batch_id'])
    write_private(root / ('status-' + plan['model'] + '.json'), job.model_dump())
    counts = job.request_counts.model_dump() if job.request_counts else {}
    summary = {'model':plan['model'], 'status':job.status, 'request_counts':counts, 'validated_records':0}
    if job.status not in ('completed', 'expired', 'cancelled', 'failed'):
        return summary
    expected = {row['key']:row for row in plan['reservations']}
    seen, labels, rejected = set(), [], []
    for kind in ('output', 'error'):
        file_id = getattr(job, kind + '_file_id', None)
        if not file_id:
            continue
        path = root / (kind + '-' + plan['model'] + '.jsonl')
        if not path.exists():
            data = client.files.content(file_id).content
            temporary = path.with_suffix('.pending')
            temporary.write_bytes(data); temporary.chmod(0o600); os.replace(temporary, path)
        for line in path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line); key = record['custom_id']
            if key not in expected or key in seen:
                raise ValueError('Unknown or duplicate batch result')
            seen.add(key)
            response = record.get('response') or {}; body = response.get('body') or {}
            usage = body.get('usage')
            if usage is not None:
                charged = (usage['prompt_tokens'] * plan['batch_input_rate_upper'] + usage['completion_tokens'] * plan['batch_output_rate']) / 1e6
                budget.settle(key, charged)
            if response.get('status_code') != 200 or not usage:
                rejected.append({'key':key,'reason':'request_failed_or_usage_missing'})
                continue
            choice = (body.get('choices') or [{}])[0]
            try:
                rows = json.loads(choice['message']['content'])['rows']
                if choice['finish_reason'] != 'stop' or sorted(row['id'] for row in rows) != sorted(expected[key]['ids']):
                    raise ValueError('Incomplete records')
                from together.notices.intelligence.policy import CATEGORIES
                if any(row['category'] not in (*CATEGORIES,'판단보류') or type(row['needs_body']) is not bool or not isinstance(row['reason'],str) for row in rows):
                    raise ValueError('Invalid review schema')
                labels.extend(rows)
            except (KeyError, TypeError, ValueError):
                rejected.append({'key':key,'reason':'incomplete_or_invalid_labels'})
    write_private(root / ('reviewed-' + plan['model'] + '.json'), {'rows':labels,'rejected':rejected,'unreturned_requests':sorted(set(expected)-seen)})
    summary.update(validated_records=len(labels), rejected_requests=len(rejected), unreturned_requests=len(set(expected)-seen))
    # 처리·비용 미확인은 예약을 유지하고 확인된 사용량만 정산한다.
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    import fcntl
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(args.env_file, override=True)
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.root / 'jobs.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = OpenAI(api_key=os.environ['GPT_API_KEY'], timeout=90, max_retries=0)
        budget = TrainingBudget(args.root / 'training-budget.sqlite3', 95)
        for plan_path in sorted(args.root.glob('plan-*.json')):
            plan = json.loads(plan_path.read_text())
            state_path = args.root / ('job-' + plan['model'] + '.json')
            state = submit(client,args.root,plan,budget) if args.submit else json.loads(state_path.read_text())
            print(json.dumps(collect(client,args.root,plan,budget,state),ensure_ascii=False),flush=True)
        print(json.dumps({'budget':budget.summary()}),flush=True)


if __name__ == '__main__':
    main()
