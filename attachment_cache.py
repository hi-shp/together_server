"""기존 첨부 보관 모듈 경로를 유지한다."""
import sys
from together.storage import attachments as _implementation

sys.modules[__name__] = _implementation
