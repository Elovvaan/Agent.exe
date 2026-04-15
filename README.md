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
pyinstaller --noconfirm --onefile --windowed --name Agent agent_app.py
```

Expected Windows output:

```text
dist/Agent.exe
```

## 5) Runtime behavior
- **New Client** creates `clients/{client-name}/assets`, `site`, and `notes/client.json`.
- **Open Client** selects client from SSD `clients/`.
- **Generate Site** reads `templates/base-site` and writes to `clients/{client-name}/site`.
- **Preview Site** opens `clients/{client-name}/site/index.html`.
- **Export Deploy** copies site files to `deploy/{client-name}`.
- **Open SSD Folder** opens the SSD root folder.

## 6) Support files
- `agent_app.py`
- `config.json`
- `templates/base-site/*`
