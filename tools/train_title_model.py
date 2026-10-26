"""이력에서 로컬 분류 후보를 학습하고 독립된 행사 묶음으로 평가한다."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_intelligence.text import family_key, NORMALIZATION_VERSION


def weak_label_issue(title, label):
    contest = bool(re.search(r'공모전|경진대회|콘테스트|해커톤|메이커톤|아이디어톤|글쓰기대회|글쓰기 대회|경연대회|경연 대회|테니스대회|선수 모집|coding challenge', title, re.I))
    teaching = bool(re.search(r'특강|세미나|강연|멘토링|워크숍|워크샵|수강생|실습과정|실습 과정', title))
    results = bool(re.search(r'결과\s*(?:공고|발표|안내)|수상자|합격자|선발결과|선발 결과|우수발표논문상 수상|회장상 수상', title))
    if label.startswith('[공모전]') and (results or (teaching and not contest)):
        return 'competition_intent_mismatch'
    if label.startswith('[공모전]') and not contest:
        return 'competition_intent_not_evident_from_title'
    if label.startswith('[공모전]') and re.search(r'수요조사|이수 알림|평가 순서|참가팀 소개', title):
        return 'administrative_or_news_notice'
    if label == '교육/특강/프로그램' and contest and not teaching:
        return 'explicit_competition_labeled_as_teaching'
    administration = bool(re.search(r'지급일|수강정정|수강인원|학위청구|교과목.*이수|계절(?:학기|수업).*교류\s*수학|이수인증신청', title))
    if administration and label != '해당없음' and not teaching:
        return 'administrative_intent_mismatch'
    if label == '서포터즈' and not re.search(r'서포터즈|홍보대사|기자단|프렌즈|파트너즈|앰배서더|모니터링|정책\s*네트워크|심사단|체험단', title):
        return 'supporter_role_not_evident_from_title'
    if label == '장학금' and (contest or not re.search(r'장학|장학생|재정지원', title)):
        return 'scholarship_intent_mismatch'
    active_opportunity = bool(re.search(r'특강|강연|교육생 모집|수강생 모집|장학생 모집|공개채용|참가자 모집|참가팀 모집', title))
    if label == '해당없음' and (contest or active_opportunity) and not (administration or results):
        return 'active_opportunity_labeled_as_excluded'
    return ''


def main():
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report, accuracy_score, f1_score
    from sklearn.model_selection import GroupShuffleSplit
    import sklearn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with args.data.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    with args.review.open(encoding='utf-8-sig', newline='') as handle:
        reserved = {family_key(row['title']) for row in csv.DictReader(handle)}
    excluded, unique = [], {}
    for row in rows:
        issue = weak_label_issue(row['title'], row['label'])
        if issue:
            excluded.append(dict(row, reason=issue))
        elif family_key(row['title']) not in reserved:
            unique.setdefault((row['key'], row['label']), row)
    rows = list(unique.values())
    x = np.array([family_key(row['title']) for row in rows])
    y = np.array([row['label'] for row in rows])
    groups = np.array([family_key(row['title']) for row in rows])
    train_cal, test = next(GroupShuffleSplit(n_splits=1, test_size=.2, random_state=20261026).split(x, y, groups))
    local_train, local_cal = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=20261027).split(
        x[train_cal], y[train_cal], groups[train_cal]))
    train, cal = train_cal[local_train], train_cal[local_cal]
    assert not set(groups[train]) & set(groups[test])
    assert not set(groups[train]) & set(groups[cal])
    assert not set(groups[cal]) & set(groups[test])
    vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 5), min_df=2,
                                 max_features=50000, sublinear_tf=True, lowercase=False)
    started = time.perf_counter()
    matrix = vectorizer.fit_transform(x[train])
    model = LogisticRegression(C=3.0, solver='lbfgs', max_iter=700, class_weight='balanced', random_state=20261026)
    model.fit(matrix, y[train])
    cal_probs = model.predict_proba(vectorizer.transform(x[cal]))
    test_probs = model.predict_proba(vectorizer.transform(x[test]))
    classes = model.classes_.tolist()
    thresholds = {}
    for number, label in enumerate(classes):
        threshold = 1.1
        for value in np.arange(.80, .995, .005):
            selected = (cal_probs.argmax(axis=1) == number) & (cal_probs[:, number] >= value)
            if selected.sum() >= 20 and (y[cal][selected] == label).mean() >= .97:
                threshold = round(float(value), 4)
                break
        thresholds[label] = threshold
    predicted = np.array(classes)[test_probs.argmax(axis=1)]
    accepted = np.array([score >= thresholds[label] for score, label in zip(test_probs.max(axis=1), predicted)])
    model_data = {
        'schema_version': 1, 'normalization': NORMALIZATION_VERSION, 'classes': classes,
        'production_approved': False, 'routing_approved': False,
        'terms': {term: [int(index), float(vectorizer.idf_[index])] for term, index in vectorizer.vocabulary_.items()},
        'weights': model.coef_.tolist(), 'intercepts': model.intercept_.tolist(), 'thresholds': thresholds,
        'provenance': {'label_type': 'legacy_weak_labels', 'source_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest(),
                       'sklearn': sklearn.__version__, 'training_rows': len(train), 'reserved_review_families': len(reserved),
                       'reserved_review_sha256': hashlib.sha256(args.review.read_bytes()).hexdigest()},
    }
    with gzip.open(args.output / 'category-model.json.gz', 'wt', encoding='utf-8') as handle:
        json.dump(model_data, handle, ensure_ascii=False, separators=(',', ':'))
    from notice_intelligence.model import TitleClassifier
    exported = TitleClassifier(args.output / 'category-model.json.gz')
    for index in test[:100]:
        result = exported.predict(rows[index]['title'])
        original = model.predict_proba(vectorizer.transform([x[index]]))[0]
        assert result['category'] == classes[int(original.argmax())]
        assert abs(result['score'] - float(original.max())) < 1e-8
    metrics = {
        'target': 'agreement_with_legacy_labels_not_true_accuracy', 'rows': len(rows),
        'train': len(train), 'calibration': len(cal), 'test': len(test), 'features': matrix.shape[1],
        'excluded_weak_label_rows': len(excluded), 'class_counts': dict(Counter(y.tolist())),
        'test_agreement': float(accuracy_score(y[test], predicted)),
        'macro_f1_against_weak_labels': float(f1_score(y[test], predicted, average='macro')),
        'accepted_coverage': float(accepted.mean()),
        'accepted_agreement': float((y[test][accepted] == predicted[accepted]).mean()) if accepted.any() else None,
        'thresholds': thresholds, 'training_seconds': time.perf_counter() - started,
        'classification_report': classification_report(y[test], predicted, output_dict=True, zero_division=0),
        'export_validation_rows': min(100, len(test)),
    }
    (args.output / 'training_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.output / 'excluded_weak_labels.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(excluded[0]) if excluded else ['reason'])
        writer.writeheader()
        writer.writerows(excluded)
    print(json.dumps({key: value for key, value in metrics.items() if key != 'classification_report'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
