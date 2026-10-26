"""외부 API 없이 제목 분류 모델을 읽고 후보를 계산한다."""
from collections import Counter
import gzip
import json
import math
from pathlib import Path

from .text import family_key, NORMALIZATION_VERSION


class TitleClassifier:
    def __init__(self, path):
        with gzip.open(Path(path), 'rt', encoding='utf-8') as handle:
            self.data = json.load(handle)
        if self.data.get('schema_version') != 1:
            raise ValueError('Unsupported classifier schema')
        if self.data.get('normalization') != NORMALIZATION_VERSION:
            raise ValueError('Classifier normalization version mismatch')
        self.terms = self.data['terms']
        self.classes = self.data['classes']

    def predict(self, title):
        text = family_key(title)
        counts = Counter(text[start:start + size] for size in range(2, 6)
                         for start in range(max(0, len(text) - size + 1)))
        features = []
        for term, count in counts.items():
            if term in self.terms:
                index, idf = self.terms[term]
                features.append((index, (1 + math.log(count)) * idf))
        length = math.sqrt(sum(value * value for _, value in features)) or 1
        logits = [bias + sum(weights[index] * value / length for index, value in features)
                  for bias, weights in zip(self.data['intercepts'], self.data['weights'])]
        maximum = max(logits)
        exponentials = [math.exp(value - maximum) for value in logits]
        total = sum(exponentials)
        scores = sorted(zip(self.classes, (value / total for value in exponentials)), key=lambda pair: pair[1], reverse=True)
        category, score = scores[0]
        threshold = self.data.get('thresholds', {}).get(category, 1.1)
        accepted = score >= threshold and len(text) >= 8
        approved = bool(self.data.get('production_approved', False))
        return {'category': category, 'score': score, 'accepted': accepted,
                'candidates': [{'category': label, 'score': value} for label, value in scores[:3]],
                'matched_features': len(features), 'threshold': threshold,
                'label_provenance': 'legacy_weak_labels', 'production_approved': approved,
                'requires_review': not (accepted and approved)}
