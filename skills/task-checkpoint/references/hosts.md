# Host capabilities and resource signals

Inspected 2026-10-26: macOS, Python 3.9.6 and 3.15.0 (both tested), Git 2.39.5, Codex CLI 0.164.0; Herdr integration version 10. These are observations, not prerequisites pinned forever. Review current host documentation before adding events.

| Surface | Observed/available | Integration status |
|---|---|---|
| Python runtime and boundary wrapper, macOS | Explicit checkpoint/verify/inspect/resume, errors and controlled signals | Executed in tests |
| Codex desktop account limits | `get_usage_limits`; account-wide window percentages/reset times, not task token budget | Live read observed; manual boundary tool call |
| CLI app-server | Documented account/rateLimits/read and thread/tokenUsage/updated | Native account collector verified; explicit profile identity and allowance checks |
| Codex project hooks | SessionStart, PreToolUse, PostToolUse, PreCompact, Stop, Interrupt, SessionEnd documented; installed trust system observed | Trusted global definitions preserved; native SessionEnd delivery verified on this Mac |
| Herdr | Existing managed SessionStart hook and CLI/worktree/machine helpers | Preserved; no modification of managed Herdr script |
| Existing Mods | `checkpoints --project PATH --from COMMIT --to COMMIT`, read-only selected commits | Optional existing inspection command, no duplicate store |
| Codex queue | Host-owned `queue_1.sqlite` observed | Referenced only; unsupported mutation avoided |
| Windows native | Python/Git design, relative paths, no POSIX lock dependency | Native 0.3 evidence retained (97/101 tests); 0.4/0.5 upgrade and cross-device tests deferred |
| Windows WSL | Separate Linux environment possible | Untested, no WSL installation performed |
| Other available model/chat | Portable skill and files; explicit review before work | Fresh-context exercise recorded in evidence |
| Automatic quota → semantic save → stop | Read-only app-server collector and normalized signal adapter now implemented | Native collector, preservation, persistent stop and reviewed recovery verified on this Mac |
| Separate Codex CLI worker | Explicit model/provider/ordinary effort, verified ChatGPT auth, real validation | Live fixed Sol / routed Luna synthetic task passed; current desktop chat is not switched |
| Clock-triggered boundary | Bounded foreground watch/wake with durable single-use ticket | Native minute collector dispatched an approved qualified local package; acceptance and duplicate suppression verified |
| Sudden power loss/SIGKILL | Last promoted durable generation survives if filesystem does | Cannot save missing semantic updates after death |

Official references: [Hooks](https://learn.chatgpt.com/docs/hooks), [App server](https://learn.chatgpt.com/docs/app-server), [Plugin hook trust](https://developers.openai.com/plugins/build/plugins). Upstream events may exceed installed 0.164.0. No PreModelRequest assumption is made. Hook coverage is incomplete: hosted web tools and specialized paths may bypass tool hooks. A project instruction is not a watcher.

`hook.py` receives actual lifecycle input from stdin, uses only its project `cwd` binding, and snapshots the last agent-maintained `.checkpoint/input.json` at supported boundaries. It records semantic-input provenance and deduplicates unchanged input/repository boundaries. `SessionStart` injects a reminder; it does not resume. Hooks do not poll or schedule work. Stop/SessionEnd cannot guarantee final conversation state arrived before process termination. Native trust is a separate user action; never edit `.hook-trust` or trust flags programmatically.

Before expensive work, use the host account-limit tool if available and normalize explicitly into `.checkpoint/signals.json`:

```json
{
  "account_quota": [{"remaining": 80, "unit": "percent_remaining", "observed_at": "CURRENT_TIME_WITH_TIMEZONE", "resets_at": "ACTUAL_RESET_WITH_TIMEZONE", "guard": 20}],
  "context": [],
  "api_budget": []
}
```

Every applicable account window is a separate record. Freshness defaults to 120 seconds; readings older than that, future-dated, or past reset are unknown. Remaining percentage is `100-usedPercent`, clamped to 0–100; preserve observed timestamps and units. Multiple windows use any fresh low window. Unknown context is not inferred from cumulative input tokens. Context accepts directly observed `tokens_remaining` with default guard 8000; API budget accepts explicitly known `usd_remaining` with default guard 1. These are adjustable guard reserves, not predictions of task completion cost. A short manual save is appropriate if telemetry is unavailable at a critical switch.

Run `policy --input .checkpoint/signals.json` to inspect the decision. On fresh low signals, trusted PreToolUse snapshots existing semantic input and denies the pending tool with `permissionDecision:deny`. PostToolUse/PreCompact/Stop use `continue:false` where supported. Repeated same input/worktree saves are suppressed. It blocks covered calls; a PreToolUse denial alone does not end the model turn. PostToolUse/PreCompact/Stop can stop the turn on supported paths, but does not kill jobs, mark goals complete, or schedule resumption. Refresh signals and explicitly resume after recovery; do not consume actual quota just to test exhaustion. Test signals are simulated; hook subprocess tests prove adapter behavior, not live hook delivery.

Usage Router 0.2.0 implements this extension contract: policy output retains `router_extension_version:1`, `checkpoint_required`, distinct signal statuses and reasons. The collector atomically supplies typed fresh signals; the boundary dispatcher selects a tested bounded model assignment while retaining existing task identity, checkpoint pointer, ownership authority, dependency/queue IDs and capability records. No new queue or checkpoint database is created. Billing is not inferred and paid fallback is not enabled. Local/free models still require independent qualification and a separately verified execution adapter. Read [the router guide](router.md) for controls, commands and manual limitations.

To activate prepared project hooks, start Codex from that project, open `/hooks`, inspect the new definitions and grant native trust if appropriate. Project config trust is also required. No trust bypass is installed. Interrupt and SessionEnd have a maximum three-second callback budget; large snapshots are best effort there, so milestone saves are the durable fallback.
