"""활동 형식이 맞는 규칙과 검증된 로컬 모델만 먼저 사용한다."""

from functools import lru_cache
import os
from pathlib import Path
import re

from .model import TitleClassifier

CONTEST = re.compile(
    r"공모전|경진대회|콘테스트|해커톤|메이커톤|아이디어톤|경연대회|글쓰기\s*대회|coding challenge",
    re.I,
)
TEACHING = re.compile(
    r"특강|강연|세미나|캠프|아카데미|워크숍|워크샵|교육생\s*모집|수강생\s*모집|교육과정|프로그램"
)
SUPPORTER = re.compile(
    r"서포터즈|홍보대사|기자단|앰배서더|프렌즈|정책\s*네트워크|모니터링|심사단|체험단"
)
ADMIN = re.compile(
    r"\[설문\]|설문조사.{0,20}(?:실시|참여|안내|협조)|지급일\s*안내|장학사정\s*결과|선발\s*결과|선정\s*결과|합격자\s*(?:발표|공고)|결과\s*공고|수상자\s*(?:발표|안내)|교과목.*(?:증원|이수\s*알림)|수강인원\s*증원|계절(?:학기|수업).*교류\s*수학|이수인증신청|학위청구|졸업사정|내규|수강정정|사후평가회|지도교수\s*배정|설문조사\s*참여|현장실습\s*신청|현장실습학기제\s*참여\s*및\s*수강신청"
)


@lru_cache(maxsize=2)
def classifier_from_path(path, modified):
    return TitleClassifier(path)


def configured_classifier(base_dir):
    path = Path(
        os.getenv("NOTICE_CLASSIFIER_PATH")
        or str(Path(base_dir) / ".runtime/intelligence/category-model.json.gz")
    )
    try:
        return classifier_from_path(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError):
        return None


def _intent_matches(title, category):
    contest = bool(CONTEST.search(title))
    teaching = bool(TEACHING.search(title))
    if ADMIN.search(title) and not re.search(r"특강|강연|워크숍|워크샵", title):
        return category == "해당없음"
    if category.startswith("[공모전]"):
        return contest and not (teaching and re.search(r"특강|강연|세미나", title))
    if category == "교육/특강/프로그램":
        return teaching and not contest
    if category == "서포터즈":
        return bool(SUPPORTER.search(title))
    if category == "장학금":
        return bool(re.search(r"장학", title)) and not contest
    if category == "봉사활동":
        return bool(
            re.search(
                r"봉사자|자원봉사|교육봉사|(?:멘토|튜터)\s*(?:추가\s*)?모집", title
            )
        )
    if category == "취업 정보":
        return bool(
            re.search(r"채용|공채|구인|시간강사|인턴|현장실습", title, re.I)
        ) and not (teaching and not re.search(r"채용설명회|채용상담회", title))
    return False


def category_hint(title, keyword_categories, classifier=None, allow_provisional=False):
    title = str(title)
    if re.search(r"SSAFY|삼성청년.*아카데미", title, re.I) and not re.search(
        r"강사|직원|조교|교수", title
    ):
        return {
            "category": "교육/특강/프로그램",
            "method": "education_intent",
            "reason": "SSAFY 교육과정 안내",
        }
    if ADMIN.search(title) and not re.search(r"특강|강연|워크숍|워크샵", title):
        return {
            "category": "해당없음",
            "method": "administrative_intent",
            "reason": "행정·결과 안내",
        }
    prediction = classifier.predict(title) if classifier else None
    usable_model = bool(
        prediction
        and (classifier.data.get("routing_approved", False) or allow_provisional)
    )
    candidates = {
        label
        for word, label in keyword_categories.items()
        if word in title and _intent_matches(title, label)
    }
    if usable_model and len(candidates) == 1:
        category = next(iter(candidates))
        if (
            prediction["category"] == category
            and prediction["accepted"]
            and prediction["score"] >= 0.85
        ):
            return {
                "category": category,
                "method": "intent_and_model_agreement",
                "prediction": prediction,
            }
    if (
        usable_model
        and prediction["accepted"]
        and prediction["score"] >= 0.90
        and _intent_matches(title, prediction["category"])
    ):
        return {
            "category": prediction["category"],
            "method": "selective_local_model",
            "prediction": prediction,
        }
    return {
        "category": None,
        "method": "needs_context",
        "prediction": prediction,
        "reason": "로컬 근거가 부족하거나 활동 형식이 충돌함",
    }
