"""Prompt locally; never echo, transmit, or commit webhook values."""
from getpass import getpass
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import set_key


def main():
    target = Path(__file__).resolve().parents[1] / '.env'
    target.touch(mode=0o600, exist_ok=True)
    os.chmod(target, 0o600)
    print('Discord에서 기존 웹훅을 삭제하고 새로 발급한 URL을 입력하세요. 빈 입력은 해당 알림을 끕니다.')
    for key in ('WEBHOOK_URL', 'DISCORD_LOG_WEBHOOK_URL',
                'DISCORD_MESSAGE_WEBHOOK_URL', 'DISCORD_EMAIL_WEBHOOK_URL'):
        while True:
            value = getpass(f'{key} (입력 숨김): ').strip()
            parsed = urlparse(value)
            if not value or (parsed.scheme == 'https' and parsed.hostname in ('discord.com', 'discordapp.com')
                             and parsed.path.startswith('/api/webhooks/')):
                break
            print('Discord webhook URL 형식을 확인하세요.')
        set_key(str(target), key, value)
    os.chmod(target, 0o600)
    print('.env 설정 완료. 값은 화면에 출력하지 않았습니다.')


if __name__ == '__main__':
    main()
