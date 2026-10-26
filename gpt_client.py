"""기존 GPT 연동 모듈 경로를 유지한다."""
import sys
from together.integrations import openai_client as _implementation

sys.modules[__name__] = _implementation
