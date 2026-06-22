"""Standalone, on-demand/CI review CLIs (code-diff review + outcome review).

Distinct from ``src/review.py`` (the in-dashboard signal reviewer + journal reflection): these
are read-only report-writers that take a git diff / the accrued logs, run a version-controlled
rubric through a DeepSeek (OpenAI-compatible) model, and write markdown to ``review_out/``. They
reuse ``review.build_provider`` for the LLM call and never edit files or place trades.
"""
