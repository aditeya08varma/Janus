import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

os.environ.setdefault("JANUS_SKIP_REDIS", "1")
os.environ.setdefault("JANUS_API_KEY", "")
os.environ.setdefault("ENABLE_RERANKER", "0")
