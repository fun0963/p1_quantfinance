"""Research framework — experiment tracking + lifecycle discipline + knowledge base.

The discipline layer: everything here exists to make research auditable and hard
to fool yourself with. Experiment records (what was run), lifecycle checks
(pre-committed promote/retire rules), the knowledge base (what was learned);
the factor library (M4) will join it.
"""
from quant.research.experiments import ExperimentStore, git_revision, log_backtest
from quant.research.lifecycle import LifecycleReport, LifecycleRules, check_lifecycle
from quant.research.notes import Note, create_note, list_notes, parse_note

__all__ = ["ExperimentStore", "LifecycleReport", "LifecycleRules", "Note",
           "check_lifecycle", "create_note", "git_revision", "list_notes",
           "log_backtest", "parse_note"]
