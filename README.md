# Agent.exe (Portable SSD Web Agency)

## 1) SSD root layout (exact placement)
Place these items in the **SSD root** (same folder level as `Agent.exe`):

```text
<SSD_ROOT>/
├─ Agent.exe
├─ config.json
├─ clients/
├─ templates/
│  └─ base-site/
│     └─ index.html
├─ prompts/
├─ assets/
├─ deploy/
├─ tools/
└─ logs/
```

The app will auto-create any missing folders on first launch.

## 2) Launch
- Double-click `Agent.exe` from the SSD root.
- Or run in terminal:

```powershell
.\Agent.exe
```

## 3) Required template placeholders
`templates/base-site` must include these placeholders across template files:

- `{{BUSINESS_NAME}}`
- `{{BUSINESS_TYPE}}`
- `{{EMAIL}}`
- `{{PHONE}}`
- `{{INSTAGRAM}}`
- `{{DESCRIPTION}}`
- `{{CTA_PRIMARY}}`
- `{{CTA_SECONDARY}}`

## 4) Build command (Windows)
Run from project root:

```powershell
pyinstaller --onefile --windowed --name Agent agent_app.py
```

Expected Windows output:

```text
dist/Agent.exe
```

## 5) Automated Windows build artifact (GitHub Actions)
A workflow is provided at `.github/workflows/build-windows.yml` and performs:

1. Install PyInstaller on `windows-latest`.
2. Run:
   ```powershell
   pyinstaller --onefile --windowed --name Agent agent_app.py
   ```
3. Package output files into a single artifact named `Agent-windows-bundle` containing:
   - `Agent.exe`
   - `config.json`
   - `templates/base-site/*`

After pushing to GitHub, download from:

```text
https://github.com/<OWNER>/<REPO>/actions/runs/<RUN_ID>
```

Then open **Artifacts** → `Agent-windows-bundle`.

## 6) Runtime behavior
- **New Client** creates `clients/{client-name}/assets`, `site`, and `notes/client.json`.
- New client folders also include `notes/intelligence_profile.json` for per-client default behavior.
- **Open Client** selects client from SSD `clients/`.
- **Generate Site** reads `templates/base-site` and writes to `clients/{client-name}/site`.
- **Preview Site** opens `clients/{client-name}/site/index.html`.
- **Export Deploy** copies site files to `deploy/{client-name}`.
- **Open SSD Folder** opens the SSD root folder.

## 7) Support files
- `agent_app.py`
- `config.json`
- `clients/.gitkeep`
- `templates/base-site/*`

## 8) Phase 13: Internal multi-agent specialization
- Execution now routes through internal agent roles:
  - `AGENT_PLANNER` (deterministic step routing)
  - `AGENT_GENERATOR` (description/CTA proposals)
  - `AGENT_EVALUATOR` (validation + scoring gate)
  - `AGENT_OPTIMIZER` (conditional recovery suggestions)
- Agents are advisory only: truth data, validation rules, and deterministic execution order remain system-controlled.
- Per-client memory (`clients/<slug>/memory.json`) now tracks `agent_performance`.
- System learning (`notes/system_learning.json`) now stores agent-level learning signals such as confidence vs accepted outcomes and rejected outputs.

## 9) Phase 14: External action execution layer
- Added controlled external action categories:
  - `FILE_WRITE`
  - `API_CALL`
  - `WEBHOOK`
  - `COMMAND`
  - `NO_OP`
- New per-client policy file: `clients/<slug>/action_policy.json`
  - `allowed_actions`
  - `allowed_domains`
  - `max_actions_per_cycle`
  - `require_approval`
  - `command_enabled`
- Supervisor now runs:
  1. propose actions
  2. validate actions
  3. execute actions only when policy allows them without approval; otherwise record them as `pending_approval` and hold execution until approval is granted
  4. log results
- Safety guardrails:
  - `FILE_WRITE` limited to `clients/<slug>/safe_outputs/`
  - network actions limited to whitelisted domains
  - protected truth/system files are blocked
  - `COMMAND` disabled unless explicitly enabled via `command_enabled`
- `clients/<slug>/memory.json` now includes `action_history` for durable execution tracking.

## 10) Phase 18: Agent-native runtime architecture (SSD)
- Execution is now **memory-first** through an in-process runtime bus that keeps live:
  - goals
  - tasks
  - agent states
  - runtime sessions
  - pending events
- Runtime sessions are persistent and reused per client. Warm context is cached in memory and invalidated only when client source signatures change.
- Goal execution is now **event-driven** (`task_ready → task_assigned → task_started → task_completed/task_failed`), with verification and escalation events handled in the same runtime core.
- Task execution uses bounded parallel workers while still honoring:
  - `max_concurrent_tasks`
  - per-role agent capacity
  - compute budget guards
  - policy/verification/learning hooks
- SSD JSON remains authoritative for persistence and audit, but writes happen at controlled checkpoints:
  - cycle start snapshot
  - task completion batch
  - verification failures
  - goal completion
  - shutdown

## 11) Phase 19: Decentralized event routing
- Added decentralized `_route_event(event)` dispatch path with:
  - direct routing by event type
  - immediate execution for high-priority failures/escalations
  - queued fallback when routing is unsafe or fails
- Task flow is now self-propagating (`task_started` runs execution and emits `task_completed/task_failed`, which then emits verification/progress events).
- Supervisor now coordinates goals/tasks and enforces safeguards while event handlers execute through the router.
- Router telemetry includes:
  - `event_route_latency`
  - `handler_execution_time`
  - immediate vs queued route ratio
  - routing collisions and retries
- Deterministic fallback remains in place: queued drain loop still processes events when router fallback is activated.

## 12) Phase 20: Markdown-driven task injection + control layer
- Markdown files under `clients/**` are now scanned as a control surface for runtime tasks.
- Supported markdown pattern:
  - goal headers: `## Goal: ...`
  - task lines: `- [task ...] ...`
  - optional hints: `priority=high`, `explore`, `urgent`
- Folder paths map to runtime context:
  - `clients/<slug>/<subpath>/file.md` → `client_slug=<slug>`, `context_id=<slug>/<subpath>`
- Parsed markdown tasks are converted to runtime `task_ready` events and injected into the Phase 19 event bus.
- Injection is deduplicated by task fingerprint/content hash and only delta tasks are injected when markdown files change.
- Markdown hints are mapped into runtime `priority_score` and exploration flags.
- Markdown input is control-only:
  - execution still flows through routing, budget checks, policy gates, and verification.
- Injection failures emit `markdown_injection_failed` events without blocking the runtime loop.
- Markdown task execution feedback is written to `logs/markdown_runtime.log`.
