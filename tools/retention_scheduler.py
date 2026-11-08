"""OFF by default. Explicit bounded scheduler for a previously created synthetic lab."""
import argparse
import json
import time
from pathlib import Path
from xblago.core.retention_ops import Runner, Schedule, initialize


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('init', 'run', 'inspect', 'reschedule'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--ticks', type=int, default=1)
    p.add_argument('--poll-seconds', type=int, default=30)
    p.add_argument('--interval', type=int, default=3600)
    args = p.parse_args()
    if not (1 <= args.ticks <= 1000 and 1 <= args.poll_seconds <= 60):
        p.error('Bounded ticks/poll interval required')
    try:
        if args.command == 'init':
            initialize(args.root, Schedule(interval=args.interval, overdue_seconds=max(7200,args.interval*2)))
            print(json.dumps({'status':'INITIALIZED_DISABLED','G04':'UNKNOWN','real_pilot':'NO-GO'}))
            return
        runner = Runner(args.root)
        for n in range(args.ticks if args.command == 'run' else 1):
            result = runner.tick() if args.command == 'run' else runner.inspect() if args.command == 'inspect' else runner.reschedule()
            print(json.dumps(result), flush=True)
            if result['status'] in ('UNKNOWN_RECOVERY','EXHAUSTED','CLOCK_ROLLBACK') or (result['status']=='FAILED' and n+1>=args.ticks):
                raise SystemExit(2)
            if result['status'] == 'DISABLED':
                return
            if n+1 < args.ticks:
                time.sleep(args.poll_seconds)
    except (Exception,) as exc:
        # No raw exception strings, paths, private records or credentials.
        print(json.dumps({'status':'UNKNOWN_OPERATION','G04':'UNKNOWN','real_pilot':'NO-GO'}), flush=True)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
