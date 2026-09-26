"""Thin CLI for the frozen candidate experiment stages.

Reusable ranking, policy, materialization, and audit logic lives in
``er.candidates``. Historical materialization stages remain available through
``er.candidates.experiments`` so completed restartable shards are not invalidated.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code" / "business_entity_resolution" / "src"))

from er.candidates.experiments import main


if __name__ == "__main__":
    main()
