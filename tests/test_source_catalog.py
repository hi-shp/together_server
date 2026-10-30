import json
from pathlib import Path
import unittest
from unittest.mock import patch

from together.crawler.catalog import (
    coalesce_pages,
    records,
    order_pages,
    set_display_ordinals,
)
from together.crawler.pages import AnnouncementPage


class CatalogTests(unittest.TestCase):
    def test_official_labels_and_order_do_not_change_cursors_or_subscription_identity(self):
        rows = list(records().values())
        selected = [rows[10], rows[0], rows[1], rows[5]]
        pages = [AnnouncementPage(row['identity_department'], row['page_url'], '', 9876543 + index)
                 for index, row in enumerate(selected)]
        before = {id(page): (page.department, page.source_url, page.number) for page in pages}
        ordered = order_pages(pages)
        self.assertEqual([page.source_url for page in ordered[:2]], [rows[0]['page_url'], rows[1]['page_url']])
        for page in ordered:
            self.assertEqual((page.department, page.source_url, page.number), before[id(page)])
            self.assertNotIn('\n', page.display_name)
            self.assertNotIn('  ', page.display_name)

    def test_existing_same_address_subscription_rows_and_unknown_rows_are_retained(self):
        url = 'https://biz.pusan.ac.kr/biz/8941/subview.do'
        pages = [AnnouncementPage('경영학과(학부)', url, '', 400),
                 AnnouncementPage('경영학과(대학원)', url, '', 500),
                 AnnouncementPage('새 학과', 'https://new.pusan.ac.kr/board', '', 12)]
        ordered = order_pages(pages)
        self.assertEqual(len(ordered), 3)
        self.assertEqual({page.department for page in ordered[:2]}, {'경영학과(학부)', '경영학과(대학원)'})
        self.assertEqual([page.number for page in ordered], [400, 500, 12])
        self.assertEqual(ordered[-1].display_name, '새 학과')

    def test_same_physical_board_is_read_once_and_keeps_all_subscription_names(self):
        url = 'https://biz.pusan.ac.kr/biz/8941/subview.do'
        pages = [
            AnnouncementPage('경영학과(학부)', url, '', 500),
            AnnouncementPage('경영학과(대학원)', url, '', 400),
        ]
        merged = coalesce_pages(order_pages(pages))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].number, 400)
        self.assertEqual(
            merged[0].notification_departments,
            ['경영학과(학부)', '경영학과(대학원)'],
        )
        self.assertEqual(merged[0].source_urls, [url])

        rows = [
            row for row in records().values()
            if row.get('canonical_board') == 'k2web:artimage:915'
        ]
        pages = [
            AnnouncementPage(row['identity_department'], row['page_url'], '', 100 + index)
            for index, row in enumerate(rows)
        ]
        merged = coalesce_pages(order_pages(pages))
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].source_urls), 2)
        self.assertEqual(len(merged[0].notification_departments), 2)

    def test_catalog_covers_all_registered_routes_without_invalidating_old_names(self):
        path = Path(__file__).resolve().parents[1] / 'together/crawler/source_catalog.json'
        rows = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(rows), len(records()))
        self.assertEqual(len(rows), 1016)
        self.assertEqual(sorted(row['order'] for row in rows), list(range(1, 1017)))
        for row in rows:
            self.assertTrue(row['display_name'].strip())
            self.assertTrue(row['identity_department'].strip())
            self.assertTrue(row['name_source'].startswith('https://'))
            self.assertTrue(row['sort_group'].strip())
            self.assertTrue(row['affiliation'].strip())
            self.assertNotEqual(row['sort_group'], '28 기타 학과·전공')
        self.assertEqual(
            [row['identity_department'] for row in rows[:4]],
            ['대학 공지', '대학 공지', '학생지원시스템(공지사항)', '학생지원시스템(장학 공지사항)'],
        )

    def test_university_state_rows_remain_separate_but_onestop_displays_two_and_three(self):
        rows = list(records().values())[:5]
        pages = [AnnouncementPage(row['identity_department'],row['page_url'],'',100+i) for i,row in enumerate(rows)]
        set_display_ordinals(pages)
        self.assertEqual([p.display_ordinal for p in pages], [1,1,2,3,4])
        self.assertEqual([p.number for p in pages], [100,101,102,103,104])


if __name__ == '__main__':
    unittest.main()
