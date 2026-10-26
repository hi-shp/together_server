"""기존 게시판 관리 모듈 경로를 유지한다."""
import sys
from together.crawler import pages as _implementation

sys.modules[__name__] = _implementation
