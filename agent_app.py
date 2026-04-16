import json
import hashlib
import os
import re
import shutil
import subprocess
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
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


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

REQUIRED_ROOT_FOLDERS = ("clients", "templates", "prompts", "assets", "deploy", "tools", "logs", "notes")
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
REASONING_CONFIDENCE_THRESHOLD = 0.7
SYSTEM_LEARNING_INTERVAL_CYCLES = 10

ADJUSTMENT_BOUNDS = {
    "evaluation_threshold": {"min": 0.70, "max": 0.95},
    "reasoning_confidence_threshold": {"min": 0.50, "max": 0.90},
    "priority_weight": {"min": 0.20, "max": 0.60},
    "source_changed_penalty": {"min": 0.0, "max": 0.08},
    "confidence_adjustment": {"min": -0.10, "max": 0.10},
}

SUPPORTED_REASONING_TASK_TYPES = {
    "improve_description",
    "improve_cta",
    "suggest_actions",
    "semantic_quality_check",
}
ACTION_FILE_WRITE = "FILE_WRITE"
ACTION_API_CALL = "API_CALL"
ACTION_WEBHOOK = "WEBHOOK"
ACTION_COMMAND = "COMMAND"
ACTION_NO_OP = "NO_OP"
SUPPORTED_ACTION_TYPES = {
    ACTION_FILE_WRITE,
    ACTION_API_CALL,
    ACTION_WEBHOOK,
    ACTION_COMMAND,
    ACTION_NO_OP,
}
PROTECTED_PATH_MARKERS = ("notes/client.json", "notes/system_learning.json", "notes/system_goals.json", "config.json")
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_COMPLETED = "completed"
GOAL_STATUS_BLOCKED = "blocked"
GOAL_FAILURE_ESCALATION_THRESHOLD = 2
DEFAULT_ACTION_POLICY = {
    "allowed_actions": [ACTION_FILE_WRITE, ACTION_NO_OP],
    "allowed_domains": [],
    "max_actions_per_cycle": 3,
    "require_approval": True,
    "command_enabled": False,
}
AGENT_PLANNER = "PLANNER_AGENT"
AGENT_GENERATOR = "GENERATOR_AGENT"
AGENT_EVALUATOR = "EVALUATOR_AGENT"
AGENT_OPTIMIZER = "OPTIMIZER_AGENT"
SUPPORTED_AGENT_ROLES = {AGENT_PLANNER, AGENT_GENERATOR, AGENT_EVALUATOR, AGENT_OPTIMIZER}

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
            "notes":     self.base_dir / "notes",
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
        self._supervisor_cycle_count = 0
        self._learning_controls = {
            "evaluation_threshold": EVALUATION_THRESHOLD,
            "reasoning_confidence_threshold": REASONING_CONFIDENCE_THRESHOLD,
            "weights": {
                "completeness": 0.4,
                "description": 0.3,
                "cta": 0.3,
            },
            "scoring_modifiers": {
                "source_changed_penalty": 0.01,
            },
            "reasoning_confidence_adjustment": 0.0,
        }
        self._system_learning_state = {
            "last_analysis": {},
            "applied_adjustments": [],
            "system_score_trend": [],
            "agent_learning_signals": [],
        }

        self._ensure_core_structure()
        self._load_system_learning_state()
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

    def _system_learning_file(self) -> Path:
        return self.paths["notes"] / "system_learning.json"

    def _system_goals_file(self) -> Path:
        return self.paths["notes"] / "system_goals.json"

    def _persist_system_goals(self, payload: dict) -> None:
        serialized = payload if isinstance(payload, dict) else {"active_goals": []}
        self._system_goals_file().write_text(json.dumps(serialized, indent=2), encoding="utf-8")

    def _load_active_goals(self) -> list[dict]:
        goals_file = self._system_goals_file()
        if not goals_file.exists():
            default_goals = {
                "active_goals": [
                    {
                        "goal_id": "client_growth_001",
                        "client_slug": "example_client",
                        "objective": "Fully optimize client profile to high-quality score",
                        "target_state": {"overall_score": 0.9},
                        "status": GOAL_STATUS_ACTIVE,
                        "priority": "high",
                    }
                ]
            }
            self._persist_system_goals(default_goals)
            return default_goals["active_goals"]
        try:
            payload = json.loads(goals_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return []
            goals = payload.get("active_goals", [])
            if not isinstance(goals, list):
                return []
            return [goal for goal in goals if isinstance(goal, dict) and goal.get("status", GOAL_STATUS_ACTIVE) == GOAL_STATUS_ACTIVE]
        except Exception as exc:
            self._log_activity(f"[GOAL] failed_to_load_goals error={exc}")
            return []

    def _load_system_learning_state(self) -> None:
        state_file = self._system_learning_file()
        if not state_file.exists():
            self._persist_system_learning_state()
            return
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            self._system_learning_state["last_analysis"] = payload.get("last_analysis", {})
            self._system_learning_state["applied_adjustments"] = payload.get("applied_adjustments", [])
            self._system_learning_state["system_score_trend"] = payload.get("system_score_trend", [])
            self._system_learning_state["agent_learning_signals"] = payload.get("agent_learning_signals", [])
            controls = payload.get("active_controls", {})
            if isinstance(controls, dict):
                self._learning_controls["evaluation_threshold"] = float(
                    controls.get("evaluation_threshold", self._learning_controls["evaluation_threshold"])
                )
                self._learning_controls["reasoning_confidence_threshold"] = float(
                    controls.get(
                        "reasoning_confidence_threshold",
                        self._learning_controls["reasoning_confidence_threshold"],
                    )
                )
                if isinstance(controls.get("weights", {}), dict):
                    self._learning_controls["weights"].update(controls.get("weights", {}))
                if isinstance(controls.get("scoring_modifiers", {}), dict):
                    self._learning_controls["scoring_modifiers"].update(controls.get("scoring_modifiers", {}))
                self._learning_controls["reasoning_confidence_adjustment"] = float(
                    controls.get(
                        "reasoning_confidence_adjustment",
                        self._learning_controls["reasoning_confidence_adjustment"],
                    )
                )
        except Exception as exc:
            self._log_activity(f"[SYSTEM_LEARNING] failed_to_load_state error={exc}")

    def _persist_system_learning_state(self) -> None:
        payload = {
            "last_analysis": self._system_learning_state.get("last_analysis", {}),
            "applied_adjustments": self._system_learning_state.get("applied_adjustments", []),
            "system_score_trend": self._system_learning_state.get("system_score_trend", [])[-50:],
            "agent_learning_signals": self._system_learning_state.get("agent_learning_signals", [])[-200:],
            "active_controls": self._learning_controls,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._system_learning_file().write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _effective_reasoning_threshold(self) -> float:
        base_threshold = float(self._learning_controls.get("reasoning_confidence_threshold", REASONING_CONFIDENCE_THRESHOLD))
        adjustment = float(self._learning_controls.get("reasoning_confidence_adjustment", 0.0))
        bounded = base_threshold + adjustment
        return max(ADJUSTMENT_BOUNDS["reasoning_confidence_threshold"]["min"], min(ADJUSTMENT_BOUNDS["reasoning_confidence_threshold"]["max"], bounded))

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
            policy_path = self._action_policy_path(client_slug)
            if not policy_path.exists():
                policy_path.write_text(
                    json.dumps(DEFAULT_ACTION_POLICY, indent=2),
                    encoding="utf-8",
                )

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
            "source_attribution": {
                "included_sources": included_sources,
                "field_sources": field_sources,
                "source_signature": source_signature,
            },
            "analysis_input": merged,
        }

    def _run_local_reasoning(self, client_context: dict, task_type: str) -> dict:
        """
        Phase 11: constrained local reasoning layer.

        Uses only structured client_context data and returns strict structured JSON shape:
          - proposed_fields {}
          - reasoning_notes []
          - confidence_score
          - source_basis []
        """
        if not isinstance(client_context, dict):
            raise ValueError("client_context must be a dictionary")
        if task_type not in SUPPORTED_REASONING_TASK_TYPES:
            raise ValueError(f"Unsupported task_type: {task_type}")

        evaluation_snapshot = client_context.get("last_evaluation_summary", {})
        if not isinstance(evaluation_snapshot, dict):
            evaluation_snapshot = {}
        source_attribution = client_context.get("source_attribution", {})
        if not isinstance(source_attribution, dict):
            source_attribution = {}
        analysis_input = client_context.get("analysis_input", {})
        if not isinstance(analysis_input, dict):
            analysis_input = {}

        proposed_fields: dict = {}
        reasoning_notes: list[str] = []
        confidence_score = 0.45
        source_basis = sorted(set(source_attribution.get("included_sources", [])))
        if not source_basis:
            source_basis = sorted(set(client_context.get("included_sources", [])))

        name = str(analysis_input.get("name", "")).strip()
        business_type = str(analysis_input.get("business_type", "")).strip()
        brand_style = str(analysis_input.get("brand_style", "")).strip()
        prior_generated = memory.get("generated_fields", {}) if isinstance(memory.get("generated_fields", {}), dict) else {}

        if task_type == "improve_description":
            current_description = str(analysis_input.get("description", "")).strip()
            if current_description and len(current_description) >= 80:
                reasoning_notes.append("description_already_sufficient")
                confidence_score = 0.5
            else:
                style_phrase = brand_style if brand_style else "clear and professional"
                subject = name if name else "your business"
                service_ref = business_type if business_type else "local services"
                proposed_fields["description"] = (
                    f"{subject} delivers trusted {service_ref} with a {style_phrase} approach. "
                    "Our team focuses on quality outcomes, responsive support, and a smooth client experience."
                ).strip()
                if str(prior_generated.get("description", "")).strip():
                    reasoning_notes.append("memory_description_available_but_refreshed_for_context_alignment")
                    confidence_score = 0.79
                else:
                    reasoning_notes.append("description_generated_from_truth_profile_context")
                    confidence_score = 0.83

        elif task_type == "improve_cta":
            current_primary = str(analysis_input.get("cta_primary", "")).strip()
            current_secondary = str(analysis_input.get("cta_secondary", "")).strip()
            if current_primary and current_secondary:
                reasoning_notes.append("cta_already_present")
                confidence_score = 0.5
            else:
                proposed_fields["cta_primary"] = f"Contact {name}" if name else "Contact us today"
                proposed_fields["cta_secondary"] = (
                    f"Explore {business_type} options" if business_type else "Learn about our services"
                )
                reasoning_notes.append("cta_generated_from_structured_context")
                confidence_score = 0.81

        elif task_type == "suggest_actions":
            issues = evaluation_snapshot.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            suggestions: list[str] = []
            if any(issue.startswith("missing_") for issue in issues):
                suggestions.append("backfill_required_fields")
            if "description_length_out_of_bounds" in issues:
                suggestions.append("normalize_description")
            if "empty_cta" in issues:
                suggestions.append("regenerate_cta")
            proposed_fields["suggested_actions"] = sorted(set(suggestions))
            reasoning_notes.append("actions_mapped_from_evaluation_issues")
            confidence_score = 0.75 if suggestions else 0.55

        elif task_type == "semantic_quality_check":
            semantic_flags: list[str] = []
            description = str(analysis_input.get("description", "")).strip().lower()
            if description:
                key_terms = [token for token in re.split(r"[^a-z0-9]+", f"{name} {business_type}".lower()) if len(token) >= 4]
                if key_terms and not any(token in description for token in key_terms[:4]):
                    semantic_flags.append("description_semantic_mismatch")
            cta_primary = str(analysis_input.get("cta_primary", "")).strip().lower()
            if cta_primary and not re.search(r"\b(book|call|contact|start|learn|shop|schedule|get)\b", cta_primary):
                semantic_flags.append("cta_primary_low_actionability")
            proposed_fields["semantic_flags"] = semantic_flags
            proposed_fields["semantic_score"] = 1.0 if not semantic_flags else 0.72
            reasoning_notes.append("semantic_quality_evaluated_from_context_only")
            confidence_score = 0.74 if semantic_flags else 0.8

        response_obj = {
            "proposed_fields": proposed_fields,
            "reasoning_notes": reasoning_notes,
            "confidence_score": round(max(0.0, min(1.0, confidence_score)), 2),
            "source_basis": source_basis,
        }
        encoded = json.dumps(response_obj, ensure_ascii=False)
        decoded = json.loads(encoded)
        if not self._is_valid_reasoning_output(decoded):
            raise ValueError("Malformed reasoning response")
        return decoded

    def _is_valid_reasoning_output(self, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("proposed_fields", {}), dict):
            return False
        if not isinstance(payload.get("reasoning_notes", []), list):
            return False
        if not isinstance(payload.get("source_basis", []), list):
            return False
        confidence = payload.get("confidence_score")
        return isinstance(confidence, (int, float)) and 0.0 <= float(confidence) <= 1.0

    def _validate_reasoning_proposals(self, task_type: str, proposals: dict) -> tuple[bool, str]:
        if not isinstance(proposals, dict):
            return False, "proposed_fields_not_object"
        if task_type == "improve_description":
            value = str(proposals.get("description", "")).strip()
            if not value:
                return False, "missing_description_proposal"
            if len(value) < 40 or len(value) > 500:
                return False, "description_out_of_bounds"
        elif task_type == "improve_cta":
            for field in ("cta_primary", "cta_secondary"):
                value = str(proposals.get(field, "")).strip()
                if not value:
                    return False, f"missing_{field}_proposal"
                if len(value) < 4 or len(value) > 80:
                    return False, f"{field}_out_of_bounds"
        return True, ""

    def _log_reasoning_call(
        self,
        task_type: str,
        confidence_score: float,
        proposed_fields: list[str],
        accepted: bool,
        fallback_reason: str = "",
        slug: str | None = None,
    ) -> None:
        status = "accepted" if accepted else "rejected"
        self._log_activity(
            f"[REASONING] task_type={task_type} confidence_score={confidence_score:.2f} "
            f"fields={','.join(proposed_fields)} status={status} fallback_reason={fallback_reason or 'none'}"
        )
        if not slug:
            return
        memory = self._load_client_memory(slug)
        history = memory.get("reasoning_history", {})
        if not isinstance(history, dict):
            history = {}
        history["total_calls"] = int(history.get("total_calls", 0)) + 1
        history["accepted_calls"] = int(history.get("accepted_calls", 0)) + (1 if accepted else 0)
        history["rejected_outputs"] = int(history.get("rejected_outputs", 0)) + (0 if accepted else 1)
        if fallback_reason and fallback_reason != "none":
            history["fallback_count"] = int(history.get("fallback_count", 0)) + 1
            last_reasons = history.get("last_fallback_reasons", [])
            if not isinstance(last_reasons, list):
                last_reasons = []
            history["last_fallback_reasons"] = (last_reasons + [fallback_reason])[-10:]
        memory["reasoning_history"] = history
        memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._update_client_memory(slug, memory)

    def _load_client_memory(self, slug: str) -> dict:
        """Load structured memory for a client; return empty shape if unavailable."""
        memory_file = self.paths["clients"] / slug / "memory.json"
        empty_memory = {
            "last_action_plan": [],
            "execution_results": {},
            "generated_fields": {},
            "last_evaluation": {},
            "evaluation_history": [],
            "scores": {},
            "issues": [],
            "stable": False,
            "freeze": False,
            "source_signature": "",
            "last_context_summary": {},
            "reasoning_history": {},
            "action_history": [],
            "agent_performance": {
                "planner": {"successes": 0, "total": 0, "success_rate": 0.0},
                "generator": {"successes": 0, "total": 0, "success_rate": 0.0},
                "evaluator": {"consistent": 0, "total": 0, "consistency_score": 0.0},
                "optimizer": {"improvements": 0, "total": 0, "improvement_rate": 0.0},
            },
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
                "evaluation_history": data.get("evaluation_history", []),
                "scores": data.get("scores", {}),
                "issues": data.get("issues", []),
                "stable": bool(data.get("stable", False)),
                "freeze": bool(data.get("freeze", False)),
                "source_signature": data.get("source_signature", ""),
                "last_context_summary": data.get("last_context_summary", {}),
                "reasoning_history": data.get("reasoning_history", {}),
                "action_history": data.get("action_history", []),
                "agent_performance": data.get("agent_performance", empty_memory["agent_performance"]),
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
            "evaluation_history": data.get("evaluation_history", existing.get("evaluation_history", [])),
            "scores": data.get("scores", existing.get("scores", {})),
            "issues": data.get("issues", existing.get("issues", [])),
            "stable": bool(data.get("stable", existing.get("stable", False))),
            "freeze": bool(data.get("freeze", existing.get("freeze", False))),
            "source_signature": data.get("source_signature", existing.get("source_signature", "")),
            "last_context_summary": data.get("last_context_summary", existing.get("last_context_summary", {})),
            "reasoning_history": data.get("reasoning_history", existing.get("reasoning_history", {})),
            "action_history": data.get("action_history", existing.get("action_history", [])),
            "agent_performance": data.get("agent_performance", existing.get("agent_performance", {
                "planner": {"successes": 0, "total": 0, "success_rate": 0.0},
                "generator": {"successes": 0, "total": 0, "success_rate": 0.0},
                "evaluator": {"consistent": 0, "total": 0, "consistency_score": 0.0},
                "optimizer": {"improvements": 0, "total": 0, "improvement_rate": 0.0},
            })),
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

    def _action_policy_path(self, slug: str) -> Path:
        return self.paths["clients"] / slug / "action_policy.json"

    def _load_action_policy(self, slug: str) -> dict:
        policy_path = self._action_policy_path(slug)
        if not policy_path.exists():
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy_path.write_text(json.dumps(DEFAULT_ACTION_POLICY, indent=2), encoding="utf-8")
            return dict(DEFAULT_ACTION_POLICY)
        try:
            parsed = json.loads(policy_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                return dict(DEFAULT_ACTION_POLICY)
            merged = dict(DEFAULT_ACTION_POLICY)
            merged.update(parsed)
            allowed_actions = merged.get("allowed_actions", [])
            if not isinstance(allowed_actions, list):
                allowed_actions = DEFAULT_ACTION_POLICY["allowed_actions"]
            merged["allowed_actions"] = [str(item).strip() for item in allowed_actions if str(item).strip()]
            allowed_domains = merged.get("allowed_domains", [])
            if not isinstance(allowed_domains, list):
                allowed_domains = []
            merged["allowed_domains"] = [str(item).strip().lower() for item in allowed_domains if str(item).strip()]
            try:
                merged["max_actions_per_cycle"] = max(0, int(merged.get("max_actions_per_cycle", 0)))
            except Exception:
                merged["max_actions_per_cycle"] = DEFAULT_ACTION_POLICY["max_actions_per_cycle"]
            merged["require_approval"] = bool(merged.get("require_approval", True))
            merged["command_enabled"] = bool(merged.get("command_enabled", False))
            return merged
        except Exception as exc:
            self._log_activity(f"[ACTION] failed_to_load_policy slug={slug} error={exc}")
            return dict(DEFAULT_ACTION_POLICY)

    def _append_action_history(self, slug: str, entry: dict) -> None:
        memory = self._load_client_memory(slug)
        history = memory.get("action_history", [])
        if not isinstance(history, list):
            history = []
        history.append(entry)
        memory["action_history"] = history[-200:]
        memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._update_client_memory(slug, memory)

    def _record_agent_learning_signal(
        self,
        slug: str,
        role: str,
        confidence: float,
        accepted: bool,
        improved_outcome: bool,
        rejected: bool = False,
    ) -> None:
        signals = self._system_learning_state.get("agent_learning_signals", [])
        if not isinstance(signals, list):
            signals = []
        signals.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "slug": slug,
                "role": role,
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "accepted": bool(accepted),
                "improved_outcome": bool(improved_outcome),
                "rejected": bool(rejected),
            }
        )
        self._system_learning_state["agent_learning_signals"] = signals[-200:]

    def _record_agent_performance(self, slug: str, role: str, success: bool) -> None:
        memory = self._load_client_memory(slug)
        perf = memory.get("agent_performance", {})
        if not isinstance(perf, dict):
            perf = {}

        role_key_map = {
            AGENT_PLANNER: "planner",
            AGENT_GENERATOR: "generator",
            AGENT_EVALUATOR: "evaluator",
            AGENT_OPTIMIZER: "optimizer",
        }
        key = role_key_map.get(role)
        if not key:
            return
        node = perf.get(key, {})
        if not isinstance(node, dict):
            node = {}
        node["total"] = int(node.get("total", 0)) + 1
        if key == "evaluator":
            node["consistent"] = int(node.get("consistent", 0)) + (1 if success else 0)
            total = max(1, node["total"])
            node["consistency_score"] = round(node["consistent"] / total, 3)
        elif key == "optimizer":
            node["improvements"] = int(node.get("improvements", 0)) + (1 if success else 0)
            total = max(1, node["total"])
            node["improvement_rate"] = round(node["improvements"] / total, 3)
        else:
            node["successes"] = int(node.get("successes", 0)) + (1 if success else 0)
            total = max(1, node["total"])
            node["success_rate"] = round(node["successes"] / total, 3)
        perf[key] = node
        memory["agent_performance"] = perf
        memory["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._update_client_memory(slug, memory)

    def _run_agent(self, role: str, client_context: dict, task: dict) -> dict:
        if role not in SUPPORTED_AGENT_ROLES:
            raise ValueError(f"Unsupported agent role: {role}")
        if not isinstance(client_context, dict):
            raise ValueError("client_context must be a dictionary")
        if not isinstance(task, dict):
            raise ValueError("task must be a dictionary")

        output: dict = {}
        confidence = 0.55
        reasoning_notes: list[str] = []
        suggested_changes: list[str] = []

        analysis_input = client_context.get("analysis_input", {})
        if not isinstance(analysis_input, dict):
            analysis_input = {}
        memory = client_context.get("memory", {})
        if not isinstance(memory, dict):
            memory = {}

        if role == AGENT_PLANNER:
            requested_plan = task.get("requested_plan", [])
            requested_set = set(requested_plan if isinstance(requested_plan, list) else [])
            missing_description = not str(analysis_input.get("description", "")).strip()
            missing_cta = (
                not str(analysis_input.get("cta_primary", "")).strip()
                or not str(analysis_input.get("cta_secondary", "")).strip()
            )
            selected_steps: list[str] = []
            for step in ["ENRICH_DATA", "GENERATE_DESCRIPTION", "GENERATE_CTA", "RESOLVE_SLUG", "PROCEED_TO_BUILD"]:
                if step == "GENERATE_DESCRIPTION":
                    if step in requested_set and missing_description:
                        selected_steps.append(step)
                elif step == "GENERATE_CTA":
                    if step in requested_set and missing_cta:
                        selected_steps.append(step)
                elif step in requested_set:
                    selected_steps.append(step)
            output = {"selected_steps": selected_steps}
            reasoning_notes.append("planner_selected_required_steps_only")
            confidence = 0.9

        elif role == AGENT_GENERATOR:
            field = str(task.get("field", "")).strip()
            if field == "description":
                reasoning = self._run_local_reasoning(client_context, "improve_description")
                output = {"field": field, "value": str(reasoning.get("proposed_fields", {}).get("description", "")).strip()}
                confidence = float(reasoning.get("confidence_score", 0.0))
                reasoning_notes = [str(n) for n in reasoning.get("reasoning_notes", [])]
            elif field == "cta":
                reasoning = self._run_local_reasoning(client_context, "improve_cta")
                proposals = reasoning.get("proposed_fields", {})
                output = {
                    "field": field,
                    "value": {
                        "cta_primary": str(proposals.get("cta_primary", "")).strip(),
                        "cta_secondary": str(proposals.get("cta_secondary", "")).strip(),
                    },
                }
                confidence = float(reasoning.get("confidence_score", 0.0))
                reasoning_notes = [str(n) for n in reasoning.get("reasoning_notes", [])]

        elif role == AGENT_EVALUATOR:
            field = str(task.get("field", "")).strip()
            candidate = task.get("candidate")
            valid = False
            score = 0.0
            if field == "description":
                text = str(candidate or "").strip()
                valid = bool(text) and 40 <= len(text) <= 500
                score = 1.0 if valid else 0.6
            elif field == "cta":
                payload = candidate if isinstance(candidate, dict) else {}
                primary = str(payload.get("cta_primary", "")).strip()
                secondary = str(payload.get("cta_secondary", "")).strip()
                valid = bool(primary and secondary) and 4 <= len(primary) <= 80 and 4 <= len(secondary) <= 80
                score = 1.0 if valid else 0.55
            output = {"field": field, "accepted": valid, "score": round(score, 2)}
            confidence = 0.88
            if not valid:
                suggested_changes.append(f"repair_{field}")

        elif role == AGENT_OPTIMIZER:
            evaluation = task.get("evaluation", {})
            if not isinstance(evaluation, dict):
                evaluation = {}
            issues = evaluation.get("issues_detected", []) if isinstance(evaluation.get("issues_detected", []), list) else []
            previous_results = memory.get("execution_results", {})
            repeated_failure = any(status == "failed" for status in previous_results.values()) if isinstance(previous_results, dict) else False
            if "description_length_out_of_bounds" in issues:
                suggested_changes.append("normalize_description_length")
            if "empty_cta" in issues:
                suggested_changes.append("regenerate_cta")
            if repeated_failure:
                suggested_changes.append("retry_failed_steps_with_safe_defaults")
            output = {"actions": sorted(set(suggested_changes))}
            confidence = 0.72 if suggested_changes else 0.5
            reasoning_notes.append("optimizer_triggered_from_low_score_or_repeated_failure")

        response = {
            "output": output,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "reasoning_notes": reasoning_notes,
            "suggested_changes": suggested_changes,
        }
        json.loads(json.dumps(response, ensure_ascii=False))
        return response

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

        semantic_signal = {"used": False, "confidence_score": 0.0, "flags": []}
        try:
            context = self._build_client_context(slug)
            reasoning = self._run_local_reasoning(context, "semantic_quality_check")
            proposals = reasoning.get("proposed_fields", {})
            semantic_flags = proposals.get("semantic_flags", []) if isinstance(proposals, dict) else []
            reasoning_threshold = self._effective_reasoning_threshold()
            if isinstance(semantic_flags, list) and reasoning.get("confidence_score", 0.0) >= reasoning_threshold:
                semantic_signal = {
                    "used": True,
                    "confidence_score": float(reasoning.get("confidence_score", 0.0)),
                    "flags": semantic_flags,
                }
                if semantic_flags:
                    unique_issues = sorted(set(unique_issues + semantic_flags))
                    unique_actions = sorted(set(unique_actions + ["review_semantic_alignment"]))
                self._log_reasoning_call(
                    "semantic_quality_check",
                    float(reasoning.get("confidence_score", 0.0)),
                    sorted(proposals.keys()) if isinstance(proposals, dict) else [],
                    accepted=True,
                    slug=slug,
                )
            else:
                self._log_reasoning_call(
                    "semantic_quality_check",
                    float(reasoning.get("confidence_score", 0.0)),
                    sorted(proposals.keys()) if isinstance(proposals, dict) else [],
                    accepted=False,
                    fallback_reason="low_confidence_or_empty_signal",
                    slug=slug,
                )
        except Exception as exc:
            self._log_reasoning_call(
                "semantic_quality_check",
                0.0,
                [],
                accepted=False,
                fallback_reason=f"fallback_due_to_error:{exc}",
                slug=slug,
            )

        weights = self._learning_controls.get("weights", {})
        completeness_weight = float(weights.get("completeness", 0.4))
        description_weight = float(weights.get("description", 0.3))
        cta_weight = float(weights.get("cta", 0.3))
        weight_total = completeness_weight + description_weight + cta_weight
        if weight_total <= 0:
            completeness_weight, description_weight, cta_weight = 0.4, 0.3, 0.3
            weight_total = 1.0
        overall_score = round(
            (
                (completeness_score * completeness_weight)
                + (description_score * description_weight)
                + (cta_score * cta_weight)
            ) / weight_total,
            2,
        )

        source_signature = self._source_signature(raw) if raw else ""
        source_changed = bool(source_signature) and source_signature != memory.get("source_signature", "")
        evaluation_threshold = float(self._learning_controls.get("evaluation_threshold", EVALUATION_THRESHOLD))
        source_changed_penalty = float(self._learning_controls.get("scoring_modifiers", {}).get("source_changed_penalty", 0.01))
        if source_changed and overall_score >= evaluation_threshold:
            overall_score = round(evaluation_threshold - source_changed_penalty, 2)
            unique_issues = sorted(set(unique_issues + ["source_input_changed"]))
            unique_actions = sorted(set(unique_actions + ["refresh_site_from_updated_input"]))
        stable = bool(overall_score >= evaluation_threshold and not source_changed)

        return {
            "slug": slug,
            "completeness_score": completeness_score,
            "description_score": description_score,
            "cta_score": cta_score,
            "overall_score": overall_score,
            "issues_detected": unique_issues,
            "recommended_actions": unique_actions,
            "semantic_signal": semantic_signal,
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
        elif overall_score < float(self._learning_controls.get("evaluation_threshold", EVALUATION_THRESHOLD)):
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
            "threshold": float(self._learning_controls.get("evaluation_threshold", EVALUATION_THRESHOLD)),
        }
        memory["scores"] = {
            "completeness_score": evaluation["completeness_score"],
            "description_score": evaluation["description_score"],
            "cta_score": evaluation["cta_score"],
            "overall_score": evaluation["overall_score"],
        }
        history = memory.get("evaluation_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "overall_score": evaluation["overall_score"],
                "completeness_score": evaluation["completeness_score"],
                "description_score": evaluation["description_score"],
                "cta_score": evaluation["cta_score"],
            }
        )
        memory["evaluation_history"] = history[-40:]
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
            self._process_external_actions(final_analysis.slug, final_analysis, execution_results)
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
        can_reuse_context_memory = (
            context.get("slug") == client_analysis.slug and "RESOLVE_SLUG" not in planned
        )
        if can_reuse_context_memory:
            previous_memory = (
                context.get("memory", {})
                if isinstance(context.get("memory", {}), dict)
                else self._load_client_memory(final_slug)
            )
            previous_generated = context.get("last_successful_generated_fields", {})
            if not isinstance(previous_generated, dict):
                previous_generated = {}
        else:
            previous_memory = {}
            previous_generated = {}
        previous_results = previous_memory.get("execution_results", {})
        execution_results: dict[str, str] = {}
        current_context = dict(context)
        current_context["analysis_input"] = dict(client_data)

        planner_response = self._run_agent(
            AGENT_PLANNER,
            current_context,
            {"requested_plan": [step for step in ordered_steps if step in planned]},
        )
        planner_steps = planner_response.get("output", {}).get("selected_steps", [])
        planned = set(planner_steps if isinstance(planner_steps, list) else [])
        planner_accepted = bool(planned)
        self._record_agent_performance(final_slug, AGENT_PLANNER, success=planner_accepted)
        self._record_agent_learning_signal(
            final_slug,
            AGENT_PLANNER,
            float(planner_response.get("confidence", 0.0)),
            accepted=planner_accepted,
            improved_outcome=planner_accepted,
            rejected=not planner_accepted,
        )

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
                            deterministic_description = (
                                f"Welcome to {client_data['name']}. "
                                "We provide quality services to our clients."
                            )
                            proposed_description = ""
                            fallback_reason = "deterministic_fallback"
                            try:
                                generator = self._run_agent(
                                    AGENT_GENERATOR,
                                    current_context,
                                    {"field": "description"},
                                )
                                candidate = str(generator.get("output", {}).get("value", "")).strip()
                                confidence = float(generator.get("confidence", 0.0))
                                truth_description = str(context.get("truth_data", {}).get("description", "")).strip()
                                conflict_with_truth = bool(
                                    truth_description
                                    and candidate
                                    and candidate != truth_description
                                )
                                evaluator = self._run_agent(
                                    AGENT_EVALUATOR,
                                    current_context,
                                    {"field": "description", "candidate": candidate},
                                )
                                eval_accepted = bool(evaluator.get("output", {}).get("accepted", False))
                                if confidence < self._effective_reasoning_threshold():
                                    fallback_reason = "low_confidence"
                                elif conflict_with_truth:
                                    fallback_reason = "conflicts_with_truth"
                                elif not eval_accepted:
                                    fallback_reason = "evaluator_rejected"
                                else:
                                    proposed_description = candidate
                                    fallback_reason = ""
                                    client_data["description"] = proposed_description
                                self._record_agent_performance(final_slug, AGENT_GENERATOR, success=bool(proposed_description))
                                self._record_agent_performance(final_slug, AGENT_EVALUATOR, success=eval_accepted)
                                self._record_agent_learning_signal(
                                    final_slug,
                                    AGENT_GENERATOR,
                                    confidence,
                                    accepted=bool(proposed_description),
                                    improved_outcome=bool(proposed_description),
                                    rejected=not bool(proposed_description),
                                )
                                self._record_agent_learning_signal(
                                    final_slug,
                                    AGENT_EVALUATOR,
                                    float(evaluator.get("confidence", 0.0)),
                                    accepted=eval_accepted,
                                    improved_outcome=eval_accepted,
                                    rejected=not eval_accepted,
                                )
                            except Exception as exc:
                                fallback_reason = f"fallback_due_to_error:{exc}"
                            if not proposed_description:
                                client_data["description"] = deterministic_description

                elif step == "GENERATE_CTA":
                    fallback_reason = ""
                    reasoning_cta_confidence = 0.0
                    reasoning_cta_fields: list[str] = []
                    reasoning_cta_accepted = False
                    cta_proposals: dict = {}
                    needs_cta_generation = (
                        not client_data.get("cta_primary", "").strip()
                        or not client_data.get("cta_secondary", "").strip()
                    )
                    if needs_cta_generation:
                        try:
                            generator = self._run_agent(
                                AGENT_GENERATOR,
                                current_context,
                                {"field": "cta"},
                            )
                            reasoning_cta_confidence = float(generator.get("confidence", 0.0))
                            cta_proposals = generator.get("output", {}).get("value", {})
                            cta_proposals = cta_proposals if isinstance(cta_proposals, dict) else {}
                            reasoning_cta_fields = sorted(cta_proposals.keys())
                            truth_data = context.get("truth_data", {})
                            truth_primary = str(truth_data.get("cta_primary", "")).strip() if isinstance(truth_data, dict) else ""
                            truth_secondary = str(truth_data.get("cta_secondary", "")).strip() if isinstance(truth_data, dict) else ""
                            proposal_primary = str(cta_proposals.get("cta_primary", "")).strip()
                            proposal_secondary = str(cta_proposals.get("cta_secondary", "")).strip()
                            conflict_with_truth = (
                                (truth_primary and proposal_primary and proposal_primary != truth_primary)
                                or (truth_secondary and proposal_secondary and proposal_secondary != truth_secondary)
                            )
                            if reasoning_cta_confidence < self._effective_reasoning_threshold():
                                fallback_reason = "low_confidence"
                            elif conflict_with_truth:
                                fallback_reason = "conflicts_with_truth"
                            else:
                                evaluator = self._run_agent(
                                    AGENT_EVALUATOR,
                                    current_context,
                                    {"field": "cta", "candidate": cta_proposals},
                                )
                                reasoning_cta_accepted = bool(evaluator.get("output", {}).get("accepted", False))
                                fallback_reason = "" if reasoning_cta_accepted else "evaluator_rejected"
                                self._record_agent_performance(final_slug, AGENT_EVALUATOR, success=reasoning_cta_accepted)
                                self._record_agent_learning_signal(
                                    final_slug,
                                    AGENT_EVALUATOR,
                                    float(evaluator.get("confidence", 0.0)),
                                    accepted=reasoning_cta_accepted,
                                    improved_outcome=reasoning_cta_accepted,
                                    rejected=not reasoning_cta_accepted,
                                )
                            self._record_agent_performance(final_slug, AGENT_GENERATOR, success=reasoning_cta_accepted)
                            self._record_agent_learning_signal(
                                final_slug,
                                AGENT_GENERATOR,
                                reasoning_cta_confidence,
                                accepted=reasoning_cta_accepted,
                                improved_outcome=reasoning_cta_accepted,
                                rejected=not reasoning_cta_accepted,
                            )
                        except Exception as exc:
                            fallback_reason = f"fallback_due_to_error:{exc}"
                    else:
                        fallback_reason = "not_needed"

                    if not client_data.get("cta_primary", "").strip():
                        if (
                            previous_results.get("GENERATE_CTA") == "success"
                            and str(previous_generated.get("cta_primary", "")).strip()
                        ):
                            client_data["cta_primary"] = previous_generated["cta_primary"]
                            self._log_activity(f"[MEMORY] reused cta_primary slug={final_slug}")
                        else:
                            if reasoning_cta_accepted:
                                client_data["cta_primary"] = str(
                                    cta_proposals.get("cta_primary", "")
                                ).strip() or "Book a free call"
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
                            if reasoning_cta_accepted:
                                client_data["cta_secondary"] = str(
                                    cta_proposals.get("cta_secondary", "")
                                ).strip() or "See our services"
                            else:
                                client_data["cta_secondary"] = "See our services"
                    self._log_activity(
                        f"[AGENT] generator_cta confidence={reasoning_cta_confidence:.2f} "
                        f"fields={','.join(reasoning_cta_fields)} accepted={reasoning_cta_accepted} "
                        f"fallback_reason={fallback_reason or 'none'}"
                    )

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
                current_context["analysis_input"] = dict(client_data)
            except Exception:
                self._log_activity(f"[ACTION] {step} status=failed")
                execution_results[step] = "failed"
                raise

        evaluator_summary = self._run_agent(
            AGENT_EVALUATOR,
            current_context,
            {"field": "description", "candidate": client_data.get("description", "")},
        )
        final_evaluator_ok = bool(evaluator_summary.get("output", {}).get("accepted", False))
        self._record_agent_performance(final_slug, AGENT_EVALUATOR, success=final_evaluator_ok)
        self._record_agent_learning_signal(
            final_slug,
            AGENT_EVALUATOR,
            float(evaluator_summary.get("confidence", 0.0)),
            accepted=final_evaluator_ok,
            improved_outcome=final_evaluator_ok,
            rejected=not final_evaluator_ok,
        )

        current_score = 1.0 if final_evaluator_ok else 0.7
        previous_result_values = previous_results.values() if isinstance(previous_results, dict) else ()
        repeated_failure = any(status == "failed" for status in previous_result_values)
        if current_score < float(self._learning_controls.get("evaluation_threshold", EVALUATION_THRESHOLD)) or repeated_failure:
            evaluator_output = evaluator_summary.get("output", {}) if isinstance(evaluator_summary.get("output", {}), dict) else {}
            issues_detected = []

            raw_issues = evaluator_output.get("issues_detected", [])
            if isinstance(raw_issues, list):
                issues_detected.extend(str(issue) for issue in raw_issues if issue)
            elif raw_issues:
                issues_detected.append(str(raw_issues))

            evaluator_issue = evaluator_output.get("issue")
            if evaluator_issue:
                issues_detected.append(str(evaluator_issue))

            failed_steps = [
                f"step_failed:{step_name}"
                for step_name, status in previous_results.items()
                if status == "failed"
            ]
            issues_detected.extend(failed_steps)

            if not final_evaluator_ok and not issues_detected:
                issues_detected.append("evaluation_rejected")

            issues_detected = list(dict.fromkeys(issues_detected))

            optimizer = self._run_agent(
                AGENT_OPTIMIZER,
                current_context,
                {"evaluation": {"issues_detected": issues_detected}},
            )
            has_suggestions = bool(optimizer.get("output", {}).get("actions", []))
            self._record_agent_performance(final_slug, AGENT_OPTIMIZER, success=has_suggestions)
            self._record_agent_learning_signal(
                final_slug,
                AGENT_OPTIMIZER,
                float(optimizer.get("confidence", 0.0)),
                accepted=has_suggestions,
                improved_outcome=has_suggestions,
                rejected=not has_suggestions,
            )

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

    def _propose_actions(self, client_context: dict) -> dict:
        analysis = client_context.get("analysis") if isinstance(client_context.get("analysis"), dict) else {}
        execution_results = client_context.get("execution_results", {})
        optimizer = client_context.get("optimizer_output", {})
        slug = str(client_context.get("slug", "")).strip()
        proposed_actions: list[dict] = []

        if slug:
            summary_payload = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "analysis_summary": {
                    "completeness_score": analysis.get("completeness_score"),
                    "validation_warnings": analysis.get("validation_warnings", []),
                    "action_plan": analysis.get("action_plan", []),
                },
                "execution_results": execution_results if isinstance(execution_results, dict) else {},
                "optimizer_actions": optimizer.get("actions", []) if isinstance(optimizer, dict) else [],
            }
            proposed_actions.append(
                {
                    "type": ACTION_FILE_WRITE,
                    "target": f"clients/{slug}/safe_outputs/action_summary.json",
                    "payload": summary_payload,
                    "reason": "Persist deterministic execution summary for external consumers",
                    "confidence": 0.92,
                }
            )

        if not proposed_actions:
            proposed_actions.append(
                {
                    "type": ACTION_NO_OP,
                    "target": "none",
                    "payload": {},
                    "reason": "No safe external actions proposed",
                    "confidence": 1.0,
                }
            )
        return {"proposed_actions": proposed_actions}

    def _validate_action(self, action: dict, slug: str, policy: dict) -> tuple[bool, str]:
        action_type = str(action.get("type", "")).strip()
        target = str(action.get("target", "")).strip()
        if action_type not in SUPPORTED_ACTION_TYPES:
            return False, "unsupported_action_type"
        if action_type not in policy.get("allowed_actions", []):
            return False, "action_type_not_allowed_by_policy"

        if any(marker in target.replace("\\", "/") for marker in PROTECTED_PATH_MARKERS):
            return False, "target_is_protected"

        if action_type == ACTION_FILE_WRITE:
            expected_root = (self.paths["clients"] / slug / "safe_outputs").resolve()
            target_path = (self.base_dir / Path(target)).resolve()
            try:
                target_path.relative_to(expected_root)
            except Exception:
                return False, "file_write_outside_safe_outputs"

        if action_type in {ACTION_API_CALL, ACTION_WEBHOOK}:
            parsed = urlparse(target)
            host = (parsed.hostname or "").lower()
            if not host:
                return False, "invalid_network_target"
            allowed_domains = policy.get("allowed_domains", [])
            if host not in allowed_domains:
                return False, "domain_not_whitelisted"

        if action_type == ACTION_COMMAND:
            if not bool(policy.get("command_enabled", False)):
                return False, "command_disabled"

        return True, "ok"

    def _execute_action(self, action: dict, slug: str, policy: dict) -> dict:
        action_type = str(action.get("type", "")).strip()
        target = str(action.get("target", "")).strip()
        payload = action.get("payload", {})

        if action_type == ACTION_NO_OP:
            return {"status": "skipped", "details": "no_op"}

        if action_type == ACTION_FILE_WRITE:
            output_root = (self.paths["clients"] / slug / "safe_outputs").resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            target_path = (self.base_dir / Path(target)).resolve()
            try:
                target_path.relative_to(output_root)
            except Exception:
                return {"status": "failed", "details": "file_write_outside_safe_outputs"}
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return {"status": "success", "details": f"wrote:{target_path}"}

        if action_type in {ACTION_API_CALL, ACTION_WEBHOOK}:
            body = json.dumps(payload if isinstance(payload, dict) else {"payload": payload}).encode("utf-8")
            req = Request(target, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=8) as resp:
                response_code = getattr(resp, "status", 200)
            return {"status": "success", "details": f"http_status:{response_code}"}

        if action_type == ACTION_COMMAND:
            command = payload.get("command", []) if isinstance(payload, dict) else []
            if not isinstance(command, list) or not command:
                return {"status": "failed", "details": "missing_command_payload"}
            completed = subprocess.run(
                [str(token) for token in command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return {
                "status": "success" if completed.returncode == 0 else "failed",
                "details": f"returncode:{completed.returncode}",
            }

        return {"status": "failed", "details": "unsupported_action_runtime"}

    def _process_external_actions(self, slug: str, analysis: ClientAnalysis, execution_results: dict) -> None:
        policy = self._load_action_policy(slug)
        proposed = self._propose_actions(
            {
                "slug": slug,
                "analysis": analysis.to_log_dict(),
                "execution_results": execution_results,
            }
        ).get("proposed_actions", [])
        if not isinstance(proposed, list):
            proposed = []

        max_actions = int(policy.get("max_actions_per_cycle", 0))
        queued_actions = proposed[:max_actions] if max_actions > 0 else []
        self._log_activity(
            f"[ACTION] proposed slug={slug} count={len(proposed)} queued={len(queued_actions)} require_approval={policy.get('require_approval', True)}"
        )
        for action in queued_actions:
            self._append_action_history(
                slug,
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "stage": "proposed",
                    "action": action,
                },
            )
            is_valid, reason = self._validate_action(action, slug, policy)
            if not is_valid:
                self._log_activity(f"[ACTION] rejected slug={slug} type={action.get('type')} reason={reason}")
                self._append_action_history(
                    slug,
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "stage": "rejected",
                        "action": action,
                        "result": {"status": "rejected", "reason": reason},
                    },
                )
                continue

            if bool(policy.get("require_approval", True)):
                self._log_activity(f"[ACTION] pending_approval slug={slug} type={action.get('type')}")
                self._append_action_history(
                    slug,
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "stage": "pending_approval",
                        "action": action,
                    },
                )
                continue

            try:
                result = self._execute_action(action, slug, policy)
                self._log_activity(
                    f"[ACTION] executed slug={slug} type={action.get('type')} status={result.get('status')}"
                )
            except Exception as exc:
                result = {"status": "failed", "details": str(exc)}
                self._log_activity(
                    f"[ACTION] failed slug={slug} type={action.get('type')} error={exc}"
                )
            self._append_action_history(
                slug,
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "stage": "executed",
                    "action": action,
                    "result": result,
                },
            )

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
            self._process_external_actions(final_analysis.slug, final_analysis, execution_results)

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
        self._supervisor_cycle_count += 1
        self._scan_existing_clients()
        self._run_goal_supervisor_cycle()
        if self._supervisor_cycle_count % SYSTEM_LEARNING_INTERVAL_CYCLES == 0:
            self._run_system_learning_cycle()
        if self._auto_mode:
            self._scan_and_process_inbox()

    def _safe_float(self, value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _evaluate_goal_progress(self, goal: dict, client_context: dict) -> dict:
        evaluation = client_context.get("evaluation", {}) if isinstance(client_context.get("evaluation", {}), dict) else {}
        memory = client_context.get("memory", {}) if isinstance(client_context.get("memory", {}), dict) else {}
        target_state = goal.get("target_state", {}) if isinstance(goal.get("target_state", {}), dict) else {}

        target_score = self._safe_float(target_state.get("overall_score", 0.9), 0.9)
        current_score = self._safe_float(evaluation.get("overall_score", 0.0), 0.0)
        unresolved_issues = evaluation.get("issues_detected", []) if isinstance(evaluation.get("issues_detected", []), list) else []

        progress_ratio = min(1.0, current_score / max(target_score, 0.01))
        progress_percent = round(progress_ratio * 100, 1)
        score_gap = round(max(0.0, target_score - current_score), 3)

        action_history = memory.get("action_history", [])
        if not isinstance(action_history, list):
            action_history = []
        executed = [entry for entry in action_history if isinstance(entry, dict) and entry.get("stage") == "executed"]

        execution_results = memory.get("execution_results", [])
        if not isinstance(execution_results, list):
            execution_results = []

        recent_execution_results = [entry for entry in execution_results if isinstance(entry, dict)][-8:]
        recent_executed = recent_execution_results or executed[-8:]
        failed_exec = []
        for entry in recent_executed:
            status = None
            if isinstance(entry.get("result", {}), dict):
                status = entry.get("result", {}).get("status")
            if status is None:
                status = entry.get("status")
            if status in {"failed", "rejected"}:
                failed_exec.append(entry)

        if recent_executed:
            effectiveness = round((len(recent_executed) - len(failed_exec)) / len(recent_executed), 2)
        else:
            effectiveness = 1.0
        repeated_failure = bool(recent_executed) and len(failed_exec) >= GOAL_FAILURE_ESCALATION_THRESHOLD
        next_needed_actions: list[str] = []
        if score_gap > 0:
            next_needed_actions.append("improve_quality_score")
        if unresolved_issues:
            next_needed_actions.append("resolve_unresolved_issues")
        if effectiveness < 0.5:
            next_needed_actions.append("revise_action_strategy")
        if repeated_failure:
            next_needed_actions.append("escalate_execution_tier")

        blocked = bool(repeated_failure and unresolved_issues)
        complete = bool(current_score >= target_score and not unresolved_issues)
        return {
            "progress_percent": progress_percent,
            "blocked": blocked,
            "complete": complete,
            "current_score": current_score,
            "target_score": target_score,
            "score_gap": score_gap,
            "unresolved_issues": unresolved_issues,
            "action_history_effectiveness": effectiveness,
            "repeated_failure": repeated_failure,
            "next_needed_actions": next_needed_actions,
        }

    def _plan_goal_actions(self, goal: dict, client_context: dict, progress: dict) -> dict:
        slug = str(goal.get("client_slug", "")).strip()
        evaluation = client_context.get("evaluation", {}) if isinstance(client_context.get("evaluation", {}), dict) else {}
        memory = client_context.get("memory", {}) if isinstance(client_context.get("memory", {}), dict) else {}
        unresolved = progress.get("unresolved_issues", []) if isinstance(progress.get("unresolved_issues", []), list) else []

        requested = ["ENRICH_DATA", "GENERATE_DESCRIPTION", "GENERATE_CTA", "RESOLVE_SLUG", "PROCEED_TO_BUILD"]
        planner = self._run_agent(
            AGENT_PLANNER,
            client_context,
            {
                "requested_plan": requested,
                "objective": goal.get("objective", ""),
                "evaluation": evaluation,
                "failure_history": memory.get("execution_results", {}),
            },
        )
        selected_steps = planner.get("output", {}).get("selected_steps", [])
        if not isinstance(selected_steps, list):
            selected_steps = []

        escalation = {
            "tier": "standard",
            "optimizer_actions": [],
            "strategy_shift": "none",
        }
        if progress.get("repeated_failure", False):
            optimizer = self._run_agent(
                AGENT_OPTIMIZER,
                client_context,
                {"evaluation": evaluation, "objective": goal.get("objective", ""), "issues": unresolved},
            )
            optimizer_actions = optimizer.get("output", {}).get("actions", [])
            if not isinstance(optimizer_actions, list):
                optimizer_actions = []
            escalation = {
                "tier": "advanced",
                "optimizer_actions": optimizer_actions,
                "strategy_shift": "failure_recovery",
            }
            if "PROCEED_TO_BUILD" not in selected_steps:
                selected_steps.append("PROCEED_TO_BUILD")

        safeguards = {
            "respect_action_policy": True,
            "respect_validation_layer": True,
            "deny_protected_paths_override": True,
        }
        return {
            "goal_id": goal.get("goal_id", ""),
            "client_slug": slug,
            "planner_steps": selected_steps,
            "next_needed_actions": progress.get("next_needed_actions", []),
            "issues": unresolved,
            "escalation": escalation,
            "safety_constraints": safeguards,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    def _update_goal_record(self, goal_id: str, updates: dict) -> None:
        goals_file = self._system_goals_file()
        if not goals_file.exists():
            return
        try:
            payload = json.loads(goals_file.read_text(encoding="utf-8"))
            goals = payload.get("active_goals", []) if isinstance(payload, dict) else []
            if not isinstance(goals, list):
                return
            changed = False
            for idx, goal in enumerate(goals):
                if isinstance(goal, dict) and goal.get("goal_id") == goal_id:
                    merged = dict(goal)
                    merged.update(updates)
                    goals[idx] = merged
                    changed = True
                    break
            if changed:
                payload["active_goals"] = goals
                self._persist_system_goals(payload)
        except Exception as exc:
            self._log_activity(f"[GOAL] failed_to_update_goal_record goal_id={goal_id} error={exc}")

    def _run_goal_supervisor_cycle(self) -> None:
        active_goals = self._load_active_goals()
        if not active_goals:
            return

        for goal in active_goals:
            goal_id = str(goal.get("goal_id", ""))
            slug = str(goal.get("client_slug", "")).strip()
            if not slug:
                self._log_activity(f"[GOAL] skipped goal_id={goal_id} reason=missing_client_slug")
                continue
            if not (self.paths["clients"] / slug).exists():
                self._log_activity(f"[GOAL] skipped goal_id={goal_id} slug={slug} reason=missing_client")
                continue

            evaluation = self._evaluate_client_state(slug)
            self._persist_client_evaluation(slug, evaluation)
            client_context = self._build_client_context(slug)
            client_context["evaluation"] = evaluation
            progress = self._evaluate_goal_progress(goal, client_context)
            now = datetime.now().isoformat(timespec="seconds")

            if progress["complete"]:
                self._update_goal_record(
                    goal_id,
                    {
                        "status": GOAL_STATUS_COMPLETED,
                        "completion_state": "target_met",
                        "progress": 100,
                        "last_run": now,
                        "completed_at": now,
                    },
                )
                self._record_agent_learning_signal(
                    slug,
                    "GOAL_COMPLETION",
                    1.0,
                    accepted=True,
                    improved_outcome=True,
                    rejected=False,
                )
                self._persist_system_learning_state()
                self._log_activity(f"[GOAL] completed goal_id={goal_id} slug={slug} score={progress['current_score']:.2f}")
                continue

            plan = self._plan_goal_actions(goal, client_context, progress)
            status = GOAL_STATUS_ACTIVE
            completion_state = "in_progress"
            run_result = "planned"
            try:
                reason = (
                    f"goal_id={goal_id};objective={goal.get('objective', '')};"
                    f"progress={progress['progress_percent']};blocked={progress['blocked']};"
                    f"actions={','.join(progress.get('next_needed_actions', [])) or 'none'}"
                )
                self._schedule_client_supervisor_work(slug, reason)
                run_result = "executed"
            except Exception as exc:
                run_result = f"failed:{exc}"
                status = GOAL_STATUS_BLOCKED if progress.get("blocked", False) else GOAL_STATUS_ACTIVE
                completion_state = "execution_failed"
                self._log_activity(f"[GOAL] execution_failed goal_id={goal_id} slug={slug} error={exc}")

            if progress.get("repeated_failure", False):
                completion_state = "escalated_recovery"
                status = GOAL_STATUS_BLOCKED if progress.get("blocked", False) else status

            self._update_goal_record(
                goal_id,
                {
                    "progress": progress["progress_percent"],
                    "status": status,
                    "blocked": progress["blocked"],
                    "last_run": now,
                    "completion_state": completion_state,
                    "last_evaluation": {
                        "current_score": progress["current_score"],
                        "target_score": progress["target_score"],
                        "score_gap": progress["score_gap"],
                        "unresolved_issues": progress["unresolved_issues"],
                        "action_history_effectiveness": progress["action_history_effectiveness"],
                    },
                    "last_plan": plan,
                    "last_result": run_result,
                },
            )
            self._log_activity(
                f"[GOAL] cycle goal_id={goal_id} slug={slug} progress={progress['progress_percent']} "
                f"blocked={progress['blocked']} result={run_result} tier={plan.get('escalation', {}).get('tier', 'standard')}"
            )

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
            if evaluation["overall_score"] >= float(self._learning_controls.get("evaluation_threshold", EVALUATION_THRESHOLD)) and not evaluation.get("source_changed", False):
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

    def _analyze_system_performance(self) -> dict:
        memory_files = sorted(self.paths["clients"].glob("*/memory.json"))
        failure_counter: dict[str, int] = {}
        issue_counter: dict[str, int] = {}
        weak_field_counter = {"description": 0, "cta": 0, "completeness": 0}
        rejected_reasoning_outputs = 0
        total_reasoning_calls = 0
        total_fallback_count = 0
        evaluation_scores: list[float] = []
        failed_clients = 0

        for memory_file in memory_files:
            try:
                memory = json.loads(memory_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(memory, dict):
                continue

            execution_results = memory.get("execution_results", {})
            if isinstance(execution_results, dict):
                has_failure = False
                for step, status in execution_results.items():
                    if status == "failed":
                        failure_counter[step] = failure_counter.get(step, 0) + 1
                        has_failure = True
                if has_failure:
                    failed_clients += 1

            scores = memory.get("scores", {})
            if isinstance(scores, dict):
                overall_score = float(scores.get("overall_score", 0.0))
                if overall_score:
                    evaluation_scores.append(overall_score)
                if float(scores.get("description_score", 0.0)) < 0.8:
                    weak_field_counter["description"] += 1
                if float(scores.get("cta_score", 0.0)) < 0.8:
                    weak_field_counter["cta"] += 1
                if float(scores.get("completeness_score", 0.0)) < 1.0:
                    weak_field_counter["completeness"] += 1

            for issue in memory.get("issues", []):
                issue_name = str(issue)
                issue_counter[issue_name] = issue_counter.get(issue_name, 0) + 1

            reasoning_history = memory.get("reasoning_history", {})
            if isinstance(reasoning_history, dict):
                rejected_reasoning_outputs += int(reasoning_history.get("rejected_outputs", 0))
                total_reasoning_calls += int(reasoning_history.get("total_calls", 0))
                total_fallback_count += int(reasoning_history.get("fallback_count", 0))

        mean_score = round(sum(evaluation_scores) / len(evaluation_scores), 3) if evaluation_scores else 0.0
        failure_rate = round(failed_clients / len(memory_files), 3) if memory_files else 0.0
        fallback_frequency = round(total_fallback_count / total_reasoning_calls, 3) if total_reasoning_calls else 0.0
        reasoning_effectiveness = round(
            max(0.0, min(1.0, 1.0 - fallback_frequency - (failure_rate * 0.2))),
            3,
        )

        recurring_failures = sorted(
            [name for name, count in failure_counter.items() if count >= 2]
        )
        weak_fields = sorted([name for name, count in weak_field_counter.items() if count > 0])
        system_issues = sorted([name for name, count in issue_counter.items() if count >= 2])[:20]

        improvement_opportunities = []
        if recurring_failures:
            improvement_opportunities.append("reduce_failed_steps_with_targeted_threshold_tuning")
        if fallback_frequency > 0.45:
            improvement_opportunities.append("decrease_reasoning_acceptance_barrier_carefully")
        if mean_score < 0.85:
            improvement_opportunities.append("increase_quality_weighting_for_weak_fields")
        if rejected_reasoning_outputs > 0:
            improvement_opportunities.append("tighten_reasoning_validation_rules")

        return {
            "system_issues": system_issues,
            "recurring_failures": recurring_failures,
            "weak_fields": weak_fields,
            "reasoning_effectiveness_score": reasoning_effectiveness,
            "improvement_opportunities": improvement_opportunities,
            "metrics": {
                "memory_files_scanned": len(memory_files),
                "mean_overall_score": mean_score,
                "rejected_reasoning_outputs": rejected_reasoning_outputs,
                "fallback_frequency": fallback_frequency,
                "failure_rate": failure_rate,
            },
        }

    def _generate_system_adjustments(self, analysis: dict) -> dict:
        metrics = analysis.get("metrics", {}) if isinstance(analysis.get("metrics", {}), dict) else {}
        fallback_frequency = float(metrics.get("fallback_frequency", 0.0))
        mean_score = float(metrics.get("mean_overall_score", 0.0))
        recurring_failures = analysis.get("recurring_failures", []) if isinstance(analysis.get("recurring_failures", []), list) else []

        recommended_threshold_changes = {}
        if fallback_frequency > 0.50:
            recommended_threshold_changes["reasoning_confidence_threshold"] = -0.03
        elif fallback_frequency < 0.10:
            recommended_threshold_changes["reasoning_confidence_threshold"] = 0.01

        if mean_score < 0.80:
            recommended_threshold_changes["evaluation_threshold"] = -0.02
        elif mean_score > 0.92:
            recommended_threshold_changes["evaluation_threshold"] = 0.01

        priority_weight_adjustments = {}
        weak_fields = analysis.get("weak_fields", []) if isinstance(analysis.get("weak_fields", []), list) else []
        if "description" in weak_fields:
            priority_weight_adjustments["description"] = 0.02
        if "cta" in weak_fields:
            priority_weight_adjustments["cta"] = 0.02
        if "completeness" in weak_fields:
            priority_weight_adjustments["completeness"] = 0.02

        return {
            "recommended_threshold_changes": recommended_threshold_changes,
            "profile_adjustments": {
                "focus_profile_refresh": bool(recurring_failures),
            },
            "validation_rule_tweaks": {
                "stricter_reasoning_validation": bool(analysis.get("system_issues", [])),
            },
            "reasoning_confidence_adjustments": {
                "offset": -0.01 if fallback_frequency > 0.55 else 0.0,
            },
            "priority_weight_adjustments": priority_weight_adjustments,
        }

    def _apply_safe_adjustments(self, adjustments: dict) -> dict:
        applied_changes: list[dict] = []
        before_controls = json.loads(json.dumps(self._learning_controls))
        forbidden_targets = ("system_logic", "truth_data", "raw_client_data", "execution_order")
        blocked_attempts = [target for target in forbidden_targets if target in adjustments]
        if blocked_attempts:
            self._log_activity(f"[SYSTEM_LEARNING] blocked_forbidden_adjustments={','.join(blocked_attempts)}")

        threshold_changes = adjustments.get("recommended_threshold_changes", {})
        if isinstance(threshold_changes, dict):
            for key in ("evaluation_threshold", "reasoning_confidence_threshold"):
                if key not in threshold_changes:
                    continue
                delta = float(threshold_changes.get(key, 0.0))
                bounds = ADJUSTMENT_BOUNDS[key]
                before = float(self._learning_controls.get(key, 0.0))
                after = max(bounds["min"], min(bounds["max"], before + delta))
                self._learning_controls[key] = round(after, 3)
                applied_changes.append({"type": "threshold", "key": key, "before": before, "after": after})

        weight_changes = adjustments.get("priority_weight_adjustments", {})
        if isinstance(weight_changes, dict):
            weights = self._learning_controls.get("weights", {})
            for key in ("completeness", "description", "cta"):
                if key not in weight_changes:
                    continue
                delta = float(weight_changes.get(key, 0.0))
                before = float(weights.get(key, 0.0))
                after = max(
                    ADJUSTMENT_BOUNDS["priority_weight"]["min"],
                    min(ADJUSTMENT_BOUNDS["priority_weight"]["max"], before + delta),
                )
                weights[key] = round(after, 3)
                applied_changes.append({"type": "weight", "key": key, "before": before, "after": after})
            self._learning_controls["weights"] = weights

        confidence_adjustment = adjustments.get("reasoning_confidence_adjustments", {})
        if isinstance(confidence_adjustment, dict):
            delta = float(confidence_adjustment.get("offset", 0.0))
            before = float(self._learning_controls.get("reasoning_confidence_adjustment", 0.0))
            after = max(
                ADJUSTMENT_BOUNDS["confidence_adjustment"]["min"],
                min(ADJUSTMENT_BOUNDS["confidence_adjustment"]["max"], before + delta),
            )
            self._learning_controls["reasoning_confidence_adjustment"] = round(after, 3)
            if delta:
                applied_changes.append(
                    {"type": "scoring_modifier", "key": "reasoning_confidence_adjustment", "before": before, "after": after}
                )

        self._log_activity(f"[SYSTEM_LEARNING] proposed_adjustments={json.dumps(adjustments, sort_keys=True)}")
        self._log_activity(f"[SYSTEM_LEARNING] applied_changes={json.dumps(applied_changes, sort_keys=True)}")

        return {
            "before_controls": before_controls,
            "after_controls": json.loads(json.dumps(self._learning_controls)),
            "applied_changes": applied_changes,
            "reversible_snapshot": before_controls,
        }

    def _run_system_learning_cycle(self) -> None:
        before_metrics = {"evaluation_threshold": self._learning_controls["evaluation_threshold"]}
        analysis = self._analyze_system_performance()
        adjustments = self._generate_system_adjustments(analysis)
        applied = self._apply_safe_adjustments(adjustments)
        after_metrics = {"evaluation_threshold": self._learning_controls["evaluation_threshold"]}

        self._system_learning_state["last_analysis"] = analysis
        self._system_learning_state["applied_adjustments"] = (
            self._system_learning_state.get("applied_adjustments", []) + [applied]
        )[-40:]
        trend = self._system_learning_state.get("system_score_trend", [])
        if not isinstance(trend, list):
            trend = []
        trend.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "reasoning_effectiveness_score": analysis.get("reasoning_effectiveness_score", 0.0),
                "mean_overall_score": analysis.get("metrics", {}).get("mean_overall_score", 0.0),
            }
        )
        self._system_learning_state["system_score_trend"] = trend[-80:]
        self._persist_system_learning_state()

        self._log_activity(
            f"[SYSTEM_LEARNING] detected_issues={','.join(analysis.get('system_issues', [])) or 'none'} "
            f"before_metrics={json.dumps(before_metrics, sort_keys=True)} "
            f"after_metrics={json.dumps(after_metrics, sort_keys=True)}"
        )

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
            self._process_external_actions(final_analysis.slug, final_analysis, execution_results)

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
