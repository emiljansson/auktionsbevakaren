"""Use this checkout's configuration; never default tests to a remote preview."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
os.environ.setdefault("REACT_APP_BACKEND_URL", "http://localhost:8000")
