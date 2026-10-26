"""기존 대학 공지 기준 모듈 경로를 유지한다."""
import sys
from together.crawler import university as _implementation

sys.modules[__name__] = _implementation
