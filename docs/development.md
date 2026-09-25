# Development and internal functions

The runtime uses Python's standard library. Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
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

## Linking: `linking.py`

| Function | Responsibility |
|---|---|
| `new_meta` | Side-channel filled by `parse`: session nodes, turn start times, delegating tool calls and their results |
| `note_codex_meta` / `note_codex_user` | Record Codex parent ids, role (main, helper, safety review) and the opening user text used for prompt matching |
| `note_claude` | Record Claude nodes (main or sidechain), Agent/Task and `ask_codex` tool calls, and `agentId` results |
| `link` | Join child to parent, mark each link exact or inferred, and stamp every row with `role`, `task_id`, `link_basis` |

Rules to preserve when changing it: never link on prompt text alone (time window required), skip harness-injected context blocks when matching, keep inferred links labelled, and leave anything that does not meet a rule unlinked.

## Report: `html_report.py`

`build_model` turns ledger rows into a plain data structure (totals, effort split, task roll-ups, daily totals, optional cost). `render` produces one self-contained HTML file with inline CSS, SVG and a few lines of JavaScript for tooltips and the theme toggle. All ledger text is HTML-escaped because prompt snippets are untrusted. Colours follow the validated palette in the data-visualisation guidance (categorical slots 1 to 4 for roles, a single blue ramp elsewhere); re-validate if you change them.

## Periods and deep-dive: `periods.py`, `deepdive.py`

`periods.resolve` turns a name into a half-open `[start, end)` interval in local time plus a label; keep the ambiguity rule (no bare `week` or `month`) and always echo the resolved range in output. `deepdive.summarize_prompts` groups rows by `task_id`; `deepdive.build` produces the per-prompt breakdown used by both `query.py deep-dive` and `html_report.py --task`. Observations are `fact` or `hypothesis`; add new ones only if they are computed from measured counters, and label anything explanatory as a hypothesis. Sessions are rolled up by `task_root`, so `--session` includes the helpers and Codex hand-offs a conversation started.

## Website and demo

`site/index.html` is one static page: inline CSS, a few lines of JavaScript for the copy buttons, no external requests. `site/demo/` holds two pages built from fabricated data, and `site/assets/report-preview.png` is a crop of the demo report.

Rebuild the demo after changing the report (use `TZ=UTC` so dates do not depend on your machine):

```sh
python3 scripts/make-demo-ledger.py --out /tmp/demo/ledger.jsonl
BANNER="Demo with fabricated data. Nothing on this page comes from a real conversation."
TZ=UTC python3 scripts/html_report.py --ledger /tmp/demo/ledger.jsonl --period 2026-09-08..2026-09-14 --banner "$BANNER" --out site/demo/index.html
TZ=UTC python3 scripts/html_report.py --ledger /tmp/demo/ledger.jsonl --task TASK_ID --banner "$BANNER" --out site/demo/prompt.html
```

Take `TASK_ID` from `python3 scripts/query.py prompts --ledger /tmp/demo/ledger.jsonl --top 1`. Regenerate `report-preview.png` from the top 980 pixels of `site/demo/index.html` at 1000 pixels wide.

### Publishing

Publishing makes the site public, so it is a manual step: in the repo settings under Pages, set Source to "GitHub Actions", then run the "Publish website" workflow from the Actions tab. The address is `https://<owner>.github.io/<repo>/`. The `og:image` address in `site/index.html` assumes that default.

### Who can merge

`main` is protected: changes need a pull request, the three CI checks (`tests (3.9)`, `tests (3.12)`, `package`) must pass, conversations must be resolved, and force pushes and deletion are blocked. Only people with write access can merge; the owner is the only collaborator. If a check is renamed in `validate.yml`, update the required checks in the branch protection settings or pull requests will wait forever.
