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
- **Open Client** selects client from SSD `clients/`.
- **Generate Site** reads `templates/base-site` and writes to `clients/{client-name}/site`.
- **Preview Site** opens `clients/{client-name}/site/index.html`.
- **Export Deploy** copies site files to `deploy/{client-name}`.
- **Open SSD Folder** opens the SSD root folder.

## 7) Support files
- `agent_app.py`
- `config.json`
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
