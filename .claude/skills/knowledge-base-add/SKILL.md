---
name: knowledge-base-add
description: Add, update, or reindex knowledge-base articles under knowledge-base, handling both research topics and related-project/GitHub ingestion, with deep summaries, source artifacts, 00-index.md entries, and synchronized .claude/MEMORY.md updates.
context: fork
---

# Knowledge Base Add

Use this skill when the user asks to add or update a KB article ("KB-add") for
the RemotePFS knowledge base.

This skill handles two ingestion classes:

1. **Research topics** (web research: protocols, filesystems, kernel subsystems, hardware).
2. **Related projects / external sources** (git repositories, GitHub projects,
   documentation/wiki pages, raw markdown/html references).

The skill always produces these outputs:

1. Article at `knowledge-base/{NN}-{slug}.md`.
2. Index entry in `knowledge-base/00-index.md`.
3. Memory update in `.claude/MEMORY.md` under `Research Topics Index`.
4. For related-project sources: optional source artifacts at
   `knowledge-base/sources/{slug}/` with a `source-manifest.md`.

The expected quality bar is a durable technical reference, not a lightweight
overview. Future implementation work must be able to proceed from the article
without rescanning all upstream sources unless verification is needed.

## Usage

- `knowledge-base-add title="<Title>" sources="URL1,URL2"`

When the request names a project or GitHub repository, index it normally:
learn the project fully, summarize it, and add it as a KB entry using the
related-project flows below.

## Naming Rules

1. Derive `slug` from the canonical topic or source name.
2. Normalize to lowercase slug with dashes.
3. Number articles sequentially: existing articles are 01-13; the next free
   number wins. Never renumber existing articles.
4. Article file: `knowledge-base/{NN}-{slug}.md`.
5. Source artifacts (related projects only): `knowledge-base/sources/{slug}/`.
6. Never generate companion HTML files; the `.md` article is authoritative.

Examples:

- `ShadowMountPlus` -> `01-shadowmountplus.md` (existing)
- `iSCSI deep dive` -> `14-iscsi-deep-dive.md` (new topic)
- GitHub repo `drakmor/ShadowMountPlus` -> `{NN}-shadowmountplus.md` +
  `sources/shadowmountplus/` (new related-project entry)

## Trigger Conditions

Use this skill when user intent includes any of the following:

- KB-add, add knowledge base article, update KB article.
- Research a topic and write it into the knowledge base.
- Add/reindex a related project, external repo, or documentation source.
- Convert research into a knowledge-base article plus memory entry.

Do not use this skill for isolated code fixes inside `src/` unless the request
explicitly includes research ingestion.

## Phase 1: Intake and Scope

Collect and normalize these inputs:

1. Topic focus or source URLs.
2. Source type: `web` (research topic), `git` (repository), or `docs`
   (wiki/html/md).
3. Optional draft summary provided by user.
4. Optional explicit slug override.

Decision logic:

1. Research topic -> web flow (Phase 2C).
2. Git repository -> repository flow (Phase 2A).
3. Docs/wiki/html/md -> snapshot flow (Phase 2B).
4. Mixed sources -> process all; merge into one summary when they represent
   a single topic.

## Phase 2: Acquire Sources

### A) Git repository / related-project flow

When the source is a project or GitHub repository, index it fully:

1. Resolve the upstream default branch and commit.
2. Acquire source artifacts under `knowledge-base/sources/{slug}/`:
   - Clone the repo (or fetch an archive of the pinned commit) into the
     sources folder, or add it as a git submodule when the user asks for a
     durable live reference:
     - `git submodule add -b {default_branch} {repo_url} knowledge-base/sources/{slug}`
     - `git submodule update --init --recursive knowledge-base/sources/{slug}`
   - When adding a submodule, ensure `.gitmodules` includes
     `branch = {default_branch}`.
3. On reindex: fetch latest refs, update recursively, keep pinned to the
   recorded commit unless the user asks for newest HEAD.
4. Write `knowledge-base/sources/{slug}/source-manifest.md` from the
   `references/source-manifest-template.md` template, recording source URL,
   indexed date, captured files, and known gaps.
5. Prefer cloning over submodules for one-off ingestion (no .gitmodules
   churn); ask the user only when the choice is ambiguous.

### B) Documentation / wiki / html / md flow

1. Create snapshot folder: `knowledge-base/sources/{slug}/`.
2. Download only topic-relevant content.
3. Preserve traceability by storing:
   - original URL list
   - fetched artifacts
   - `source-manifest.md` (URL -> local filename mapping, from the template)
4. Avoid full-site mirror unless the user explicitly asks.

### C) Web research topic flow

1. Verify claims: at least one web search per major claim; prefer primary
   sources (manufacturer datasheets, upstream repos, RFCs, kernel
   documentation); cross-reference claims affecting critical design
   decisions; mark unverifiable claims as `[UNVERIFIED]`.
2. Cite upstream URLs directly in the article; no local artifacts needed
   unless the user asks.

## Phase 3: Article Authoring

Create `knowledge-base/{NN}-{slug}.md` with evidence-backed, source-centric
analysis. Use `references/report-template.md` as scaffold when the source is
a related project.

Required sections:

1. Title and source identity.
2. Scope metadata:
   - researched date
   - source URLs
   - source artifact folder path (if any)
3. Executive summary.
4. Table of contents when the article is long or multi-topic.
5. Relevant structure/modules.
6. Critical technical findings tied to the RemotePFS topic focus.
7. Behavior, compatibility, and operational notes evidenced by the source.
8. Constraints, caveats, discrepancies, and unresolved unknowns.
9. Practical checklist for implementation reuse.
10. Source index with direct local file links and upstream links.

When the source is a technical implementation repo or protocol/filesystem
reference, also include where applicable:

1. Supported formats, modes, or variants as a table.
2. End-to-end flow or pipeline description.
3. Important structs, constants, flags, config keys, and path conventions.
4. Data layout or folder layout rules.
5. Validation rules and failure conditions.
6. Tooling/scripts/build helpers relevant to reproducing behavior.
7. A concrete generator/consumer checklist if the source implies one.

Depth requirements:

1. Prefer exhaustive coverage of topic-relevant behavior over brief summaries.
2. If a finding controls compatibility or implementation decisions, explain
   the actual code path and decision points.
3. Call out mismatches between code defaults, docs, examples, and runtime
   behavior.
4. Include enough source references that a future reader can audit every
   important claim quickly.
5. If the user supplied a draft, treat it as a hypothesis layer: verify each
   important claim and correct mismatches.

Citation rules:

1. Every non-trivial claim must point to a concrete source.
2. Prefer local snapshot/source links first, then upstream URL.
3. Keep claim-to-source mapping auditable.
4. For high-value findings, cite the most direct implementation file rather
   than only README-level docs.
5. For discrepancies, cite both sides when available.

## Phase 4: Index and Memory Synchronization

Update `knowledge-base/00-index.md`:

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
3. For related-project entries, include a priority files/pages list for fast
   re-validation (see `references/memory-entry-template.md`).

Idempotency rules:

1. If the article already exists, update it in place; keep the number.
2. Do not duplicate index or memory entries for the same topic.
3. Keep unrelated entries untouched.

## Phase 5: Quality Gates

Before completion, verify all checks:

1. Article exists at `knowledge-base/{NN}-{slug}.md` and references valid
   sources.
2. Index entry exists in `00-index.md` with What/Relevance/Gotchas/Upstream.
3. Memory entry exists under `Research Topics Index`.
4. Naming follows the `{NN}-{slug}.md` standard.
5. For related projects: source folder exists at
   `knowledge-base/sources/{slug}/` with a source manifest.
6. Local links resolve.
7. No companion HTML file was created.
8. Article is sufficiently deep for the source type:
   - implementation repos document architecture, flow, key constants/config
   - docs/wiki sources capture only relevant pages but preserve traceability
9. Article contains actionable technical information derived from the
   sources themselves, not just descriptive prose.
10. Important compatibility-affecting claims are backed by direct source
    references.
11. Unverifiable claims are marked `[UNVERIFIED]`.
12. Article does not rely on or reference implementation details from the
    RemotePFS codebase, other knowledge-base entries, or prior local
    knowledge unless the user explicitly requested a comparison.

If any check fails, fix before completing.

## Source-Centric Analysis Rule

When analyzing a related project, extract as much knowledge as possible from
that related project itself.

Required behavior:

1. Prefer the related project's own code, docs, configs, build files, tests,
   examples, and submodules as evidence.
2. Treat user-provided drafts as hypotheses to verify, not as facts to
   inherit.
3. Do not import technical claims from the RemotePFS codebase unless the
   user explicitly asks for cross-project comparison or integration mapping.
4. Do not import technical claims from other knowledge-base articles unless
   the user explicitly asks for comparison.
5. If a concept is not evidenced by the related project itself, mark it
   unknown rather than filling gaps from prior knowledge.

Forbidden by default:

1. "This matters to RemotePFS because..." framing inside the deep summary.
2. Explaining the RemotePFS implementation as if it were part of the related
   project.
3. Pulling format or workflow details from another related project to
   complete the report.

Allowed only when explicitly requested:

1. Cross-project comparison sections.
2. Integration notes between the related project and RemotePFS.
3. "How this compares to X" analysis.

## Reindex Mode

When the user asks to reindex/update:

1. Refresh sources (fetch, re-clone, or re-search as appropriate).
2. Recompute summary with explicit change-focused verification.
3. Update researched/indexed date and changed findings.
4. Patch index and memory entries; do not append duplicates.

## Failure Handling

If sources are partially inaccessible:

1. Continue in degraded mode with available artifacts.
2. Mark missing sources explicitly in the article and source manifest.
3. List follow-up actions needed to reach full fidelity.

Never fabricate findings for unavailable content.

## Repository-Specific Guardrails

1. Keep transient files under `tmp/` only.
2. Keep durable research articles under `knowledge-base/`.
3. Keep related-project source artifacts under `knowledge-base/sources/`.
4. Do not create standalone docs outside requested output paths.
5. Preserve existing formatting in `00-index.md` and `.claude/MEMORY.md`.
6. The `.md` article is authoritative; no committed HTML.

## Recommended Companion Templates

Use these templates for consistency:

1. `references/report-template.md`
2. `references/memory-entry-template.md`
3. `references/source-manifest-template.md`

Use template content as structure, but always prefer source-truth over
boilerplate.

## ShadowMountPlus-Level Standard

Use the ShadowMountPlus article as the benchmark for a successful technical
summary when the source is similarly implementation-heavy.

That means the summary should usually include, where relevant:

1. Project structure breakdown.
2. Format/support matrix.
3. End-to-end execution or mount/build flow.
4. Internal constants, structs, and option tables.
5. Layout rules and required files.
6. Config/default discrepancies and runtime caveats.
7. System paths, limits, and operational constraints.
8. Source file index for fast follow-up inspection.
9. A distilled checklist directly usable for future work derived from this
   source alone.
