# Installation and ongoing collection

This folder is a portable Agent Skill. Copy it into the harness's supported skills directory. The included installer supports personal Codex (`$CODEX_HOME/skills/token-audit`) and Claude Code (`~/.claude/skills/token-audit`) discovery. It refuses to overwrite an existing installation. Restart the harness or start a fresh task if discovery is cached. Installation is not automatic skill execution on every turn.

```sh
python3 scripts/install.py
```

Run the collector on demand, or leave this process running on any platform with Python 3.9+:

```sh
python3 <skill-directory>/scripts/token_audit.py --watch 60 --out <report-directory> --include-prompts
```

Ctrl-C stops foreground monitoring. Sources are rescanned at each interval, trading simplicity and idempotence for I/O cost on large histories. Increase the interval for large archives. Keep the report directory out of source directories. One collector should own each output directory; do not manually refresh the monitor's output concurrently.

## macOS background collector

```sh
python3 scripts/install.py --enable-monitor
```

This installs both skill copies, runs an initial snapshot, and registers `local.token-audit.collector` with launchd, scanning once every 60 seconds while logged in. It uses the current Python interpreter, so that interpreter must remain available. No LLM calls, paid APIs, telemetry servers, or network access are used. It captures only logs readable by that local process; new calls appear after the harness writes usage. It starts again at login. Sleep/logout pauses collection; the next scan reconstructs the available history.

Reports: `~/.local/share/token-audit/report.md`, `ledger.jsonl`, `coverage.json`. Prompt snippets are enabled for this personal monitor to identify expensive prompts. `monitor.log` captures collector output/errors. Directories are private to the user where created. Source logs are never modified.

Stop the monitor:

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.token-audit.collector.plist
```

To prevent next-login startup, also remove that exact plist. Skill folders and report data can be removed separately when no longer wanted. The installer never edits AGENTS.md, CLAUDE.md, provider credentials, or existing hook settings.

## Using the skill

Codex: invoke `$token-audit` and ask which prompts used the most tokens.
Claude Code: invoke `/token-audit` or ask to use the token-audit skill.
Other harnesses: load SKILL.md and supply compatible request-level exports via --import-jsonl.

Every new conversation with accessible local logs is included by the collector regardless of whether the model invokes the skill. Exact coverage of other devices, remote workers, browser-only chats, and unsupported harnesses requires their own telemetry/export integration.

## Terminal summary and inspection

Define this function in the current shell for a short command (add it to your shell configuration yourself if desired):

```sh
token-audit() { python3 "$HOME/.codex/skills/token-audit/scripts/query.py" "$@"; }
token-audit summary
token-audit summary --group-by turn_id --since 2026-09-25
token-audit categories --harness claude
token-audit calls --top 5
token-audit inspect RESPONSE_ID --context
```

Output is JSON, suitable for piping into other tools. `--since` uses UTC; date-only means UTC midnight. Categories report known subtotals with field coverage, not missing values silently treated as zero. Cache and reasoning are subsets and must not be added again to input/output. Estimated imports remain excluded from rankings and summary totals; inspect can explicitly inspect an estimated event.
