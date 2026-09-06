---
name: style
description: Project conventions for README, docs, and Markdown formatting (tone, visual identity, hero block, sections, prose rules)
type: project
---

# Style Guide

Visual and prose style for README.md, docs pages, and markdown files.

## Tone

- Practical, concise, slightly technical. Active voice, present tense.
- Short paragraphs and bullet lists. No marketing hyperbole.
- No lifecycle labels (alpha/beta) unless explicitly requested.

## Visual identity

- Brand color: blue. Palette: #2563EB (primary), #3B82F6 (accent), #0F172A (dark text).
- Emoji for section headings and quick-links: 📚 📦 ⌨️ 🖥️ 💙 🔗 💖.
- Badges: shields.io, `style=for-the-badge` for top cluster. Include: status, license, Python version. No PyPI or docs badges yet.

## Hero block (README)

Centered, in order:
1. Icon (`assets/images/icon.png`) with rounded border + shadow.
2. H1: `ProjectName: ShortTagline`.
3. One-line product sentence (H3).
4. Top badge cluster.
5. Quick-links row (emoji + middot-separated), then sponsor on its own line.

## Section order (README)

1. Why / Overview
2. Main Features
3. Installation
4. Command Overview
5. GUI (if present)
6. Sponsorship
7. Contributors and Thanks
8. Related projects
9. Contributing

## Images

- Store under `assets/images/`. Descriptive names. SVG preferred for illustrations.
- Reference by path only (no base64). Use alt text. Caption screenshots.

## Prose rules

- Sentences <= 24 words. Code font for file names and commands.
- Fenced code blocks with language. Bullets for lists.
- No em dashes. Use commas, hyphens, or semicolons.
- No placeholder language signaling unfinished work.

## Metadata alignment

- README claims must align with `pyproject.toml` (name, version, Python, license).

## Enforcement

- Before merging docs changes: `uv run --frozen ruff check .` and `uv run --frozen pytest`.
- No MkDocs site yet; skip mkdocs checks.
- Prefer in-repo docs (`specs/`, `knowledge-base/`) until a published site exists.
