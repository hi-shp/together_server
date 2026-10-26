"""기존 이메일 양식 모듈 경로를 유지한다."""
import sys
from together.plato import content as _implementation

sys.modules[__name__] = _implementation
