"""기존 API 사용량 모듈 경로를 유지한다."""
import sys
from together.storage import api_usage as _implementation

sys.modules[__name__] = _implementation
