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

4. Temporary Files
   - Use `./tmp/` for scratch files and generated HTML reports; never commit it.
   - Prefer `./tmp/<task-name>/` for grouped work; delete when done.
   - For detailed research, verification, or status reports: use the `html-reporting` skill
     (produces chat response + companion HTML in `./tmp/`; see `.claude/skills/html-reporting/SKILL.md`)

5. Git
   - Conventional Commits for all messages

6. Knowledge Base
   - `knowledge-base/` holds the 14 specification-phase research articles plus `sources/` for related-project artifacts; add/update via the knowledge-base-add and knowledge-base-index skills

## Docs Style

- README: centered hero block (icon, H1 tagline, badges, quick-links). Section order: Why, Features, Install, Commands, Sponsor, Contributors, Related projects, Contributing.
- Visual: blue (#2563EB primary, #3B82F6 accent, #0F172A dark text). Emoji headings. Shields.io badges (`style=for-the-badge`). Images under `assets/images/`.
- Prose: active voice, short paragraphs. No em dashes. No placeholder language. Code font for file names and commands.
- `PFS` capitalization: uppercase in class/type names (`PFSImageInfo`), lowercase in snake_case (`pfs_version`). Never `Pfs`.
- No MkDocs site yet; link in-repo docs (`specs/`, `knowledge-base/`).

## Code Formatting and Linting

- Ruff
  - Format: `uv run --frozen ruff format .`
  - Check: `uv run --frozen ruff check .`
  - Fix: `uv run --frozen ruff check . --fix`

## Agent / Skills

- `.claude/` contains memory, settings, and skills
- Skills available:
  - `knowledge-base-add`: add/update KB article in `knowledge-base/`
  - `knowledge-base-index`: rebuild/verify `00-index.md` and MEMORY index
  - `html-reporting`: produce companion HTML under `./tmp/`
  - `fix-tests`: run checks and fix failing tests before push

## Project Conventions

- Python 3.11+, uv, Ruff (line-length=119), pytest, Google docstrings
- Specs are canonical: `specs/01-08`
- Plans: `plans/01-project-roadmap.md`, `plans/02-design-decisions.md`
- Service code will live under `src/` with FastAPI + Pydantic v2 + uvicorn

