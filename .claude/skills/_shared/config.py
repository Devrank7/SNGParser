"""Shared configuration: env loading, project paths."""

import sys
from pathlib import Path

# Project root: DemoSender/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env.local"
SERVICE_ACCOUNT_FILE = PROJECT_ROOT / "service_account.json"
OUTPUT_DIR = PROJECT_ROOT / "output"


def load_env(env_path: Path = None) -> dict:
    """Load environment variables from .env.local file."""
    if env_path is None:
        env_path = ENV_FILE
    env_vars = {}
    if not env_path.exists():
        print(f"ERROR: .env.local not found at {env_path}")
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                env_vars[key] = value
    return env_vars
