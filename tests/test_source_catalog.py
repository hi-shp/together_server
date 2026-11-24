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
    def test_verified_centers_and_teacher_support_are_in_their_own_sections(self):
        rows = list(records().values())
        expected = {
            'bali': ('70 연구소·센터', 'research'),
            'super-erc': ('70 연구소·센터', 'research'),
            'aiedap': ('32 학생 이용 기관', 'support'),
        }
        for site, (section, kind) in expected.items():
            matched = [row for row in rows if '/' + site + '/' in row['page_url']]
            self.assertTrue(matched)
            for row in matched:
                self.assertEqual((row['sort_group'], row['kind']), (section, kind))
                self.assertEqual(row['affiliation_confidence'], '공식 페이지 확인')

    def test_smart_city_follows_urban_engineering_in_the_same_college(self):
        rows = sorted(records().values(), key=lambda row: row['order'])
        urban = [row for row in rows if row['organization'] == '도시공학과']
        smart = [row for row in rows if row['organization'] == '스마트시티전공']
        future = [row for row in rows if row['organization'] == '미래도시건축환경융합전공']
        self.assertEqual(smart[0]['order'], urban[-1]['order'] + 1)
        self.assertEqual(future[0]['order'], smart[-1]['order'] + 1)
        self.assertTrue(all(row['sort_group'] == '10 공과대학' for row in urban + smart + future))
        self.assertEqual(len({row['related_group'] for row in urban + smart + future}), 1)

    def test_college_sections_and_related_academic_fields_are_contiguous(self):
        rows = sorted(records().values(), key=lambda row: row['order'])
        for field in ('sort_group', 'organization'):
            groups = {}
            for row in rows:
                # The university itself owns the core, student and staff boards.
                key = row[field]
                if field == 'organization' and key == '부산대학교':
                    continue
                groups.setdefault(key, []).append(row['order'])
            for key, positions in groups.items():
                with self.subTest(field=field, key=key):
                    self.assertEqual(positions, list(range(positions[0], positions[-1] + 1)))
        academic_groups = {}
        for row in rows:
            self.assertTrue(row['related_group'].strip())
            if '10' <= row['sort_group'][:2] <= '29':
                key = (row['sort_group'], row['related_group'])
                academic_groups.setdefault(key, []).append(row['order'])
        for key, positions in academic_groups.items():
            with self.subTest(academic_group=key):
                self.assertEqual(positions, list(range(positions[0], positions[-1] + 1)))

    def test_college_notice_precedes_its_departments(self):
        rows = sorted(records().values(), key=lambda row: row['order'])
        for section in {row['sort_group'] for row in rows if '10' <= row['sort_group'][:2] <= '27'}:
            members = [row for row in rows if row['sort_group'] == section]
            college = section[3:]
            headers = [row for row in members if row['organization'] == college]
            if headers:
                with self.subTest(college=college):
                    self.assertEqual(members[0]['organization'], college)

    def test_labs_with_verified_department_are_kept_together(self):
        rows = sorted(records().values(), key=lambda row: row['order'])
        groups = {}
        for row in rows:
            if row['sort_group'].startswith('60 '):
                groups.setdefault(row['affiliation'], []).append(row['order'])
        for affiliation, positions in groups.items():
            with self.subTest(affiliation=affiliation):
                self.assertEqual(positions, list(range(positions[0], positions[-1] + 1)))

    def test_new_official_boards_have_distinct_identities_and_stay_with_their_organization(self):
        rows = list(records().values())
        keys = {'k2web:omse:1213', 'k2web:fsn:654', 'k2web:archaeology:393', 'k2web:chemistrylab:10270', 'k2web:chemistrylab:10271', 'k2web:molbiology:800', 'k2web:rocket-:19117'}
        selected = [r for r in rows if r.get('canonical_board') in keys]
        self.assertEqual(len(selected), 7)
        self.assertEqual(len({r['identity_department'] for r in selected}), 7)
        for row in selected:
            same = sorted(r['order'] for r in rows if r['organization'] == row['organization'])
            self.assertEqual(same, list(range(min(same), max(same) + 1)))
            self.assertTrue(row['menu_source'].startswith('https://'))
        notice = next(r for r in selected if r['canonical_board'] == 'k2web:molbiology:800')
        self.assertEqual(notice['display_name'], '분자생물학과(공지)')

    def test_physical_education_is_one_department_inside_education_college(self):
        rows=sorted(records().values(),key=lambda r:r['order'])
        phys=[r for r in rows if r['organization']=='체육교육과']
        self.assertEqual(len(phys),6)
        self.assertTrue(all(r['sort_group']=='18 사범대학' for r in phys))
        self.assertEqual([r['order'] for r in phys],list(range(phys[0]['order'],phys[0]['order']+6)))
        self.assertEqual(phys[0]['navigation_path'][0],'체육교육과')
        groups=[r['sort_group'] for r in rows]
        positions=[i for i,g in enumerate(groups) if g=='18 사범대학']
        self.assertEqual(positions,list(range(min(positions),max(positions)+1)))
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

    def test_moved_school_boards_preserve_saved_number_and_subscription_identity(self):
        from together.crawler.pages import URL_OVERRIDES
        for host, old, new, number in [('larc','72331','18236',6), ('primate','71680','18295',0), ('shinlab','71292','18249',69), ('bioenergy','28644','18105',55), ('marketing','27901','18266',44), ('powerlab','24686','18776',3), ('powerlab','24698','18777',2), ('cmfd','31708','18738',4)]:
            old_url=f'https://{host}.pusan.ac.kr/{host}/{old}/subview.do'
            row=next(r for r in records().values() if r['page_url']==old_url)
            page=AnnouncementPage(row['identity_department'],old_url,'',number)
            self.assertEqual(page.page_url,f'https://{host}.pusan.ac.kr/{host}/{new}/subview.do')
            self.assertEqual(page.source_url,old_url)
            self.assertEqual(page.department,row['identity_department'])
            self.assertEqual(page.number,number)

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
        self.assertGreaterEqual(len(rows), 1016)
        self.assertEqual(sorted(row['order'] for row in rows), list(range(1, len(rows)+1)))
        for row in rows:
            self.assertTrue(row['display_name'].strip())
            self.assertTrue(row['identity_department'].strip())
            self.assertTrue(row['name_source'].startswith('https://'))
            self.assertTrue(row['sort_group'].strip())
            self.assertTrue(row['affiliation'].strip())
            self.assertNotEqual(row['sort_group'], '28 기타 학과·전공')

    def test_migrated_modules_keep_subscription_keys_and_new_leme_news_is_grouped(self):
        rows=list(records().values())
        for host,old,new,number in [('stemcell','44956','18418',1),('stemcell','44960','18422',38),('ibd','28668','18403',10),('yseo','23078','18312',6)]:
            source=f'https://{host}.pusan.ac.kr/{host}/{old}/subview.do'
            row=next(r for r in rows if r['page_url']==source)
            page=AnnouncementPage(row['identity_department'],source,'',number)
            self.assertEqual((page.source_url,page.department,page.number),(source,row['identity_department'],number))
            self.assertEqual(page.page_url,f'https://{host}.pusan.ac.kr/{host}/{new}/subview.do')
            self.assertTrue(row['previous_canonical_boards'])
        labs=[r for r in sorted(rows,key=lambda r:r['order']) if r['organization']=='환경미생물에너지연구실']
        self.assertEqual([r['order'] for r in labs],list(range(labs[0]['order'],labs[0]['order']+3)))
        self.assertEqual(labs[-1]['canonical_board'],'k2web:leme:2835')
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
