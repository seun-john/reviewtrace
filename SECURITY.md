# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately, using the repository's private vulnerability
reporting (GitHub Security Advisories) rather than a public issue. Include a minimal file that
reproduces the problem where possible.

## Threat model

ReviewTrace reads documents that may come from third parties (a reviewer's `.docx`, a client's
report). It treats every input as untrusted and is designed so that opening one cannot cause
side effects.

| Concern | Mitigation |
| --- | --- |
| Malicious or corrupt DOCX (ZIP bombs) | Entry-count and declared-size limits; every part read is size-capped |
| XML attacks (entity expansion, external entities, network DTDs) | lxml parser with entity resolution, DTD loading and network access disabled |
| YAML code execution | `yaml.safe_load` only; the schema rejects unknown keys |
| Prompt injection in comments or documents | Comment and document text is data. No text from an input is ever executed, fetched or sent anywhere, and the core makes no network calls |
| Terminal escape injection | Control characters (including ESC) are stripped from issue text; terminal output is built from Rich `Text`, never markup |
| Markdown link/image injection in reports | `md_escape` neutralises `[]`, `<>` and `|`, so rendering a report cannot fetch a URL |
| CSV formula injection | Cells starting with `= + - @` are prefixed with `'` |
| Overwriting a source file | Inputs are opened read-only; `--output` and `--out-dir` refuse to write over an input |
| Path access through the MCP server | Set `REVIEWTRACE_MCP_ROOT` to confine all paths to one directory |
| Data leaving the machine | No telemetry, no LLM calls, no API keys. A semantic reviewer must be constructed and passed in explicitly by the caller |

## Out of scope

- The MCP client, the model behind it, and any semantic reviewer supplied by a caller.
- Malware embedded in documents: ReviewTrace never executes macros or embedded objects, but it
  does not scan for them.
