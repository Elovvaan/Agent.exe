# Agent.exe

Portable SSD-based web agency desktop app built with Python + Tkinter.

## Project Structure

```
Agent.exe/
├─ agent_app.py
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

## What the compiled `Agent.exe` does

When launched from the SSD root, the app acts as a local control center:

1. **New Client**
   - Opens a form for Client Name, Business Type, Brand/Style, Email, Phone, Instagram, Short Description, and CTA text.
   - Creates:
     - `clients/{client-name}/assets`
     - `clients/{client-name}/site`
     - `clients/{client-name}/notes`
   - Stores form data in `clients/{client-name}/notes/client.json`.

2. **Open Client**
   - Refreshes and selects from discovered client folders.

3. **Generate Site**
   - Reads all files from `templates/base-site`.
   - Replaces placeholders:
     - `{{BUSINESS_NAME}}`
     - `{{BUSINESS_TYPE}}`
     - `{{EMAIL}}`
     - `{{PHONE}}`
     - `{{INSTAGRAM}}`
     - `{{DESCRIPTION}}`
     - `{{CTA_PRIMARY}}`
     - `{{CTA_SECONDARY}}`
   - Writes generated output to `clients/{client-name}/site`.

4. **Preview Site**
   - Opens `clients/{client-name}/site/index.html` in default browser.

5. **Export Deploy**
   - Copies `clients/{client-name}/site` into `deploy/{client-name}`.

6. **Open SSD Folder**
   - Opens SSD root folder in File Explorer on Windows.

## Build a standalone Windows executable (PyInstaller)

Run this command from the project root:

```bash
pyinstaller --noconfirm --onefile --windowed --name Agent agent_app.py
```

After build:
- `dist/Agent.exe` is your standalone executable.
- Place `Agent.exe` in SSD root (next to `clients/`, `templates/`, `deploy/`, etc.) for expected portable behavior.

## Notes

- Offline/local only: no cloud APIs, no login, no database, no web framework.
- Designed for SSD portability and robust path handling relative to where the executable is launched.
- Includes clear error dialogs for missing files/folders and invalid operations.
