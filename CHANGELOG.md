# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses semantic versioning.

## Unreleased

- Value corrections written as a statement are now assessed: "The cost is 18.6 million, not 16.8", "it should be seven, not five", "began in March, not May" and "Staffing should read 34; 43 is out of date". Number words and digits count as the same value (a digit target never matches the word "one"). A question such as "Why 3, not 4?" is still not a correction. On the 30 study artifacts this decides 10 more comments, all correctly.
- Audit reports have a `coverage` block (comments decided or not, with reasons such as `not_assessable_explain` and `needs_review`), a `Coverage:` line in the terminal and Markdown reports, and the same block in the JSON.
- `audit --min-coverage PCT`: exit 4 when fewer than PCT percent of comments were decided and `--fail-on` did not trigger.

## [0.2.0] - 2026-10-07

### Added
- `reviewtrace guard`: report unrequested changes between two DOCX files (protected wording,
  numbers, removed hedging words, table changes). Number comparison ignores formatting.

## [0.1.0] - 2026-09-20

First usable release.

### Added
- Word comment extraction: author, initials, date, text, anchored text, paragraph, section,
  neighbouring paragraphs, reply threads, and Word's resolved flag (kept separate from
  ReviewTrace's assessment). Comments in tables and footnotes are supported.
- OOXML review reader for `comments.xml`, `commentsExtended.xml`, `commentsIds.xml`,
  `footnotes.xml`, `endnotes.xml` and tracked insertions/deletions/moves in `document.xml`.
- Reviewer-report extraction from `.docx`, `.md` and `.txt` (numbered, bulleted, labelled, tabular
  and prose reports) and a strict, human-editable `issues.yml` format.
- Hierarchical structural diff (sections, paragraphs, words) with moved-text detection, plus
  table and reference-list comparison.
- Conservative request classification (21 categories), compound-comment decomposition
  (`RT-014.1`…), and duplicate flagging without merging.
- Evidence-based assessment with six statuses (`RESOLVED`, `PARTIALLY_RESOLVED`, `UNRESOLVED`,
  `NEEDS_REVIEW`, `NOT_ASSESSABLE`, `ERROR`) and a separate `HIGH`/`MEDIUM`/`LOW` confidence.
- Reports: Rich terminal output, Markdown, JSON, CSV, traceability matrix, and a grounded
  response-to-reviewers draft.
- CLI: `comments`, `extract`, `diff`, `audit`, `response`, `matrix`, `inspect`, `mcp`.
- MCP server exposing seven tools over the same core engine.
- Optional `SemanticReviewer` interface and merge policy (disabled by default; no provider
  adapters are included).
- Input SHA-256 recorded in every audit.

### Security
- Hardened ZIP/XML handling, safe YAML, and neutralisation of terminal, Markdown and CSV
  injection from untrusted comment text.
