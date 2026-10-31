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


def category_hint(
    title, keyword_categories, classifier=None, allow_provisional=False, *, body=""
):
    title = str(title)
    if re.search(r"지도교수\s*(?:상담사업|선정\s*일정)|학위논문\s*발표회", title) and not re.search(r"특강|워크숍|워크샵|작성법", title):
        return {"category": "해당없음", "method": "administrative_intent", "reason": "지도교수 배정·상담 또는 학위심사 안내"}
    if re.search(r"특강.*자료집.*(?:파일\s*)?공유", title):
        return {"category": "해당없음", "method": "administrative_intent", "reason": "기존 특강 자료 배포"}
    if re.search(r"설문\s*응답|의견\s*수렴", title) and not CONTEST.search(title):
        return {
            "category": "해당없음",
            "method": "administrative_intent",
            "reason": "설문·의견 수렴 안내",
        }
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
    if (
        re.search(r"학술\s*(?:회의|대회)", title)
        and re.search(r"논문|패널|발표", title)
        and re.search(r"모집|공모|call\s+for\s+papers", title, re.I)
        and re.search(r"발표\s*논문|개인\s*발표|패널\s*(?:구성|모집)|발표자", body)
        and not re.search(r"공모전|경진대회|경연|상금|시상|대상\s*수상", title + " " + body)
    ):
        return {
            "category": "교육/특강/프로그램",
            "method": "academic_presentation_recruitment",
            "reason": "학술회의 발표·패널 참여 모집 확인",
        }
    if (
        re.search(r"장학생.*(?:선정|선발)", title)
        and re.search(r"장학생\s*명단|선발\s*결과|최종\s*선발", body)
        and not re.search(r"신청\s*기간|접수\s*기간|지원\s*방법|신청\s*방법", body)
    ):
        return {
            "category": "해당없음",
            "method": "selection_result_body",
            "reason": "본문의 선정 결과·후속 서류 안내 확인",
        }
    if (
        CONTEST.search(title)
        and (re.search(
            r"(?:시각화|숏폼|솟폼|숏츠|영상|카드뉴스).{0,15}(?:부문|부분|제출|제작|출품)",
            body,
        ) or re.search(r"필수\s*(?:과제|산출물|제작물).{0,120}(?:영상|숏츠|카드뉴스)", body))
        and not re.search(
            r"소스\s*코드|코드\s*제출|프로그램\s*구현|SW\s*개발|소프트웨어\s*개발",
            body,
            re.I,
        )
    ):
        return {
            "category": "[공모전] 미술/디자인/건축",
            "method": "visual_submission_body",
            "reason": "본문의 시각물·영상 출품 형식 확인",
        }
    if re.search(
        r"봉사단.{0,40}(?:참가자|참여자|파견팀|봉사자|단원).{0,15}모집", title
    ):
        return {
            "category": "봉사활동",
            "method": "volunteer_recruitment_intent",
            "reason": "봉사단 참가자 모집",
        }
    if re.search(r"현장실습(?:학기제)?.{0,25}(?:학생|실습기관).{0,15}모집", title):
        return {
            "category": "취업 정보",
            "method": "internship_recruitment_intent",
            "reason": "현장실습 참여 모집",
        }
    if (
        re.search(r"(?:고사|시험).{0,10}(?:진행|감독|운영)요원.{0,10}모집", title)
        and re.search(r"수당|시급|급여", str(body))
        and not re.search(r"무급|수당\s*없|시급\s*없", str(body))
    ):
        return {
            "category": "취업 정보",
            "method": "paid_exam_staff_intent",
            "reason": "본문의 근무·수당 조건 확인",
        }
    if (
        re.search(r"(?:관리|운영)\s*멘토.{0,20}모집", title)
        and re.search(r"(?:일|하루)\s*\d+\s*시간|근무\s*시간", body)
        and re.search(r"\d[\d,.]*\s*(?:만)?원.{0,15}(?:수당|급여)|(?:수당|시급|일당|급여).{0,30}\d[\d,.]*\s*(?:만)?원", body)
        and not re.search(r"장학|봉사", title)
        and not re.search(r"무급|수당\s*없|시급\s*없|근로장학|봉사시간\s*(?:인정|부여)", body)
    ):
        return {
            "category": "취업 정보",
            "method": "paid_management_mentor",
            "reason": "관리 멘토의 근무시간·수당 조건 확인",
        }
    if (
        re.search(r"심화과정|교육과정|수강생|수업", title)
        and not re.search(r"모집|신청|접수|설명회|특강|강연", title)
        and re.search(r"등록된\s*학생|수강\s*중인\s*학생|기존\s*수강생", body)
        and re.search(r"각\s*반\s*게시판|수료\s*(?:요건|기준)|교과\s*과제", body)
        and re.search(r"보고서|과제", body)
        and "제출" in body
    ):
        return {
            "category": "해당없음",
            "method": "enrolled_course_assignment",
            "reason": "기존 교육과정 학생의 과제·보고서 제출 안내",
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
