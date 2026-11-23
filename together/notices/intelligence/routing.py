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
    confirmed = verified_activity_category(title, body)
    if confirmed:
        return confirmed
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
        # 모집 계획의 '최종 선발 일정'이나 제출 양식의 '명단'은 결과가 아니다.
        and re.search(
            r"장학생\s*명단\s*[:：]\s*(?:개별|붙임|첨부|아래|다음)"
            r"|(?:선정|선발)(?:된|되었|되신|하였)"
            r"|(?:선정|선발)\s*결과\s*(?:를|는|[:：]|안내|공지|통보)",
            body,
        )
        and not re.search(
            r"(?:신청|접수|지원|추천)\s*(?:기간|기한|마감|방법|대상|자격|서류|요청)"
            r"|(?:신청서|지원서)\s*(?:제출|접수)",
            body,
        )
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


def verified_activity_category(title, body):
    """본문으로 확인한 활동 형식만 강제한다. 단어 하나로 결과를 뒤집지 않는다."""

    # 교직 이수자의 교과목·학점 인정 절차는 새 봉사자 참여 모집과 구분한다.
    if (re.search(r'교육봉사활동', str(title))
            and re.search(r'이수|학점\s*인정|시행\s*안내', str(title))
            and not re.search(r'모집|봉사자|자원봉사', str(title))
            and re.search(r'교원자격|교직|XA4000347|교과목번호', str(body))):
        return {'category': '해당없음', 'method': 'curricular_volunteer_credit',
                'reason': '교원자격 취득을 위한 교육봉사 교과목 이수·학점 인정 절차이며 새 봉사자 모집이 아님'}
    t = re.sub(r'\s+', '', str(title))
    b = re.sub(r'\s+', '', str(body or ''))
    category, reason = None, None
    if re.search(r'재능이보여|전공봉사단', t) and '모집' in t and re.search(r'\d+시간이상봉사활동진행', b):
        category, reason = '봉사활동', '파견 기관에서 정해진 시간 이상 봉사를 수행하는 전공·예술 봉사단 모집'
    elif '경진대회' in t and all(v in b for v in ('코딩캠프', '딥러닝접근', '프로젝트예시')):
        category, reason = '[공모전] 공학/IT/SW', '딥러닝 모델·코드를 구현해 제출하고 평가받는 기술 경진대회'
    elif '현장실습' in t and re.search(r'모집|프로그램운영', t) and re.search(r'기업부담실습지원비|실습비[\d,]+원/월', b) and re.search(r'직무수행|산재보험', b):
        category, reason = '취업 정보', '기업·연구기관에서 직무를 수행하며 월 실습비를 받는 현장실습 모집'
    elif re.search(r'투표일정변경', t) and re.search(r'작품이접수되었습니다|접수가마감', b) and re.search(r'투표시작시|투표방법.*별도안내', b):
        category, reason = '해당없음', '작품 접수 후 심사·투표 일정 변경이며 새 응모나 투표 신청 안내가 아님'
    elif re.search(r'PASS상담', t, re.I) and re.search(r'미상담|미신청', b) and '성적열람불가' in b:
        category, reason = '해당없음', '성적 열람 요건으로 지정된 기존 학생의 필수 교수 상담'
    elif re.search(r'마이크로디그리.*취득.*안내', t) and re.search(r'이수교과목|취득자에한해|이수학점', b) and not re.search(r'모집|참가신청|설명회|특강', t):
        category, reason = '해당없음', '새 프로그램 참여 모집이 아닌 학위 이수·취득 요건 안내'
    elif re.search(r'학술지|논문지|논총', t) and re.search(r'논문투고|투고논문|원고모집', b) and not re.search(r'논문경진|논문공모전|우수논문상|시상|상금', t+b):
        category, reason = '해당없음', '학술지 게재용 논문 투고이며 경쟁 공모전이 아님'
    elif re.search(r'학생심사단.*모집', t) and re.search(r'우수작선정|심사대상|심사기준', b):
        category, reason = '서포터즈', '작품 응모·봉사가 아닌 학생 심사단 참여 역할 모집'
    elif re.search(r'합격자.*공고|합격자.*발표', t) and re.search(r'합격자|면접전형', b) and not re.search(r'추가모집|추가채용|재모집|지원자모집', t):
        category, reason = '해당없음', '새 채용 지원 모집이 아닌 전형 합격 결과·후속 일정 공고'
    elif re.search(r'고사장|수험생안내|선발.*전형진행안내', t) and re.search(r'수험표|입실|합격자|수험생', b) and not re.search(r'모집|접수안내|신규신청', t):
        category, reason = '해당없음', '이미 신청한 응시자의 고사장·전형 진행 안내'
    elif re.search(r'배드민턴|테니스|탁구|골든벨', t) and re.search(r'참가대상|신청기한|참가신청', b) and not re.search(r'결과|수상|후기', t):
        category, reason = '교육/특강/프로그램', '학생이 신청해 참여하는 체육·퀴즈 행사'
    if category:
        return {'category': category, 'method': 'verified_activity_intent', 'reason': reason}
    return None
