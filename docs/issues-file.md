# The issues file

`reviewtrace extract` writes an `issues.yml` you can correct before auditing; you can also write
one from scratch (Mode C).

```yaml
version: 1
issues:
  - id: RT-001                 # optional; defaults to RT-001, RT-002, ...
    reviewer: Supervisor       # optional
    date: 2024-04-01           # optional
    comment: >                 # required
      Explain how the sample size was calculated.
    anchor: >                  # optional: the text the comment refers to
      The study included 396 respondents.
    context: ""                # optional: free text kept with the issue
    location:                  # optional
      section: Sample Size     # a heading, or a number such as "3.3"
      paragraph: 84            # ReviewTrace paragraph number in the ORIGINAL (see `extract`)
      paragraph_end: 86
      table: 2                 # the second table in the original
    category: EXPLAIN          # optional; overrides the classifier
    severity: major            # major | minor | suggestion
    source_id: "4"             # optional; your own reference
    parts:                     # optional; overrides compound-comment decomposition
      - Define the theory
      - Explain its assumptions
```

Rules:

- `version` must be `1`. Unknown keys are errors, so a typo like `comments:` is caught.
- `comment` may not be empty; ids must be unique; at most 5000 issues; file at most 5 MB.
- Only safe YAML is read. Tags such as `!!python/object` are rejected.
- The more precise the location, the stronger the evidence: an `anchor` that occurs once in the
  original, or a `paragraph`, gives an exact target; a `section` alone gives a section-level
  target.
