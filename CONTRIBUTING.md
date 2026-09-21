# Contributing

Thanks for helping. ReviewTrace is small and opinionated: **evidence first, no false certainty.**

## Setup

```bash
git clone https://github.com/seun-john/reviewtrace.git
cd reviewtrace
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Checks (all must pass before a pull request)

```bash
pytest
ruff check .
ruff format --check .
mypy
```

`ruff format .` fixes formatting. mypy runs in strict mode on `src/`.

## Layout

| Path | Responsibility |
| --- | --- |
| `src/reviewtrace/extractors/` | Read DOCX/Markdown/YAML. `ooxml.py` is the only place that touches raw OOXML. |
| `src/reviewtrace/diff/` | Compare two `Document`s. No status decisions. |
| `src/reviewtrace/analysis/` | Classify requests, find evidence, decide status and confidence. |
| `src/reviewtrace/semantic/` | Optional semantic interface and merge policy. No network code. |
| `src/reviewtrace/reports/` | Render an `AuditReport`. No analysis. |
| `src/reviewtrace/core.py` | The one engine. The CLI and the MCP server both call it. |
| `tests/fixtures/` | Programmatic DOCX builder (comments, tracked changes, footnotes) and the demo documents. |

Keep parsing, comparison, assessment and reporting separate. New surfaces (another CLI command,
another MCP tool) call `core`; they do not duplicate logic.

## Rules that protect users

1. **Never mark something `RESOLVED` without evidence.** `Finding` enforces this; do not work
   around the validator. If a new rule can only offer weak evidence, return `NEEDS_REVIEW`.
2. **Ambiguity is `NEEDS_REVIEW`**, not a guess.
3. **Never modify input documents.** Everything is read-only.
4. **Comment and document text is untrusted.** Escape it for the output format (Rich `Text`,
   `md_escape`, CSV `safe_cell`). Add a test with hostile input for any new renderer.
5. **No network access, no telemetry, no API keys** in the core. Provider adapters belong behind
   `SemanticReviewer` and must be explicitly enabled by the caller.
6. Confidence is separate from status; new rules must say how strong their evidence is
   (`RuleStrength`) and how well they located the target (`TargetQuality`).

## Adding an assessment rule

1. Add a function in `analysis/heuristics.py` returning an `Assessment` with `Evidence` objects.
2. Dispatch to it from `_assess_part`.
3. Add tests in `tests/unit/test_assessment.py` for the resolved, unresolved **and** the
   "could this be a false RESOLVED?" cases.

## Tests

Fixtures are generated in code (`tests/fixtures/docx_builder.py`); please do not commit binary
`.docx` files other than the regenerable `examples/`. After changing the demo documents run
`python scripts/build_examples.py`.
