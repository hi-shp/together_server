"""제목 표시와 공지 내용을 확인하고 의심되는 자료를 따로 보관한다."""
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit,urljoin
from bs4 import BeautifulSoup

_reviews={}


class PostVerificationError(RuntimeError):pass


def clean_text(value):
    return ''.join(char for char in str(value) if unicodedata.category(char) not in ('Cf','Cs') and
                   (unicodedata.category(char)!='Cc' or char in '\n\t\r'))


def display_title(value):
    value=clean_text(value)
    for _ in range(3):
        decoded=html.unescape(value)
        if decoded==value:break
        value=decoded
    return ' '.join(value.split()).replace('<','＜').replace('>','＞')


def sanitize_html(value,base_url=''):
    soup=BeautifulSoup(value or '','html.parser')
    for tag in soup.select('script,style,object,embed,svg,math,form,input,button,link,meta,base,noscript'):
        if tag.parent is not None:tag.decompose()
    for tag in list(soup.find_all('iframe')):
        source=urljoin(base_url,tag.get('src',''))
        if urlsplit(source).scheme in ('http','https'):
            link=soup.new_tag('a',href=source);link.string='영상·자료 보기';tag.replace_with(link)
        else:tag.decompose()
    allowed=set('p div span a img br hr strong b em i u s sub sup blockquote pre code ul ol li table thead tbody tfoot tr th td h1 h2 h3 h4 h5 h6 dl dt dd figure figcaption'.split())
    attributes={'href','src','alt','title','class','width','height','colspan','rowspan','style'}
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed:tag.unwrap();continue
        for name in list(tag.attrs):
            if name not in attributes:del tag.attrs[name];continue
            if name in ('href','src'):
                url=urljoin(base_url,clean_text(tag[name]).strip());scheme=urlsplit(url).scheme.lower()
                valid=scheme in ('http','https','mailto','tel') or (not scheme and url.startswith(('/','#')))
                if name=='src' and re.match(r'^data:image/(?:png|jpeg|gif|webp);base64,',url,re.I):valid=True
                if not valid:del tag.attrs[name]
                else:tag[name]=url
            if name=='style' and re.search(r'url\s*\(|expression|behavior|binding|display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\D|$)|font-size\s*:\s*0(?:\D|$)',str(tag[name]),re.I):
                del tag.attrs[name]
    return str(soup)


def title_problems(value):
    value=display_title(value)
    reasons=[]
    visible=[char for char in value if char.isalnum()]
    if not visible or value in ('제목 없음','내용 없음'):reasons.append('empty_title')
    if len(value)>500:reasons.append('title_too_long')
    if '\ufffd' in value:reasons.append('invalid_character_encoding')
    if len(re.findall(r'[ÃÂìë][^\s]{0,3}',value))>=3:reasons.append('possible_mojibake')
    return reasons


def remember_review(combined,result):
    key=hashlib.sha256(combined.encode()).hexdigest()
    _reviews[key]={'notice_type':result.get('notice_type','unknown'),'reason':str(result.get('reason',''))[:400]}
    if len(_reviews)>128:_reviews.pop(next(iter(_reviews)))


def review_for(combined):return _reviews.get(hashlib.sha256(combined.encode()).hexdigest(),{})


def assess(announcement,model_review=None):
    title=display_title(announcement.title)
    reasons=title_problems(title)
    text=title+' '+(announcement.content_text or '')[:6000]
    personal=bool(re.search(r'개인\s*중고|중고\s*(?:팝니다|판매)|(?:도박|카지노)\s*사이트\s*가입|무료\s*리딩방|대출\s*상담\s*문의|코인\s*수익\s*보장',text))
    if personal:reasons.append('personal_sale_or_advertisement')
    if (model_review or {}).get('notice_type')=='personal_or_ad':reasons.append('semantic_personal_or_advertisement')
    body=announcement.content_text or ''
    if body.count('\ufffd')>max(2,len(body)*.01):reasons.append('body_character_encoding')
    return {'ok':not reasons,'title':title,'reasons':reasons,'model_review':model_review or {},'checked_at':datetime.now().isoformat()}


def quarantine(announcement,result,base_dir):
    root=Path(base_dir)/'.runtime/quarantine';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=hashlib.sha256(announcement.url.encode()).hexdigest()
    target=root/(key+'.json')
    value={'source_url':announcement.url,'title':getattr(announcement,'original_title',announcement.title),
           'body_excerpt':(announcement.content_text or '')[:6000],'assessment':result}
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(target,0o600)
    files=sorted(root.glob('*.json'),key=lambda path:path.stat().st_mtime,reverse=True)
    for path in files[1000:]:path.unlink()


def inspect_rendered_post(driver,expected_subject,expected_text='',source_url=''):
    data=driver.execute_script('''
      const title=document.querySelector('.ubboard_view .subject h3');
      const body=document.querySelector('.ubboard_view .content .text_to_html');
      function visible(node){if(!node)return false;const s=getComputedStyle(node),r=node.getBoundingClientRect();
        return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0&&r.width>0&&r.height>0&&parseFloat(s.fontSize)>0;}
      return {title:title?title.innerText.trim():'',title_visible:visible(title),
        body:body?body.innerText.trim():'',body_visible:visible(body),
        links:body?Array.from(body.querySelectorAll('a[href]')).map(n=>n.href):[],
        images:body?Array.from(body.querySelectorAll('img')).map(n=>({url:n.src,loaded:n.complete&&n.naturalWidth>0})):[]};
    ''')
    normalize=lambda text:' '.join(html.unescape(str(text)).split())
    reasons=[]
    if not data['title_visible'] or normalize(data['title'])!=normalize(expected_subject):reasons.append('rendered_title_mismatch')
    if not data['body_visible'] or (not data['body'] and not any(item['loaded'] for item in data['images'])):reasons.append('rendered_body_empty')
    if source_url and source_url not in data['links']:reasons.append('source_link_missing')
    expected=normalize(expected_text)
    compact=lambda text:''.join(normalize(text).split())
    if expected and compact(expected)[:200] not in compact(data['body']):reasons.append('rendered_body_mismatch')
    if any(not item['loaded'] for item in data['images']):reasons.append('image_load_unconfirmed')
    return {'ok':not reasons,'reasons':reasons,'title':data['title'],'body_characters':len(data['body']),
            'images':len(data['images']),'loaded_images':sum(item['loaded'] for item in data['images'])}
