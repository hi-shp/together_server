import json
from pathlib import Path
import unittest

from tools.shorten_source_display_names import shorten_display_name, shorten_row_display_name


class DisplayNameShorteningTests(unittest.TestCase):
    def test_confirmed_examples(self):
        cases = {
            "학생지원시스템(공지사항)": "학생지원시스템(공지)",
            "고분자공학과(취업정보)": "고분자공학과(취업)",
            "조선해양공학과(학부공지사항)": "조선해양공학과(학부)",
            "화공생명공학부(취업 및 장학)": "화공생명공학부(취업·장학)",
            "산업공학과(게시판·공지사항)": "산업공학과(공지)",
            "도시공학과(대학원과정·일반대학원·공지사항)": "도시공학과(일반대학원)",
            "Advanced Structures and Materials Laboratory(Notice)": "Advanced Structures and Materials Laboratory(공지)",
            "Space Propulsion Laboratory(News & Event)": "Space Propulsion Laboratory(소식·행사)",
            "조선해양공학과(동문회 및 장학회·부산대학교 조선해양공학과동문장학재단·공지사항)": "조선해양공학과(동문·장학재단 공지)",
        }
        for before, after in cases.items():
            with self.subTest(before=before):
                self.assertEqual(shorten_display_name(before), after)

    def test_meaningful_board_distinctions_remain(self):
        self.assertEqual(shorten_display_name("전자공학과(취업공지)"), "전자공학과(취업)")
        self.assertEqual(shorten_display_name("연구소(학술행사)"), "연구소(학술행사)")
        self.assertEqual(shorten_display_name("학과(자료실·공지사항)"), "학과(자료·공지)")

    def test_rewrite_is_idempotent(self):
        name = "건축공학과(대학원 공지사항)"
        once = shorten_display_name(name)
        self.assertEqual(shorten_display_name(once), once)

    def test_compound_and_nested_notice_labels(self):
        cases = {
            "사범대학 화학교육과(대학원·교육대학원·공지사항)": "사범대학 화학교육과(대학원·교육대학원)",
            "혁신융합형 물리인재 교육연구단(자치회 공지사항)": "혁신융합형 물리인재 교육연구단(자치회)",
            "구강점막면역 연구실(공지사항(Announcements))": "구강점막면역 연구실(공지)",
        }
        for before, after in cases.items():
            with self.subTest(before=before):
                self.assertEqual(shorten_display_name(before), after)

    def test_common_english_labels_and_known_collision(self):
        cases = {
            "School of Materials Science and Engineering(News&Event)": "School of Materials Science and Engineering(소식·행사)",
            "Education Center(Seminar)": "Education Center(세미나)",
            "Computational Lab(General Notice)": "Computational Lab(공지)",
            "Experimental?Thermo-Fluids Mechanics and Energy?Systems?Lab.(Notice)": "Experimental Thermo-Fluids Mechanics and Energy Systems Lab.(공지)",
        }
        for before, after in cases.items():
            with self.subTest(before=before):
                self.assertEqual(shorten_display_name(before), after)
        self.assertEqual(
            shorten_row_display_name({
                "display_name": "나노반도체공정장비계약학과(공지)",
                "identity_department": "나노반도체공정장비계약학과(장비이용안내)",
            }),
            "나노반도체공정장비계약학과(장비 공지)",
        )

    def test_nested_course_and_generic_board_labels(self):
        cases = {
            "윤리교육과(공지사항(얼바람말글창))": "윤리교육과(얼바람말글창 공지)",
            "경영대학원(석사과정(MBA)·공지사항)": "경영대학원(MBA 공지)",
            "심리학과(공지&자료게시판)": "심리학과(공지·자료)",
            "지질환경과학과(학과게시판)": "지질환경과학과(자유)",
            "사범대 영어교육과(학부게시판)": "사범대 영어교육과(학부)",
            "기계기술연구원(전공·게시판·공지사항)": "기계기술연구원(전공·공지)",
        }
        for before, after in cases.items():
            with self.subTest(before=before):
                self.assertEqual(shorten_display_name(before), after)

    def test_committed_catalog_is_stable_and_keeps_distinct_boards(self):
        path = Path(__file__).resolve().parents[1] / "together/crawler/source_catalog.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1016)
        self.assertEqual(
            [row["display_name"] for row in rows],
            [shorten_row_display_name(row) for row in rows],
        )
        for row in rows:
            self.assertNotIn("공지사항", row["display_name"])
            self.assertNotIn("취업정보", row["display_name"])
            self.assertNotIn("장학정보", row["display_name"])
            self.assertNotIn("게시판", row["display_name"])
            self.assertNotIn("?", row["display_name"])
        names = {row["identity_department"]: row["display_name"] for row in rows}
        self.assertEqual(names["지질환경과학과"], "지질환경과학과(공지)")
        self.assertEqual(names["지질환경과학과(자유)"], "지질환경과학과(자유)")
        self.assertEqual(names["나노반도체공정장비계약학과"], "나노반도체공정장비계약학과(공지)")
        self.assertEqual(names["나노반도체공정장비계약학과(장비이용안내)"], "나노반도체공정장비계약학과(장비 공지)")


if __name__ == "__main__":
    unittest.main()
