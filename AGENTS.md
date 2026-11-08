# Repository Guidelines

## Project Structure & Module Organization

- `main.py` is the FastAPI entry point. Backend services live in `internal/`; keep platform, Codex, model, and workspace logic in existing modules.
- `static/` contains the no-bundler frontend. `app.js` is the entry point; reusable ES modules belong in `static/modules/`, with styles in `styles.css` and the browser helper in `static/browser-helper/`.
- `resources/` holds tracked CTF skill sources and dictionaries. Do not install runtime tools here.
- `temp/` holds tracked scripts, tests, Playwright config, and `.env.example`. `work/` is ignored runtime data: configs, downloads, sessions, caches, and installed tools.
- `docs/api_doc.md` documents the local API. Update it when endpoints or payloads change.

## Build, Test, and Development Commands

There is no build step. Set up with `python3 -m venv .venv`, `PIP_CACHE_DIR="$PWD/work/cache/tools/pip" .venv/bin/python -m pip install -r requirements.txt`, and `npm ci`.

```bash
.venv/bin/python main.py                 # run at http://127.0.0.1:8080
.venv/bin/pytest -q temp/tests/python   # backend tests
npm run check:js                        # JavaScript syntax checks
npm run test:e2e                        # Playwright browser/API tests
.venv/bin/ruff check internal main.py temp/scripts temp/tests/python
.venv/bin/mypy --strict internal main.py temp/scripts/benchmarks/benchmark_qwb2026.py temp/scripts/benchmarks/report_qwb2026.py
git diff --check
```

Use `temp/.env` only for `listenaddr`; application credentials and runtime settings belong in `work/runtime/`.

## Coding Style & Naming Conventions

- Python: four-space indentation, type hints, `snake_case`, and `pathlib`.
- JavaScript/CSS: two-space indentation, ES modules, `camelCase` functions/variables, and kebab-case DOM IDs/classes. Import helpers from `static/modules/` rather than duplicating logic in `app.js`.
- Keep patches scoped. Preserve API envelopes, SSE event shapes, and existing workspace storage boundaries.

## Testing Guidelines

Python tests use pytest and are named `test_*.py`; place new cases in `temp/tests/python/`. Browser tests use Playwright in `temp/tests/e2e/` and mock the API/SSE layer. Add regression coverage for behavior changes, especially auth, downloads, environments, archives, and Codex session flows. Tests must not read or mutate real credentials under `work/runtime/`.

## Commit & Pull Request Guidelines

Use Conventional Commit-style subjects, for example `fix(ui): stabilize sidebar transition` or `refactor(workspace): separate runtime data`. Keep commits focused and explain the reason when behavior changes.

Pull requests should include a concise summary, affected areas, verification commands/results, and a screenshot or recording for visible UI changes. Call out API, configuration, migration, or security implications explicitly.

## Security & Configuration Tips

Never commit `.venv/`, `node_modules/`, `work/`, secrets, downloaded challenge files, or generated runtime databases. Do not expose tokens in logs, tests, WPs, or terminal output. Runtime installation belongs under `work/tools/`; source-controlled skill and dictionary assets stay under `resources/`.
