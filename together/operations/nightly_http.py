"""Reuse exact bytes fetched earlier in the same audit, not yesterday's data."""
import gzip
import hashlib
import json
import time
import requests


def cached_response(directory, url, *, now=None):
    key=hashlib.sha256(url.encode()).hexdigest()
    try:
        meta=json.loads((directory/(key+'.json')).read_text())
        raw=directory/(key+'.gz')
        age=(time.time() if now is None else now)-raw.stat().st_mtime
        if not 0 <= age <= 6*3600 or meta.get('status')!=200 or meta.get('routing_unresolved') or meta.get('url')!=url:
            return None
        with gzip.open(raw,'rb') as stream:
            content=stream.read(20*1024**2+1)
        if len(content)>20*1024**2:return None
        response=requests.Response()
        response.status_code=meta['status'];response.url=meta.get('final_url',url)
        response.headers['Content-Type']=meta.get('content_type','')
        response.encoding=requests.utils.get_encoding_from_headers(response.headers)
        response._content=content
        response.audit_snapshot_reused=True
        return response
    except (OSError,ValueError,EOFError):
        return None
