"""Exercise real Chrome start/restart/quit without credentials or website writes."""
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Selenium's module imports the crawler's duplicate checker. This smoke test must
# not initialize or call the GPT client, even if production credentials exist.
sys.modules['gpt_client'] = types.SimpleNamespace(answer_gpt=None, check_title_similarity=None)
from selenium_service import WriteNoticeService
from browser_runtime import workspace_root


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', required=True)
    args = parser.parse_args()
    service = WriteNoticeService.__new__(WriteNoticeService)
    service.driver = None
    service._workspace = None
    service.chrome_driver_path = args.driver
    service.window_size = '1920,1080'
    service.user_agent = 'Mozilla/5.0'
    for _ in range(3):
        try:
            service.init_driver()
            service.safe_get('data:text/html,<title>together-smoke</title>')
            assert service.driver.title == 'together-smoke'
            service.restart_driver()
            service.safe_get('data:text/html,<title>restart-ok</title>')
            assert service.driver.title == 'restart-ok'
        finally:
            service.close()
    assert not list(workspace_root().glob('driver-*')), 'Workspace leak detected'
    print('PASS: 6 real Chrome sessions, restart/quit, zero driver workspaces remain')


if __name__ == '__main__':
    main()
