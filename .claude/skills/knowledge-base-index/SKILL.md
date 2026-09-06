---
name: knowledge-base-index
description: Rebuild and verify research/knowledge-base/00-index.md and the memory topic index, keeping entries and cross-references in sync with actual articles on disk.
context: fork
---

# Knowledge Base Index

Use this skill when the user asks to rebuild, verify, or reindex the
RemotePFS knowledge base index.

## What It Maintains

1. `research/knowledge-base/00-index.md`:
   - One `## {NN} - {Display Name}` section per article.
   - Each section has: What, Relevance, Gotchas, Upstream, Internal.
   - A Cross-Reference Matrix (Topic, Depends On, Informs) covering all
     articles.
2. `.claude/MEMORY.md` `Research Topics Index` section:
   - One compact bullet per article: `{NN} {Topic}: two-sentence summary.`

## Rebuild Procedure

1. List articles on disk: `research/knowledge-base/{NN}-{slug}.md`.
2. Detect gaps:
   - Articles on disk missing from `00-index.md`.
   - Index entries with no article on disk (stale; flag, do not silently
     delete; ask or annotate as removed).
   - Numbering collisions or gaps.
   - Broken `Internal:` links.
3. For each missing entry, derive What/Relevance/Gotchas/Upstream from the
   article's own Executive Summary, Gotchas, and Source sections. Do not
   invent claims not present in the article.
4. Regenerate the Cross-Reference Matrix from article content.
5. Sync `.claude/MEMORY.md` `Research Topics Index` bullets with the same
   set of articles.
6. Preserve:
   - Existing numbering (never renumber).
   - Index header structure and formatting style.
   - Notes that HTML reports were removed and `.md` is authoritative.

## Verification Checks

After rebuild, verify:

1. Every article on disk has exactly one index section.
2. Every index section points to an existing article file.
3. Every matrix row has a matching index section.
4. Memory bullet count matches article count.
5. No HTML file references remain (`.md` is authoritative).
6. Links to specs use `../../specs/` relative paths from
   `research/knowledge-base/` (repo-root form: `specs/`).

## Conventions

- Articles are never deleted silently; removal requires an explicit user
  request and leaves a tombstone note in the index.
- New articles get the next free number (current max: 13).
- The index is the navigation layer; articles are the ground truth.

## Failure Handling

- Unreadable or empty article: skip rebuild for it, flag in the report.
- Conflicting metadata (title mismatch between index and article): prefer
  the article's own title, update the index.
