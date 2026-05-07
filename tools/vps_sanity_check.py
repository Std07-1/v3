"""One-shot import sanity check for VPS post-pull state."""

import runtime.api_v3.endpoints  # noqa: F401
import runtime.ws.app_keys  # noqa: F401
from core.smc.types import NarrativeBlock

print("imports: OK")
print("NarrativeBlock has to_wire:", hasattr(NarrativeBlock, "to_wire"))
print(
    "ActiveScenario has to_wire:",
    hasattr(
        __import__("core.smc.types", fromlist=["ActiveScenario"]).ActiveScenario,
        "to_wire",
    ),
)
