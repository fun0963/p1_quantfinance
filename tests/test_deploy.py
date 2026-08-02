"""Deployment contracts for the 24/7 live image.

Dockerfile.live installs a hand-listed subset of the dependencies and skips the
two research engines. Both facts are assumptions that would rot silently: a new
`import vectorbt` in the execution path, or a new runtime dependency added to
pyproject, would only surface as a crash on the cloud VM at 16:10 ET. These
tests turn both into CI failures instead.
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "quant"

# Research-only engines: heavy, least ARM-friendly, and never needed to trade.
RESEARCH_ONLY = {"vectorbt", "backtrader"}

# Packages the scheduler actually walks through on a live decision.
LIVE_PACKAGES = ["execution", "risk", "ops", "strategies", "data", "core", "utils"]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_live_path_never_imports_research_engines():
    """The trading path must run on an image without vectorbt/backtrader."""
    offenders = []
    for pkg in LIVE_PACKAGES:
        for py in (SRC / pkg).rglob("*.py"):
            hit = _imported_roots(py) & RESEARCH_ONLY
            if hit:
                offenders.append(f"{py.relative_to(ROOT)} imports {sorted(hit)}")
    assert not offenders, (
        "live path gained a research-engine import; either revert it or add the "
        "package to Dockerfile.live:\n  " + "\n  ".join(offenders))


def test_cli_module_import_is_engine_free():
    """`quant schedule` must start without the engines: the CLI's top-level
    imports have to stay lazy (engines imported inside the command bodies)."""
    assert not (_imported_roots(SRC / "cli.py") & RESEARCH_ONLY)


def _pyproject_runtime_deps() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def test_live_image_installs_every_runtime_dependency():
    """Dockerfile.live's hand-listed deps must cover pyproject's runtime set
    (minus the research engines), version pins included."""
    dockerfile = (ROOT / "Dockerfile.live").read_text(encoding="utf-8")
    missing = []
    for dep in _pyproject_runtime_deps():
        name = re.split(r"[><=!\[ ]", dep, maxsplit=1)[0].strip()
        if name in RESEARCH_ONLY:
            assert f'"{dep}"' not in dockerfile, f"{name} must stay out of the live image"
            continue
        if f'"{dep}"' not in dockerfile:
            missing.append(dep)
    assert not missing, (
        "Dockerfile.live is out of sync with pyproject dependencies; add:\n  "
        + "\n  ".join(missing))


def test_live_compose_pins_the_single_writer_host():
    """hostname and EXECUTE_HOST must agree, or the guard blocks the VM itself."""
    compose = (ROOT / "docker-compose.live.yml").read_text(encoding="utf-8")
    assert "hostname: quant-live" in compose
    assert "EXECUTE_HOST: quant-live" in compose
    # Both schedulers must be present and actually routing orders. Count only
    # the quoted form used inside command arrays - prose mentions it too.
    assert compose.count('"--execute"') == 2
    assert "spy_momentum" in compose and "qqq_scalp_1min" in compose
