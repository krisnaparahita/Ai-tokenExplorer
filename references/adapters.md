# Sources and adapter contract

Verified 2026-09-25 against this machine's local transcript structures and official documentation. Local transcript schemas are version-dependent implementation details. New schema versions need fixture validation before claiming complete coverage.

## Built-in adapters

Codex: `token_usage_record.payload.usage` with response ID is preferred. Do not also sum `turn_token_usage`, `thread_token_usage`, or duplicate `event_msg/token_count` records. In files without modern records, difference consecutive cumulative `total_token_usage` counters. The first legacy counter and reset counters are excluded and flagged because their attribution cannot be established. Thus legacy totals are a lower bound. Files containing both schemas use modern records only; older legacy-only portions can be missing. Repeated response IDs are counted once globally within the harness, including forked transcript copies.

Claude Code: assistant `message.usage`, keyed by message ID (fallback request ID or transcript UUID). Multiple blocks/stream snapshots of one response are coalesced using the largest observed value for each counter. This is suitable for monotonically increasing streaming usage; corrections or incompatible chunks need adapter review. A user tool-result record is not a new human prompt. The latest preceding non-meta user message supplies positional turn attribution. Parent messages copied into branches are deduplicated. This does not reconstruct a full branch graph.

Report counts are usage as recorded locally, not an independently reconciled provider invoice. Subagents and retries can only be included if their individual usage records are present. Real Codex logs have been smoke-tested; Claude fixture tests and a local smoke test validate the supported format, not every Claude distribution/version.

## Other harnesses / frontier models

Export one JSON object per completed model request, including every agent, retry with reported usage, and compaction call. Use provider response IDs as `event_id`, stable conversation/turn IDs, and retain those IDs across reimports. For streaming, emit final assembled usage, not each delta. Do not send inclusive session totals as per-request events.

```json
{"schema":"token-audit/v1","harness":"my-agent","event_id":"provider-response-123","session_id":"conversation-42","turn_id":"prompt-7","timestamp":"2026-09-25T01:00:00Z","model":"exact-provider-model-id","stage":"research","prompt_label":"Compare suppliers","measurement":"reported","usage_kind":"openai","usage":{"input_tokens":10000,"input_tokens_details":{"cached_tokens":8000},"output_tokens":1200,"output_tokens_details":{"reasoning_tokens":600}}}
```

`usage_kind` supports:

- `openai`: Responses input/output fields or Chat Completions prompt/completion fields, including nested cached/reasoning details. Input already includes caches; output includes reasoning.
- `anthropic`: input_tokens (uncached), output_tokens, cache_read_input_tokens, cache_creation_input_tokens. Combined input is their three input counters' sum.
- `canonical`: input_tokens (all input including caches), output_tokens (all generated output including reasoning), cache_read_tokens, cache_write_tokens, reasoning_tokens. Missing breakdowns remain null. Map other providers only after verifying their counting semantics.

Input/output are required nonnegative integers. `stage`, `prompt_label`, model, timestamp, and turn_id are optional; use them for meaningful attribution. `measurement` is reported (default) or estimated. Only use reported for actual provider/harness counters. Do not mix native transcripts and imported copies under different harness names or IDs: cross-format duplicate detection is not possible without shared identities.

ChatGPT or Claude web/mobile conversations without accessible usage exports cannot provide exact retrospective tracking. Character counts and locally tokenized text omit hidden context, reasoning, multimodal accounting, retries, and caching. Report unavailable usage explicitly.

## Official references

- [OpenAI counting tokens](https://developers.openai.com/api/docs/guides/token-counting): provider output counts can include tokens not visible in the answer.
- [Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced): telemetry configuration; local collector does not change it.
- [Claude Code monitoring](https://code.claude.com/docs/en/monitoring-usage): supported monitoring and usage metrics.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache accounting fields.

For managed deployments, prefer native provider/harness telemetry when it exposes request IDs and usage. This package does not configure or receive OpenTelemetry exports.
