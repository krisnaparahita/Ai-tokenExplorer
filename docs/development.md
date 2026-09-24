# Development and internal functions

The runtime uses Python's standard library. Run from the repository root:

```sh
python3 -m unittest discover -s token-audit/tests -v
```

Tests cover duplicate usage records, streaming snapshots, cache/reasoning subsets, tool-result prompt boundaries, legacy counter resets, estimated imports, malformed records, missing usage, query filtering, ambiguous call IDs, and transcript character inventory.

## Collector: `token_audit.py`

| Function | Responsibility |
|---|---|
| `number` | Accept nonnegative integer counters; reject booleans and invalid values |
| `normalize` | Map provider usage into common fields without double-counting caches or reasoning |
| `text_content` | Extract visible text from supported content blocks |
| `prompt_label` | Create a short label while excluding recognized harness context wrappers |
| `read_records` | Read JSONL and collect malformed-record/read warnings |
| `parse` | Interpret native Codex, Claude, or canonical import records with session/turn metadata |
| `collect` | Discover files, combine parsed events, and deduplicate by harness and request identity |
| `atomic` | Replace a single output file atomically; output files as a set are not a transaction |
| `report` | Write the ledger, ranked Markdown report, and coverage metadata |
| `main` | Parse collection options and run one scan or a foreground scan loop |

## Query: `query.py`

| Function | Responsibility |
|---|---|
| `categories` | Return known category subtotals, field coverage, and subset relationships |
| `context_inventory` | Inspect visible transcript block sizes and source lines, without pretending they are exact request tokens |
| `main` | Load a snapshot, filter records, and route summary/categories/calls/inspect commands |

## Installer: `install.py`

`main` checks for existing installations, copies the skill to both personal harness directories, and optionally creates a macOS launchd service with an initial collector run. It intentionally refuses to overwrite an existing installation. It does not implement automatic upgrades or roll back partial filesystem changes if a later step fails; inspect printed output before retrying a failed installation.

## Adapter changes

Add synthetic fixtures and accounting assertions before supporting a new format. Validate request identity, streaming semantics, cache inclusion, reasoning inclusion, timestamp handling, and nested agent behavior. Never commit real user transcripts as fixtures. Preserve null for unknown breakdowns. Do not turn positional associations or visible text sizes into claims of exact token attribution.

The query tool reads an atomically replaced ledger, but the neighboring coverage file may briefly come from an adjacent scan. The monitor rescans complete histories, so very large archives may benefit from a longer interval or a future incremental implementation.
