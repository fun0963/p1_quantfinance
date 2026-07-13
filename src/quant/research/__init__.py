"""Research framework — experiment tracking + lifecycle discipline.

The discipline layer: everything here exists to make research auditable and hard
to fool yourself with. Experiment records (what was run), lifecycle checks
(pre-committed promote/retire rules); the factor library (M4) will join it.
"""
from quant.research.experiments import ExperimentStore, git_revision, log_backtest
from quant.research.lifecycle import LifecycleReport, LifecycleRules, check_lifecycle

__all__ = ["ExperimentStore", "LifecycleReport", "LifecycleRules", "check_lifecycle",
           "git_revision", "log_backtest"]
