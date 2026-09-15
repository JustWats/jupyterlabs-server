"""Hardware assessment and opt-in local compute configuration."""
from .hardware import assess
from .runtime import configure, start_cluster
from .analysis import run_analysis

__all__ = ["assess", "configure", "start_cluster", "run_analysis"]
