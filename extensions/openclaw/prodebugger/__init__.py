"""ProDebugger DSL debugger (migrated from experiments/dsl)."""
import sys
from pathlib import Path

# Core modules use flat imports relative to the core directory (``import record``),
# so every submodule needs it on sys.path regardless of import order.
CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
