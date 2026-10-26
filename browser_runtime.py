"""기존 브라우저 관리 모듈 경로를 유지한다."""
import sys
from together.plato import browser_runtime as _implementation

sys.modules[__name__] = _implementation
