"""게시 주소를 기록하고 같은 원문이 반복 게시되는 것을 막는다."""
import hashlib,json,os,time
from pathlib import Path
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from .quality import inspect_rendered_post,PostVerificationError
from .post_editor import PlatoPostEditor,PostEditError
from together.storage.attachments import file_digest


def _path(base_dir):
    path=Path(base_dir)/'.runtime/published-receipts.json'
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path


def _load(base_dir):
    path=_path(base_dir)
    if not path.exists():return {}
    if path.stat().st_size>4*1024*1024:raise PostVerificationError('Publication index exceeds limit')
    return json.loads(path.read_text(encoding='utf-8'))


def known_receipt(base_dir,source_url):
    return _load(base_dir).get(hashlib.sha256(source_url.encode()).hexdigest())


def remember_receipt(base_dir,receipt):
    rows=_load(base_dir)
    key=hashlib.sha256(receipt['source_url'].encode()).hexdigest()
    value={k:v for k,v in receipt.items() if not k.startswith('_')};value['updated_at']=time.time()
    rows[key]=value
    rows={k:v for k,v in sorted(rows.items(),key=lambda item:item[1]['updated_at'],reverse=True)[:1000]}
    path=_path(base_dir);pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8');os.chmod(pending,0o600);os.replace(pending,path)


def verify_publication(service,receipt,body_html,files,base_dir):
    url=receipt.get('article_url')
    if not url:raise PostVerificationError('Published article address needs inspection')
    service.safe_get(url,login_required=True)
    WebDriverWait(service.driver,30).until(lambda driver:driver.find_elements(By.CSS_SELECTOR,'.ubboard_view .subject h3'))
    expected_text=BeautifulSoup(body_html,'html.parser').get_text(' ',strip=True)
    check=inspect_rendered_post(service.driver,receipt['subject'],expected_text,receipt['source_url'])
    warnings=[reason for reason in check['reasons'] if reason=='image_load_unconfirmed']
    hard=[reason for reason in check['reasons'] if reason!='image_load_unconfirmed']
    if check['images'] and not check['loaded_images'] and len(expected_text)<150:
        hard.append('image_only_body_unavailable')
    editor=PlatoPostEditor(service,Path(base_dir)/'.runtime/intelligence/edit-backups')
    snapshot=None
    try:
        snapshot=editor.read(url)
        expected={Path(path).name:file_digest(path) for path in files}
        actual={item['name']:item['sha256'] for item in snapshot['files']}
        if expected!=actual:hard.append('published_attachment_mismatch')
    except PostEditError as exc:
        if 'limit' in str(exc):
            actual_names={item['name'] for item in editor._attachment_links(url)}
            if actual_names!={Path(path).name for path in files}:hard.append('published_attachment_mismatch')
            warnings.append('large_attachment_hash_unverified')
        else:raise
    if hard and not receipt.get('repair_attempted') and 'published_attachment_mismatch' not in hard and snapshot:
        receipt['repair_attempted']=True;remember_receipt(base_dir,receipt)
        result=editor.update(url,receipt['subject'],body_html,expected_signature=snapshot['signature'])
        snapshot=result['after']
        service.safe_get(url,login_required=True)
        check=inspect_rendered_post(service.driver,receipt['subject'],expected_text,receipt['source_url'])
        hard=[reason for reason in check['reasons'] if reason!='image_load_unconfirmed']
        if check['images'] and not check['loaded_images'] and len(expected_text)<150:
            hard.append('image_only_body_unavailable')
    if hard:
        receipt.update(state='failed',problems=hard);remember_receipt(base_dir,receipt)
        raise PostVerificationError('Published post failed validation')
    receipt.update(state='verified',validation=check,warnings=warnings)
    remember_receipt(base_dir,receipt)
    if snapshot:receipt['_snapshot']=snapshot
    return receipt
