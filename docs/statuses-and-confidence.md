# Statuses and confidence

## How a status is reached

```
issue ──► classify ──► locate target ──► gather evidence ──► rule ──► status + confidence
```

1. **Classify.** A rule-based classifier assigns one of 21 categories (ADD, EXPAND, EXPLAIN,
   CLARIFY, CORRECT, REMOVE, REPLACE, UPDATE, CITE, REFERENCE, REFORMAT, RESTRUCTURE, MOVE,
   MERGE, SPLIT, VERIFY, CONSISTENCY, TABLE, FIGURE, STATISTICAL, OTHER). Unknown wording is
   `OTHER`. Comments that ask for nothing ("Good point.") are non-actionable.
2. **Locate the target**, in order of reliability: the table the comment is in; explicit
   paragraph numbers (Word anchors, `issues.yml`); the anchor text (exact, then approximate); a
   named table or figure; a named section; a quoted phrase; a section whose short heading is
   covered by the request's own words; a paragraph that alone contains three or more of the
   request's topic words. Each gives a `TargetQuality` (`EXACT`, `FUZZY`, `SECTION`, `NONE`) and
   the finding records how it was located (`located_by`).
3. **Gather evidence** from the diff: text added/removed/modified/moved, heading changes, table
   and reference changes, tracked revisions.
4. **Apply the rule** for the category (below).
5. **Finalise.** Confidence is computed from rule strength and target quality. A `RESOLVED` that
   would be `LOW` confidence, or that has no supporting evidence, becomes `NEEDS_REVIEW`.

## Rules by category

| Category | Deterministic outcomes | Otherwise |
| --- | --- | --- |
| REMOVE | Anchored text gone → `RESOLVED`; still present, or only relocated → `UNRESOLVED`; duplicate count reduced/unchanged | Rewritten instead of removed → `NEEDS_REVIEW`; no target → `NOT_ASSESSABLE` |
| REPLACE / CORRECT with values | Heading renamed; text or numeric value replaced everywhere in scope → `RESOLVED`; some places → `PARTIALLY_RESOLVED`; none → `UNRESOLVED` | Old value missing from original, or removed without replacement → `NEEDS_REVIEW` |
| TABLE | A stated value corrected in the named table; table unchanged → `UNRESOLVED`; table removal | Table changed but no checkable value → `NEEDS_REVIEW`; table not found → `NOT_ASSESSABLE` |
| REFERENCE / CITE | Explicit criteria (`at least N`, `since YEAR`); a named `Author (Year)` in list and text; unchanged list → `UNRESOLVED` | References added with no criterion → `NEEDS_REVIEW` |
| ADD / EXPAND / EXPLAIN / CLARIFY | No new text at the target → `UNRESOLVED`; all requested concepts present in new text → `RESOLVED` (MEDIUM); some → `PARTIALLY_RESOLVED` | Anything else → `NEEDS_REVIEW` |
| MOVE | Paragraph now in the named section → `RESOLVED`; unchanged → `UNRESOLVED` | Moved elsewhere → `NEEDS_REVIEW` |
| MERGE / SPLIT / RESTRUCTURE | No structural change → `UNRESOLVED` | Changes made → `NEEDS_REVIEW` |
| REFORMAT (incl. citation style), VERIFY | | `NOT_ASSESSABLE` |
| FIGURE | | `NEEDS_REVIEW` (captions only; images are not compared) |
| Subjective / vague | Anchored text and section unchanged → `UNRESOLVED` | Changed → `NEEDS_REVIEW`; no location → `NOT_ASSESSABLE` |

### Lexical coverage (content requests)

For "explain/add/expand/clarify" requests ReviewTrace looks for the *distinguishing* words of
the request in the text that is **new** to the target section. Words that already occur in the
original section, that merely name the section ("limitation" in "Limitations"), or that are
generic reviewer wording ("major", "present", "concerning") are ignored. Coordinated concepts
("validity and reliability") are checked one by one, which is what makes `PARTIALLY_RESOLVED`
possible. Text that was only moved is not counted as new. This is deliberately shallow evidence,
which is why it is capped at MEDIUM.

## Compound issues

Each sub-requirement gets its own finding. The parent is `RESOLVED` only if every part is,
`PARTIALLY_RESOLVED` if some part is resolved/partial and another is unresolved/partial,
`NEEDS_REVIEW` if the remainder is undecidable, and its confidence is the lowest of its parts.

## Confidence

| Rule strength → | Target located `EXACT`/`FUZZY` | `SECTION` | `NONE` |
| --- | --- | --- | --- |
| Deterministic (exact condition) | HIGH | HIGH | MEDIUM |
| Lexical | MEDIUM | MEDIUM | LOW |
| Structural only | MEDIUM | LOW | LOW |

`requires_human_review` is true for everything except a HIGH-confidence `RESOLVED` or
`UNRESOLVED`.

## The three concepts

Each report row shows the reviewer's tool (`word_comment_resolved`), the objective diff
observation (`document_changed`) and ReviewTrace's own `status`. They are independent: a comment
marked resolved in Word can be `UNRESOLVED`, and an open one can be `RESOLVED`.
