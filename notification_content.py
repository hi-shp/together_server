"""쪽지와 이메일에 담을 공지 내용을 구성한다."""
import base64
from html import escape
from pathlib import Path
from bs4 import BeautifulSoup
from notice_intelligence.quality import sanitize_html


def email_content(title,url,content,department,base_dir):
    soup=BeautifulSoup(sanitize_html(content,url),'html.parser')
    for tag in soup.select('script,form,input,button'):tag.decompose()
    for tag in soup.find_all(True):
        for key in list(tag.attrs):
            if key.lower().startswith('on'):del tag.attrs[key]
    banner=Path(base_dir)/'together.png'
    image=''
    if banner.is_file():
        encoded=base64.b64encode(banner.read_bytes()).decode('ascii')
        image=f'<img src="data:image/png;base64,{encoded}" alt="TOGETHER 배너" width="350" height="51">'
    return ('<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"></head><body>'
            f'<p>[{escape(department)}] 새로운 공지가 등록되었습니다.</p><p><strong>제목: {escape(title)}</strong></p>'
            f'<p><a href="{escape(url,quote=True)}">{escape(url)}</a></p>'
            f'<blockquote style="margin-left:20px;border-left:4px solid #ccc;padding-left:10px">{soup}</blockquote>'
            f'{image}<p>부산대학교의 모든 공지사항을 한곳에서 편리하게, 자율강좌 투게더</p>'
            '<p><a href="https://dev-plato.pusan.ac.kr/course/view.php?id=157301">투게더 강좌 바로가기</a></p></body></html>')
