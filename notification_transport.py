"""기존 알림 전송 모듈 경로를 유지한다."""
import sys
from together.plato import notifications as _implementation

sys.modules[__name__] = _implementation
