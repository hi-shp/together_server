"""전체 이력의 유사 행사 후보를 찾는다. 후보를 중복 정답으로 간주하지 않는다."""

import argparse
import csv
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import random
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.notices.intelligence.text import family_key, identity
from together.notices.intelligence.events import Notice, compare_notices


def legacy_text(title):
    title = re.sub(r"【.*?】\s*", "", title)
    pattern = (
        r'\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]'
        if "신입" in title or "채용" in title
        else r'\[.*?\]|\(.*?\)|\]|[<>_\-*/@#$%^&*(),.?":{}|<>]'
    )
    return re.sub(pattern, "", title).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.neighbors import NearestNeighbors

    rows = []
    for number, line in enumerate(
        args.history.read_text(encoding="utf-8").splitlines(), 1
    ):
        timestamp, title = line.split("$", 1)
        rows.append((number, timestamp, title))
    texts = [family_key(row[2]) for row in rows]
    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(3, 5),
        min_df=2,
        max_features=70000,
        dtype=np.float32,
    )
    matrix = vectorizer.fit_transform(texts)
    nearest = NearestNeighbors(
        n_neighbors=5, metric="cosine", algorithm="brute", n_jobs=2
    ).fit(matrix)
    output = []
    seen = set()
    for start in range(0, len(rows), 200):
        distances, indices = nearest.kneighbors(matrix[start : start + 200])
        for offset, (scores, neighbors) in enumerate(zip(distances, indices)):
            index = start + offset
            for distance, other in zip(scores, neighbors):
                other = int(other)
                pair = tuple(sorted((index, other)))
                if index == other or pair in seen or distance > 0.5:
                    continue
                seen.add(pair)
                a, b = rows[pair[0]], rows[pair[1]]
                gap = (
                    abs(
                        (
                            datetime.fromisoformat(a[1]) - datetime.fromisoformat(b[1])
                        ).total_seconds()
                    )
                    / 86400
                )
                old_score = SequenceMatcher(
                    None, legacy_text(a[2]), legacy_text(b[2])
                ).ratio()
                result = compare_notices(Notice(a[2], a[1]), Notice(b[2], b[1]))
                output.append(
                    {
                        "left_id": a[0],
                        "right_id": b[0],
                        "left_time": a[1],
                        "right_time": b[1],
                        "left_title": a[2],
                        "right_title": b[2],
                        "observed_gap_days": round(gap, 3),
                        "retrieval_similarity": round(1 - float(distance), 5),
                        "legacy_similarity": round(old_score, 5),
                        "legacy_085_within10days": old_score >= 0.85 and gap <= 10,
                        "new_relation": result["relation"],
                        "reason": result["reason"],
                    }
                )
        if start % 2000 == 0:
            print(
                f"Compared neighbor candidates for {min(start + 200, len(rows))}/{len(rows)} titles",
                flush=True,
            )
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "event_pair_candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    rng = random.Random(20261026)
    samples = []
    buckets = {
        "same_core": [
            r
            for r in output
            if r["new_relation"] == "same_event" and r["observed_gap_days"] <= 14
        ],
        "year_guard": [r for r in output if r["reason"] == "years_conflict"],
        "round_guard": [
            r
            for r in output
            if r["reason"].startswith("round_") and r["observed_gap_days"] <= 14
        ],
        "hard_candidates": [
            r
            for r in output
            if r["new_relation"] == "possible_duplicate"
            and r["legacy_similarity"] < 0.85
            and r["observed_gap_days"] <= 10
        ],
        "high_legacy": [
            r
            for r in output
            if r["legacy_085_within10days"] and r["new_relation"] != "same_event"
        ],
    }
    for bucket, candidates in buckets.items():
        samples.extend(
            dict(row, sample_bucket=bucket)
            for row in rng.sample(candidates, min(15, len(candidates)))
        )
    samples = list({(row["left_id"], row["right_id"]): row for row in samples}.values())
    with (args.output / "event_pair_review.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(samples[0]))
        writer.writeheader()
        writer.writerows(samples)
    print(
        json.dumps(
            {
                "history_rows": len(rows),
                "candidate_pairs": len(output),
                "bucket_counts": {key: len(value) for key, value in buckets.items()},
                "review_pairs": len(samples),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
