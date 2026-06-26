"""Read-only results dashboard (FastAPI) — a thin web layer over the existing
research/execution functions. Optional: install with `pip install -e ".[web]"`.

Import lazily (the package must still import without fastapi installed); the
`quant web` CLI command brings this up via uvicorn.
"""
