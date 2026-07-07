"""Research framework — experiment tracking (and, later, the factor library).

The discipline layer: everything here exists to make research auditable and hard
to fool yourself with. Currently the experiment record system; factor computation
and testing (M4) will join it.
"""
from quant.research.experiments import ExperimentStore, git_revision, log_backtest

__all__ = ["ExperimentStore", "git_revision", "log_backtest"]
