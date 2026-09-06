# Development Guidelines

Follow these guidelines precisely when working in this repository.

## Rules

1. Package Management
   - ONLY use uv, NEVER pip
   - Install: `uv add package`
   - Upgrade: `uv add --dev package --upgrade-package package`
   - FORBIDDEN: `uv pip install`, `@latest` syntax

2. Code Quality
   - Type hints required for all code
   - Google-style docstrings for all functions/classes/modules
   - Prefer explicit keyword arguments; avoid relying on parameter position
   - Keep service wiring isolated from domain logic; HTTP API in FastAPI layer, core logic in domain modules
   - Shared modules: constants in `consts.py`, progress in `pbar.py`, utilities in `utils.py`

3. Testing Requirements
   - Framework: `uv run --frozen pytest`
   - Convenience: `./run-tests.sh` (when added)
   - Lint/format: `uv run --frozen ruff format .` and `uv run --frozen ruff check . --fix`
   - New features require tests; bug fixes require regression tests

4. Temporary Artifacts and Reports
   - Use `./tmp/` for scratch files and generated HTML reports
   - Do not commit files from `./tmp/`
   - For detailed research/verification, produce chat response + companion HTML in `./tmp/`

5. Git
   - Conventional Commits for all messages

6. Read-only research archive
   - `research/` is a frozen archive of the specification-phase knowledge base and state; update via the knowledge-base-add/index skills

## Code Formatting and Linting

- Ruff
  - Format: `uv run --frozen ruff format .`
  - Check: `uv run --frozen ruff check .`
  - Fix: `uv run --frozen ruff check . --fix`

## Agent / Skills

- `.claude/` contains rules, memory, settings, and skills
- Skills available:
  - `knowledge-base-add`: add/update KB article in `research/knowledge-base/`
  - `knowledge-base-index`: rebuild/verify `00-index.md` and MEMORY index
  - `related-project-add`: ingest external related projects into `related-projects/`
  - `html-reporting`: produce companion HTML under `./tmp/`
  - `fix-tests`: run checks and fix failing tests before push

## Project Conventions

- Python 3.11+, uv, Ruff (line-length=119), pytest, Google docstrings
- No em dashes; `PFS` capitalization per conventions in `research/knowledge-base/12-mkpfs-conventions.md`
- Specs are canonical: `specs/01-08`
- Plans: `plans/01-project-roadmap.md`, `plans/02-design-decisions.md`
- Service code will live under `src/` with FastAPI + Pydantic v2 + uvicorn

## License

- GPL-3.0 (same as MkPFS)
