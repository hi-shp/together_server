"""기존 PLATO 처리 모듈 경로를 유지한다."""
import sys
from together.plato import service as _implementation

sys.modules[__name__] = _implementation
