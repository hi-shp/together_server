"""기존 제목 이력 모듈 경로를 유지한다."""
import sys
from together.notices import history as _implementation

sys.modules[__name__] = _implementation
