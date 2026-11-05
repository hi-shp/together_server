"""중복으로 생략하기 전에 실제 대표 게시글을 조회한다. 저장 요청은 없다."""
import hashlib, json, os
from pathlib import Path
import re, time
from urllib.parse import urljoin,urlsplit,parse_qs,urlencode
from bs4 import BeautifulSoup
from together.storage.pending_notices import notice_identity
from .enrichment import PublishedNoticeStore
from .quality import display_title
from .text import normalize_title


class RepresentativeUnconfirmed(RuntimeError):
    pass


def _subject(value):
    return normalize_title(display_title(value)).lstrip('⭐').strip()


def _article(client,url,title,source='',*,indexed=False):
    with client.get(url,timeout=(5,30)) as response:
        if response.status_code in (404,410): return False
        response.raise_for_status()
        soup=BeautifulSoup(response.text,'html.parser')
    heading=soup.select_one('.ubboard_view .subject h3')
    content=soup.select_one('.ubboard_view')
    if not heading or not content:
        raise RepresentativeUnconfirmed('Representative article is not a verified article page')
    links=[urljoin(url,a['href']) for a in content.select('a[href]')]
    if source:
        # 수동 제목 수정이나 페이지 별칭에도 원문 연결로 대표 글을 확인한다.
        if any(notice_identity(v)==notice_identity(source) for v in links): return True
        if indexed:raise RepresentativeUnconfirmed('Representative source link changed; inspect manual editing')
        return False
    actual,expected=_subject(heading.get_text(' ',strip=True)),_subject(title)
    if actual!=expected and not actual.endswith(' '+expected): return False
    return any(urlsplit(v).netloc.endswith('.pusan.ac.kr') and urlsplit(v).netloc!='dev-plato.pusan.ac.kr' for v in links)


def _search(client,board,title,source=''):
    # 학교 검색은 괄호를 입력에서 지워 전체 제목 검색이 실패할 수 있다.
    # 원래 제목에 실제로 포함된 안전한 낱말로 찾고, 글의 원문을 다시 대조한다.
    words=re.findall(r'[가-힣A-Za-z0-9]+',display_title(title))
    words=[v for v in words if len(v)>=2]
    if not words:raise RepresentativeUnconfirmed('No stable literal title search anchor')
    anchor=max(words,key=len)
    query=board+'&'+urlencode({'keyfield':'subject','keyword':anchor})
    pending,seen,found=[query],set(),{}
    board_id=parse_qs(urlsplit(board).query).get('id')
    while pending:
        url=pending.pop(0)
        if url in seen: continue
        seen.add(url)
        with client.get(url,timeout=(5,30)) as response:
            response.raise_for_status();soup=BeautifulSoup(response.text,'html.parser')
        form=soup.select_one('#ubboard_list_form')
        if not form:
            raise RepresentativeUnconfirmed('Full school title search was not verified')
        keyword=soup.select_one('input[name="keyword"]')
        if keyword is not None and keyword.get('value','')!=anchor:
            raise RepresentativeUnconfirmed('School search transformed the literal query')
        for a in form.select('a[href]'):
            link=urljoin(url,a['href']);parts=urlsplit(link);args=parse_qs(parts.query)
            if parts.netloc!='dev-plato.pusan.ac.kr' or args.get('id')!=board_id: continue
            if parts.path=='/mod/ubboard/article.php' and args.get('bwid'):
                actual,expected=_subject(a.get_text(' ',strip=True)),_subject(title)
                if source or actual==expected or actual.endswith(' '+expected):found[link]=actual
        for a in soup.select('a[href]'):
            link=urljoin(url,a['href']);parts=urlsplit(link);args=parse_qs(parts.query)
            if (parts.netloc=='dev-plato.pusan.ac.kr' and parts.path=='/mod/ubboard/view.php'
                    and args.get('id')==board_id and args.get('page',[''])[0].isdigit()
                    and a.find_parent(class_=re.compile('pag|paging')) and link not in seen):
                # 학교 페이지 링크가 검색어를 생략해도 원래 검색 조건을 유지한다.
                next_url=query+'&'+urlencode({'page':int(args['page'][0])})
                if next_url not in seen:pending.append(next_url)
    return list(found),len(seen)


def _first_publication(base_dir, evidence, current):
    """A failed lookup may not block a first post or authorize a second POST."""
    from .publication import _load
    if not current or not current.get('title') or not evidence.get('current_source_url'):
        return False
    titles={_subject(t) for t in (current['title'], evidence.get('title','')) if t}
    sources={notice_identity(u) for u in (evidence['current_source_url'],evidence.get('source_url')) if u}
    def matches(title, urls):
        name=_subject(title or '')
        return any(notice_identity(u) in sources for u in urls if u) or any(
            name==t or name.endswith(' '+t) for t in titles)
    # Retain uncertain saves and historical/moved/manual posts for reconciliation.
    for r in _load(base_dir).values():
        if matches(r.get('subject'),[r.get('source_url')]):return False
    for r in PublishedNoticeStore(base_dir).load():
        if matches(r['notice'].get('title'),[*r.get('sources',[]),r['notice'].get('source_url')]):return False
    return True


def verify_representative(service,evidence,base_dir,boards,*,current_notice=None):
    """verified/missing/pending. 미확인을 미게시로 바꾸거나 재요청하지 않는다."""
    title=evidence.get('title','');source=evidence.get('source_url','')
    result={'status':'pending','candidate_title':title,'candidate_source_url':source,
            'checked_at':time.time(),'search_pages':0,'searched_boards':0,'post_or_send_requests':0}
    client=getattr(service,'_http',None)
    temporary_client=False
    remote_candidate_seen=False
    try:
        if client is None and getattr(service,'driver',None) is not None:
            # HTTP 로그인 경로가 실패해도 이미 로그인된 기존 브라우저를 쓸 수 있다.
            import requests
            from together.plato.http_client import PlatoHttpClient
            session=requests.Session()
            for cookie in service.driver.get_cookies():
                if (cookie.get('domain','').lstrip('.')=='pusan.ac.kr'
                        or cookie.get('domain','').lstrip('.').endswith('.pusan.ac.kr')):
                    session.cookies.set(cookie['name'],cookie['value'],domain=cookie['domain'],path=cookie.get('path','/'))
            client=PlatoHttpClient(session);temporary_client=True
        if client is None or not title or not boards:
            raise RepresentativeUnconfirmed('Authenticated read, matched title, or board list is unavailable')
        records=PublishedNoticeStore(base_dir).load()
        active=[r for r in records if not r.get('moved_to') and not r.get('duplicate_of')]
        matches=[r for r in active if
                 ((source and source in r.get('sources',[])) or _subject(r['notice']['title'])==_subject(title))]
        # A merged department notice is not appended to the article body.
        # Only an explicit, unique stored alias can use the representative's link.
        bound=[r for r in matches if source and source in r.get('sources',[])]
        if len({r['article_url'] for r in bound})>1:
            raise RepresentativeUnconfirmed('Conflicting representative source bindings')
        checked=set()
        for record in bound or matches:
            url=record['article_url'];checked.add(url)
            primary=record.get('notice',{}).get('source_url','')
            oldsource=(primary if bound and primary in record['sources'] else source) or primary
            if _article(client,url,title,oldsource,indexed=True):
                result.update(status='verified',article_url=url,reason='actual_indexed_representative_exists')
                return result
        if not matches and current_notice and active:
            # 제목 이력에는 '다른 글의 중복이라 게시하지 않았던 제목'도 있다.
            # 그 제목을 못 찾았다는 이유로 다시 게시하지 않고 실제 게시된
            # 원문 후보를 이용해 의미 판정을 한 번 다시 하도록 돌려준다.
            from together.notices.history import candidate_title_similarities,comparison_title
            ranked=candidate_title_similarities(current_notice['title'],[r['notice']['title'] for r in active])
            available=list(active);selected=[]
            for _,cleaned in ranked:
                match=next((r for r in available if comparison_title(r['notice']['title'])==cleaned),None)
                if match:
                    selected.append(match);available.remove(match)
            if selected:
                result.update(status='recheck',reason='history_alias_requires_actual_published_candidates',
                              _actual_candidates=[{'title':r['notice']['title'],'source_url':r['notice']['source_url'],
                                                   'department':r['notice'].get('department',''),
                                                   'body_excerpt':r['notice'].get('body_text',''),
                                                   'publication_time':r['notice'].get('publication_time',''),
                                                   'publication_time_is_explicit':bool(r['notice'].get('publication_time'))}
                                                  for r in selected])
                return result
        # 색인이 없다는 이유로 재게시하지 않는다. 학교의 모든 분류 게시판에서
        # 일치 제목 검색의 마지막 페이지까지 읽은 뒤 실제 글을 확인한다.
        for board_name in boards:
            board=client.board_url(board_name)
            links,pages=_search(client,board,title,source)
            result['search_pages']+=pages;result['searched_boards']+=1
            remote_candidate_seen=remote_candidate_seen or bool(links)
            for url in links:
                if url in checked: continue
                checked.add(url)
                if _article(client,url,title,source):
                    result.update(status='verified',article_url=url,reason='actual_school_search_representative_exists')
                    return result
        result.update(status='missing',reason='all_school_category_searches_completed_without_representative')
    except Exception as exc:
        result.update(status='pending',reason=type(exc).__name__)
        if not remote_candidate_seen:
            try:
                if _first_publication(base_dir,evidence,current_notice):
                    result.update(status='first_publication',reason='no_prior_post_or_save_attempt')
            except (OSError,KeyError,ValueError,TypeError):
                pass  # Unreadable evidence is not an empty publication ledger.
    finally:
        if temporary_client:client.close()
        root=Path(base_dir)/'.runtime/intelligence/duplicate-representatives'
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        key=hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        path=root/(key+'.json');temp=path.with_suffix('.pending')
        temp.write_text(json.dumps({k:v for k,v in result.items() if not k.startswith('_')},ensure_ascii=False,indent=2),encoding='utf-8');temp.chmod(0o600);os.replace(temp,path)
    return result
