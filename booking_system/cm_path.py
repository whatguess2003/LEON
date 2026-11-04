"""Put the channel manager repo on sys.path. CM_HOME overrides the sibling checkout."""

import os
import sys
from pathlib import Path


def mount():
    candidates = []
    configured = os.environ.get("CM_HOME", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path("/opt/cm"))
    candidates.append(Path(__file__).resolve().parents[2] / "CM")
    for root in candidates:
        if (root / "aiosell_channel_manager.py").is_file():
            text = str(root)
            if text not in sys.path:
                sys.path.insert(0, text)
            return text
    return ""


mount()
