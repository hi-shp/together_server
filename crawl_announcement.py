"""기존 공지 수집 모듈 경로를 유지한다."""
import sys
from together.crawler import announcements as _implementation

sys.modules[__name__] = _implementation
