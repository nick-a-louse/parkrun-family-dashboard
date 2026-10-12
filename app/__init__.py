import os

# Release number. The build adds its git commit (APP_VERSION, e.g. "sha-20ecd4f...") at image build time.
RELEASE = "1.1.1"
_build = os.environ.get("APP_VERSION", "").strip()
if _build.startswith("sha-"):
    _build = _build[:11]                      # sha-1234567
__version__ = RELEASE if not _build or _build.lstrip("v") == RELEASE else f"{RELEASE} ({_build})"
