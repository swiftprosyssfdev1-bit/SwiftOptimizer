"""
swift_agent.py — Swift Agent (v3.6)
"""
import sys
from pathlib import Path

# Add the parent (project root) directory to sys.path so we can import
# root-level modules like registry_utils. This script's own directory
# (agent/) is already on sys.path automatically because Python adds the
# running script's directory when it's executed directly -- that's what
# lets `from agent_core import main` below resolve. Guarded with a
# membership check so re-running/importing this module twice in the same
# process doesn't keep growing sys.path.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_core import main

if __name__ == "__main__":
    sys.exit(main())
