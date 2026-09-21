# ReviewTrace

Review feedback should not disappear into a revised document.

ReviewTrace maps every reviewer comment to the change that addressed it and shows the evidence.

```
reviewed.docx  +  revised.docx   ──►   ReviewTrace   ──►   Revision Compliance Report
```

A supervisor, editor, peer reviewer or client asks for changes. You send back a revised
document. ReviewTrace answers the question that follows: **were those changes actually
addressed, and where is the proof?**

It is an auditor, not a Word editor. It reads your files, never modifies them, and runs
entirely on your machine.

## What it does

- Reads Word comments (author, date, threads, resolved flag, **anchored text**, section and
  neighbouring paragraphs), tracked insertions/deletions, tables, footnotes and reference lists.
- Extracts issues from a separate reviewer report (`.docx`, `.md`, `.txt`) or from a hand-written
  `issues.yml`.
- Compares the original and revised documents structurally (sections → paragraphs → words;
  moved text is recognised as moved, not as an unrelated delete and insert).
- Assesses every issue with explainable rules and reports a **status**, a separate
  **confidence**, and the **evidence** behind both, with logical locations
  (`Section 2.1, paragraph 7`; never page numbers, which DOCX does not store).
- Produces a terminal report, Markdown, JSON, CSV, a formal traceability matrix and a
  grounded draft response-to-reviewers letter.
- Exposes the same engine as an MCP server.

## Why it exists

"The comment disappeared" is not evidence. Neither is "related words appear somewhere in the
revised document", "the document changed nearby", or a confident-sounding paragraph from a
language model. ReviewTrace never marks a comment `RESOLVED` on any of those grounds. Every status
must be backed by evidence, and ambiguity becomes `NEEDS_REVIEW` instead of being forced into
resolved/unresolved.

ReviewTrace also keeps three things apart that are easy to confuse:

| Concept | Where it comes from | Field |
| --- | --- | --- |
| The reviewer's tool says the comment is resolved | Word metadata | `word_comment_resolved` |
| The document changed at the place the comment points to | An objective diff | `document_changed`, `evidence` |
| The request was addressed | ReviewTrace's assessment | `status`, `confidence` |

## Example output

The repository ships a small thesis chapter (`examples/thesis/`). The supervisor's four comments:

1. Explain the major constructs of the Health Belief Model.
2. Show how the model relates to the present study.
3. Add recent references.
4. Remove the repeated paragraph in Section 2.4.

The revision fully addresses comment 1, only loosely touches comment 2, adds one reference for
comment 3, and leaves the duplicated paragraph in place. Running

```bash
reviewtrace audit examples/thesis/reviewed.docx examples/thesis/revised.docx
```

prints (abridged to two of the four issues):

```
REVIEWTRACE
word comments · semantic assessment: disabled

  Review issues:    4
  RESOLVED          1
  UNRESOLVED        1
  NEEDS REVIEW      2
────────────────────────────────────────────────────────────
┌─ RT-001 ────────────────────────────────────────────────────────────────────────────────┐
│ Reviewer: Prof. Adaeze Okafor                                                           │
│ “Explain the major constructs of the Health Belief Model.”                              │
│ Anchored text: “The Health Belief Model”                                                │
│                                                                                         │
│ RESOLVED   Confidence: MEDIUM   (human review recommended)                              │
│ Reviewer's tool: n/a  ·  Document: changed  ·  Basis: deterministic/lexical-coverage    │
│                                                                                         │
│ Evidence:                                                                               │
│ + 67 words of new text at Section 2.1 Theoretical Framework, paragraph 7; contains the  │
│ requested terms (constructs)                                                            │
│ + 2.1 Theoretical Framework grew from 9 to 90 words (+81)                               │
│ + tracked insertion by A. Student on 2024-05-12                                         │
│                                                                                         │
│ Revised location:                                                                       │
│ Section 2.1 Theoretical Framework, paragraph 7                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
┌─ RT-004 ────────────────────────────────────────────────────────────────────────────────┐
│ Reviewer: Prof. Adaeze Okafor                                                           │
│ “Remove the repeated paragraph in Section 2.4.”                                         │
│ Anchored text: “Evidence from the region is mixed and the empirical base remains        │
│ limited, which motivates the present study.”                                            │
│                                                                                         │
│ UNRESOLVED   Confidence: HIGH                                                           │
│ Reviewer's tool: n/a  ·  Document: unchanged  ·  Basis: deterministic/duplicate-check   │
│                                                                                         │
│ Evidence:                                                                               │
│ - the duplicated paragraph (2 copies at Section 2.4 Summary of the Literature,          │
│ paragraphs 14–15) still appears 2 times in the revised document (Section 2.4 Summary of │
│ the Literature, paragraphs 16–17)                                                       │
│ - duplicate of paragraph 14 still present                                               │
│                                                                                         │
│ Revised location:                                                                       │
│ Section 2.4 Summary of the Literature, paragraphs 16–17                                 │
│                                                                                         │
│ Remaining action:                                                                       │
│ Remove the repeated paragraph.                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

The four results are `RESOLVED`, `NEEDS_REVIEW`, `NEEDS_REVIEW` and `UNRESOLVED`:

- **RT-001 is RESOLVED with MEDIUM confidence, and says why.** New text containing the requested
  concept ("constructs") was added in the section the comment points to. That is *lexical*
  evidence; whether the explanation is correct and sufficient has not been assessed, so the
  finding is flagged for human review.
- **RT-002 is NEEDS_REVIEW.** Text was added, but it never says how the model *relates* to the
  study. It may be worded differently, so ReviewTrace declines to decide.
- **RT-003 is NEEDS_REVIEW.** One 2024 reference was added. "Recent" is not an objective
  criterion, so adequacy is a human judgement. Given a criterion such as *"at least two
  references published since 2020"* the same comment is decided deterministically.
- **RT-004 is UNRESOLVED with HIGH confidence.** The identical paragraph still appears twice.

## Installation

Requires Python 3.10 or newer.

```bash
git clone https://github.com/seun-john/reviewtrace.git
cd reviewtrace
pip install -e .            # the CLI and library
pip install -e ".[mcp]"     # adds the MCP server
pip install -e ".[dev]"     # adds pytest, Ruff, mypy and the MCP SDK for development
```

The project uses standard `pyproject.toml` metadata, so [uv](https://docs.astral.sh/uv/) works too
(`uv pip install -e ".[dev]"`); CI and the test suite use plain pip.

Check it works:

```bash
reviewtrace --help
```

## Quick start

```bash
# what did the reviewer write, and where?
reviewtrace comments examples/thesis/reviewed.docx

# were the comments addressed?
reviewtrace audit examples/thesis/reviewed.docx examples/thesis/revised.docx

# keep everything: JSON, Markdown, CSV matrix and a draft response letter
reviewtrace audit examples/thesis/reviewed.docx examples/thesis/revised.docx --out-dir out/
```

The commands in this README run against the files in `examples/`; they are exercised by the test
suite. `python scripts/build_examples.py` regenerates those files.

### Commands

| Command | Purpose |
| --- | --- |
| `reviewtrace comments FILE.docx [--json]` | Show every Word comment with author, date, anchored text and context |
| `reviewtrace extract FILE -o issues.yml` | Turn comments or a reviewer report into an editable issues file |
| `reviewtrace diff ORIGINAL.docx REVISED.docx [-f json] [-v]` | Structural comparison |
| `reviewtrace audit ...` | The audit (three workflows below) |
| `reviewtrace response audit.json [--excerpts]` | Draft a response-to-reviewers letter |
| `reviewtrace matrix audit.json [-f markdown\|csv\|json]` | The traceability matrix |
| `reviewtrace inspect audit.json RT-004` | One issue with its full evidence trail |
| `reviewtrace mcp` | Run the MCP server on stdio |

`audit` accepts `-f terminal|markdown|json|csv`, `-o FILE`, `--out-dir DIR`, `-v`, and
`--fail-on unresolved|not-resolved` (exit status 3, for CI). It refuses to write over an input file.

## Word-comment workflow

`reviewed.docx` contains the reviewer's comments; `revised.docx` is the response.

```bash
reviewtrace audit reviewed.docx revised.docx
```

For each comment ReviewTrace records the comment text, author, initials, date, the **anchored
text**, the paragraph and section containing it, the neighbouring paragraphs, reply threads and
Word's own resolved flag. A comment that just says "Explain this." is therefore assessed against
the sentence it was attached to, not against nothing.

It reads `word/comments.xml`, `commentsExtended.xml`, `commentsIds.xml`, `footnotes.xml`,
`endnotes.xml` and the comment-range and tracked-change markup in `document.xml` directly, because
python-docx exposes none of that. python-docx is used to open and validate the package and to
resolve styles.

Word's resolved flag is recorded as `word_comment_resolved` and shown next to the result. It is
never used to decide the status: a reviewer can resolve a comment whose text was not changed, and
an open comment may have been fixed in a later clean version.

## Separate-review-report workflow

```bash
reviewtrace audit --original original.docx --review reviewer_comments.docx --revised revised.docx
reviewtrace audit --original original.docx --review reviewer_comments.md   --revised revised.docx
```

The report can use numbered lists, bullets, `Comment 3:` labels, a comments table, or plain
prose (in which case only sentences that read as requests are extracted, and the rest are
counted and reported). Headings such as "Reviewer 2" and "Major comments" set the reviewer and
severity. A quoted passage or a section reference in an item (`In Section 3.3, "…"`) is used to
locate what it refers to.

Extract first, correct the issues by hand, then audit:

```bash
reviewtrace extract reviewer_comments.md -o issues.yml
reviewtrace audit --original original.docx --issues issues.yml --revised revised.docx
```

```yaml
version: 1
issues:
  - id: RT-001
    reviewer: Supervisor
    comment: >
      Explain how the sample size was calculated.
    anchor: >
      The study included 396 respondents.
    location:
      section: Sample Size
    category: EXPLAIN
    severity: major
```

Unknown keys are rejected (a typo such as `comments:` is an error, not silently ignored). See
[docs/issues-file.md](docs/issues-file.md).

## Compound and duplicate comments

"Define the theory, explain its assumptions and show how it relates to this study." is three
requirements. It becomes `RT-014` with sub-requirements `RT-014.1`–`RT-014.3`, each assessed on
its own. The parent is `RESOLVED` only if every part is; if only one part was addressed it is
`PARTIALLY_RESOLVED` or `NEEDS_REVIEW`. Probable duplicate comments are flagged
(`possible_duplicates`) but never merged, so each keeps its provenance.

## Traceability matrix

```bash
reviewtrace matrix out/audit.json                 # Markdown
reviewtrace matrix out/audit.json -f csv -o matrix.csv
```

Columns: ID, Reviewer, Comment, Anchored Context, Requested Action, Status, Confidence,
Evidence, Revised Location, Remaining Issue. The CSV and JSON forms add *Reviewer Marked
Resolved*, *Document Changed*, *Assessment Source*, *Method* and *Needs Human Review*. Excerpt
for the demo:

| ID | Comment | Status | Confidence | Revised Location | Remaining Issue |
| --- | --- | --- | --- | --- | --- |
| RT-001 | Explain the major constructs of the Health Belief Model. | RESOLVED | MEDIUM | Section 2.1 Theoretical Framework, paragraph 7 | |
| RT-002 | Show how the model relates to the present study. | NEEDS REVIEW | MEDIUM | Section 2.1 Theoretical Framework, paragraphs 7–8 | 'relates' not found in the new text |
| RT-003 | Add recent references. | NEEDS REVIEW | MEDIUM | | |
| RT-004 | Remove the repeated paragraph in Section 2.4. | UNRESOLVED | HIGH | Section 2.4 Summary of the Literature, paragraphs 16–17 | Remove the repeated paragraph. |

CSV cells that begin with `= + - @` are prefixed with an apostrophe so a reviewer's comment can
never execute as a spreadsheet formula.

## Response-to-reviewers generation

```bash
reviewtrace response out/audit.json -o response.md
```

Every sentence comes from a finding; nothing is invented. Only `RESOLVED` findings are answered
"Addressed."; `PARTIALLY_RESOLVED` and `UNRESOLVED` say "Further revision required" and name what
is outstanding. Excerpt:

```
### Comment 4

Remove the repeated paragraph in Section 2.4.

**Response:**

Further revision required.

**Not yet done:**

Remove the repeated paragraph.
```

The command also reports which responses need author attention before the letter is sent (any
finding that is not a HIGH-confidence resolution).

## Status definitions

| Status | Meaning |
| --- | --- |
| `RESOLVED` | Supporting evidence shows the requested change was made. Never produced without at least one supporting evidence item. |
| `PARTIALLY_RESOLVED` | Part of the request was met and one or more identifiable requirements remain (named in *Missing* / *Remaining action*). |
| `UNRESOLVED` | Evidence shows the change was not made, was reversed, or is clearly absent. |
| `NEEDS_REVIEW` | Changes exist but adequacy cannot safely be judged automatically. |
| `NOT_ASSESSABLE` | The evidence needed is not available (e.g. the comment cannot be tied to any location, or it asks for something ReviewTrace does not analyse, such as citation-style compliance). |
| `ERROR` | A technical problem prevented assessment of that issue. The rest of the audit still completes. |

Examples of what is decided deterministically: removal of anchored or duplicated text, a heading
rename (`Change 'A' to 'B'`), a numeric or text replacement (`should be 396, not 384`, including in
a named table), a paragraph moved to a named section, and reference counts against explicit
criteria (`at least two … since 2020`). Requests that ask for new explanatory content are checked
lexically and otherwise sent to `NEEDS_REVIEW`.

## Confidence definitions

Confidence is separate from status. It reflects how strong the rule behind the status is and how
firmly the request could be tied to a place in the document.

| Confidence | Typical cause |
| --- | --- |
| `HIGH` | An exact, checkable condition, evaluated where the comment points (removal, value, heading, count) |
| `MEDIUM` | Lexical evidence at a located target, or structural evidence at an anchored target |
| `LOW` | Structural evidence only, or the request could not be located firmly |

`RESOLVED` is never `LOW`: if the evidence would only support LOW confidence the status becomes
`NEEDS_REVIEW`. Anything other than a HIGH-confidence `RESOLVED`/`UNRESOLVED` is marked
`requires_human_review`. See [docs/statuses-and-confidence.md](docs/statuses-and-confidence.md).

## Semantic assessment and its limits

Many comments are inherently semantic ("Use a more convincing argument"). ReviewTrace works
without any language model and by default **semantic assessment is disabled**: such comments
become `NEEDS_REVIEW` (when changes exist at the location) or `NOT_ASSESSABLE` (when there is
nothing to examine).

The library defines the `SemanticReviewer` interface for an optional, explicitly enabled
reviewer. Design rules, enforced by `merge_assessment`:

- The reviewer is never asked "was this resolved? yes/no". It receives the request, the original
  and revised context and the detected changes, and must return structured output: status
  recommendation, explanation, evidence references, missing requirements and confidence.
- A semantic `RESOLVED`/`PARTIALLY_RESOLVED`/`UNRESOLVED` must cite evidence that exists;
  otherwise it is downgraded to `NEEDS_REVIEW`.
- Semantic confidence is capped at MEDIUM, the result is always flagged for human review, and it
  cannot override a HIGH-confidence deterministic result.
- Reports mark the source (`assessment_source: semantic` vs `deterministic`) and keep the
  deterministic status alongside.

**Version 0.1.0 ships the interface and the merge policy only. It contains no provider
adapter** (Anthropic, OpenAI, Gemini, local or OpenAI-compatible endpoints are planned), and the
CLI has no option to enable one. See [docs/semantic-review.md](docs/semantic-review.md).

Even without a model, note what "lexical" evidence is: a check that the words the reviewer asked
for appear in newly added text at the right place. It rules out unrelated edits; it does not
prove the new text is good.

## Privacy

ReviewTrace is local-first.

- Files stay on your machine. No document content is uploaded.
- No telemetry.
- No external LLM or network calls. No API key is needed or read. (The test suite asserts an
  audit runs with network connections blocked.)
- Source documents are opened read-only and never modified; every audit records each input's
  SHA-256 so you can verify that afterwards.
- Any future semantic integration must be configured explicitly.

## MCP usage

```bash
pip install "reviewtrace[mcp]"
reviewtrace mcp            # or: python -m reviewtrace.mcp.server
```

Example client configuration:

```json
{
  "mcpServers": {
    "reviewtrace": {
      "command": "reviewtrace",
      "args": ["mcp"],
      "env": { "REVIEWTRACE_MCP_ROOT": "/path/to/my/documents" }
    }
  }
}
```

Tools: `extract_review_comments`, `extract_review_issues`, `compare_documents`,
`audit_revision`, `inspect_issue`, `generate_traceability_matrix`,
`generate_response_to_reviewers`. They are thin wrappers over the same engine the CLI uses and
return structured data. Set `REVIEWTRACE_MCP_ROOT` to confine file access to one directory. The
server was developed against MCP Python SDK 2.x (`MCPServer`); a fallback for the 1.x `FastMCP`
API exists but is not covered by the test suite.

## Supported file types

| Input | Support |
| --- | --- |
| `.docx` (original, reviewed, revised, reviewer report) | Yes |
| `.md`, `.markdown`, `.txt` reviewer report | Yes |
| `issues.yml` / `.yaml` | Yes (safe YAML only) |
| `.doc`, `.odt`, `.pdf`, Google Docs, Microsoft 365 API | No (see roadmap) |

## Known limitations

- **English-language heuristics.** Request classification and term matching assume English.
- **Lexical, not semantic, checking for content requests.** A `RESOLVED` from lexical coverage
  means the requested words appear in new text at the right location, nothing more. It is
  capped at MEDIUM and flagged for review. A paraphrase that avoids the reviewer's words gives
  `NEEDS_REVIEW`, not a false `UNRESOLVED`, but a shallow sentence that uses the right words can
  give `RESOLVED`.
- **Requests without a location.** If a comment cannot be tied to a place (no anchor, section
  or distinctive topic words) it never reaches `RESOLVED`.
- **No formatting or citation-style comparison** (fonts, spacing, APA/Harvard conformity):
  reported as `NOT_ASSESSABLE`. Images are not compared; figure requests compare captions only.
- **Not read:** text boxes, headers/footers, comments in headers/footers, equations, PDFs.
- **Logical locations, not pages.** Paragraph numbers count non-empty body paragraphs in order;
  they are not shown by Word. Section numbers come from heading text.
- **Matching is text-similarity based.** A heavily rewritten paragraph can be reported as a
  removal plus an addition.
- **Comment threads** use Word's `commentsExtended` metadata; documents saved by tools that do
  not write it show no resolved state or replies.
- **Testing.** The suite was run on Python 3.14 (Windows). Python 3.10–3.13 are targeted by CI
  but were not run locally.

## Roadmap

**0.2** PDF reviewer-annotation import, better compound-comment decomposition, optional semantic
providers, HTML report, GitHub Action.

**0.3** Google Docs review support, Microsoft Word cloud integration, journal response-letter
templates, configurable institutional review workflows.

**Companion ecosystem.** ReviewTrace should later delegate specialised checks. "Ensure all
references use APA 7th" would go to a citation checker (for example CiteProof) instead of being
re-implemented here; SpecGuard, DocGuard and CleanOutput are other candidates.

Deliberately out of scope for 0.1: a GUI, hosted service, accounts, PDF annotation extraction,
automatic rewriting, accepting/rejecting tracked changes, and page-level visual comparison.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
mypy
```

## Licence

MIT. See [LICENSE](LICENSE).
