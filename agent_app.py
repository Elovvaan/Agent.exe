import json
import hashlib
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

INBOX_SCAN_INTERVAL = 6  # seconds between supervisor scans
EVALUATION_THRESHOLD = 0.85

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


@dataclass
class ClientAnalysis:
    """Structured, validated, enriched output from the decision layer."""
    name: str
    slug: str
    business_type: str
    brand_style: str
    email: str
    phone: str
    instagram: str
    description: str
    cta_primary: str
    cta_secondary: str
    completeness_score: float       # 0.0–1.0 based on fields present in raw input
    enriched_fields: list[str]      # fields absent in raw input, filled with defaults
    validation_warnings: list[str]  # non-fatal issues detected during analysis
    action_plan: list[str]          # deterministic follow-up actions inferred from analysis

    def to_dict(self) -> dict:
        """Clean field values consumed by _run_site_generation."""
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

    def to_log_dict(self) -> dict:
        """Full structured record written to analysis.log."""
        return {
            "name": self.name,
            "slug": self.slug,
            "business_type": self.business_type,
            "brand_style": self.brand_style,
            "email": self.email,
            "phone": self.phone,
            "instagram": self.instagram,
            "description": self.description,
            "cta_primary": self.cta_primary,
            "cta_secondary": self.cta_secondary,
            "completeness_score": self.completeness_score,
            "enriched_fields": self.enriched_fields,
            "validation_warnings": self.validation_warnings,
            "action_plan": self.action_plan,
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
        self._known_clients: list[str] = []

        self._ensure_core_structure()
        self._build_ui()
        self.refresh_client_list()

        # Clean shutdown when the window is closed
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Background supervisor loop starts immediately
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
        client_names = self._discover_clients()
        selected_index = None
        for idx, name in enumerate(client_names):
            self.client_list.insert(END, name)
            if name == self.selected_client:
                selected_index = idx
        self._known_clients = client_names

        if selected_index is not None:
            self.client_list.selection_set(selected_index)
            self.client_list.activate(selected_index)
        elif self.selected_client is not None:
            self.selected_client = None

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

    def _lookup_existing_client_root(self, client_slug: str) -> Path | None:
        client_root = self.paths["clients"] / client_slug
        if client_root.exists() and client_root.is_dir():
            return client_root
        return None

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

    def _analyze_client(self, raw: dict, context: dict | None = None) -> ClientAnalysis:
        """
        Decision layer: validate, enrich, and normalize raw client data.

        Raises ValueError for unrecoverable problems (empty name, reserved name).
        All other missing fields are filled with safe defaults.
        Returns a fully-populated ClientAnalysis — no free-form text, structured
        schema only.
        """
        enriched: list[str] = []
        warnings: list[str] = []
        context = context or {}
        profile = context.get("profile", {}) if isinstance(context.get("profile", {}), dict) else {}
        profile_defaults = profile.get("defaults", {}) if isinstance(profile.get("defaults", {}), dict) else {}

        def _raw_text(key: str) -> str:
            value = raw.get(key, "")
            if value is None:
                return ""
            return str(value)

        # ---- name (required; must pass filesystem validation) ----
        name = _raw_text("name").strip()
        if not name:
            raise ValueError("'name' is required and cannot be empty.")
        is_valid, validation_msg = self._validate_client_name(name)
        if not is_valid:
            raise ValueError(validation_msg)
        slug = self.sanitize_client_name(name)

        # ---- business_type ----
        business_type = _raw_text("business_type").strip()
        if not business_type:
            business_type = str(
                profile_defaults.get("business_type")
                or profile.get("business_type")
                or "Local Business"
            ).strip()
            enriched.append("business_type")

        # ---- brand_style ----
        brand_style = _raw_text("brand_style").strip()
        if not brand_style:
            brand_style = str(
                profile_defaults.get("brand_style")
                or profile.get("brand_style")
                or "modern and professional"
            ).strip()
            enriched.append("brand_style")

        # ---- email (normalize: lowercase + stripped) ----
        email = _raw_text("email").strip().lower()
        if not email:
            warnings.append("email is missing")

        # ---- phone (normalize: collapse internal whitespace) ----
        phone = re.sub(r"\s+", " ", _raw_text("phone").strip())
        if not phone:
            warnings.append("phone is missing")

        # ---- instagram (strip leading @, lowercase) ----
        instagram = _raw_text("instagram").strip()
        if instagram.startswith("@"):
            instagram = instagram[1:]
        instagram = instagram.lower()

        # ---- description (default if absent; truncate if over 500 chars) ----
        description = _raw_text("description").strip()
        if not description:
            description = f"Welcome to {name}. We provide quality services to our clients."
            enriched.append("description")
        elif len(description) > 500:
            description = description[:497] + "..."
            warnings.append("description truncated to 500 characters")

        # ---- cta_primary ----
        cta_primary = _raw_text("cta_primary").strip()
        if not cta_primary:
            cta_primary = str(
                profile_defaults.get("cta_primary")
                or profile.get("cta_primary")
                or "Book a free call"
            ).strip()
            enriched.append("cta_primary")

        # ---- cta_secondary ----
        cta_secondary = _raw_text("cta_secondary").strip()
        if not cta_secondary:
            cta_secondary = str(
                profile_defaults.get("cta_secondary")
                or profile.get("cta_secondary")
                or "See our services"
            ).strip()
            enriched.append("cta_secondary")

        # ---- completeness score (measured against raw input, before enrichment) ----
        scored_keys = (
            "name", "business_type", "brand_style", "email", "phone",
            "instagram", "description", "cta_primary", "cta_secondary",
        )
        raw_presence = {k: bool(_raw_text(k).strip()) for k in scored_keys}
        filled = sum(1 for k in scored_keys if raw_presence[k])
        completeness_score = round(filled / len(scored_keys), 2)

        # ---- action plan (deterministic ordering; analysis-only, no execution) ----
        action_plan: list[str] = []
        if completeness_score < 0.7:
            action_plan.append("ENRICH_DATA")
        if not raw_presence["description"]:
            action_plan.append("GENERATE_DESCRIPTION")
        if not raw_presence["cta_primary"] or not raw_presence["cta_secondary"]:
            action_plan.append("GENERATE_CTA")

        slug_conflict = (self.paths["clients"] / slug).exists() and (raw.get("overwrite") is not True)
        if slug_conflict:
            action_plan.append("RESOLVE_SLUG")
        if warnings:
            action_plan.append("LOG_WARNINGS")
        if all(raw_presence.values()):
            action_plan.append("PROCEED_TO_BUILD")

        return ClientAnalysis(
            name=name,
            slug=slug,
            business_type=business_type,
            brand_style=brand_style,
            email=email,
            phone=phone,
            instagram=instagram,
            description=description,
            cta_primary=cta_primary,
            cta_secondary=cta_secondary,
            completeness_score=completeness_score,
            enriched_fields=enriched,
            validation_warnings=warnings,
            action_plan=action_plan,
        )

    def _run_site_generation(self, client_root: Path, client_data: dict) -> int:
        """
        Core site-generation pipeline.
        Thread-safe — no Tkinter calls.
        Returns the number of files written.  Raises on any failure.
        """
        truth_client_root = self._lookup_existing_client_root(client_root.name)
        if not truth_client_root:
            raise FileNotFoundError(f"Client folder not found in clients/: {client_root.name}")
        client_root = truth_client_root

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

    def _resolve_unique_slug(self, base_slug: str) -> str:
        """Return a collision-safe slug by appending a numeric suffix when needed."""
        candidate = base_slug
        index = 2
        while (self.paths["clients"] / candidate).exists():
            candidate = f"{base_slug}-{index}"
            index += 1
        return candidate

    def _required_fields_valid_for_build(self, client_data: dict) -> bool:
        required_fields = (
            "name",
            "business_type",
            "brand_style",
            "email",
            "phone",
            "instagram",
            "description",
            "cta_primary",
            "cta_secondary",
        )
        return all(bool(str(client_data.get(field, "")).strip()) for field in required_fields)

    def _safe_read_json_dict(self, path: Path, label: str) -> tuple[dict, str]:
        if not path.exists():
            return {}, "missing"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                self._log_activity(f"[CONTEXT] ignored {label}: expected object got {type(data).__name__}")
                return {}, "invalid"
            return data, "ok"
        except Exception as exc:
            self._log_activity(f"[CONTEXT] ignored {label}: {exc}")
            return {}, "invalid"

    def _build_client_context(self, slug: str, raw_client_data: dict | None = None) -> dict:
        client_root = self.paths["clients"] / slug
        notes_file = client_root / "notes" / "client.json"
        profile_file = client_root / "notes" / "intelligence_profile.json"
        included_sources: list[str] = []

        identity: dict = {}
        raw_input = raw_client_data if isinstance(raw_client_data, dict) else {}
        if raw_input:
            identity = {"slug": slug, "name": str(raw_input.get("name", "")).strip()}

        skip_disk_sources = bool(
            raw_input and client_root.exists() and raw_input.get("overwrite") is not True
        )
        if skip_disk_sources:
            self._log_activity(
                f"[CONTEXT] skipped disk-backed sources for {slug}: existing client root detected "
                "while building context for inbound data without overwrite"
            )
            truth_data, truth_status = {}, "skipped"
            profile_data, profile_status = {}, "skipped"
            memory = {}
        else:
            truth_data, truth_status = self._safe_read_json_dict(notes_file, f"{slug}:notes/client.json")
            if truth_status == "ok":
                included_sources.append("truth")
                identity["name"] = str(truth_data.get("name", identity.get("name", ""))).strip()

            profile_data, profile_status = self._safe_read_json_dict(
                profile_file, f"{slug}:notes/intelligence_profile.json"
            )
            if profile_status == "ok":
                included_sources.append("profile")

            memory = self._load_client_memory(slug)
            included_sources.append("memory")
        evaluation = memory.get("last_evaluation", {})
        stable = bool(memory.get("stable", False))
        frozen = bool(profile_data.get("freeze", False) or memory.get("freeze", False))

        generated_fields = memory.get("generated_fields", {})
        execution_results = memory.get("execution_results", {})
        reusable_generated: dict[str, str] = {}
        for field in ("description", "cta_primary", "cta_secondary"):
            value = str(generated_fields.get(field, "")).strip()
            if not value:
                continue
            from_success = (
                execution_results.get("GENERATE_DESCRIPTION") == "success"
                if field == "description"
                else execution_results.get("GENERATE_CTA") == "success"
            )
            if from_success and stable:
                reusable_generated[field] = value

        required_fields = (
            "name", "business_type", "brand_style", "email", "phone",
            "instagram", "description", "cta_primary", "cta_secondary",
        )
        merged = {k: str(raw_input.get(k, "") or "").strip() for k in required_fields}
        field_sources = {k: "raw" for k in required_fields}

        for field in required_fields:
            truth_value = str(truth_data.get(field, "") or "").strip()
            if truth_value:
                merged[field] = truth_value
                field_sources[field] = "truth"
                continue
            memory_value = str(reusable_generated.get(field, "") or "").strip()
            if memory_value and not merged[field]:
                merged[field] = memory_value
                field_sources[field] = "memory"

        profile_defaults = profile_data.get("defaults", {}) if isinstance(profile_data.get("defaults", {}), dict) else {}
        for field in ("business_type", "brand_style", "cta_primary", "cta_secondary"):
            if merged[field]:
                continue
            profile_value = str(profile_defaults.get(field) or profile_data.get(field) or "").strip()
            if profile_value:
                merged[field] = profile_value
                field_sources[field] = "profile"

        source_signature_payload = {
            "slug": slug,
            "truth": truth_data,
            "profile": profile_data,
            "reusable_generated": reusable_generated,
            "raw_input": raw_input,
            "stable": stable,
            "frozen": frozen,
        }
        source_signature = hashlib.sha256(
            json.dumps(source_signature_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        self._log_activity(
            f"[CONTEXT] build slug={slug} sources={','.join(included_sources)} "
            f"stable={stable} frozen={frozen}"
        )
        self._log_activity(
            f"[CONTEXT] fields slug={slug} "
            f"{','.join(f'{k}:{v}' for k, v in sorted(field_sources.items()))}"
        )

        return {
            "slug": slug,
            "identity": identity,
            "truth_data": truth_data,
            "profile": profile_data,
            "memory": memory,
            "last_successful_generated_fields": reusable_generated,
            "last_evaluation_summary": {
                "last_evaluation": evaluation,
                "scores": memory.get("scores", {}),
                "issues": memory.get("issues", []),
            },
            "stable": stable,
            "frozen": frozen,
            "source_signature": source_signature,
            "included_sources": included_sources,
            "field_sources": field_sources,
            "analysis_input": merged,
        }

    def _load_client_memory(self, slug: str) -> dict:
        """Load structured memory for a client; return empty shape if unavailable."""
        memory_file = self.paths["clients"] / slug / "memory.json"
        empty_memory = {
            "last_action_plan": [],
            "execution_results": {},
            "generated_fields": {},
            "last_evaluation": {},
            "scores": {},
            "issues": [],
            "stable": False,
            "freeze": False,
            "source_signature": "",
            "last_context_summary": {},
            "timestamp": "",
        }
        if not memory_file.exists():
            return empty_memory
        try:
            data = json.loads(memory_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return empty_memory
            return {
                "last_action_plan": data.get("last_action_plan", []),
                "execution_results": data.get("execution_results", {}),
                "generated_fields": data.get("generated_fields", {}),
                "last_evaluation": data.get("last_evaluation", {}),
                "scores": data.get("scores", {}),
                "issues": data.get("issues", []),
                "stable": bool(data.get("stable", False)),
                "freeze": bool(data.get("freeze", False)),
                "source_signature": data.get("source_signature", ""),
                "last_context_summary": data.get("last_context_summary", {}),
                "timestamp": data.get("timestamp", ""),
            }
        except Exception as exc:
            self._log_activity(f"[MEMORY] load failed slug={slug}: {exc}")
            return empty_memory

    def _update_client_memory(self, slug: str, data: dict) -> None:
        """Persist structured memory for a client."""
        client_root = self.paths["clients"] / slug
        client_root.mkdir(parents=True, exist_ok=True)
        memory_file = client_root / "memory.json"
        existing = self._load_client_memory(slug) if memory_file.exists() else {}
        payload = {
            "last_action_plan": data.get("last_action_plan", existing.get("last_action_plan", [])),
            "execution_results": data.get("execution_results", existing.get("execution_results", {})),
            "generated_fields": data.get("generated_fields", existing.get("generated_fields", {})),
            "last_evaluation": data.get("last_evaluation", existing.get("last_evaluation", {})),
            "scores": data.get("scores", existing.get("scores", {})),
            "issues": data.get("issues", existing.get("issues", [])),
            "stable": bool(data.get("stable", existing.get("stable", False))),
            "freeze": bool(data.get("freeze", existing.get("freeze", False))),
            "source_signature": data.get("source_signature", existing.get("source_signature", "")),
            "last_context_summary": data.get("last_context_summary", existing.get("last_context_summary", {})),
            "timestamp": data.get("timestamp", datetime.now().isoformat(timespec="seconds")),
        }
        memory_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._log_activity(f"[MEMORY] updated slug={slug} path={memory_file}")

    def _persist_context_summary(self, slug: str, context: dict) -> None:
        memory = self._load_client_memory(slug)
        profile = context.get("profile", {}) if isinstance(context.get("profile", {}), dict) else {}
        defaults = profile.get("defaults", {}) if isinstance(profile.get("defaults", {}), dict) else {}
        active_profile_values = {}
        for field in ("business_type", "brand_style", "cta_primary", "cta_secondary"):
            value = defaults.get(field, profile.get(field, ""))
            if str(value).strip():
                active_profile_values[field] = value

        memory["last_context_summary"] = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "active_profile_values": active_profile_values,
            "reused_generated_fields": context.get("last_successful_generated_fields", {}),
            "evaluation_snapshot": context.get("last_evaluation_summary", {}),
            "source_signature": context.get("source_signature", ""),
        }
        memory["freeze"] = bool(context.get("frozen", False))
        memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._update_client_memory(slug, memory)
        self._log_activity(f"[CONTEXT] persisted summary slug={slug}")

    def _source_signature(self, raw_data: dict) -> str:
        normalized = json.dumps(raw_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _evaluate_client_state(self, slug: str) -> dict:
        client_root = self.paths["clients"] / slug
        notes_file = client_root / "notes" / "client.json"
        memory = self._load_client_memory(slug)
        issues_detected: list[str] = []
        recommended_actions: list[str] = []

        raw: dict = {}
        if not notes_file.exists():
            issues_detected.append("missing_client_notes")
            recommended_actions.append("restore_client_notes")
        else:
            try:
                raw = json.loads(notes_file.read_text(encoding="utf-8"))
            except Exception:
                issues_detected.append("invalid_client_notes")
                recommended_actions.append("repair_client_notes_json")

        required_fields = (
            "name", "business_type", "brand_style", "email", "phone",
            "instagram", "description", "cta_primary", "cta_secondary",
        )
        present_required = 0
        placeholder_pattern = re.compile(r"{{[^{}]+}}")
        execution_results = memory.get("execution_results", {})

        for field in required_fields:
            value = str(raw.get(field, "")).strip()
            if value:
                present_required += 1
            else:
                issues_detected.append(f"missing_{field}")

            if placeholder_pattern.search(value):
                issues_detected.append(f"placeholder_in_{field}")

            if re.search(r"\b(traceback|exception|error|failed)\b", value, re.IGNORECASE):
                issues_detected.append(f"failed_residue_in_{field}")

        completeness_score = round(present_required / len(required_fields), 2)

        description = str(raw.get("description", "")).strip()
        description_score = 1.0
        if not description:
            description_score -= 0.6
            recommended_actions.append("regenerate_description")
        if description and (len(description) < 40 or len(description) > 500):
            description_score -= 0.25
            issues_detected.append("description_length_out_of_bounds")
            recommended_actions.append("normalize_description_length")
        name_tokens = [t for t in re.split(r"[^a-z0-9]+", str(raw.get("name", "")).lower()) if len(t) >= 3]
        business_tokens = [t for t in re.split(r"[^a-z0-9]+", str(raw.get("business_type", "")).lower()) if len(t) >= 4]
        keyword_tokens = set(name_tokens[:3] + business_tokens[:3])
        if description and keyword_tokens:
            lowered_description = description.lower()
            if not any(token in lowered_description for token in keyword_tokens):
                description_score -= 0.15
                issues_detected.append("description_missing_keywords")
                recommended_actions.append("inject_business_keywords_in_description")
        if placeholder_pattern.search(description):
            description_score -= 0.25
        description_score = round(max(0.0, min(1.0, description_score)), 2)

        cta_primary = str(raw.get("cta_primary", "")).strip()
        cta_secondary = str(raw.get("cta_secondary", "")).strip()
        cta_score = 1.0
        action_words = ("book", "call", "contact", "start", "learn", "shop", "schedule", "get")
        if not cta_primary or not cta_secondary:
            cta_score -= 0.6
            issues_detected.append("empty_cta")
            recommended_actions.append("regenerate_cta")
        for cta_field, value in (("cta_primary", cta_primary), ("cta_secondary", cta_secondary)):
            if value and (len(value) < 4 or len(value) > 80):
                cta_score -= 0.2
                issues_detected.append(f"{cta_field}_length_out_of_bounds")
            if value and not any(word in value.lower() for word in action_words):
                cta_score -= 0.1
                issues_detected.append(f"{cta_field}_missing_action_keyword")
        cta_score = round(max(0.0, min(1.0, cta_score)), 2)

        if any(status == "failed" for status in execution_results.values()):
            issues_detected.append("failed_execution_residue")
            recommended_actions.append("rerun_failed_actions")

        unique_issues = sorted(set(issues_detected))
        unique_actions = sorted(set(recommended_actions))

        overall_score = round(
            (completeness_score * 0.4) + (description_score * 0.3) + (cta_score * 0.3),
            2,
        )

        source_signature = self._source_signature(raw) if raw else ""
        source_changed = bool(source_signature) and source_signature != memory.get("source_signature", "")
        if source_changed and overall_score >= EVALUATION_THRESHOLD:
            overall_score = round(EVALUATION_THRESHOLD - 0.01, 2)
            unique_issues = sorted(set(unique_issues + ["source_input_changed"]))
            unique_actions = sorted(set(unique_actions + ["refresh_site_from_updated_input"]))
        stable = bool(overall_score >= EVALUATION_THRESHOLD and not source_changed)

        return {
            "slug": slug,
            "completeness_score": completeness_score,
            "description_score": description_score,
            "cta_score": cta_score,
            "overall_score": overall_score,
            "issues_detected": unique_issues,
            "recommended_actions": unique_actions,
            "source_signature": source_signature,
            "source_changed": source_changed,
            "stable": stable,
        }

    def _priority_rank(self, evaluation: dict) -> dict:
        issues = set(evaluation.get("issues_detected", []))
        overall_score = float(evaluation.get("overall_score", 0.0))
        completeness_score = float(evaluation.get("completeness_score", 0.0))
        source_changed = bool(evaluation.get("source_changed", False))

        if (
            "failed_execution_residue" in issues
            or "missing_client_notes" in issues
            or "invalid_client_notes" in issues
            or any(issue.startswith("failed_residue_in_") for issue in issues)
        ):
            bucket = "broken_failed_clients"
            priority_value = 0
        elif completeness_score < 1.0:
            bucket = "missing_required_fields"
            priority_value = 1
        elif overall_score < EVALUATION_THRESHOLD:
            bucket = "low_quality_outputs"
            priority_value = 2
        elif source_changed or not evaluation.get("stable", False):
            bucket = "stale_incomplete_clients"
            priority_value = 3
        else:
            bucket = "healthy_clients_skipped"
            priority_value = 4

        return {
            "bucket": bucket,
            "priority_value": priority_value,
            "sort_key": (priority_value, overall_score),
        }

    def _persist_client_evaluation(self, slug: str, evaluation: dict) -> None:
        memory = self._load_client_memory(slug)
        memory["last_evaluation"] = {
            "evaluated_at": datetime.now().isoformat(timespec="seconds"),
            "threshold": EVALUATION_THRESHOLD,
        }
        memory["scores"] = {
            "completeness_score": evaluation["completeness_score"],
            "description_score": evaluation["description_score"],
            "cta_score": evaluation["cta_score"],
            "overall_score": evaluation["overall_score"],
        }
        memory["issues"] = evaluation["issues_detected"]
        memory["stable"] = bool(evaluation.get("stable", False))
        memory["source_signature"] = evaluation.get("source_signature", "")
        memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._update_client_memory(slug, memory)

    def _schedule_client_supervisor_work(self, slug: str, reason: str) -> None:
        client_root = self.paths["clients"] / slug
        notes_file = client_root / "notes" / "client.json"
        if not notes_file.exists():
            self._log_activity(f"[SUPERVISOR] scheduled slug={slug} reason={reason} skipped=missing_notes")
            return
        try:
            raw_data = json.loads(notes_file.read_text(encoding="utf-8"))
            raw_data["overwrite"] = True
            context = self._build_client_context(slug, raw_data)
            analysis = self._analyze_client(context["analysis_input"], context=context)
            execution = self._execute_action_plan(analysis, context=context)
            final_analysis: ClientAnalysis = execution["analysis"]
            client_data: dict = execution["client_data"]
            execution_results: dict = execution.get("execution_results", {})
            self._log_analysis(final_analysis, f"supervisor:{slug}")
            notes_file.write_text(json.dumps(client_data, indent=2), encoding="utf-8")
            self._persist_context_summary(final_analysis.slug, context)

            memory = self._load_client_memory(final_analysis.slug)
            memory["last_action_plan"] = final_analysis.action_plan
            memory["execution_results"] = execution_results
            memory["generated_fields"] = {
                "description": client_data.get("description", ""),
                "cta_primary": client_data.get("cta_primary", ""),
                "cta_secondary": client_data.get("cta_secondary", ""),
            }
            memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
            self._update_client_memory(final_analysis.slug, memory)
            self._log_activity(f"[SUPERVISOR] scheduled slug={slug} reason={reason} status=processed")
        except Exception as exc:
            self._log_activity(f"[SUPERVISOR] scheduled slug={slug} reason={reason} status=failed error={exc}")

    def _execute_action_plan(self, client_analysis: ClientAnalysis, context: dict | None = None) -> dict:
        """
        Execute analysis.action_plan in strict deterministic order (no parallelism).

        Returns:
            dict with:
              - analysis: updated ClientAnalysis
              - client_data: mutated build-ready dictionary
              - client_root: final client folder
              - copied: generated file count (0 if build not requested)
        Raises:
            Exception from failed step; caller is responsible for marking job failed.
        """
        ordered_steps = [
            "ENRICH_DATA",
            "GENERATE_DESCRIPTION",
            "GENERATE_CTA",
            "RESOLVE_SLUG",
            "PROCEED_TO_BUILD",
        ]
        planned = set(client_analysis.action_plan)
        client_data = client_analysis.to_dict()
        final_slug = client_analysis.slug
        copied = 0
        context = context or {}
        previous_memory = context.get("memory", {}) if isinstance(context.get("memory", {}), dict) else self._load_client_memory(final_slug)
        previous_generated = context.get("last_successful_generated_fields", {})
        if not isinstance(previous_generated, dict):
            previous_generated = {}
        previous_results = previous_memory.get("execution_results", {})
        execution_results: dict[str, str] = {}

        for field in ("description", "cta_primary", "cta_secondary"):
            if not str(client_data.get(field, "")).strip() and str(previous_generated.get(field, "")).strip():
                client_data[field] = previous_generated[field]
                self._log_activity(f"[MEMORY] preload field={field} slug={final_slug}")

        for step in ordered_steps:
            if step not in planned:
                continue

            self._log_activity(f"[ACTION] {step} status=started")
            try:
                if step == "ENRICH_DATA":
                    if not client_data.get("business_type", "").strip():
                        client_data["business_type"] = "Local Business"
                    if not client_data.get("brand_style", "").strip():
                        client_data["brand_style"] = "modern and professional"

                elif step == "GENERATE_DESCRIPTION":
                    if not client_data.get("description", "").strip():
                        if (
                            previous_results.get("GENERATE_DESCRIPTION") == "success"
                            and str(previous_generated.get("description", "")).strip()
                        ):
                            client_data["description"] = previous_generated["description"]
                            self._log_activity(f"[MEMORY] reused description slug={final_slug}")
                        else:
                            client_data["description"] = (
                                f"Welcome to {client_data['name']}. "
                                "We provide quality services to our clients."
                            )

                elif step == "GENERATE_CTA":
                    if not client_data.get("cta_primary", "").strip():
                        if (
                            previous_results.get("GENERATE_CTA") == "success"
                            and str(previous_generated.get("cta_primary", "")).strip()
                        ):
                            client_data["cta_primary"] = previous_generated["cta_primary"]
                            self._log_activity(f"[MEMORY] reused cta_primary slug={final_slug}")
                        else:
                            client_data["cta_primary"] = "Book a free call"
                    if not client_data.get("cta_secondary", "").strip():
                        if (
                            previous_results.get("GENERATE_CTA") == "success"
                            and str(previous_generated.get("cta_secondary", "")).strip()
                        ):
                            client_data["cta_secondary"] = previous_generated["cta_secondary"]
                            self._log_activity(f"[MEMORY] reused cta_secondary slug={final_slug}")
                        else:
                            client_data["cta_secondary"] = "See our services"

                elif step == "RESOLVE_SLUG":
                    final_slug = self._resolve_unique_slug(final_slug)

                elif step == "PROCEED_TO_BUILD":
                    if not self._required_fields_valid_for_build(client_data):
                        raise ValueError("Required fields missing; cannot proceed to build.")
                    client_root = self.paths["clients"] / final_slug
                    (client_root / "assets").mkdir(parents=True, exist_ok=True)
                    (client_root / "site").mkdir(parents=True, exist_ok=True)
                    (client_root / "notes").mkdir(parents=True, exist_ok=True)
                    copied = self._run_site_generation(client_root, client_data)

                self._log_activity(f"[ACTION] {step} status=success")
                execution_results[step] = "success"
            except Exception:
                self._log_activity(f"[ACTION] {step} status=failed")
                execution_results[step] = "failed"
                raise

        updated_analysis = ClientAnalysis(
            name=client_analysis.name,
            slug=final_slug,
            business_type=client_data["business_type"],
            brand_style=client_data["brand_style"],
            email=client_data["email"],
            phone=client_data["phone"],
            instagram=client_data["instagram"],
            description=client_data["description"],
            cta_primary=client_data["cta_primary"],
            cta_secondary=client_data["cta_secondary"],
            completeness_score=client_analysis.completeness_score,
            enriched_fields=client_analysis.enriched_fields,
            validation_warnings=client_analysis.validation_warnings,
            action_plan=client_analysis.action_plan,
        )

        return {
            "analysis": updated_analysis,
            "client_data": client_data,
            "client_root": self.paths["clients"] / final_slug,
            "copied": copied,
            "execution_results": execution_results,
        }

    def generate_site(self):
        """Manual Generate Site button handler."""
        client_root = self._require_selected_client()
        if not client_root:
            return
        try:
            raw_data = self._read_client_data(client_root)
            slug = self.selected_client or self.sanitize_client_name(str(raw_data.get("name", "")))
            context = self._build_client_context(slug, raw_data)
            analysis = self._analyze_client(context["analysis_input"], context=context)
            execution = self._execute_action_plan(analysis, context=context)
            final_analysis: ClientAnalysis = execution["analysis"]
            client_data: dict = execution["client_data"]
            copied: int = execution["copied"]
            execution_results: dict = execution.get("execution_results", {})
            self._log_analysis(final_analysis, self.selected_client or "manual")

            notes_file = execution["client_root"] / "notes" / "client.json"
            notes_file.parent.mkdir(parents=True, exist_ok=True)
            notes_file.write_text(json.dumps(client_data, indent=2), encoding="utf-8")
            self._persist_context_summary(final_analysis.slug, context)
            self._update_client_memory(
                final_analysis.slug,
                {
                    "last_action_plan": final_analysis.action_plan,
                    "execution_results": execution_results,
                    "generated_fields": {
                        "description": client_data.get("description", ""),
                        "cta_primary": client_data.get("cta_primary", ""),
                        "cta_secondary": client_data.get("cta_secondary", ""),
                    },
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                },
            )

            self.status_var.set(f"Generated site for {final_analysis.slug} ({copied} files).")
            messagebox.showinfo("Generate Site", f"Generated {copied} files for {final_analysis.slug}.")
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
        """Background thread: continuously supervises inbox and client state."""
        while not self._stop_event.is_set():
            try:
                self._run_supervisor_cycle()
            except Exception as exc:
                self._log_activity(
                    f"[ERROR] Unhandled exception in supervisor loop: {exc}\n"
                    f"{traceback.format_exc()}"
                )
            self._stop_event.wait(timeout=INBOX_SCAN_INTERVAL)

    def _run_supervisor_cycle(self) -> None:
        self._scan_existing_clients()
        self._evaluate_and_prioritize_clients()
        if self._auto_mode:
            self._scan_and_process_inbox()

    def _evaluate_and_prioritize_clients(self) -> None:
        clients = self._discover_clients()
        if not clients:
            return

        ranked: list[tuple[tuple[int, float], str, dict, dict]] = []
        for slug in clients:
            evaluation = self._evaluate_client_state(slug)
            priority = self._priority_rank(evaluation)
            ranked.append((priority["sort_key"], slug, evaluation, priority))
            self._persist_client_evaluation(slug, evaluation)
            self._log_activity(
                f"[SUPERVISOR] evaluation slug={slug} "
                f"score={evaluation['overall_score']:.2f} "
                f"priority={priority['bucket']}"
            )

        ranked.sort(key=lambda item: item[0])
        for _, slug, evaluation, priority in ranked:
            if evaluation["overall_score"] >= EVALUATION_THRESHOLD and not evaluation.get("source_changed", False):
                self._log_activity(
                    f"[SUPERVISOR] skipped slug={slug} reason=stable_above_threshold "
                    f"score={evaluation['overall_score']:.2f}"
                )
                continue

            reason = (
                f"priority={priority['bucket']};"
                f"issues={','.join(evaluation['issues_detected']) or 'none'};"
                f"score={evaluation['overall_score']:.2f}"
            )
            self._schedule_client_supervisor_work(slug, reason)

    def _discover_clients(self) -> list[str]:
        self.paths["clients"].mkdir(parents=True, exist_ok=True)
        try:
            return sorted(
                p.name for p in self.paths["clients"].iterdir()
                if p.is_dir() and p.name != "inbox"
            )
        except PermissionError as exc:
            self._log_activity(f"[ERROR] Cannot read clients directory: {exc}")
            return []

    def _scan_existing_clients(self) -> None:
        clients = self._discover_clients()
        if clients == self._known_clients:
            return
        self._known_clients = clients
        self._log_activity(f"Client scan: {len(clients)} client(s) detected.")
        self._schedule_ui_update(self.refresh_client_list)

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
            # 1. Read raw client.json from the job folder
            client_json_path = job_dir / "client.json"
            if not client_json_path.exists():
                raise FileNotFoundError(f"client.json not found in job folder: {job_name}")
            with client_json_path.open("r", encoding="utf-8") as f:
                raw_data = json.load(f)

            inferred_slug = self.sanitize_client_name(str(raw_data.get("name", "")))
            context = self._build_client_context(inferred_slug, raw_data)

            # 2. Decision layer: validate, enrich, normalize
            analysis = self._analyze_client(context["analysis_input"], context=context)

            # 3. Execute action plan deterministically (may resolve slug and build site)
            execution = self._execute_action_plan(analysis, context=context)
            final_analysis: ClientAnalysis = execution["analysis"]
            client_data: dict = execution["client_data"]
            client_root: Path = execution["client_root"]
            copied: int = execution["copied"]
            execution_results: dict = execution.get("execution_results", {})

            # 4. Persist structured analysis and normalized client data
            self._log_analysis(final_analysis, job_name)
            (client_root / "assets").mkdir(parents=True, exist_ok=True)
            (client_root / "site").mkdir(parents=True, exist_ok=True)
            (client_root / "notes").mkdir(parents=True, exist_ok=True)
            notes_file = client_root / "notes" / "client.json"
            notes_file.write_text(json.dumps(client_data, indent=2), encoding="utf-8")
            self._persist_context_summary(final_analysis.slug, context)

            self._update_client_memory(
                final_analysis.slug,
                {
                    "last_action_plan": final_analysis.action_plan,
                    "execution_results": execution_results,
                    "generated_fields": {
                        "description": client_data.get("description", ""),
                        "cta_primary": client_data.get("cta_primary", ""),
                        "cta_secondary": client_data.get("cta_secondary", ""),
                    },
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                },
            )

            # 5. Copy any assets bundled with the job
            job_assets = job_dir / "assets"
            if job_assets.is_dir():
                for src in job_assets.rglob("*"):
                    if src.is_file():
                        dst = client_root / "assets" / src.relative_to(job_assets)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)

            # 6. Mark completed
            self._set_job_status(job_dir, JOB_COMPLETED)

            with self._auto_lock:
                self._stats["processed"] += 1

            self._log_activity(
                f"Job completed: {job_name} → {final_analysis.slug} ({copied} file(s))"
            )
            self._schedule_ui_update(self._on_job_complete, final_analysis.slug)

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

    def _log_analysis(self, analysis: ClientAnalysis, source: str) -> None:
        """Append one structured JSON record to logs/analysis.log (JSONL format)."""
        try:
            logs_path = self.paths["logs"]
            logs_path.mkdir(parents=True, exist_ok=True)
            log_file = logs_path / "analysis.log"
            record = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "analysis": analysis.to_log_dict(),
            }
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            self._log_error(f"Failed to write analysis log entry for source '{source}': {exc}")

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
