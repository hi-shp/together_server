"""별도로 검토한 제목에 로컬 분류 경로를 대조하고 적용 범위를 기록한다."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_intelligence.model import TitleClassifier
from notice_intelligence.routing import category_hint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'env.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--approve-route', action='store_true')
    args = parser.parse_args()
    model = TitleClassifier(args.model)
    review_hash = hashlib.sha256(args.review.read_bytes()).hexdigest()
    if model.data['provenance'].get('reserved_review_sha256') != review_hash:
        raise ValueError('Review data must be reserved before training')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    with args.review.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    decisions, uncertain_decisions = [], []
    methods = Counter()
    for row in rows:
        hint = category_hint(row['title'], config['KEYWORD_CATEGORIES'], model, allow_provisional=True)
        row['local_category'] = hint['category'] or ''
        row['local_method'] = hint['method']
        methods[hint['method']] += 1
        if hint['category']:
            (decisions if row['reviewed_label'] else uncertain_decisions).append(row)
    errors = [row for row in decisions if row['local_category'] != row['reviewed_label']]
    n = len(decisions)
    p = (n - len(errors)) / n if n else 0
    z = 1.96
    lower = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n) if n else None
    approved = n >= 40 and not errors and not uncertain_decisions
    metrics = {'review_rows': len(rows), 'title_decidable_rows': sum(bool(row['reviewed_label']) for row in rows),
               'local_decisions': n, 'local_errors': len(errors), 'uncertain_rows_decided': len(uncertain_decisions),
               'decision_agreement': p, 'wilson95_lower': lower,
               'method_counts': dict(methods), 'approval_gate_passed': approved,
               'review_provenance': 'assistant_title_only_review_not_independent_human_gold',
               'development_sample_not_population_estimate': True, 'review_sha256': review_hash}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'router_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.output / 'router_review.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    if args.approve_route:
        if not approved:
            raise SystemExit('Route gate failed; model remains unapproved')
        model.data['routing_approved'] = True
        model.data['routing_review'] = metrics
        with gzip.open(args.model, 'wt', encoding='utf-8') as handle:
            json.dump(model.data, handle, ensure_ascii=False, separators=(',', ':'))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
