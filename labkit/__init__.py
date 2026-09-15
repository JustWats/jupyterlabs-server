"""Hardware assessment and opt-in local compute configuration."""
from .hardware import assess
from .runtime import configure, start_cluster

__all__ = ["assess", "configure", "start_cluster"]
