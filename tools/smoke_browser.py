"""로그인 없이 Chrome 시작·재시작·종료를 확인한다."""

from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 브라우저 시험에서는 GPT 클라이언트를 만들지 않는다.
sys.modules["together.integrations.openai_client"] = types.SimpleNamespace(
    answer_gpt=None, check_title_similarity=None
)
from together.plato.service import WriteNoticeService
from together.plato.browser_runtime import workspace_root


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", required=True)
    args = parser.parse_args()
    service = WriteNoticeService.__new__(WriteNoticeService)
    service.driver = None
    service._workspace = None
    service.chrome_driver_path = args.driver
    service.window_size = "1920,1080"
    service.user_agent = "Mozilla/5.0"
    for _ in range(3):
        try:
            service.init_driver()
            service.safe_get("data:text/html,<title>together-smoke</title>")
            assert service.driver.title == "together-smoke"
            service.restart_driver()
            service.safe_get("data:text/html,<title>restart-ok</title>")
            assert service.driver.title == "restart-ok"
        finally:
            service.close()
    assert not list(workspace_root().glob("driver-*")), "Workspace leak detected"
    print("PASS: 6 real Chrome sessions, restart/quit, zero driver workspaces remain")


if __name__ == "__main__":
    main()
