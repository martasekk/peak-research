"""
peak_research/tools/paths.py
============================
Single source of truth for every writable location the pipeline touches.

Why this module exists: as a Claude Code plugin the skill directory lives under
``~/.claude/plugins/...``. It is shared, it is replaced wholesale on update, and
it must be treated as read-only. The pre-plugin version wrote its cache, its run
state and its regenerated subagent script next to ``__file__``, and hardcoded a
``D:\\`` deliverable path belonging to one machine. None of that survives
installation on a second box.

So: nothing writable resolves from ``__file__``. Everything resolves from the
workspace, which defaults to the directory the user is researching *from*.

    PEAK_WORKSPACE    run state, caches      default <cwd>/.peak-research
    PEAK_CACHE_DIR    API response cache     default <workspace>/cache
    PEAK_RUNS_DIR     per-topic run dirs     default <workspace>/runs
    PEAK_OUTPUT_DIR   published .md          default <cwd>/research
    PEAK_CATALOG      curated source list    default unset (optional)

Directories are created lazily, on first write, so merely importing the toolkit
never litters the current directory.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["workspace", "cache_dir", "runs_dir", "output_dir", "catalog_path", "ensure"]


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def workspace() -> Path:
    """Root for everything the pipeline writes but does not publish."""
    return _env_path("PEAK_WORKSPACE") or Path.cwd() / ".peak-research"


def cache_dir() -> Path:
    """Raw API responses, LLM adjudication responses, and the cost log."""
    return _env_path("PEAK_CACHE_DIR") or workspace() / "cache"


def runs_dir() -> Path:
    """Per-topic intermediates: PLAN.json, EVIDENCE.json, AUDIT.json, ..."""
    return _env_path("PEAK_RUNS_DIR") or workspace() / "runs"


def output_dir() -> Path:
    """Where a published deliverable lands. ``--output-dir`` overrides this."""
    return _env_path("PEAK_OUTPUT_DIR") or Path.cwd() / "research"


def catalog_path() -> Path | None:
    """The curated source catalog, if the user has one. Optional by design.

    v2 hardcoded one machine's ``D:\\peak-search\\...``. The METHOD only ever
    *consults* the catalog, so its absence degrades source selection rather than
    breaking the run.
    """
    p = _env_path("PEAK_CATALOG")
    return p if p and p.exists() else None


def ensure(path) -> Path:
    """mkdir -p, tolerating a read-only or otherwise unwritable target."""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p
