# Scout AI — configuration.
# All values can be overridden with environment variables of the same name.
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env sitting next to this file (works regardless of cwd, e.g. cron).
# Runs before any os.environ reads below; existing env vars take precedence.
load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.environ.get("SCOUT_DB_PATH", DATA_DIR / "scout.db"))

# Model: Sonnet 5 — near-Opus quality on research/drafting at ~40% of the cost.
MODEL = os.environ.get("SCOUT_MODEL", "claude-sonnet-5")

# Your profile lives here — resume, skills, preferences. See profile/README.
PROFILE_DIR = ROOT / "profile"

# Tavily — client-side web search/fetch (the API proxy in use doesn't run
# server-side web tools).
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# How far back "recently funded" means, in days.
FUNDING_WINDOW_DAYS = int(os.environ.get("SCOUT_FUNDING_WINDOW_DAYS", "90"))

# Minimum match score (0-100) for a startup to reach the outreach stage.
MIN_MATCH_SCORE = int(os.environ.get("SCOUT_MIN_MATCH_SCORE", "60"))

# Max startups to fully research per run (controls token spend).
MAX_RESEARCH_PER_RUN = int(os.environ.get("SCOUT_MAX_RESEARCH_PER_RUN", "10"))
