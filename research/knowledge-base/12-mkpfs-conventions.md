# 12 - MkPFS Conventions and Project Style

## Source

- Local copy: `../mkpfs/` (full project)
- GitHub: https://github.com/PSBrew/MkPFS
- CLAUDE.md: `../mkpfs/CLAUDE.md`
- README: `../mkpfs/README.md`
- .claude settings: `../mkpfs/.claude/settings.json`
- Rules: `../mkpfs/.claude/rules/`

## Claude/Harness Ecosystem Patterns (Deep Study)

MkPFS uses a sophisticated `.claude/` setup that combines rules, skills,
memory, and settings into a cohesive agent workflow. RemotePFS should
replicate the patterns that make sense for a research + implementation project.

### `.claude/` Directory Structure

```
.claude/
  settings.json          # Base permissions + autoFix config
  settings.local.json    # Local permissions (git, gh, python, skills)
  MEMORY.md              # Curated project memory (durable knowledge base)
  rules/                 # Project conventions (auto-loaded reference docs)
    readme-style.md
    markdown-style.md
    venv-activation.md
    pypi-project-guidelines.md
    html-reporting.md
    tmp-usage.md
  skills/                # Reusable procedures (fork-context skills)
    fix-tests/
      SKILL.md
    related-project-add/
      SKILL.md
      references/        # Deep-dive reference files for each related project
    html-reporting/
      SKILL.md
    related-projects-index/
      SKILL.md
      references/         # Executive summaries for each related project
```

### Rules vs Skills vs Memory

- **Rules** (`rules/*.md`): Static project conventions. Auto-loaded as
  reference context. Define *how* to do things in this project: README style,
  markdown formatting, venv activation, PyPI packaging, HTML reporting,
  tmp usage. Each has YAML frontmatter with `name`, `description`, `type`.
- **Skills** (`skills/<name>/SKILL.md`): Reusable procedures with
  `context: fork` (each invocation is isolated). Define multi-step
  workflows: fix-tests (run + repair loop), related-project-add (ingest
  external source -> deep summary -> memory sync), html-reporting
  (generate companion HTML), related-projects-index (quick orientation
  to related projects).
- **Memory** (`MEMORY.md`): Curated durable knowledge base for active
  development. Not scratch - structured sections for core references,
  related projects (with summary + priority files), and update standards.
  Read first for high-signal context. Treats linked source files as
  ground truth.

### Key Skill Patterns to Adopt

#### related-project-add (10.5KB SKILL.md)
- Most sophisticated skill: 5-phase pipeline (Intake -> Acquire ->
  Author -> Memory Sync -> Quality Gates).
- Produces 3 outputs: source artifact folder, deep summary markdown,
  MEMORY.md entry.
- Source-centric analysis rule: extract knowledge from the source
  itself, not from parent repo or other related projects.
- Deep summary quality bar: "durable technical reference, not
  lightweight overview." Required sections: source identity, scope
  metadata, executive summary, TOC, structure/modules, technical
  findings, behavior/compatibility, constraints, checklist, source
  index.
- Citation rules: every non-trivial claim points to concrete source.
  Prefer local links first, then upstream URL.

#### related-projects-index (7.7KB SKILL.md)
- Executive index format per project:
  - **What**: one-line summary
  - **Relevance**: why it matters for this project
  - **Gotchas**: edge cases/pitfalls
  - **Upstream**: source URL
  - **Internal**: pointer to deeper reference file
- This is the pattern for `knowledge-base/00-index.md` in RemotePFS.

#### html-reporting
- Produces two artifacts: chat response + companion HTML.
- HTML conventions: self-contained HTML5, inline CSS, executive
  summary near top, sections (Summary, Findings, Evidence,
  Recommendations, References), monospace code blocks, constrained
  width.
- Historical note: RemotePFS research originally produced companion HTML
  reports beside each `.md` file; they were removed 2026-09-05. The `.md`
  files are authoritative.

#### fix-tests
- Loop: inspect changes -> run validation -> group failures by root
  cause -> apply minimal fixes -> re-run -> confirm clean.

### settings.json vs settings.local.json

- `settings.json`: base permissions (uv, run-tests, WebSearch, build,
  twine) + autoFix (format, lint, test, docs with retries + timeout).
- `settings.local.json`: extended local permissions (git, gh, python,
  venv, skill invocations, additional directories).
- RemotePFS should have both: `settings.json` for reproducible CI
  permissions, `settings.local.json` for developer-specific allowances.

### MEMORY.md Pattern

- Structured sections: How To Use, Core References, Related Projects
  Knowledge Base (per-project: repo link, submodule folder, deep
  summary link, short summary, priority files), Update Standard.
- "Read this file first for high-signal project context."
- "Treat as navigation layer; use linked source files as ground truth."
- "Prefer adding compact bullets over long prose."
- RemotePFS adaptation: `state/PROGRESS.md` serves as the resumable
  state tracker (similar role but focused on research progress).

### Agent Workflow Patterns

1. **Read MEMORY.md first** for context.
2. **Load relevant rules** for conventions (auto-loaded).
3. **Invoke skills** for multi-step procedures (fix-tests,
   related-project-add, html-reporting).
4. **Dispatch parallel subagents** for independent research topics
   (scout agent for read-only exploration).
5. **Use IRC** for subagent coordination (file delivery, status).
6. **Track progress** in state file for resumability.
7. **Commit with Conventional Commits**.

### Application to RemotePFS

RemotePFS should adopt:
- `CLAUDE.md` with development guidelines.
- `.claude/settings.json` with autoFix config.
- `.claude/rules/` for project conventions (adapt from mkpfs).
- `.claude/skills/` for reusable procedures (html-reporting,
  research-workflow).
- `.claude/MEMORY.md` for project memory (core references, related
  projects, update standard).
- `state/PROGRESS.md` for resumable research state (adapted from
  mkpfs tmp-usage pattern but made durable).
- Knowledge base articles follow related-projects-index format.
- HTML reports follow html-reporting skill conventions.
## Executive Summary

MkPFS is the PSBrew org's reference project for coding style, project
organization, and documentation conventions. RemotePFS should follow the same
patterns: Python 3.11+, uv for package management, Ruff for lint/format, pytest
for testing, Google-style docstrings, and MkDocs Material for documentation.

## Package Management

- **Use `uv` only, never `pip` directly.**
- Install: `uv add package`
- Upgrade: `uv add --dev package --upgrade-package package`
- FORBIDDEN: `uv pip install`, `@latest` syntax.
- Sync: `uv sync --group dev`

## Code Quality

- **Type hints required** for all code.
- **Google-style docstrings** for all functions, classes, and modules.
  Describe parameters, return values, and raised exceptions.
- **Prefer explicit keyword arguments** when calling functions or
  instantiating classes.
- **CLI wiring** in `cli.py` (`build_cli`, `cmd_*`); core logic in domain
  modules.
- **Shared modules**: constants in `consts.py`, progress in `pbar.py`,
  utilities in `utils.py`.
- **Avoid broad exceptions** (`except Exception:`). Catch specific types.
- **Imports at top of file** unless local import is strictly necessary.
- **No `from __future__ import annotations`** unless needed for forward refs.
- **Avoid duplicate code**; extract helpers.

## Type Annotations

- Target: Python 3.11+.
- Prefer built-in generics: `list`, `dict`, `tuple`, `set` (not
  `typing.List`, etc.).
- Use `X | None` instead of `Optional[X]`.
- **All local variables must have explicit type annotations** at definition
  site, even simple literals. Example: `count: int = 0`.
- Reason about nullability: differentiate `None` from `0` or `""`.

## Ruff Configuration

```toml
[tool.ruff]
line-length = 119
target-version = "py311"

[tool.ruff.lint]
preview = true
select = ["ANN", "B", "D", "E", "F", "I", "PTH", "RUF", "SIM", "UP", "W"]
ignore = [
    "ANN401",  # Any type
    "B007",    # Unused loop variable
    "B008",    # Function call in default argument
    "B905",    # zip() without strict=True
    "COM812",  # Missing trailing comma
    "COM819",  # Trailing comma prohibited
    "D1",      # Missing docstring
    "D203",    # Blank line before class docstring
    "D205",    # Blank line between summary and description
    "D212",    # Multi-line docstring summary first line
    "D213",    # Multi-line docstring summary second line
    "D400",    # First line should end with period
    "G004",    # f-string in logging
    "ISC001",  # Implicit string concatenation single line
    "ISC002",  # Implicit string concatenation multiple lines
    "PTH123",  # open() should be Path.open()
    "RUF002",  # Ambiguous Unicode in docstrings
    "RUF003",  # Ambiguous Unicode in comments
    "SIM108",  # Use ternary instead of if/else
]
```

## Testing

- Framework: `uv run --frozen pytest`
- Coverage: `--cov --cov-report=term-missing --cov-report=xml`
- New features require tests; bug fixes require regression tests.
- CLI smoke tests in `tests/test_main.py` validate help output.
- `./run-tests.sh` runs: `uv sync`, pre-commit install, Ruff format + check
  with `--fix`, then pytest.

## Git

- **Conventional Commits** for all commit messages.
- Example: `feat: add exFAT gadget backing store`, `fix: correct sector
  size in SCSI READ_CAPACITY response`.

## README Style

- Centered hero block with project icon (rounded, shadow, light border).
- H1: `ProjectName: ShortTagline`.
- One-line product sentence.
- Top badge cluster: status, PyPI, license, Python version, docs.
- Quick-links row: emoji-prefixed, separated by middot.
- Section order: Why, Features, Installation, Commands, GUI (if any),
  Sponsorship, Contributors, Related projects, Contributing.
- Visual identity: blue primary (#2563EB), accent (#3B82F6), dark
  (#0F172A).
- Badges: shields.io, `style=for-the-badge` for top cluster, `flat-square`
  for second row.
- Images under `assets/images/`.
- **No em dashes** in prose. Use commas, hyphens, or semicolons.
- `PFS` capitalization: uppercase in class/type names (`PFSImageInfo`),
  lowercase in snake_case (`pfs_version`). Never `Pfs`.

## HTML Reporting

- Self-contained HTML5 document.
- Descriptive `<title>`.
- Executive summary near top.
- Sections: Summary, Findings, Evidence, Recommendations, References.
- Simple inline CSS for readable typography, constrained width.
- Style code blocks with monospace font, preserve whitespace.
- Historical: RemotePFS research HTML reports were removed 2026-09-05; the `.md` files are authoritative.

## pyproject.toml Structure

```toml
[project]
name = "remotepfs"
version = "0.0.1"
requires-python = ">=3.11"
license = { file = "LICENSE" }

[project.scripts]
remotepfs = "remotepfs.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["remotepfs"]

[dependency-groups]
dev = ["build", "coverage", "pytest", "pytest-cov", "ruff", "twine"]
```

## Project Structure Convention

```
remotepfs/
  remotepfs/
    __init__.py
    cli.py          # CLI wiring (build_cli, cmd_*)
    consts.py       # Constants
    utils.py        # Utility helpers
    pbar.py         # Progress bar
    gadget.py       # USB gadget emulation
    cache.py        # Multi-tier cache
    network.py      # Network protocol layer
    exfat.py        # exFAT geometry/emulation
  tests/
    test_main.py    # CLI smoke tests
  docs/             # MkDocs documentation
  README.md
  CLAUDE.md
  pyproject.toml
  run-tests.sh
  .pre-commit-config.yaml
```

## GitHub CLI Tips

- Use `GH_PAGER=cat gh <command>` to prevent pager blocking.
- Use `GIT_PAGER='' git <command>` for git commands.
- Prefer Python subprocess for automation: `subprocess.run(['gh', ...],
  capture_output=True, text=True, env={**os.environ, 'GH_PAGER': 'cat'})`.

## Application to RemotePFS

RemotePFS should follow all MkPFS conventions:

1. **Python 3.11+** with `uv` for package management.
2. **Ruff** for lint/format with same config.
3. **pytest** with coverage.
4. **Google-style docstrings** on all functions.
5. **MkDocs Material** for documentation site.
6. **Conventional Commits**.
7. **Same README structure**: hero block, badges, sections.
8. **Same pyproject.toml structure** with hatchling backend.
9. **CLAUDE.md** with development guidelines.
10. **`.claude/settings.json`** with autoFix config.
11. **`.claude/rules/`** for project conventions.
12. **`.claude/MEMORY.md`** for project memory.
