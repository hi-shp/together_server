"""운영 원본의 사본에서 전수 검토 입력과 고정 평가 분할을 만든다. 유료 호출 없음."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from together.notices.intelligence.text import family_key
from together.notices.intelligence.prompts import CATEGORY_SYSTEM
from together.notices.intelligence.policy import CATEGORIES


def scrub(text):
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[연락처 생략]", str(text or ""))
    return re.sub(r"(?<!\d)(?:01[016789]|051)[- ]?\d{3,4}[- ]?\d{4}(?!\d)", "[연락처 생략]", text)


def prepare(root):
    history = json.loads((root / "legacy-history.json").read_text())
    labels = json.loads((root / "legacy-labels.json").read_text())
    observations = json.loads((root / "observations.json").read_text())
    records, malformed = {}, []

    def add(title, year, body="", source="", origin=None):
        # 과거 라벨을 모델 입력에 넣지 않는다. 다른 연도와 다른 본문은 합치지 않는다.
        key = hashlib.sha256(json.dumps([title, year, body], ensure_ascii=False).encode()).hexdigest()
        family = family_key(title)
        split_number = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 100
        split = "test" if split_number < 15 else "calibration" if split_number < 30 else "train"
        item = records.setdefault(key, {"id":key[:20], "title":scrub(title), "year":year, "body":scrub(body), "source_url":source,
                                      "split":split, "family_sha256":hashlib.sha256(family.encode()).hexdigest(), "origins":[]})
        if origin is not None: item["origins"].append(origin)

    for i,line in enumerate(history):
        try:
            stamp,title = line.split("$",1)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",stamp): raise ValueError()
            add(title,stamp[:4],origin=f"history:{i+1}")
        except ValueError: malformed.append(i+1)
    existing = {}
    for item in records.values():
        existing.setdefault(item["title"], []).append(item)
    for i,row in enumerate(labels):
        title=row.get("제목","")
        if title:
            matches = existing.get(scrub(title), [])
            if matches:
                # 연도 없는 과거 라벨은 해당 제목의 검토 항목들과 연결만 한다.
                # 어느 연도의 정답인지 추측하거나 모델 입력에 라벨을 넣지 않는다.
                for item in matches:
                    item["origins"].append(f"labels:{i+1}")
            else:
                add(title,"unknown",origin=f"labels:{i+1}")
    for i,row in enumerate(observations):
        add(row["title"],row["observed_at"][:4],row.get("body_excerpt", ""),row["source_url"],f"observation:{i+1}")
    items=sorted(records.values(),key=lambda r:r["id"])
    path=root/"review-corpus.jsonl"
    path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in items),encoding="utf-8")
    path.chmod(0o600)
    origins={origin for item in items for origin in item["origins"]}
    manifest={"records":len(items),"origins":sum(len(r["origins"]) for r in items),"unique_origins":len(origins),
              "input_rows":{"history":len(history),"labels":len(labels),"observations":len(observations)},
              "covered_origins":dict(Counter(origin.split(':',1)[0] for origin in origins)),
              "body_records":sum(bool(r["body"]) for r in items),
              "splits":dict(Counter(r["split"] for r in items)),"malformed_history_rows":malformed,
              "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"labels_are":"independent_model_review_candidates_not_human_ground_truth",
              "production_changed":False,"training_total_cap_usd":95}
    (root/"review-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return items,manifest


def category_batch(rows, model):
    schema={"type":"object","properties":{"rows":{"type":"array","items":{"type":"object","properties":{
        "id":{"type":"string"},"category":{"type":"string","enum":list(CATEGORIES)+["판단보류"]},
        "needs_body":{"type":"boolean"},"reason":{"type":"string"}},"required":["id","category","needs_body","reason"],"additionalProperties":False}}},
        "required":["rows"],"additionalProperties":False}
    return {"model":model,"messages":[{"role":"system","content":CATEGORY_SYSTEM+"\n입력 records 각각을 독립적으로 판단한다. 모든 id를 정확히 한 번 반환한다. reason은 40자 이내로 쓴다. 본문이 없어 확정할 수 없으면 판단보류로 남긴다. 교직 이수의 필수 실습, 기존 교과의 보고서/과제/논문발표/지도교수 상담/교육이수 관리는 새 참여 프로그램이 아니다. 공개 신청하는 별도 특강과 구분한다. 제목만 있고 필수 이수인지 자율 프로그램인지 분명하지 않으면 판단보류와 needs_body=true로 남긴다. 지급받는 돈이 있다는 이유만으로 장학금이나 유급근로로 단정하지 말고 봉사/교육/고용/장학의 실제 목적이 불명확하면 본문을 요청한다. year는 관측 연도이며 원문의 행사 연도보다 우선하지 않는다."},
        {"role":"user","content":json.dumps({"records":[{k:r[k] for k in ('id','title','year','body')} for r in rows]},ensure_ascii=False)}],
        "response_format":{"type":"json_schema","json_schema":{"name":"review_notice_batch","strict":True,"schema":schema}},
        "reasoning_effort":"low","max_completion_tokens":min(12000,1000+len(rows)*150)}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",required=True,type=Path)
    args=parser.parse_args()
    _,manifest=prepare(args.root)
    print(json.dumps(manifest,ensure_ascii=False))
