---
name: research-verification
description: Always verify research claims against original sources via web search; never trust local copies or cached knowledge alone
type: project
---

# Research Verification Rule

When conducting research for the RemotePFS project, follow these verification
requirements:

## Source Verification

1. **Always check original sources first.** Do not rely solely on local copies,
   cached files, or prior knowledge. Local copies may be outdated.

2. **Perform at least one web search** for each research topic to verify:
   - Product specifications (manufacturer pages, datasheets, official docs)
   - Software behavior (upstream repos, release notes, documentation)
   - Protocol details (RFCs, kernel docs, specification pages)
   - Compatibility requirements (official requirements, community findings)

3. **Prefer primary sources over secondary:**
   - Manufacturer datasheets over community wikis
   - Upstream repository README over third-party summaries
   - Official specification over blog post interpretations
   - Kernel documentation over forum posts

4. **Cross-reference claims** against at least two independent sources when
   the claim affects a critical design decision.

## Web Search Patterns

Recommended search queries for common research types:

- Hardware specs: `"<product name>" specifications datasheet`
- Linux kernel: `"<subsystem>" kernel documentation site:kernel.org`
- Protocol specs: `"<protocol>" RFC specification`
- PS5 behavior: `"PS5" "<behavior>" jailbreak homebrew`
- exFAT: `exfat specification site:microsoft.com`

## Local Copy Handling

1. **Mark local copy date** in research documents. Note when the local snapshot
   was taken.

2. **Include upstream URL** in every research document. Readers can verify
   against the current upstream.

3. **Treat local copies as snapshots**, not authoritative sources. When local
   copies and upstream disagree, upstream wins.

4. **Do not propagate unverified claims.** If a claim from a local copy cannot
   be verified against an upstream source, mark it as <code>[UNVERIFIED]</code>.

## Quality Gate

Before finalizing any research deliverable (knowledge base article, HTML
report, spec):

1. At least one web search was performed to verify key claims.
2. All specifications are cited with upstream URLs.
3. Any unverifiable claims are explicitly marked <code>[UNVERIFIED]</code>.
4. The research date is recorded.
5. Sources are listed with URLs and access dates.

## For Subagent Research Tasks

When dispatching research subagents, include this rule's requirements in
their task context:

- Must perform at least one web search per major claim.
- Must cite upstream URLs (not just local paths).
- Must mark unverifiable claims as <code>[UNVERIFIED]</code>.
- Must include research date and source list.
