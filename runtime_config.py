"""Shared configuration; never print credentials or webhook URLs."""
import os
from pathlib import Path

from dotenv import load_dotenv
import requests

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env', override=False)
HTTP_TIMEOUT = (5, 30)
_warned = set()


def notify(content=None, *, channel='WEBHOOK_URL', log_path=None):
    """Discord is optional; a notification failure must not repeat a posted notice.

    Do not retry POSTs: a timeout can happen after Discord accepted the message.
    Log only exception types/statuses, because requests exceptions contain URLs.
    """
    url = os.getenv(channel, '').strip()
    if not url:
        if channel not in _warned:
            print(f'Discord disabled: {channel} is unset', flush=True)
            _warned.add(channel)
        return False
    try:
        if log_path is None:
            response = requests.post(url, json={'content': (content or '')[:2000]}, timeout=HTTP_TIMEOUT)
        else:
            with open(log_path, 'rb') as handle:
                response = requests.post(url, data={'content': content or Path(log_path).name},
                                         files={'file': (Path(log_path).name, handle, 'text/plain')},
                                         timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        response.close()
        return True
    except (requests.RequestException, OSError) as exc:
        print(f'Discord {channel}: {type(exc).__name__}', flush=True)
        return False
