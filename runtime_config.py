"""기존 환경설정 모듈 경로를 유지한다."""
import sys
from together import config as _implementation

sys.modules[__name__] = _implementation
