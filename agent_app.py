import json
import os
import re
import shutil
import sys
import webbrowser
from dataclasses import dataclass
from html import escape
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, VERTICAL, W, Button, Entry, Frame, Label, Listbox, Menu, Scrollbar, StringVar, Tk, Toplevel, filedialog, messagebox
from urllib.parse import quote


PLACEHOLDERS = {
    "{{BUSINESS_NAME}}": "name",
    "{{BUSINESS_TYPE}}": "business_type",
    "{{EMAIL}}": "email",
    "{{PHONE}}": "phone",
    "{{INSTAGRAM}}": "instagram",
    "{{DESCRIPTION}}": "description",
    "{{CTA_PRIMARY}}": "cta_primary",
    "{{CTA_SECONDARY}}": "cta_secondary",
}


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
        self.root.title("Agent.exe")
        self.root.geometry("620x430")
        self.root.minsize(620, 430)

        self.base_dir = self._resolve_base_dir()
        self.paths = {
            "clients": self.base_dir / "clients",
            "templates": self.base_dir / "templates" / "base-site",
            "prompts": self.base_dir / "prompts",
            "assets": self.base_dir / "assets",
            "deploy": self.base_dir / "deploy",
            "tools": self.base_dir / "tools",
            "logs": self.base_dir / "logs",
            "config": self.base_dir / "config.json",
        }

        self.selected_client: str | None = None

        self._ensure_core_structure()
        self._build_ui()
        self.refresh_client_list()

    def _resolve_base_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            return exe_dir
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
        for key in ("clients", "templates", "prompts", "assets", "deploy", "tools", "logs"):
            self.paths[key].mkdir(parents=True, exist_ok=True)

    def _build_ui(self) -> None:
        main = Frame(self.root, padx=14, pady=14)
        main.pack(fill=BOTH, expand=True)

        title = Label(main, text="Agent.exe — Portable SSD Web Agency", font=("Segoe UI", 14, "bold"))
        title.pack(anchor=W, pady=(0, 10))

        body = Frame(main)
        body.pack(fill=BOTH, expand=True)

        left_col = Frame(body)
        left_col.pack(side=LEFT, fill=BOTH, expand=False, padx=(0, 10))

        button_specs = [
            ("New Client", self.open_new_client_form),
            ("Open Client", self.open_client_dialog),
            ("Generate Site", self.generate_site),
            ("Preview Site", self.preview_site),
            ("Export Deploy", self.export_deploy),
            ("Open SSD Folder", self.open_ssd_folder),
        ]
        for text, command in button_specs:
            Button(
                left_col,
                text=text,
                width=22,
                pady=6,
                command=command,
            ).pack(pady=4, anchor=W)

        right_col = Frame(body)
        right_col.pack(side=RIGHT, fill=BOTH, expand=True)

        Label(right_col, text="Clients", font=("Segoe UI", 11, "bold")).pack(anchor=W)
        list_frame = Frame(right_col)
        list_frame.pack(fill=BOTH, expand=True, pady=(6, 0))

        self.client_list = Listbox(list_frame, exportselection=False)
        self.client_list.pack(side=LEFT, fill=BOTH, expand=True)
        self.client_list.bind("<<ListboxSelect>>", self._on_client_select)

        scrollbar = Scrollbar(list_frame, orient=VERTICAL, command=self.client_list.yview)
        scrollbar.pack(side=RIGHT, fill="y")
        self.client_list.config(yscrollcommand=scrollbar.set)

        self.status_var = StringVar(value="Ready.")
        Label(main, textvariable=self.status_var, anchor=W).pack(fill=BOTH, pady=(10, 0))

        menu = Menu(self.root)
        self.root.config(menu=menu)
        file_menu = Menu(menu, tearoff=0)
        menu.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Choose SSD Root...", command=self.choose_root)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

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
            p.name for p in self.paths["clients"].iterdir() if p.is_dir()
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
        self.base_dir = Path(new_root)
        self.paths.update(
            {
                "clients": self.base_dir / "clients",
                "templates": self.base_dir / "templates" / "base-site",
                "prompts": self.base_dir / "prompts",
                "assets": self.base_dir / "assets",
                "deploy": self.base_dir / "deploy",
                "tools": self.base_dir / "tools",
                "logs": self.base_dir / "logs",
                "config": self.base_dir / "config.json",
            }
        )
        self._ensure_core_structure()
        self.refresh_client_list()
        self.status_var.set(f"SSD root set to: {self.base_dir}")

    def open_new_client_form(self) -> None:
        form = Toplevel(self.root)
        form.title("New Client")
        form.geometry("520x430")
        form.transient(self.root)

        entries: dict[str, Entry] = {}
        fields = [
            ("Client Name", "name"),
            ("Business Type", "business_type"),
            ("Brand/Style", "brand_style"),
            ("Email", "email"),
            ("Phone", "phone"),
            ("Instagram", "instagram"),
            ("Short Description", "description"),
            ("Primary CTA", "cta_primary"),
            ("Secondary CTA", "cta_secondary"),
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

    def create_client(self, data: ClientData) -> None:
        client_slug = self.sanitize_client_name(data.name)
        client_root = self.paths["clients"] / client_slug
        notes_file = client_root / "notes" / "client.json"
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

    def _is_html_like_file(self, path: Path) -> bool:
        return path.suffix.lower() in {".html", ".svg", ".xml"}

    def _get_safe_placeholder_value(self, path: Path, placeholder: str, key: str, client_data: dict) -> str:
        value = str(client_data.get(key, ""))
        if not self._is_html_like_file(path):
            return value

        placeholder_name = placeholder.upper()

        if "MAILTO" in placeholder_name or ("EMAIL" in placeholder_name and any(token in placeholder_name for token in ("HREF", "URL", "LINK"))):
            return quote(value, safe="@._+-")

        if "TEL" in placeholder_name or ("PHONE" in placeholder_name and any(token in placeholder_name for token in ("HREF", "URL", "LINK"))):
            return quote(value, safe="+0123456789()-")

        if "INSTAGRAM" in placeholder_name and any(token in placeholder_name for token in ("HREF", "URL", "LINK", "PATH", "HANDLE")):
            return quote(value, safe="._")

        return escape(value, quote=True)

    def generate_site(self):
        client_root = self._require_selected_client()
        if not client_root:
            return

        template_root = self.paths["templates"]
        if not template_root.exists():
            self._show_error(f"Template folder not found: {template_root}")
            return

        try:
            client_data = self._read_client_data(client_root)
            target_site = client_root / "site"
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

            self.status_var.set(f"Generated site for {self.selected_client} ({copied} files).")
            messagebox.showinfo("Generate Site", f"Generated {copied} files for {self.selected_client}.")
        except Exception as exc:
            self._show_error(f"Site generation failed: {exc}")

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
        target = str(target_path)
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                webbrowser.open(target_path.as_uri())
            self.status_var.set(f"Opened SSD folder: {target}")
        except Exception as exc:
            self._show_error(f"Could not open SSD folder: {exc}")

    def _is_text_file(self, path: Path) -> bool:
        text_extensions = {
            ".html",
            ".css",
            ".js",
            ".txt",
            ".json",
            ".md",
            ".xml",
            ".svg",
            ".yml",
            ".yaml",
        }
        return path.suffix.lower() in text_extensions

    def _show_error(self, msg: str):
        self.status_var.set(f"Error: {msg}")
        messagebox.showerror("Agent.exe", msg)


if __name__ == "__main__":
    root = Tk()
    app = AgentApp(root)
    root.mainloop()
