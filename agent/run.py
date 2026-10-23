"""Entry point.
  python -m agent.run --now                 investigate the current state (live demo)
  python -m agent.run --watch               wait for an alert, then run all phases
  python -m eval.run_offline --pipeline X   same phases against recorded scenario X (no demo needed)
Kill switch: create a file named STOP in the repo root.
"""
import argparse

from agent import pipeline


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--watch", action="store_true")
    g.add_argument("--now", action="store_true")
    a = ap.parse_args()
    pipeline.run(mode="watch" if a.watch else "now")


if __name__ == "__main__":
    main()
