"""Central configuration constants for the Zepto discovery pipeline.

Later phases import from here instead of hardcoding values, so a change
(e.g. the confirmed Stage 1 batch size) only needs to happen once.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scrape target
PLAY_STORE_APP_ID = "com.zeptoconsumerapp"
PLAY_STORE_LOCALE = "en_IN"

# Time window
TIME_WINDOW_DAYS = 90
TIMEFRAME_OPTIONS_DAYS = (7, 30, 90)

# Sampling
SAMPLE_SIZE_CAP = 1200
RECENCY_CHUNKS = 3

# Filtering
MIN_REVIEW_WORD_COUNT = 3

# Stage 1 tagging — placeholder until confirmed empirically in Phase 4
STAGE1_BATCH_SIZE = 175

# Below this many reviews, a timeframe's synthesis is flagged as
# low-evidence in the UI rather than presented as equally robust.
THIN_WINDOW_MIN_REVIEWS = 50

# Data directories (contents are gitignored; see .gitignore)
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
FILTERED_DIR = DATA_DIR / "filtered"
SAMPLED_DIR = DATA_DIR / "sampled"
TAGGED_DIR = DATA_DIR / "tagged"
CLUSTERED_DIR = DATA_DIR / "clustered"
SYNTHESIS_DIR = DATA_DIR / "synthesis"
