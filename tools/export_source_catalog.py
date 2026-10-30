"""읽기 전용으로 현재 서버의 표시 순서·이름·처리 번호를 내보낸다."""
import argparse,csv,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from together.crawler.pages import PageUrlManager
from together.crawler.article_id_boards import configured_board
from together.crawler.auxiliary_cms import manager_for
from together.crawler.onestop import menu_for


def export(handle):
    writer=csv.DictWriter(handle,fieldnames=['순서','사이트 이름','기존 구독 이름','목록 주소','마지막 처리 값','처리 값 종류'])
    writer.writeheader()
    for index,page in enumerate(PageUrlManager().announcement_pages,1):
        writer.writerow({'순서':page.display_ordinal,'사이트 이름':page.display_name,'기존 구독 이름':page.department,'목록 주소':page.source_url,'마지막 처리 값':page.number,'처리 값 종류':'글 ID' if configured_board(page.page_url) or manager_for(page.page_url) or menu_for(page.page_url) else '목록 번호'})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.output:
        with args.output.open('w',encoding='utf-8-sig',newline='') as handle:export(handle)
    else:export(sys.stdout)
