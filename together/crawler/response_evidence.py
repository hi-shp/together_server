"""일시적으로 목록이 빠진 공개 응답을 중복 없이 비공개 보존한다."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from together.operations.events import safe_url


def preserve_missing_list(base_dir, source_url, response):
    body = response.text.encode('utf-8')
    key = hashlib.sha256(source_url.encode()).hexdigest()[:16] + '-' + hashlib.sha256(body).hexdigest()[:20]
    root = Path(base_dir) / '.runtime/board-response-evidence'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    html = root / (key + '.html')
    metadata = root / (key + '.json')
    if not html.exists():
        with html.open('xb') as handle:
            os.chmod(html, 0o600);handle.write(body)
    value = json.loads(metadata.read_text(encoding='utf-8')) if metadata.exists() else {'source_url': safe_url(source_url), 'final_url': safe_url(getattr(response, 'url', source_url)), 'first_seen': datetime.now().astimezone().isoformat(), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'occurrences': 0}
    value.update(last_seen=datetime.now().astimezone().isoformat(), occurrences=value['occurrences'] + 1)
    temp = metadata.with_suffix('.pending');temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8');temp.chmod(0o600);os.replace(temp, metadata)
