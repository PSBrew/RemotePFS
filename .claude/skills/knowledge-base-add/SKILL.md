---
name: knowledge-base-add
description: Add, update, or reindex research knowledge-base articles under research/knowledge-base with a deep markdown article, a 00-index.md entry, and a synchronized .claude/MEMORY.md entry.
context: fork
---

# Knowledge Base Add

Use this skill when the user asks to add or update a KB article ("KB-add") for
the RemotePFS research knowledge base.

This skill supports three source classes:

1. Web research topics (protocols, filesystems, kernel subsystems, hardware).
2. Git repositories and documentation/wiki pages.
3. Raw markdown/html reference sources.

The skill always produces three outputs:

1. Article at `research/knowledge-base/{NN}-{slug}.md`.
2. Index entry in `research/knowledge-base/00-index.md`.
3. Memory update in `.claude/MEMORY.md` under `Research Topics Index`.

The expected quality bar is a durable technical reference, not a lightweight
overview. Future implementation work must be able to proceed from the article
without rescanning all upstream sources unless verification is needed.

## Naming Rules

1. Derive `slug` from the canonical topic or source name.
2. Normalize to lowercase slug with dashes.
3. Number articles sequentially: existing articles are 01-13; the next free
   number wins. Never renumber existing articles.
4. Use the same slug for the article file name: `{NN}-{slug}.md`.
5. Never generate companion HTML files; the `.md` article is authoritative.

Examples:

- `ShadowMountPlus` -> `01-shadowmountplus.md` (existing)
- `USB OTG gadget` -> `06-usb-otg-gadget.md` (existing)
- New topic `iSCSI deep dive` -> `14-iscsi-deep-dive.md`

## Trigger Conditions

Use this skill when user intent includes any of the following:

- KB-add, add knowledge base article, update KB article.
- Research a topic and write it into the knowledge base.
- Convert research into a knowledge-base article plus memory entry.

Do not use this skill for isolated code fixes inside `src/` unless the request
explicitly includes research ingestion.

## Phase 1: Intake and Scope

Collect and normalize these inputs:

1. Topic focus and source URLs (if any).
2. Source type: `web`, `git`, or `docs`.
3. Optional draft summary provided by user.
4. Optional explicit slug override.

## Phase 2: Acquire Sources

Follow the research-verification rule at
`.claude/rules/research-verification.md`:

1. Perform at least one web search per major claim.
2. Prefer primary sources (manufacturer datasheets, upstream repos, RFCs,
   kernel documentation).
3. Cross-reference claims that affect critical design decisions.
4. Mark unverifiable claims as `[UNVERIFIED]`.

For git/docs sources, store snapshots or submodule references only when the
user asks for durable local artifacts; otherwise cite upstream URLs directly
in the article.

## Phase 3: Article Authoring

Create `research/knowledge-base/{NN}-{slug}.md` with evidence-backed,
source-centric analysis.

Required sections:

1. Title and source identity.
2. Scope metadata:
   - researched date
   - source URLs
3. Executive summary.
4. Table of contents when the article is long or multi-topic.
5. Relevant structure/modules or mechanism overview.
6. Critical technical findings tied to the RemotePFS topic focus.
7. Behavior, compatibility, and operational notes.
8. Constraints, caveats, discrepancies, and unresolved unknowns.
9. Practical checklist for implementation reuse.
10. Source index with upstream links.

When the topic is a technical implementation or protocol reference, also
include where applicable:

1. Supported formats, modes, or variants as a table.
2. End-to-end flow or pipeline description.
3. Important structs, constants, flags, config keys, and path conventions.
4. Data layout rules.
5. Validation rules and failure conditions.
6. Tooling/scripts relevant to reproducing behavior.

Depth requirements:

1. Prefer exhaustive coverage of topic-relevant behavior over brief summaries.
2. If a finding controls compatibility or implementation decisions, explain
   the actual mechanism and decision points.
3. Call out mismatches between docs, examples, and runtime behavior.
4. Every non-trivial claim must cite a concrete source URL.
5. If the user supplied a draft, treat it as a hypothesis layer: verify each
   important claim and correct mismatches.

## Phase 4: Index Synchronization

Update `research/knowledge-base/00-index.md`:

1. Add a `## {NN} - {Display Name}` section with:
   - What: one-line summary.
   - Relevance: why it matters for RemotePFS.
   - Gotchas: edge cases and pitfalls.
   - Upstream: canonical source URL.
   - Internal: `{NN}-{slug}.md`.
2. Update the Cross-Reference Matrix rows for the new topic.

Update `.claude/MEMORY.md` under `Research Topics Index`:

1. One compact bullet: `{NN} {Topic}: two-sentence summary.`
2. If the topic changes a key technical decision, also update the decisions
   table.

Idempotency rules:

1. If the article already exists, update it in place; keep the number.
2. Do not duplicate index entries for the same topic.
3. Keep unrelated entries untouched.

## Phase 5: Quality Gates

Before completion, verify all checks:

1. Article exists at `research/knowledge-base/{NN}-{slug}.md` and references
   valid sources.
2. Index entry exists in `00-index.md` with What/Relevance/Gotchas/Upstream.
3. Memory entry exists under `Research Topics Index`.
4. Naming follows the `{NN}-{slug}.md` standard.
5. No companion HTML file was created.
6. Article contains actionable technical information derived from the
   sources themselves, not just descriptive prose.
7. Important compatibility-affecting claims are backed by direct source
   references.
8. Unverifiable claims are marked `[UNVERIFIED]`.

If any check fails, fix before completing.

## Reindex Mode

When the user asks to reindex/update an article:

1. Refresh sources and verify changed claims.
2. Update researched date and changed findings.
3. Patch index and memory entries; do not append duplicates.

## Failure Handling

If sources are partially inaccessible:

1. Continue in degraded mode with available material.
2. Mark missing sources explicitly in the article.
3. List follow-up actions needed to reach full fidelity.

Never fabricate findings for unavailable content.

## Repository-Specific Guardrails

1. Keep transient files under `tmp/` only.
2. Keep durable research articles under `research/knowledge-base/`.
3. Do not create standalone docs outside requested output paths.
4. Preserve existing formatting in `00-index.md` and `.claude/MEMORY.md`.
5. The `.md` article is authoritative; no committed HTML.
