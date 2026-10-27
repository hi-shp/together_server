"""제목 표기를 정리하고 행사 회차 정보를 추출한다."""

import html
import re
import unicodedata

NORMALIZATION_VERSION = "family_key_v2"

YEARS = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
ROUNDS = re.compile(
    r"(?<!\d)(?:제\s*)?(\d{1,3})\s*(회|기|차)(?:(?![가-힣])|(?=모집|참가|선발|지원|신청|개최|강연|교육|안내|공고|회차))"
)
SEMESTERS = re.compile(r"([12])\s*학기|하계|동계|여름학기|겨울학기")
DEADLINE = re.compile(r"[\(\[][^\)\]]*(?:마감|까지|~|∼)[^\)\]]*[\)\]]")
DECORATION = re.compile(r"^[\s★☆⭐※✔✓▶▷◆◇■□●○!]+")
ADMIN_PREFIX = re.compile(
    r"^\[(?:공지|안내|필독|중요|새글|재공지|학부|대학원|모집|홍보)\]\s*"
)


def normalize_title(title):
    title = unicodedata.normalize("NFKC", html.unescape(str(title)))
    title = "".join(char for char in title if unicodedata.category(char) != "Cf")
    return " ".join(title.split()).strip()


def identity(title):
    title = normalize_title(title)
    return {
        "years": sorted(set(YEARS.findall(title))),
        "rounds": sorted(
            set(f"{int(number)}{unit}" for number, unit in ROUNDS.findall(title))
        ),
        "semesters": sorted(set(re.findall(r"([12])\s*학기", title))),
        "seasons": sorted(
            {
                "summer" if match.group(0) in ("하계", "여름학기") else "winter"
                for match in re.finditer(r"하계|동계|여름학기|겨울학기", title)
            }
        ),
    }


def clean_display_title(title):
    title = DECORATION.sub("", normalize_title(title))
    pieces = []
    index = 0
    while index < len(title):
        if title[index] not in "([":
            pieces.append(title[index])
            index += 1
            continue
        start = index
        stack = [")" if title[index] == "(" else "]"]
        index += 1
        while index < len(title) and stack:
            if title[index] in "([":
                stack.append(")" if title[index] == "(" else "]")
            elif title[index] == stack[-1]:
                stack.pop()
            index += 1
        chunk = title[start:index]
        dated = bool(re.search(r"\d{1,2}\s*(?:[./]\s*\d{1,2}|월)", chunk))
        if stack or not (dated and re.search(r"마감|까지|~|∼", chunk)):
            pieces.append(chunk)
    title = "".join(pieces)
    while ADMIN_PREFIX.match(title):
        title = ADMIN_PREFIX.sub("", title)
    return " ".join(title.split()).strip(" -_")


def exact_key(title):
    return "".join(
        char.lower() for char in clean_display_title(title) if char.isalnum()
    )


def family_key(title):
    title = YEARS.sub("연도", clean_display_title(title))
    title = re.sub(r"(?<!\d)\d{1,2}[./월]\s*\d{1,2}(?:[./일])?", "날짜", title)
    return "".join(char.lower() for char in title if char.isalnum())


def normalize_label(label, categories):
    value = str(label).strip()
    if value in categories:
        return value
    if value.startswith("[") and value.endswith("]") and value[1:-1] in categories:
        return value[1:-1]
    aliases = {
        "채용 정보": "취업 정보",
        "취업정보": "취업 정보",
        "미술/디자인/건축": "[공모전] 미술/디자인/건축",
        "문학/수기/에세이": "[공모전] 문학/수기/에세이",
    }
    return aliases.get(value)


def occurrence_conflict(left, right):
    a, b = identity(left), identity(right)
    for key in ("years", "semesters", "seasons"):
        if a[key] and b[key] and not set(a[key]) & set(b[key]):
            return key
    for unit in ("회", "기", "차"):
        first = {value for value in a["rounds"] if value.endswith(unit)}
        second = {value for value in b["rounds"] if value.endswith(unit)}
        if first and second and not first & second:
            return f"round_{unit}"
    return None


def requires_department_evidence(title):
    # 제목 이력에는 담당 학과와 모집 인원이 없어 동일 제목만으로 합치지 않는다.
    return bool(
        re.search(
            r"근로\s*장학|교내\s*장학|학과\s*장학|조교|수강신청|수강정정|졸업논문|학위청구|지도교수|학점인정|졸업사정",
            normalize_title(title),
        )
    )
