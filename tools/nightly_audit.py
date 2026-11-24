"""Run the server's weekday audit; no UI or desktop scheduler required."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from together.operations.nightly import run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-now', action='store_true', help='Separate immediate weekday verification; does not consume the 22:00 slot')
    parser.add_argument('--plan', action='store_true', help='Inspect eligibility without network or Work requests')
    args = parser.parse_args()
    result=run(ROOT, manual=args.run_now, plan=args.plan)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result['status']=='interrupted' else 0)
