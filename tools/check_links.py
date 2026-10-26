"""게시판 주소를 조회하고 별도 CSV에 점검 결과를 저장한다."""
import argparse
import csv
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def check(row, insecure=False, overrides=None):
    source_url = row['page_url']
    url = (overrides or {}).get(source_url, source_url)
    result = {'department': row.get('department', ''), 'page_url': source_url, 'checked_url': url,
              'status': '', 'final_url': '', 'result': '', 'title': ''}
    if urlparse(url).scheme not in ('http', 'https'):
        result['result'] = 'invalid_url'
        return result
    try:
        with requests.get(url, timeout=(5, 20), verify=not insecure) as response:
            result['status'] = response.status_code
            result['final_url'] = response.url
            soup = BeautifulSoup(response.content, 'html.parser')
            result['title'] = soup.title.get_text(' ', strip=True) if soup.title else ''
            if response.status_code != 200:
                result['result'] = 'http_error'
            elif not soup.select('td._artclTdNum, td.number, td.num, td.td-num'):
                result['result'] = 'board_markup_missing'
            else:
                result['result'] = 'ok'
    except requests.RequestException as exc:
        result['result'] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', default='pages.csv')
    parser.add_argument('--output', default='link-audit.csv')
    parser.add_argument('--delay', type=float, default=1.0, help='Seconds between requests (minimum 1)')
    parser.add_argument('--insecure', action='store_true', help='Match legacy crawler TLS behavior')
    parser.add_argument('--overrides', default=str(Path(__file__).resolve().parents[1] / 'url_overrides.json'))
    args = parser.parse_args()
    if Path(args.csv).resolve() == Path(args.output).resolve():
        parser.error('output must not overwrite the source CSV')
    if args.insecure:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    with open(args.csv, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    with open(args.overrides, encoding='utf-8') as handle:
        overrides = json.load(handle)
    results = []
    network_failures = 0
    for index, row in enumerate(rows):
        if network_failures >= 3:
            results.append({'department': row.get('department', ''), 'page_url': row['page_url'],
                            'checked_url': overrides.get(row['page_url'], row['page_url']),
                            'status': '', 'final_url': '', 'result': 'not_checked_network_backoff', 'title': ''})
            continue
        if index:
            time.sleep(max(1.0, args.delay))
        result = check(row, args.insecure, overrides)
        results.append(result)
        failed = result['result'] in ('ConnectTimeout', 'ReadTimeout', 'ConnectionError', 'SSLError') or result['status'] in (403, 429)
        network_failures = network_failures + 1 if failed else 0
        if (index + 1) % 25 == 0:
            print(f'Checked {index + 1}/{len(rows)}', flush=True)
    with open(args.output, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]) if results else
                                ['department', 'page_url', 'checked_url', 'status', 'final_url', 'result', 'title'])
        writer.writeheader()
        writer.writerows(results)
    failures = sum(row['result'] != 'ok' for row in results)
    print(f'Checked {len(results)} boards; review {failures}; report: {args.output}')


if __name__ == '__main__':
    main()
