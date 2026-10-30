"""공지 사이트 표시 이름의 반복적인 괄호 표현을 짧게 정리한다."""
import argparse
import json
from pathlib import Path
import re


ENGLISH_EXACT = {
    "notice": "공지",
    "notices": "공지",
    "notice board": "공지",
    "announcement": "공지",
    "announcements": "공지",
    "news": "소식",
    "recent news": "소식",
    "lab news": "연구실 소식",
    "lab. news": "연구실 소식",
    "bio news": "Bio 소식",
    "what's notice": "공지",
    "notice news": "소식·공지",
    "news & notice": "소식·공지",
    "notice & news": "소식·공지",
    "notice & events": "공지·행사",
    "news & event": "소식·행사",
    "news & issue": "소식·이슈",
    "newsletter": "뉴스레터",
    "news letter": "뉴스레터",
    "event reports": "행사후기",
    "event": "행사",
    "seminar": "세미나",
    "events/conference": "행사·학술",
    "general notice": "공지",
    "news&event": "소식·행사",
}


FULL_NAME_EXACT = {
    "윤리교육과(공지사항(얼바람말글창))": "윤리교육과(얼바람말글창 공지)",
    "경영대학원(석사과정(MBA)·공지사항)": "경영대학원(MBA 공지)",
    "경영대학원(최고경영자과정(AMP)·공지사항)": "경영대학원(AMP 공지)",
    "행정대학원(석사과정(MPA)·공지사항)": "행정대학원(MPA 공지)",
    "행정대학원(최고관리자과정(APMP)·공지사항)": "행정대학원(APMP 공지)",
}


def shorten_parenthetical(content):
    value = " ".join(content.split()).strip()
    if not value:
        return value
    english = ENGLISH_EXACT.get(value.casefold())
    if english:
        return english

    replacements = (
        (r"학부\s*[·/]?\s*학부\s*공지(?:사항)?", "학부"),
        (r"학부\s*공지(?:사항)?", "학부"),
        (r"대학원\s*공지(?:사항)?", "대학원"),
        (r"교육대학원\s*[·/]?\s*공지(?:사항)?", "교육대학원"),
        (r"환경대학원\s*공지(?:사항)?", "환경대학원"),
        (r"학과\s*공지(?:사항)?", "공지"),
        (r"연구실\s*공지(?:사항)?", "공지"),
        (r"장학\s*(?:공지|정보)(?:사항)?(?:\s*게시판)?", "장학"),
        (r"장학금\s*안내", "장학"),
        (r"취업\s*(?:공지|정보|게시판|공고)(?:사항|게시판)?", "취업"),
        (r"채용\s*(?:공고|정보|게시판)", "채용"),
        (r"입학관련\s*공지(?:사항)?", "입학"),
        (r"입학\s*공지(?:사항)?", "입학"),
        (r"학생회\s*공지(?:사항)?", "학생회"),
        (r"자치회\s*공지(?:사항)?", "자치회"),
        (r"공지\s*사항", "공지"),
        (r"행사\s*(?:안내|공지)", "행사"),
        (r"공지\s*&\s*자료게시판", "공지·자료"),
        (r"학과게시판", "자유"),
        (r"장학(?:회)?\s*게시판", "장학"),
        (r"학부게시판", "학부"),
        (r"대학원게시판", "대학원"),
        (r"취업\s*[,·/]\s*과외게시판", "취업·과외"),
        (r"·게시판·공지", "·공지"),
    )
    for pattern, target in replacements:
        value = re.sub(pattern, target, value, flags=re.I)

    value = value.replace(" 및 ", "·")
    exact = {
        "자유게시판": "자유",
        "공지게시판": "공지",
        "게시판·공지": "공지",
        "알림·공지": "공지",
        "커뮤니티·공지": "커뮤니티·공지",
        "정보광장·공지": "정보광장·공지",
        "자료실·공지": "자료·공지",
        "전체공지": "전체",
        "알림사항": "알림",
        "취업·장학": "취업·장학",
        "채용·진학정보": "채용·진학",
        "입학·학사": "입학·학사",
        "언론보도·새소식": "언론·소식",
        "행사·세미나 공지": "행사·세미나",
        "대학원과정·일반대학원·공지": "일반대학원",
        "대학원과정·환경대학원·공지": "환경대학원",
        "대학원·일반대학원·공지": "일반대학원",
        "동문회·장학회·부산대학교 조선해양공학과동문장학재단·공지": "동문·장학재단 공지",
    }
    value = exact.get(value, value)

    pieces = [piece.strip() for piece in value.split("·") if piece.strip()]
    compact = []
    for piece in pieces:
        if piece not in compact:
            compact.append(piece)
    if len(compact) > 1 and compact[-1] == "공지" and compact[-2] in {
        "학부", "대학원", "교육대학원", "일반대학원", "환경대학원",
        "학생", "교수", "학생회", "자치회", "입학", "취업", "장학",
        "채용", "학사", "행사", "세미나",
    }:
        compact.pop()
    return "·".join(compact)


def shorten_display_name(name):
    original = " ".join(str(name).replace("?", " ").split()).replace(" (", "(")
    if original in FULL_NAME_EXACT:
        return FULL_NAME_EXACT[original]
    value = original
    pattern = re.compile(r"\(([^()]*)\)")
    for _ in range(6):
        changed = pattern.sub(lambda match: f"({shorten_parenthetical(match.group(1))})", value)
        if changed == value:
            break
        value = changed
    return value.replace("(공지사항(공지))", "(공지)")


def shorten_row_display_name(row):
    value = shorten_display_name(row["display_name"])
    if row.get("identity_department") == "나노반도체공정장비계약학과(장비이용안내)":
        return "나노반도체공정장비계약학과(장비 공지)"
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    rows = json.loads(args.catalog.read_text(encoding="utf-8"))
    changes = []
    for row in rows:
        previous = row["display_name"]
        current = shorten_row_display_name(row)
        if current != previous:
            changes.append({"order": row["order"], "before": previous, "after": current})
            row["display_name"] = current
    if args.write:
        with args.catalog.open("w", encoding="utf-8", newline="\r\n") as stream:
            stream.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"rows": len(rows), "changed": len(changes), "changes": changes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
