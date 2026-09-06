# Source Manifest — {Display Name}

Use this file inside `knowledge-base/sources/{slug}/` when the source is a git repo snapshot, documentation/wiki/html/md, or mixed.

## Metadata

- Raw source slug: {slug}
- Canonical source URL: {canonical_url}
- Indexed on: {YYYY-MM-DD}
- Topic focus: {topic_focus}

## Captured Sources

| Source URL | Local file | Relevance note |
|-----------|------------|----------------|
| {url_1} | {local_file_1} | {why_relevant_1} |
| {url_2} | {local_file_2} | {why_relevant_2} |

## Excluded Sources

| Source URL | Exclusion reason |
|-----------|------------------|
| {url_3} | {reason_1} |

## Acquisition Notes

- Method: {manual_fetch|tool_fetch|git_clone|mixed}
- Known gaps:
  - {gap_1}
  - {gap_2}
- Retry instructions:
  1. {retry_step_1}
  2. {retry_step_2}
