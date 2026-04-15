import json
import os
import re
import shutil
import sys
import threading
import traceback
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from tkinter import (
    BOTH, END, LEFT, RIGHT, VERTICAL, W,
    Button, Entry, Frame, Label, Listbox, Menu,
    Scrollbar, StringVar, Tk, Toplevel,
    filedialog, messagebox,
)
from urllib.parse import quote


PLACEHOLDERS = {
    "{{BUSINESS_NAME}}": "name",
    "{{BUSINESS_TYPE}}": "business_type",
    "{{BRAND_STYLE}}": "brand_style",
    "{{EMAIL}}": "email",
    "{{PHONE}}": "phone",
    "{{INSTAGRAM}}": "instagram",
    "{{DESCRIPTION}}": "description",
    "{{CTA_PRIMARY}}": "cta_primary",
    "{{CTA_SECONDARY}}": "cta_secondary",
}

REQUIRED_ROOT_FOLDERS = ("clients", "templates", "prompts", "assets", "deploy", "tools", "logs")
REQUIRED_TEMPLATE_PLACEHOLDERS = (
    "{{BUSINESS_NAME}}",
    "{{BUSINESS_TYPE}}",
    "{{EMAIL}}",
    "{{PHONE}}",
    "{{INSTAGRAM}}",
    "{{DESCRIPTION}}",
    "{{CTA_PRIMARY}}",
    "{{CTA_SECONDARY}}",
)

INBOX_SCAN_INTERVAL = 4  # seconds between inbox scans

JOB_PENDING    = "pending"
JOB_PROCESSING = "processing"
JOB_COMPLETED  = "completed"
JOB_FAILED     = "failed"


@dataclass
class ClientData:
    name: str
    business_type: str
    brand_style: str
    email: str
    phone: str
    instagram: str
    description: str
    cta_primary: str
    cta_secondary: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "business_type": self.business_type,
            "brand_style": self.brand_style,
            "email": self.email,
            "phone": self.phone,
            "instagram": self.instagram,
            "description": self.description,
            "cta_primary": self.cta_primary,
            "cta_secondary": self.cta_secondary,
        }


class AgentApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.geometry("620x490")
        self.root.minsize(620, 490)

        self.base_dir = self._resolve_base_dir()
        self.paths = {
            "clients":   self.base_dir / "clients",
            "inbox":     self.base_dir / "clients" / "inbox",
            "templates": self.base_dir / "templates" / "base-site",
            "prompts":   self.base_dir / "prompts",
            "assets":    self.base_dir / "assets",
            "deploy":    self.base_dir / "deploy",
            "tools":     self.base_dir / "tools",
            "logs":      self.base_dir / "logs",
            "config":    self.base_dir / "config.json",
        }

        self.config      = self._load_config()
        self.app_title   = self.config.get("title",   "Agent.exe")
        self.app_version = self.config.get("version", "")
        self.app_tagline = self.config.get("tagline", "Portable SSD Web Agency")

        window_title = self.app_title if not self.app_version else f"{self.app_title} {self.app_version}"
        self.root.title(window_title)

        self.selected_client: str | None = None

        # Auto-mode state
        self._auto_mode  = False
        self._stop_event = threading.Event()
        self._auto_lock  = threading.Lock()
        self._stats      = {"found": 0, "processed": 0, "errors": 0}

        self._ensure_core_structure()
        self._build_ui()
        self.refresh_client_list()

        # Clean shutdown when the window is closed
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Background loop starts immediately; only processes jobs when auto mode is ON
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()

    # ------------------------------------------------------------------ #
    #  Startup / config
    # ------------------------------------------------------------------ #

    def _resolve_base_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    def _load_config(self) -> dict:
        config_path = self.paths["config"]
        if not config_path.exists():
            return {}
        try:
            with config_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            self._show_error(f"Could not read config.json: {exc}")
            return {}

    def _ensure_core_structure(self) -> None:
        for key in REQUIRED_ROOT_FOLDERS:
            self.paths[key].mkdir(parents=True, exist_ok=True)
        (self.base_dir / "templates" / "base-site").mkdir(parents=True, exist_ok=True)
        self.paths["inbox"].mkdir(parents=True, exist_ok=True)
        if not self.paths["config"].exists():
            default_config = {
                "title":   "Agent.exe",
                "version": "1.0.0",
                "tagline": "Portable SSD Web Agency",
            }
            self.paths["config"].write_text(
                json.dumps(default_config, indent=2),
                encoding="utf-8",
            )

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        main = Frame(self.root, padx=14, pady=14)
        main.pack(fill=BOTH, expand=True)

        header_text = self.app_title if not self.app_version else f"{self.app_title} {self.app_version}"
        if self.app_tagline:
            header_text = f"{header_text} — {self.app_tagline}"

        Label(main, text=header_text, font=("Segoe UI", 14, "bold")).pack(anchor=W, pady=(0, 10))

        body = Frame(main)
        body.pack(fill=BOTH, expand=True)

        left_col = Frame(body)
        left_col.pack(side=LEFT, fill=BOTH, expand=False, padx=(0, 10))

        button_specs = [
            ("New Client",      self.open_new_client_form),
            ("Open Client",     self.open_client_dialog),
            ("Generate Site",   self.generate_site),
            ("Preview Site",    self.preview_site),
            ("Export Deploy",   self.export_deploy),
            ("Open SSD Folder", self.open_ssd_folder),
        ]
        for text, command in button_specs:
            Button(left_col, text=text, width=22, pady=6, command=command).pack(pady=4, anchor=W)

        # Auto Mode toggle — store default bg so we can restore it when OFF
        self._auto_btn = Button(
            left_col,
            text="Auto Mode: OFF",
            width=22,
            pady=6,
            command=self._toggle_auto_mode,
        )
        self._auto_btn_default_bg = self._auto_btn.cget("bg")
        self._auto_btn.pack(pady=(10, 4), anchor=W)

        right_col = Frame(body)
        right_col.pack(side=LEFT, fill=BOTH, expand=True)

        Label(right_col, text="Clients", font=("Segoe UI", 11, "bold")).pack(anchor=W)
        list_frame = Frame(right_col)
        list_frame.pack(fill=BOTH, expand=True, pady=(6, 0))

        self.client_list = Listbox(list_frame, exportselection=False)
        self.client_list.pack(side=LEFT, fill=BOTH, expand=True)
        self.client_list.bind("<<ListboxSelect>>", self._on_client_select)

        scrollbar = Scrollbar(list_frame, orient=VERTICAL, command=self.client_list.yview)
        scrollbar.pack(side=LEFT, fill="y")
        self.client_list.config(yscrollcommand=scrollbar.set)

        # Status bar
        self.status_var = StringVar(value="Ready.")
        Label(main, textvariable=self.status_var, anchor=W).pack(fill="x", pady=(10, 2))

        # Live stats panel (small font, horizontal)
        stats_frame = Frame(main)
        stats_frame.pack(fill="x")
        self._stat_found_var     = StringVar(value="Jobs found: 0")
        self._stat_processed_var = StringVar(value="Processed: 0")
        self._stat_errors_var    = StringVar(value="Errors: 0")
        for var in (self._stat_found_var, self._stat_processed_var, self._stat_errors_var):
            Label(stats_frame, textvariable=var, anchor=W, font=("Segoe UI", 8)).pack(
                side=LEFT, padx=(0, 16)
            )

        # Menu
        menu = Menu(self.root)
        self.root.config(menu=menu)
        file_menu = Menu(menu, tearoff=0)
        menu.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Choose SSD Root...", command=self.choose_root)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

    # ------------------------------------------------------------------ #
    #  UI event handlers
    # ------------------------------------------------------------------ #

    def _on_client_select(self, _event=None):
        selection = self.client_list.curselection()
        if not selection:
            return
        self.selected_client = self.client_list.get(selection[0])
        self.status_var.set(f"Selected client: {self.selected_client}")

    def refresh_client_list(self) -> None:
        self.client_list.delete(0, END)
        self.paths["clients"].mkdir(parents=True, exist_ok=True)
        client_names = sorted(
            p.name for p in self.paths["clients"].iterdir()
            if p.is_dir() and p.name != "inbox"
        )
        for name in client_names:
            self.client_list.insert(END, name)

    def choose_root(self):
        new_root = filedialog.askdirectory(
            title="Choose SSD root (contains clients/, templates/, deploy/, etc.)",
            initialdir=str(self.base_dir),
        )
        if not new_root:
            return
        candidate = Path(new_root)
        if not candidate.exists():
            self._show_error(f"SSD root path does not exist: {candidate}")
            return
        if not os.access(candidate, os.R_OK | os.W_OK):
            self._show_error(f"SSD root must be readable and writable: {candidate}")
            return
        self.base_dir = candidate
        self.paths.update({
            "clients":   self.base_dir / "clients",
            "inbox":     self.base_dir / "clients" / "inbox",
            "templates": self.base_dir / "templates" / "base-site",
            "prompts":   self.base_dir / "prompts",
            "assets":    self.base_dir / "assets",
            "deploy":    self.base_dir / "deploy",
            "tools":     self.base_dir / "tools",
            "logs":      self.base_dir / "logs",
            "config":    self.base_dir / "config.json",
        })
        self._ensure_core_structure()
        self.refresh_client_list()
        self.status_var.set(f"SSD root set to: {self.base_dir}")

    # ------------------------------------------------------------------ #
    #  Client management
    # ------------------------------------------------------------------ #

    def open_new_client_form(self) -> None:
        form = Toplevel(self.root)
        form.title("New Client")
        form.geometry("520x430")
        form.transient(self.root)

        entries: dict[str, Entry] = {}
        fields = [
            ("Client Name",       "name"),
            ("Business Type",     "business_type"),
            ("Brand/Style",       "brand_style"),
            ("Email",             "email"),
            ("Phone",             "phone"),
            ("Instagram",         "instagram"),
            ("Short Description", "description"),
            ("Primary CTA",       "cta_primary"),
            ("Secondary CTA",     "cta_secondary"),
        ]

        wrapper = Frame(form, padx=12, pady=12)
        wrapper.pack(fill=BOTH, expand=True)

        for i, (label_text, key) in enumerate(fields):
            Label(wrapper, text=label_text).grid(row=i, column=0, sticky=W, pady=4)
            entry = Entry(wrapper, width=46)
            entry.grid(row=i, column=1, sticky=W, pady=4)
            entries[key] = entry

        entries["cta_primary"].insert(0, "Book a free call")
        entries["cta_secondary"].insert(0, "See our services")

        def submit():
            data = ClientData(
                name=entries["name"].get().strip(),
                business_type=entries["business_type"].get().strip(),
                brand_style=entries["brand_style"].get().strip(),
                email=entries["email"].get().strip(),
                phone=entries["phone"].get().strip(),
                instagram=entries["instagram"].get().strip(),
                description=entries["description"].get().strip(),
                cta_primary=entries["cta_primary"].get().strip(),
                cta_secondary=entries["cta_secondary"].get().strip(),
            )
            if not data.name:
                self._show_error("Client Name is required.")
                return
            self.create_client(data)
            form.destroy()

        Button(wrapper, text="Create Client", command=submit, pady=5).grid(
            row=len(fields), column=1, sticky=W, pady=14
        )

    def sanitize_client_name(self, raw_name: str) -> str:
        cleaned = raw_name.strip().lower()
        cleaned = re.sub(r"[^a-z0-9._-]+", "-", cleaned)
        cleaned = re.sub(r"-+", "-", cleaned).strip("-")
        return cleaned or "client"

    def _validate_client_name(self, raw_name: str) -> tuple[bool, str]:
        cleaned = raw_name.strip()
        if not cleaned:
            return False, "Client Name is required."
        if len(cleaned) > 80:
            return False, "Client Name is too long (max 80 characters)."
        if cleaned in {".", ".."}:
            return False, "Client Name cannot be '.' or '..'."
        if re.search(r'[<>:"/\\|?*]', cleaned):
            return False, "Client Name contains invalid filesystem characters."

        client_slug = self.sanitize_client_name(raw_name)
        reserved_names = {
            "CON", "PRN", "AUX", "NUL", "INBOX",
            "COM1", "COM2", "COM3", "COM4", "COM5",
            "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
            "LPT6", "LPT7", "LPT8", "LPT9",
        }
        normalized_slug = client_slug.rstrip(" .").split(".", 1)[0].upper()
        if normalized_slug in reserved_names:
            return (
                False,
                f"Client Name resolves to reserved Windows folder name '{client_slug}'. Choose a different name.",
            )
        return True, ""

    def create_client(self, data: ClientData) -> None:
        is_valid, validation_message = self._validate_client_name(data.name)
        if not is_valid:
            self._show_error(validation_message)
            return
        client_slug = self.sanitize_client_name(data.name)
        client_root = self.paths["clients"] / client_slug
        notes_file  = client_root / "notes" / "client.json"
        try:
            if client_root.exists() or notes_file.exists():
                overwrite = messagebox.askyesno(
                    "Client already exists",
                    f"Client '{client_slug}' already exists. Overwrite existing client metadata?",
                )
                if not overwrite:
                    self.status_var.set(f"Client creation cancelled: {client_slug}")
                    return

            (client_root / "assets").mkdir(parents=True, exist_ok=True)
            (client_root / "site").mkdir(parents=True, exist_ok=True)
            (client_root / "notes").mkdir(parents=True, exist_ok=True)

            with notes_file.open("w", encoding="utf-8") as f:
                json.dump(data.to_dict(), f, indent=2)

            self.refresh_client_list()
            self.selected_client = client_slug
            self.status_var.set(f"Created client: {client_slug}")
            messagebox.showinfo("Success", f"Client '{client_slug}' created.")
        except Exception as exc:
            self._show_error(f"Failed to create client: {exc}")

    def open_client_dialog(self):
        self.refresh_client_list()
        if self.client_list.size() == 0:
            self._show_error("No clients found. Create a client first.")
            return
        self.status_var.set("Select a client from the list.")

    def _require_selected_client(self) -> Path | None:
        if not self.selected_client:
            selection = self.client_list.curselection()
            if selection:
                self.selected_client = self.client_list.get(selection[0])
        if not self.selected_client:
            self._show_error("Please select a client first.")
            return None
        client_root = self.paths["clients"] / self.selected_client
        if not client_root.exists():
            self._show_error(f"Selected client does not exist: {self.selected_client}")
            return None
        return client_root

    def _read_client_data(self, client_root: Path) -> dict:
        notes_file = client_root / "notes" / "client.json"
        if not notes_file.exists():
            raise FileNotFoundError(f"Client data missing: {notes_file}")
        with notes_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------ #
    #  Site generation
    # ------------------------------------------------------------------ #

    def _is_html_like_file(self, path: Path) -> bool:
        return path.suffix.lower() in {".html", ".svg", ".xml"}

    def _get_safe_placeholder_value(
        self, path: Path, placeholder: str, key: str, client_data: dict
    ) -> str:
        value = str(client_data.get(key, ""))
        if not self._is_html_like_file(path):
            return value

        placeholder_name = placeholder.upper()
        if "MAILTO" in placeholder_name or (
            "EMAIL" in placeholder_name
            and any(t in placeholder_name for t in ("HREF", "URL", "LINK"))
        ):
            return quote(value, safe="@._+-")
        if "TEL" in placeholder_name or (
            "PHONE" in placeholder_name
            and any(t in placeholder_name for t in ("HREF", "URL", "LINK"))
        ):
            return quote(value, safe="+0123456789()-")
        if "INSTAGRAM" in placeholder_name and any(
            t in placeholder_name for t in ("HREF", "URL", "LINK", "PATH", "HANDLE")
        ):
            return quote(value, safe="._")

        return escape(value, quote=True)

    def _run_site_generation(self, client_root: Path, client_data: dict) -> int:
        """
        Core site-generation pipeline.
        Thread-safe — no Tkinter calls.
        Returns the number of files written.  Raises on any failure.
        """
        template_root = self.paths["templates"]
        if not template_root.exists():
            raise FileNotFoundError(f"Template folder not found: {template_root}")
        if not (template_root / "index.html").exists():
            raise FileNotFoundError(f"Template index.html missing: {template_root / 'index.html'}")

        template_files = [p for p in template_root.rglob("*") if p.is_file()]
        if not template_files:
            raise FileNotFoundError(f"No template files found in: {template_root}")

        self._validate_required_placeholders(template_files)

        target_site = client_root / "site"
        if target_site.exists():
            shutil.rmtree(target_site)
        target_site.mkdir(parents=True, exist_ok=True)

        copied = 0
        for src in template_root.rglob("*"):
            relative = src.relative_to(template_root)
            dst = target_site / relative
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if self._is_text_file(src):
                content = src.read_text(encoding="utf-8", errors="replace")
                for placeholder, key in PLACEHOLDERS.items():
                    content = content.replace(
                        placeholder,
                        self._get_safe_placeholder_value(src, placeholder, key, client_data),
                    )
                dst.write_text(content, encoding="utf-8")
            else:
                shutil.copy2(src, dst)
            copied += 1

        return copied

    def generate_site(self):
        """Manual Generate Site button handler."""
        client_root = self._require_selected_client()
        if not client_root:
            return
        try:
            client_data = self._read_client_data(client_root)
            copied = self._run_site_generation(client_root, client_data)
            self.status_var.set(f"Generated site for {self.selected_client} ({copied} files).")
            messagebox.showinfo("Generate Site", f"Generated {copied} files for {self.selected_client}.")
        except Exception as exc:
            self._show_error(f"Site generation failed: {exc}")

    # ------------------------------------------------------------------ #
    #  Preview / deploy / folder
    # ------------------------------------------------------------------ #

    def preview_site(self):
        client_root = self._require_selected_client()
        if not client_root:
            return
        index_file = client_root / "site" / "index.html"
        if not index_file.exists():
            self._show_error(f"Preview file not found: {index_file}")
            return
        webbrowser.open(index_file.resolve().as_uri())
        self.status_var.set(f"Opened preview for {self.selected_client}")

    def export_deploy(self):
        client_root = self._require_selected_client()
        if not client_root:
            return
        src_site = client_root / "site"
        if not src_site.exists():
            self._show_error("Client site folder not found. Generate the site first.")
            return
        dst = self.paths["deploy"] / self.selected_client
        try:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src_site, dst)
            self.status_var.set(f"Exported deploy package: {dst}")
            messagebox.showinfo("Export Deploy", f"Deploy export complete:\n{dst}")
        except Exception as exc:
            self._show_error(f"Deploy export failed: {exc}")

    def open_ssd_folder(self):
        target_path = self.base_dir.resolve()
        try:
            if os.name == "nt":
                os.startfile(str(target_path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(target_path.as_uri())
            self.status_var.set(f"Opened SSD folder: {target_path}")
        except Exception as exc:
            self._show_error(f"Could not open SSD folder: {exc}")

    # ------------------------------------------------------------------ #
    #  Auto mode — toggle & background loop
    # ------------------------------------------------------------------ #

    def _toggle_auto_mode(self):
        self._auto_mode = not self._auto_mode
        if self._auto_mode:
            self._auto_btn.config(text="Auto Mode: ON", bg="pale green")
            self.status_var.set("Auto mode enabled — watching inbox...")
            self._log_activity("Auto mode enabled.")
        else:
            self._auto_btn.config(text="Auto Mode: OFF", bg=self._auto_btn_default_bg)
            self.status_var.set("Auto mode disabled.")
            self._log_activity("Auto mode disabled.")

    def _auto_loop(self) -> None:
        """Background thread: wakes every INBOX_SCAN_INTERVAL seconds."""
        while not self._stop_event.is_set():
            if self._auto_mode:
                try:
                    self._scan_and_process_inbox()
                except Exception as exc:
                    self._log_activity(
                        f"[ERROR] Unhandled exception in auto loop: {exc}\n"
                        f"{traceback.format_exc()}"
                    )
            self._stop_event.wait(timeout=INBOX_SCAN_INTERVAL)

    def _scan_and_process_inbox(self) -> None:
        inbox = self.paths["inbox"]
        inbox.mkdir(parents=True, exist_ok=True)

        try:
            job_dirs = [p for p in inbox.iterdir() if p.is_dir()]
        except PermissionError as exc:
            self._log_activity(f"[ERROR] Cannot read inbox: {exc}")
            return

        pending = [d for d in job_dirs if self._get_job_status(d) == JOB_PENDING]
        if not pending:
            return

        with self._auto_lock:
            self._stats["found"] += len(pending)
        self._schedule_ui_update(self._refresh_stats)
        self._log_activity(f"Inbox scan: {len(pending)} new job(s) detected.")

        for job_dir in pending:
            self._process_job(job_dir)

    # ------------------------------------------------------------------ #
    #  Job lifecycle
    # ------------------------------------------------------------------ #

    def _get_job_status(self, job_dir: Path) -> str:
        job_file = job_dir / "job.json"
        if not job_file.exists():
            return JOB_PENDING
        try:
            data = json.loads(job_file.read_text(encoding="utf-8"))
            return data.get("status", JOB_PENDING)
        except Exception as exc:
            self._log_activity(
                f"[WARN] Invalid job.json for {job_dir.name}; marking job as failed until fixed: {exc}"
            )
            return JOB_FAILED

    def _set_job_status(self, job_dir: Path, status: str, error: str = "") -> None:
        job_file = job_dir / "job.json"
        data: dict = {}
        if job_file.exists():
            try:
                data = json.loads(job_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["status"] = status
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if status == JOB_PROCESSING and "created_at" not in data:
            data["created_at"] = data["updated_at"]
        if error:
            data["error"] = error
        elif status == JOB_COMPLETED:
            data.pop("error", None)
        try:
            job_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log_activity(f"[WARN] Could not write job.json for {job_dir.name}: {exc}")

    def _process_job(self, job_dir: Path) -> None:
        job_name = job_dir.name
        self._log_activity(f"Processing job: {job_name}")
        self._set_job_status(job_dir, JOB_PROCESSING)
        self._schedule_ui_update(self.status_var.set, f"Auto: processing '{job_name}'...")

        try:
            # 1. Read client.json from the job folder
            client_json_path = job_dir / "client.json"
            if not client_json_path.exists():
                raise FileNotFoundError(f"client.json not found in job folder: {job_name}")
            with client_json_path.open("r", encoding="utf-8") as f:
                client_data = json.load(f)

            # 2. Validate client name
            raw_name = client_data.get("name", "").strip()
            if not raw_name:
                raise ValueError("client.json is missing the required 'name' field.")
            is_valid, msg = self._validate_client_name(raw_name)
            if not is_valid:
                raise ValueError(f"Invalid client name: {msg}")

            # 3. Resolve slug and paths
            client_slug = self.sanitize_client_name(raw_name)
            client_root = self.paths["clients"] / client_slug

            # 4. Refuse to overwrite an existing client unless explicitly allowed
            overwrite_existing = client_data.get("overwrite") is True
            if client_root.exists() and not overwrite_existing:
                raise FileExistsError(
                    f"Client folder already exists for slug '{client_slug}'. "
                    f"Set 'overwrite': true in client.json to replace it."
                )

            # 5. Create client directory structure
            (client_root / "assets").mkdir(parents=True, exist_ok=True)
            (client_root / "site").mkdir(parents=True, exist_ok=True)
            (client_root / "notes").mkdir(parents=True, exist_ok=True)

            # 5. Write notes/client.json
            notes_file = client_root / "notes" / "client.json"
            notes_file.write_text(json.dumps(client_data, indent=2), encoding="utf-8")

            # 6. Copy any assets bundled with the job
            job_assets = job_dir / "assets"
            if job_assets.is_dir():
                job_assets_root = job_assets.resolve()
                for src in job_assets.rglob("*"):
                    if not src.is_file() or src.is_symlink():
                        continue

                    resolved_src = src.resolve()
                    try:
                        resolved_src.relative_to(job_assets_root)
                    except ValueError:
                        continue

                    dst = client_root / "assets" / src.relative_to(job_assets)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            # 7. Generate site
            copied = self._run_site_generation(client_root, client_data)

            # 8. Mark completed
            self._set_job_status(job_dir, JOB_COMPLETED)

            with self._auto_lock:
                self._stats["processed"] += 1

            self._log_activity(
                f"Job completed: {job_name} → {client_slug} ({copied} file(s))"
            )
            self._schedule_ui_update(self._on_job_complete, client_slug)

        except Exception as exc:
            error_msg = str(exc)
            self._set_job_status(job_dir, JOB_FAILED, error=error_msg)
            with self._auto_lock:
                self._stats["errors"] += 1
            self._log_activity(f"[ERROR] Job failed '{job_name}': {error_msg}")
            self._schedule_ui_update(
                self.status_var.set, f"Auto: job '{job_name}' failed — see logs."
            )
        finally:
            self._schedule_ui_update(self._refresh_stats)

    def _on_job_complete(self, client_slug: str) -> None:
        """Called on the main thread after a job completes."""
        self.refresh_client_list()
        self.status_var.set(f"Auto: completed job for '{client_slug}'.")

    # ------------------------------------------------------------------ #
    #  Threading helpers
    # ------------------------------------------------------------------ #

    def _schedule_ui_update(self, func, *args) -> None:
        """Queue a callable onto the Tkinter main thread (thread-safe)."""
        try:
            if args:
                self.root.after(0, func, *args)
            else:
                self.root.after(0, func)
        except Exception:
            pass  # App is closing

    def _join_background_threads(self, timeout: float = 2.0) -> None:
        """Wait briefly for app-owned background threads to stop."""
        current = threading.current_thread()
        seen = set()

        for value in self.__dict__.values():
            if not isinstance(value, threading.Thread):
                continue
            if value is current or not value.is_alive():
                continue
            ident = id(value)
            if ident in seen:
                continue
            seen.add(ident)
            value.join(timeout)

    def _on_close(self) -> None:
        """Signal background work to stop, wait briefly, then destroy the window."""
        self._auto_mode = False
        self._stop_event.set()
        self._join_background_threads(timeout=2.0)
        self.root.destroy()

    # ------------------------------------------------------------------ #
    #  Stats panel
    # ------------------------------------------------------------------ #

    def _refresh_stats(self) -> None:
        """Update the live stats labels. Must run on the main thread."""
        with self._auto_lock:
            found     = self._stats["found"]
            processed = self._stats["processed"]
            errors    = self._stats["errors"]
        self._stat_found_var.set(f"Jobs found: {found}")
        self._stat_processed_var.set(f"Processed: {processed}")
        self._stat_errors_var.set(f"Errors: {errors}")

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _is_text_file(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".html", ".css", ".js", ".txt", ".json",
            ".md", ".xml", ".svg", ".yml", ".yaml",
        }

    def _show_error(self, msg: str):
        self._log_error(msg)
        self.status_var.set(f"Error: {msg}")
        dialog_title = getattr(self, "app_title", "Agent.exe")
        messagebox.showerror(dialog_title, msg)

    def _validate_required_placeholders(self, template_files: list[Path]) -> None:
        combined = "\n".join(
            f.read_text(encoding="utf-8", errors="replace")
            for f in template_files
            if self._is_text_file(f)
        )
        missing = [p for p in REQUIRED_TEMPLATE_PLACEHOLDERS if p not in combined]
        if missing:
            raise ValueError(f"Template placeholders missing: {', '.join(missing)}")

    def _log_error(self, message: str) -> None:
        try:
            logs_path = self.paths["logs"]
            logs_path.mkdir(parents=True, exist_ok=True)
            log_file  = logs_path / "agent.log"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [ERROR] {message}\n")
                if sys.exc_info()[0] is not None:
                    f.write(traceback.format_exc())
                    f.write("\n")
        except Exception:
            pass

    def _log_activity(self, message: str) -> None:
        try:
            logs_path = self.paths["logs"]
            logs_path.mkdir(parents=True, exist_ok=True)
            log_file  = logs_path / "agent.log"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass


if __name__ == "__main__":
    root = Tk()
    app = AgentApp(root)
    root.mainloop()
