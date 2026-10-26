"""기존 실행 상태 모듈 경로를 유지한다."""
import sys
from together.operations import state as _implementation

sys.modules[__name__] = _implementation
