"""Run the server's weekday audit; no UI or desktop scheduler required."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from together.operations.nightly import run,scheduled_slot,KST,save
from datetime import datetime

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-now', action='store_true', help='Separate immediate weekday verification; does not consume the 22:00 slot')
    parser.add_argument('--plan', action='store_true', help='Inspect eligibility without network or Work requests')
    args = parser.parse_args()
    result=run(ROOT, manual=args.run_now, plan=args.plan)
    print(json.dumps(result, ensure_ascii=False))
    if not args.plan and result['status'] in ('complete','completed_with_evidence_gaps'):
        slot=scheduled_slot(datetime.now(KST))
        queued=ROOT/'.runtime/nightly-audit/deferred'/slot.strftime('%Y%m%dT%H%M%S.json') if slot else None
        if queued and queued.exists():
            resumed=run(ROOT)
            save(queued,{'scheduled_kst':slot.isoformat(),'status':resumed['status'],'run':resumed.get('run')})
            print(json.dumps(resumed,ensure_ascii=False))
    raise SystemExit(1 if result['status']=='interrupted' else 0)
