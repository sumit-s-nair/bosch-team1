"""Alert-driven Day 1 CLI; step trace on stdout, optional JSON artifact."""
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
from agent.loop import run_agent


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Incident Zero read-only investigation')
    parser.add_argument('alert')
    parser.add_argument('--max-steps', type=int, default=12)
    parser.add_argument('--output', type=Path, help='Save final JSON for checkpoint review')
    args = parser.parse_args()
    if not 1 <= args.max_steps <= 50:
        parser.error('--max-steps must be between 1 and 50')
    result = run_agent(args.alert, args.max_steps)
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
