from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from hype_autopilot.phase2.config import resolve_inside_workspace


def load_phase2_openai_environment(workspace_root: str | Path) -> bool:
    """Load the ignored Phase 2 .env without overriding an explicit process environment."""
    env_path = resolve_inside_workspace(".env", workspace_root)
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)
    return bool(os.environ.get("OPENAI_API_KEY"))
