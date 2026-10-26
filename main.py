"""기존 공지 처리 실행 모듈 경로를 유지한다."""
import sys
from together import application as _implementation

if __name__ == '__main__':
    _implementation.cli()
else:
    sys.modules[__name__] = _implementation
