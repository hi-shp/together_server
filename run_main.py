"""기존 반복 실행 모듈 경로를 유지한다."""
import sys
from together import supervisor as _implementation

if __name__ == '__main__':
    _implementation.cli()
else:
    sys.modules[__name__] = _implementation
