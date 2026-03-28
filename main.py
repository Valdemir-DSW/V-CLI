"""
V CLI - Interface Gráfica Compacta e Funcional
Reorganizada com: console em destaque, histórico de projetos,
opções avançadas e monitor serial
"""

import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, scrolledtext
from pathlib import Path
from cli_backend import CLIBackend
import threading
import time
import locale
from datetime import datetime
import re


class VCliApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.locale_dir = Path.cwd() / "locales"
        self.translations = {}
        self.lang = "en"
        self._load_i18n()
        self.title(self.t("app.title", "V CLI - VS Code Arduino plugin"))
        self.geometry("900x600")
        self.minsize(700, 400)
        self.resizable(False, False)
        
        self.current_project = None
        self.current_config = None
        self.console = None
        self.backend = None
        self.serial_connection = None
        self.serial_stamp_enabled = False
        self.serial_tx_enabled = False
        self.serial_tx_log = []
        self.available_ports = []
        self.baud_options = ["9600", "19200", "38400", "57600", "115200"]
        self.recent_projects_file = Path.cwd() / ".recent_projects.json"
        self.app_icon_path = Path.cwd() / ".ico"
        
        # Cache de placas para não recarregar toda hora
        self.boards_cache = None
        self.boards_cache_time = 0
        self.boards_loading = False
        
        self._load_recent_projects()
        self._create_ui()  # IMPORTANTE: cria console ANTES do backend
        self._apply_window_icon(self)
        self.backend = CLIBackend(os.getcwd(), self.log)  # Agora log() funciona
        self.after(300, self._load_initial_data)

    def _load_i18n(self):
        self.lang = self._detect_system_lang()
        base = {}
        en_file = self.locale_dir / "en.json"
        lang_file = self.locale_dir / f"{self.lang}.json"
        try:
            if en_file.exists():
                base = json.loads(en_file.read_text(encoding="utf-8-sig"))
        except Exception:
            base = {}
        overlay = {}
        try:
            if lang_file.exists():
                overlay = json.loads(lang_file.read_text(encoding="utf-8-sig"))
        except Exception:
            overlay = {}
        self.translations = {**base, **overlay}

    def _detect_system_lang(self):
        try:
            loc = locale.getdefaultlocale()[0] if locale.getdefaultlocale() else ""
            if not loc:
                loc = locale.getlocale()[0] if locale.getlocale() else ""
            if loc and loc.lower().startswith("pt"):
                return "pt"
        except Exception:
            pass
        return "en"

    def t(self, key: str, default: str = ""):
        return self.translations.get(key, default or key)

    def _apply_window_icon(self, window):
        try:
            if self.app_icon_path.exists():
                window.iconbitmap(str(self.app_icon_path))
        except Exception:
            pass
    
    def _create_ui(self):
        """Interface compacta com console em destaque"""
        main_frame = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        
        # PAINEL ESQUERDO: HISTÓRICO
        left_frame = ttk.Frame(main_frame, width=130)
        main_frame.add(left_frame)
        
        ttk.Label(left_frame, text=self.t("nav.new_open", "NEW/OPEN"), font=("Arial", 8, "bold")).pack(anchor="w")
        btn_f = ttk.Frame(left_frame)
        btn_f.pack(fill=tk.X, pady=2)
        ttk.Button(btn_f, text=self.t("btn.new", "New"), command=self._create_project, width=6).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_f, text=self.t("btn.open", "Open"), command=self._open_project, width=6).pack(side=tk.LEFT, padx=1)
        
        ttk.Label(left_frame, text=self.t("nav.history", "HISTORY"), font=("Arial", 8, "bold")).pack(anchor="w", pady=(5, 2))
        self.recent_listbox = tk.Listbox(left_frame, height=10, font=("Arial", 7), activestyle='none')
        self.recent_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 3))
        self.recent_listbox.bind('<Double-1>', self._open_recent)
        self.recent_listbox.bind('<Button-3>', self._remove_recent)
        self._populate_recent_projects()
        
        ttk.Label(left_frame, text=self.t("hint.history", "dblclick: open\nright: remove"), font=("Arial", 6), justify=tk.LEFT).pack(anchor="w")
        
        # PAINEL DIREITO: ABAS + CONSOLE
        right_frame = ttk.Frame(main_frame)
        main_frame.add(right_frame, weight=1)
        
        r_split = ttk.PanedWindow(right_frame, orient=tk.VERTICAL)
        r_split.pack(fill=tk.BOTH, expand=True)
        
        self.notebook = ttk.Notebook(r_split)
        r_split.add(self.notebook, weight=1)
        
        self._create_code_tab()
        self._create_boards_tab()
        self._create_libs_tab()
        self._create_serial_tab()
        
        # CONSOLE EM DESTAQUE (30%)
        console_frame = ttk.LabelFrame(r_split, text=self.t("panel.output", "OUTPUT"), padding=2)
        r_split.add(console_frame, weight=0)
        
        self.console = scrolledtext.ScrolledText(console_frame, height=7, bg="#1e1e1e", fg="#00ff00", font=("Courier", 8))
        self.console.pack(fill=tk.BOTH, expand=True)
        
        # Tags de color para console
        self.console.tag_config("error", foreground="#ff4444", background="#1e1e1e", font=("Courier", 8, "bold"))
        self.console.tag_config("warning", foreground="#ffaa00", background="#1e1e1e", font=("Courier", 8))
        self.console.tag_config("success", foreground="#00ff00", background="#1e1e1e", font=("Courier", 8, "bold"))
        self.console.tag_config("info", foreground="#44aaff", background="#1e1e1e", font=("Courier", 8))
    
    def _create_code_tab(self):
        """Código - Informações + Configurações em uma única seção"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=self.t("tab.code", "Code"))
        
        # BOTÕES DE AÇÃO (Topo)
        bframe = ttk.Frame(tab)
        bframe.pack(fill=tk.X, padx=2, pady=2)
        
        # Botão abrir pasta no Explorer
        btn_explorer = tk.Button(bframe, text="...", command=self._open_project_folder, 
                                bg="#808080", fg="white", font=("Arial", 9, "bold"),
                                relief=tk.RAISED, bd=1, padx=6, width=3)
        btn_explorer.pack(side=tk.LEFT, padx=1)
        
        btn_code = tk.Button(bframe, text=self.t("btn.vscode", "VS Code"), command=self._open_vscode, 
                             bg="#0078d4", fg="white", font=("Arial", 9, "bold"),
                             relief=tk.RAISED, bd=1, padx=10)
        btn_code.pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        btn_compile = tk.Button(bframe, text=self.t("btn.compile", "Compile"), command=self._compile_with_modal,
                               bg="#90ee90", fg="#000000", font=("Arial", 9, "bold"),
                               relief=tk.RAISED, bd=1, padx=10)
        btn_compile.pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)

        btn_upload = tk.Button(bframe, text=self.t("btn.upload", "Upload"), command=self._upload,
                              bg="#228b22", fg="white", font=("Arial", 9, "bold"),
                              relief=tk.RAISED, bd=1, padx=10)
        btn_upload.pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)

        btn_export = tk.Button(bframe, text=self.t("btn.export_binary", "Export binary"), command=self._export_binary,
                               bg="#ff8c00", fg="white", font=("Arial", 9, "bold"),
                               relief=tk.RAISED, bd=1, padx=10)
        btn_export.pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        # Painel de Configurações (sem canvas, sem splits)
        config_frame = ttk.LabelFrame(tab, text=self.t("cfg.project_and_settings", "Project and Settings"), padding=8)
        config_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # ===== NOME DO PROJETO =====
        nome_row = ttk.Frame(config_frame)
        nome_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(nome_row, text=self.t("cfg.name", "Name:"), font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)
        self.code_project_name = tk.Label(nome_row, text="...", font=("Courier", 9), fg="#333333", bg="#f5f5f5")
        self.code_project_name.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(nome_row, text="...", width=3, command=self._edit_project_name).pack(side=tk.LEFT, padx=2)
        ttk.Button(nome_row, text=self.t("btn.properties", "Properties"), width=12, command=self._edit_project_properties).pack(side=tk.LEFT, padx=2)
        
        # ===== PLACA =====
        placa_row = ttk.Frame(config_frame)
        placa_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(placa_row, text=self.t("cfg.board", "Board:"), font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)
        self.settings_board_var = tk.StringVar(value="")
        self.settings_board_display = tk.Label(
            placa_row,
            textvariable=self.settings_board_var,
            font=("Courier", 9),
            relief=tk.GROOVE,
            bd=1,
            padx=4,
            pady=2,
            width=30,
            anchor="w"
        )
        self.settings_board_display.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(placa_row, text="...", width=3, command=self._open_boards_dialog).pack(side=tk.LEFT, padx=2)
        
        # ===== PORTA SERIAL =====
        port_row = ttk.Frame(config_frame)
        port_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(port_row, text=self.t("cfg.port_label", "Serial Port:"), font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)
        self.settings_port_var = tk.StringVar(value="auto")
        self.settings_port_display = tk.Label(
            port_row,
            textvariable=self.settings_port_var,
            font=("Courier", 9),
            relief=tk.GROOVE,
            bd=1,
            padx=4,
            pady=2,
            width=30,
            anchor="w"
        )
        self.settings_port_display.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(port_row, text="...", width=3, command=self._open_port_modal).pack(side=tk.LEFT, padx=2)
        
        # ===== BAUDRATE =====
        baud_row = ttk.Frame(config_frame)
        baud_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(baud_row, text=self.t("cfg.baud_label", "Baud rate (bps):"), font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)
        self.settings_baud_var = tk.StringVar(value="115200")
        self.settings_baud_display = tk.Label(
            baud_row,
            textvariable=self.settings_baud_var,
            font=("Courier", 9),
            relief=tk.GROOVE,
            bd=1,
            padx=4,
            pady=2,
            width=30,
            anchor="w"
        )
        self.settings_baud_display.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(baud_row, text="...", width=3, command=self._open_baud_modal).pack(side=tk.LEFT, padx=2)
        
        # Divisor visual
        ttk.Separator(config_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        
        # ===== SEÇÃO DINÂMICA (Variante + Ferramentas) =====
        ttk.Label(config_frame, text=self.t("cfg.dynamic_board_settings", "Board settings (loaded dynamically):"), font=("Arial", 8, "italic")).pack(anchor="w", padx=5, pady=5)

        # Container com scroll para não cortar opções longas de placa
        dynamic_container = ttk.Frame(config_frame)
        dynamic_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))
        self.dynamic_config_canvas = tk.Canvas(dynamic_container, highlightthickness=0, height=180)
        self.dynamic_config_scrollbar = ttk.Scrollbar(dynamic_container, orient="vertical", command=self.dynamic_config_canvas.yview)
        self.dynamic_config_frame = ttk.Frame(self.dynamic_config_canvas)
        self.dynamic_config_window = self.dynamic_config_canvas.create_window((0, 0), window=self.dynamic_config_frame, anchor="nw")
        self.dynamic_config_canvas.configure(yscrollcommand=self.dynamic_config_scrollbar.set)
        self.dynamic_config_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.dynamic_config_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.dynamic_config_frame.bind(
            "<Configure>",
            lambda e: self.dynamic_config_canvas.configure(scrollregion=self.dynamic_config_canvas.bbox("all"))
        )
        self.dynamic_config_canvas.bind(
            "<Configure>",
            lambda e: self.dynamic_config_canvas.itemconfigure(self.dynamic_config_window, width=e.width)
        )
        self.dynamic_config_canvas.bind(
            "<MouseWheel>",
            lambda e: self.dynamic_config_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        )

        # Referências globais para widgets dinâmicos
        self.settings_variant = None
        self.tools_widgets = {}
        self.tools_frame = None

    def _sanitize_project_name(self, raw_name: str) -> str:
        clean = re.sub(r'[^A-Za-z0-9_]+', '_', (raw_name or '').strip())
        clean = re.sub(r'_+', '_', clean)
        clean = clean.strip('_')
        return clean or 'VCLI_project'

    def _resolve_unique_project_path(self, target: Path) -> Path:
        candidate = Path(target)
        suffix = 1
        while candidate.exists():
            candidate = target.parent / f"{target.name}_{suffix}"
            suffix += 1
        return candidate

    def _ensure_project_path_clean(self, folder: Path) -> Path:
        sanitized_name = self._sanitize_project_name(folder.name)
        if sanitized_name == folder.name:
            return folder

        target = self._resolve_unique_project_path(folder.parent / sanitized_name)
        try:
            folder.rename(target)
            self.log(f"[INFO] Nome do projeto ajustado para: {target.name}")
            return target
        except Exception as exc:
            messagebox.showerror(self.t("error.title", "Error"), f"{self.t('error.project_name_adjust', 'Could not sanitize project name')}: {exc}")
            return None

    def _find_option(self, options: list, option_id: str) -> dict:
        for option in options:
            if option.get("id") == option_id:
                return option
        return options[0] if options else {}

    def _open_option_modal(self, title: str, options: list, current_id: str, on_select):
        dialog = tk.Toplevel(self)
        self._apply_window_icon(dialog)
        dialog.title(title)
        dialog.geometry("380x320")
        dialog.resizable(False, False)
        ttk.Label(dialog, text=title, font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 0))

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        listbox = tk.Listbox(frame, activestyle="none")
        listbox.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.config(yscrollcommand=scrollbar.set)

        if options:
            for idx, option in enumerate(options):
                text = option.get("name", option.get("id", ""))
                listbox.insert(tk.END, text)
                if option.get("id") == current_id:
                    listbox.selection_set(idx)
        else:
            listbox.insert(tk.END, "Sem opções disponíveis")
            listbox.config(state="disabled")

        def confirm():
            if not options:
                return
            selection = listbox.curselection()
            if not selection:
                return
            selected_option = options[selection[0]]
            on_select(selected_option)
            dialog.destroy()

        listbox.bind("<Double-1>", lambda _: confirm())

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="OK", command=confirm).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        ttk.Button(btn_frame, text="Cancelar", command=dialog.destroy).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))

    def _open_port_modal(self):
        ports = self._get_serial_ports()
        self.available_ports = ports
        options = [{"id": "auto", "name": "auto"}] + [{"id": p, "name": p} for p in ports]
        self._open_option_modal(
            self.t("cfg.port", "Serial Port"),
            options,
            self.settings_port_var.get() or "auto",
            lambda opt: self._set_port_value(opt),
        )

    def _set_port_value(self, option: dict):
        if not option:
            return
        self.settings_port_var.set(option.get("id", "auto"))
        self._auto_save_config()

    def _open_baud_modal(self):
        options = [{"id": b, "name": b} for b in self.baud_options]
        self._open_option_modal(
            self.t("cfg.baud", "Baud rate"),
            options,
            self.settings_baud_var.get() or "115200",
            lambda opt: self._set_baud_value(opt),
        )

    def _set_baud_value(self, option: dict):
        if not option:
            return
        self.settings_baud_var.set(option.get("id", "115200"))
        self._auto_save_config()

    def _set_variant_value(self, option: dict, display_label: tk.Label):
        if not option:
            return
        self.current_config['variant'] = option.get("id", "")
        display_label.config(text=option.get("name", option.get("id", "")))
        self._save_config()
        self.log(f"Variante atualizada: {display_label.cget('text')}")

    def _set_tool_value(self, tool_id: str, option: dict, display_label: tk.Label):
        if not option:
            return
        tools = self.current_config.setdefault('tools', {})
        tools[tool_id] = option.get("id", "")
        display_label.config(text=option.get("name", option.get("id", "")))
        self._save_config()
        self.log(f"Ferramenta '{tool_id}' ajustada: {display_label.cget('text')}")
    
    def _create_boards_tab(self):
        """Placas - Lista simples"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=self.t("tab.boards", "Boards"))
        
        bframe = ttk.Frame(tab)
        bframe.pack(fill=tk.X, padx=2, pady=2)
        ttk.Button(bframe, text="Atualizar", command=self._load_boards).pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        ttk.Button(bframe, text="Adicionar JSON", command=self._add_board_json).pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        cols = ("FQBN",)
        self.boards_tree = ttk.Treeview(tab, columns=cols, height=15)
        self.boards_tree.column("#0", width=150)
        self.boards_tree.column("FQBN", width=300)
        self.boards_tree.heading("#0", text="Placa")
        self.boards_tree.heading("FQBN", text="FQBN")
        self.boards_tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.boards_tree.bind("<Double-1>", self._select_board)
    
    def _create_libs_tab(self):
        """Bibliotecas"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=self.t("tab.libs", "Libraries"))
        
        bframe = ttk.Frame(tab)
        bframe.pack(fill=tk.X, padx=2, pady=2)
        ttk.Button(bframe, text="Atualizar", command=self._load_libs).pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        ttk.Button(bframe, text="ZIP", command=self._install_lib_zip).pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        ttk.Button(bframe, text="Buscar", command=self._search_lib).pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        cols = ("Versão", "Descrição")
        self.libs_tree = ttk.Treeview(tab, columns=cols, height=15)
        self.libs_tree.column("#0", width=130)
        self.libs_tree.column("Versão", width=70)
        self.libs_tree.column("Descrição", width=350)
        self.libs_tree.heading("#0", text="Biblioteca")
        self.libs_tree.heading("Versão", text="V")
        self.libs_tree.heading("Descrição", text="Descrição")
        self.libs_tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.libs_tree.bind("<Double-1>", self._on_lib_double_click)
    
    def _create_serial_tab(self):
        """Monitor Serial - usa configuracoes da aba Config"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=self.t("tab.serial", "Serial"))

        ctrl_frame = ttk.Frame(tab)
        ctrl_frame.pack(fill=tk.X, padx=2, pady=(2, 1))

        self.serial_toggle_button = ttk.Button(ctrl_frame, text=self.t("serial.connect", "Connect"), command=self._serial_toggle)
        self.serial_toggle_button.pack(side=tk.LEFT, padx=2)
        self.serial_stamp_button = ttk.Button(ctrl_frame, text=self.t("serial.stamp_off", "Stamp time: OFF"), command=self._toggle_serial_stamp)
        self.serial_stamp_button.pack(side=tk.LEFT, padx=2)
        self.serial_clear_button = ttk.Button(ctrl_frame, text=self.t("serial.clear", "Clear"), command=self._serial_clear_log)
        self.serial_clear_button.pack(side=tk.LEFT, padx=2)
        self.serial_export_button = ttk.Button(ctrl_frame, text=self.t("serial.export", "Export"), command=self._serial_export_log)
        self.serial_export_button.pack(side=tk.LEFT, padx=2)
        self.serial_tx_button = ttk.Button(ctrl_frame, text=self.t("serial.tx_off", "Log TX: OFF"), command=self._toggle_tx_log)
        self.serial_tx_button.pack(side=tk.LEFT, padx=2)
        ttk.Label(ctrl_frame, text=self.t("serial.decode", "Decode:")).pack(side=tk.LEFT, padx=(8, 2))
        self.serial_decode_var = tk.StringVar(value="UTF-8")
        self.serial_decode_combo = ttk.Combobox(
            ctrl_frame,
            width=10,
            state="readonly",
            values=["UTF-8", "HEX"],
            textvariable=self.serial_decode_var,
        )
        self.serial_decode_combo.pack(side=tk.LEFT, padx=2)
        self.serial_status_label = ttk.Label(ctrl_frame, text=self.t("serial.status_disconnected", "Status: disconnected"))
        self.serial_status_label.pack(side=tk.LEFT, padx=10)

        self.serial_text = scrolledtext.ScrolledText(tab, bg="black", fg="#00ff00", font=("Courier", 8))
        self.serial_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        inp = ttk.Frame(tab)
        inp.pack(fill=tk.X, padx=2, pady=(0, 1))
        ttk.Label(inp, text=self.t("serial.send", "Send:")).pack(side=tk.LEFT, padx=2)
        self.serial_input = tk.Entry(inp, font=("Arial", 8), bd=1, relief=tk.SOLID)
        self.serial_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(inp, text=">>", command=self._serial_send, width=3).pack(side=tk.LEFT)

    # HISTÓRICO
    def _load_recent_projects(self):
        try:
            if self.recent_projects_file.exists():
                with open(self.recent_projects_file, encoding='utf-8') as f:
                    self.recent_projects = json.load(f)
            else:
                self.recent_projects = []
        except:
            self.recent_projects = []
    
    def _save_recent_projects(self):
        try:
            with open(self.recent_projects_file, 'w', encoding='utf-8') as f:
                json.dump(self.recent_projects, f, ensure_ascii=False)
        except:
            pass
    
    def _populate_recent_projects(self):
        self.recent_listbox.delete(0, tk.END)
        for proj in self.recent_projects[:12]:
            self.recent_listbox.insert(tk.END, Path(proj).name)
    
    def _add_to_recent(self, path):
        path_str = str(path)
        if path_str in self.recent_projects:
            self.recent_projects.remove(path_str)
        self.recent_projects.insert(0, path_str)
        self.recent_projects = self.recent_projects[:20]
        self._save_recent_projects()
        self._populate_recent_projects()
    
    def _open_recent(self, event):
        selection =self.recent_listbox.curselection()
        if not selection:
            return
        path = self.recent_projects[selection[0]]
        if Path(path).exists():
            self._load_project_path(path)
        else:
            self.recent_projects.remove(path)
            self._save_recent_projects()
            self._populate_recent_projects()
            messagebox.showerror(self.t("error.title", "Error"), self.t("error.project_missing", "Project does not exist"))
    
    def _remove_recent(self, event):
        selection = self.recent_listbox.curselection()
        if selection:
            project_path = self.recent_projects[selection[0]]
            name = Path(project_path).name
            if not messagebox.askyesno("Confirmar", f"Remover '{name}' do histórico?"):
                return
            self.recent_projects.pop(selection[0])
            self._save_recent_projects()
            self._populate_recent_projects()
    
    # PROJETOS
    def _create_project(self):
        folder = filedialog.askdirectory(title="[+] Selecione pasta para novo projeto")
        if not folder:
            return

        folder_path = Path(folder)
        folder_path = self._ensure_project_path_clean(folder_path)
        if folder_path is None:
            return
        if self.backend.create_project(str(folder_path), project_name=folder_path.name):
            self._add_to_recent(folder_path)
            self._load_project_path(folder_path)
            self.log(f"[NEW] Projeto criado: {folder_path.name}")
            self.log(f"[FILE] Arquivo: {folder_path.name}.ino (com setup() e loop())")
    
    def _open_project(self):
        folder = filedialog.askdirectory(title="Abrir projeto")
        if folder:
            self._load_project_path(folder)
    
    def _load_project_path(self, path):
        config = self.backend.load_project(str(path))
        if config:
            project_path = Path(path)
            config.setdefault('name', project_path.name)
            self.current_project = project_path
            self.current_config = config
            self._add_to_recent(path)
            self._update_project_info()
    
    def _update_project_info(self):
        if not self.current_config or not self.current_project:
            return
        
        # Atualizar nome do projeto
        project_display_name = self.current_config.get('name', self.current_project.name)
        self.code_project_name.config(text=project_display_name)
        
        # Carregar valores salvos nas configurações
        saved_fqbn = self.current_config.get('fqbn', '')
        saved_port = self.current_config.get('port', 'auto')
        saved_baud = self.current_config.get('baudrate', '115200')
        
        # Atualizar seleção de placa
        if saved_fqbn:
            self.settings_board_var.set(saved_fqbn)
        
        # Atualizar porta e baudrate
        self.settings_port_var.set(saved_port if saved_port else "auto")
        if saved_baud in self.baud_options:
            self.settings_baud_var.set(saved_baud)
        else:
            self.settings_baud_var.set("115200")
        
        # Carregar detalhes da placa se houver FQBN salvo
        if saved_fqbn:
            self.after(200, self._on_board_selected)
        
        self._update_serial_info()
    
    # CÓDIGO
    def _open_vscode(self):
        if not self.current_project:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project", "Select a project"))
            return
        if self.backend.open_code_editor(str(self.current_project)):
            self.log(f"VS Code: {self.current_project.name}")
    
    def _open_project_folder(self):
        """Abre a pasta do projeto no Explorer do Windows"""
        if not self.current_project:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project", "Select a project"))
            return
        import subprocess
        try:
            # Abre o Explorer do Windows com a pasta do projeto
            subprocess.Popen(f'explorer "{self.current_project}"')
            self.log(f"[INFO] Pasta aberta: {self.current_project}")
        except Exception as e:
            messagebox.showerror(self.t("error.title", "Error"), f"Erro ao abrir pasta: {e}")
            self.log(f"[ERROR] {e}")
    
    def _show_busy_modal(self, title: str, subtitle: str, debug_lines: list):
        """Mostra modal bloqueante com barra de progresso, pre-debug e abortar."""
        modal = tk.Toplevel(self)
        modal.title(title)
        self._apply_window_icon(modal)
        modal.geometry("560x280")
        modal.resizable(False, False)
        modal.transient(self)
        modal.grab_set()
        modal.protocol("WM_DELETE_WINDOW", lambda: None)

        ttk.Label(modal, text=title, font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        subtitle_var = tk.StringVar(value=subtitle)
        ttk.Label(modal, textvariable=subtitle_var).pack(anchor="w", padx=12, pady=(0, 8))

        progress = ttk.Progressbar(modal, mode="indeterminate")
        progress.pack(fill=tk.X, padx=12, pady=(0, 10))
        progress.start(12)

        debug_box = scrolledtext.ScrolledText(modal, height=9, font=("Courier", 8), bg="#f7f7f7")
        debug_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        debug_box.insert("1.0", "\n".join(debug_lines))
        debug_box.config(state=tk.DISABLED)

        abort_btn = tk.Button(
            modal,
            text="Abortar",
            bg="#c62828",
            fg="white",
            font=("Arial", 9, "bold"),
            command=lambda: self._request_abort_action(subtitle_var),
        )
        abort_btn.pack(anchor="e", padx=12, pady=(0, 10))

        return {"window": modal, "progress": progress, "subtitle_var": subtitle_var, "abort_btn": abort_btn}

    def _close_busy_modal(self, busy_modal):
        if not busy_modal:
            return
        window = busy_modal.get("window")
        progress = busy_modal.get("progress")
        try:
            if progress:
                progress.stop()
            if window and window.winfo_exists():
                window.grab_release()
                window.destroy()
        except Exception:
            pass

    def _update_busy_modal(self, busy_modal, subtitle: str):
        if not busy_modal:
            return
        subtitle_var = busy_modal.get("subtitle_var")
        if subtitle_var:
            subtitle_var.set(subtitle)

    def _request_abort_action(self, subtitle_var=None):
        aborted = self.backend.abort_current_action() if self.backend else False
        if subtitle_var:
            subtitle_var.set("Abortando acao em andamento...")
        if aborted:
            self.log("[ABORT] Solicitacao de aborto enviada")
        else:
            self.log("[ABORT] Nenhuma acao em execucao para abortar")

    def _log_build_summary(self, output: str):
        if not output:
            return
        summary_lines = []
        for raw in output.splitlines():
            line = raw.strip()
            if not line:
                continue
            lowered = line.lower()
            if (
                "sketch uses" in lowered
                or "global variables use" in lowered
                or "ram:" in lowered
                or "flash:" in lowered
                or "program storage space" in lowered
                or "data memory use" in lowered
            ):
                summary_lines.append(line)
        if summary_lines:
            self.log("[MEMORIA] Resumo de uso:")
            for line in summary_lines:
                self.log(f"  {line}")

    def _extract_compile_metrics(self, output: str):
        flash_pct = 0.0
        ram_pct = 0.0
        warning_lines = []
        flash_line = ""
        ram_line = ""
        for raw in (output or "").splitlines():
            line = raw.strip()
            lowered = line.lower()
            if "warning:" in lowered:
                warning_lines.append(line)
            if "sketch uses" in lowered or "program storage space" in lowered:
                flash_line = line
                match = re.search(r"\(([\d.,]+)%\)", line)
                if match:
                    flash_pct = float(match.group(1).replace(",", "."))
            if "global variables use" in lowered or "dynamic memory" in lowered:
                ram_line = line
                match = re.search(r"\(([\d.,]+)%\)", line)
                if match:
                    ram_pct = float(match.group(1).replace(",", "."))
        return flash_pct, ram_pct, flash_line, ram_line, warning_lines

    def _show_compile_success_modal(self, output: str, title: str = "Compilacao concluida com sucesso"):
        flash_pct, ram_pct, flash_line, ram_line, warning_lines = self._extract_compile_metrics(output)
        win = tk.Toplevel(self)
        win.title(title)
        self._apply_window_icon(win)
        win.geometry("560x360")
        win.resizable(False, False)
        win.transient(self)
        ttk.Label(win, text=title, font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 8))

        frame = ttk.Frame(win)
        frame.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(frame, text=f"Flash: {flash_pct:.1f}%").pack(anchor="w")
        flash_bar = ttk.Progressbar(frame, maximum=100, value=max(0, min(100, flash_pct)))
        flash_bar.pack(fill=tk.X, pady=(2, 8))
        if flash_line:
            ttk.Label(frame, text=flash_line, font=("Courier", 8)).pack(anchor="w")

        ttk.Label(frame, text=f"RAM: {ram_pct:.1f}%").pack(anchor="w", pady=(8, 0))
        ram_bar = ttk.Progressbar(frame, maximum=100, value=max(0, min(100, ram_pct)))
        ram_bar.pack(fill=tk.X, pady=(2, 8))
        if ram_line:
            ttk.Label(frame, text=ram_line, font=("Courier", 8)).pack(anchor="w")

        ttk.Label(win, text="Warnings").pack(anchor="w", padx=12, pady=(10, 2))
        warn_box = scrolledtext.ScrolledText(win, height=7, font=("Courier", 8))
        warn_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        warn_box.insert("1.0", "\n".join(warning_lines) if warning_lines else "Sem warnings")
        warn_box.config(state=tk.DISABLED)

    def _compile_with_modal(self):
        if not self.current_project or not self.current_config:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project", "Select a project"))
            return

        fqbn = self.current_config.get('fqbn', 'arduino:avr:uno')
        self.log(f"[COMPILE] Compilando {self.current_project.name}...")
        self.log(f"[INFO] Placa: {fqbn}")
        self.log(f"[INFO] Caminho: {self.current_project}")
        self.log(f"[CMD] arduino-cli compile --fqbn {fqbn} {self.current_project}")

        busy_modal = self._show_busy_modal(
            "Compilando Projeto",
            "Aguarde, a compilacao esta em andamento...",
            [
                "[PRE-DEBUG]",
                f"Projeto: {self.current_project.name}",
                f"Placa (FQBN): {fqbn}",
                f"Caminho: {self.current_project}",
                f"Comando: arduino-cli compile --fqbn {fqbn} {self.current_project}",
            ],
        )

        def compile_thread():
            output, success, error_msg = self.backend.compile_action(str(self.current_project), fqbn, config=self.current_config)

            def finish_compile():
                self._close_busy_modal(busy_modal)
                if not success:
                    self._show_error_modal("Compilacao", error_msg, output)
                else:
                    self.log(f"[OK] Compilacao concluida com sucesso")
                    self.log(f"[FILE] Binario em: {self.current_project}\\build")
                    self._log_build_summary(output)
                    self._show_compile_success_modal(output)

            self.after(0, finish_compile)

        threading.Thread(target=compile_thread, daemon=True).start()
    
    def _upload(self):
        if not self.current_project or not self.current_config:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project", "Select a project"))
            return
        
        port = self.current_config.get('port')
        if not port or port == 'auto':
            port = simpledialog.askstring("Porta Serial", "Porta COM (Ex: COM3):")
            if not port:
                return
        
        fqbn = self.current_config.get('fqbn', 'arduino:avr:uno')
        baudrate = self.current_config.get('baudrate', '115200')
        self.log(f"[UPLOAD] Upload para {port} @ {baudrate} baud...")
        self.log(f"[INFO] Placa: {fqbn}")
        self.log(f"[CMD] arduino-cli upload -p {port} --fqbn {fqbn} {self.current_project}")

        busy_modal = self._show_busy_modal(
            "Upload de Firmware",
            "Compilando antes do envio...",
            [
                "[PRE-DEBUG]",
                f"Projeto: {self.current_project.name}",
                f"Placa (FQBN): {fqbn}",
                f"Porta: {port}",
                f"Comando 1: arduino-cli compile --fqbn {fqbn} {self.current_project}",
                f"Comando 2: arduino-cli upload -p {port} --fqbn {fqbn} {self.current_project}",
            ],
        )

        def upload_thread():
            compile_output, compile_ok, compile_err = self.backend.compile_action(
                str(self.current_project), fqbn, config=self.current_config
            )
            if not compile_ok:
                self.after(0, lambda: (
                    self._close_busy_modal(busy_modal),
                    self._show_error_modal("Upload (compilacao)", compile_err, compile_output),
                ))
                return

            self.after(0, lambda: self._update_busy_modal(busy_modal, "Compilacao concluida. Enviando para a placa..."))
            upload_output, upload_ok, upload_err = self.backend.upload_action(
                str(self.current_project), fqbn, port, config=self.current_config
            )

            def finish_upload():
                self._close_busy_modal(busy_modal)
                if not upload_ok:
                    self._show_error_modal("Upload", upload_err, upload_output)
                else:
                    self.log(f"[OK] Upload concluido com sucesso")
                    self.log(f"[SUCCESS] Placa reprogramada!")
                    self._log_build_summary(compile_output)
                    self._show_compile_success_modal(compile_output, "Upload concluido com sucesso")

            self.after(0, finish_upload)
        
        threading.Thread(target=upload_thread, daemon=True).start()

    def _export_binary(self):
        if not self.current_project or not self.current_config:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project", "Select a project"))
            return

        fqbn = self.current_config.get('fqbn', 'arduino:avr:uno')
        self.log(f"[EXPORT] Exportando binario de {self.current_project.name}...")
        self.log(f"[CMD] arduino-cli compile --fqbn {fqbn} --export-binaries {self.current_project}")

        busy_modal = self._show_busy_modal(
            "Exportar Binario",
            "Gerando binarios da compilacao...",
            [
                "[PRE-DEBUG]",
                f"Projeto: {self.current_project.name}",
                f"Placa (FQBN): {fqbn}",
                f"Comando: arduino-cli compile --fqbn {fqbn} --export-binaries {self.current_project}",
            ],
        )

        def export_thread():
            output, success, error_msg = self.backend.export_binary_action(
                str(self.current_project), fqbn, config=self.current_config
            )

            def finish_export():
                self._close_busy_modal(busy_modal)
                if not success:
                    self._show_error_modal("Exportar binario", error_msg, output)
                else:
                    self.log(f"[SUCCESS] Binario exportado em: {self.current_project}\\build")
                    self._log_build_summary(output)
                    self._show_compile_success_modal(output, "Exportacao concluida com sucesso")

            self.after(0, finish_export)

        threading.Thread(target=export_thread, daemon=True).start()
    
    def _show_error_modal(self, title, error_msg, output):
        """Mostra modal de erro para compilação/upload"""
        error_window = tk.Toplevel(self)
        self._apply_window_icon(error_window)
        error_window.title(f"Erro em {title}")
        error_window.geometry("600x400")
        error_window.resizable(True, True)
        
        ttk.Label(error_window, text=f"Erro durante {title.lower()}:", font=("Arial", 10, "bold")).pack(padx=5, pady=5)
        
        text = scrolledtext.ScrolledText(error_window, height=15, bg="#fff3cd", fg="#856404", font=("Courier", 8))
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.insert(1.0, error_msg)
        text.config(state=tk.DISABLED)
        
        if output:
            ttk.Label(error_window, text="Output Completo:", font=("Arial", 9, "bold")).pack(padx=5, pady=(10, 5))
            out_text = scrolledtext.ScrolledText(error_window, height=8, bg="white", fg="black", font=("Courier", 7))
            out_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            out_text.insert(1.0, output)
            out_text.config(state=tk.DISABLED)
        
        ttk.Button(error_window, text="Fechar", command=error_window.destroy).pack(pady=5)
        self.log(f" Erro: {error_msg}")
    
    # PLACAS
    def _open_boards_dialog(self):
        """Abre dialog para selecionar placa"""
        if not self.current_project:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project_first", "Select a project first"))
            return
        
        # Carregar placas se não tiver em cache
        if not self.boards_cache:
            messagebox.showinfo(self.t("info.title", "Info"), self.t("info.loading_boards", "Loading boards..."))
            def load_boards():
                boards = self.backend.list_boards()
                self.boards_cache = boards
                self.boards_cache_time = time.time()
                self.after(0, self._open_boards_dialog)
            threading.Thread(target=load_boards, daemon=True).start()
            return
        
        # Criar dialog
        dialog = tk.Toplevel(self)
        dialog.title("Selecionar Placa")
        self._apply_window_icon(dialog)
        dialog.geometry("620x420")
        dialog.resizable(False, False)

        search_frame = ttk.Frame(dialog)
        search_frame.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(search_frame, text="Pesquisar placa:", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        search_entry.focus()

        cols = ("FQBN",)
        tree = ttk.Treeview(dialog, columns=cols, height=15)
        tree.column("#0", width=220)
        tree.column("FQBN", width=360)
        tree.heading("#0", text="Placa")
        tree.heading("FQBN", text="FQBN")
        tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        def _refresh_board_list(filter_text=""):
            tree.delete(*tree.get_children())
            term = (filter_text or "").strip().lower()
            for plc in self.boards_cache:
                name = plc.get("name", "?")
                fqbn = plc.get("fqbn", "")
                if term and term not in name.lower() and term not in fqbn.lower():
                    continue
                tree.insert("", tk.END, text=name, values=(fqbn,))

        search_var.trace_add("write", lambda *_: _refresh_board_list(search_var.get()))
        _refresh_board_list()
        
        # Botões
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        def on_ok():
            sel = tree.selection()
            if sel:
                fqbn = tree.item(sel[0], "values")[0]
                self.settings_board_var.set(fqbn)
                self._on_board_selected()
                dialog.destroy()
            else:
                messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_board", "Select a board"))
        
        def on_cancel():
            dialog.destroy()
        
        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(btn_frame, text="Cancelar", command=on_cancel).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
    
    def _load_boards(self):
        # Usar cache em threading para não travar
        def load_thread():
            boards = self.backend.list_boards()
            self.boards_cache = boards
            self.boards_cache_time = time.time()
            
            self.after(0, lambda: self._update_boards_tree(boards))
        
        import threading
        threading.Thread(target=load_thread, daemon=True).start()
    
    def _update_boards_tree(self, boards):
        """Atualiza tree de placas na UI"""
        self.boards_tree.delete(*self.boards_tree.get_children())
        for b in boards:
            self.boards_tree.insert("", tk.END, text=b.get("name", "?"), values=(b.get("fqbn", ""),))
        self.log("Placas carregadas")
    
    def _select_board(self, event):
        sel = self.boards_tree.selection()
        if not sel or not self.current_project:
            return
        fqbn = self.boards_tree.item(sel[0], "values")[0]
        self.current_config['fqbn'] = fqbn
        self._save_config()
        self._update_project_info()
        self.log(f"Placa: {fqbn}")
    
    def _add_board_json(self):
        url = simpledialog.askstring("JSON", "URL:")
        if not url:
            return

        busy_modal = self._show_busy_modal(
            "Atualizar placas",
            "Preparando download...",
            [
                "[PRE-DEBUG]",
                f"URL: {url}",
            ],
        )

        def update_status(text):
            self.after(0, lambda: self._update_busy_modal(busy_modal, text))

        def download_thread():
            output, success, err = self.backend.add_board_json_sync(url, progress_callback=update_status)
            self.after(0, lambda: self._close_busy_modal(busy_modal))
            if not success:
                self._show_error_modal("Atualizar placas", err, output)
                return
            messagebox.showinfo(self.t("boards.title", "Boards"), self.t("boards.updated_success", "Boards updated successfully"))
            self._load_boards()

        threading.Thread(target=download_thread, daemon=True).start()
    
    def _update_boards_combo_cached(self):
        """Mantido por compatibilidade (seleção agora é via modal)."""
        if self.boards_cache is None:
            def load_boards():
                boards = self.backend.list_boards()
                self.boards_cache = boards
                self.boards_cache_time = time.time()
            threading.Thread(target=load_boards, daemon=True).start()
    
    
    def _on_board_selected(self):
        """Quando placa é selecionada, carregar variantes e tools dinamicamente"""
        fqbn = (self.settings_board_var.get() or "").strip()
        if not fqbn or not self.current_config:
            return
        
        # Se a placa mudou, RESETAR as configurações associadas
        old_fqbn = self.current_config.get('fqbn', '')
        if old_fqbn and old_fqbn != fqbn:
            # Resetar configurações que dependem da placa
            self.current_config['variant'] = ''
            # Remover as ferramentas customizadas anteriores
            if 'tools' in self.current_config:
                del self.current_config['tools']
            self.log(f"⚠ Configurações resetadas (placa mudou)")
        
        # Atualizar configuração com a placa selecionada
        self.current_config['fqbn'] = fqbn
        self._save_config()
        
        self.log(f"Placa selecionada: {fqbn}")
        self.log(f"Carregando configurações...")
        
        # Carregar variantes e tools em thread
        def load_thread():
            self.log("→ Carregando variantes...")
            variants = self.backend.get_board_variants(fqbn)

            self.log("→ Carregando ferramentas...")
            tools = self.backend.get_platform_tools(fqbn)

            self.after(0, lambda: self._update_board_details(variants, tools))
        
        threading.Thread(target=load_thread, daemon=True).start()
    
    def _edit_project_name(self):
        """Abre dialog para editar o nome do projeto"""
        if not self.current_project:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project_first", "Select a project first"))
            return
        
        new_name = simpledialog.askstring("Editar Nome", f"Nome do projeto:", initialvalue=self.current_project.name)
        if not new_name:
            return

        sanitized = self._sanitize_project_name(new_name)
        if sanitized == self.current_project.name:
            return

        old_ino = self.current_project / f"{self.current_project.name}.ino"
        new_ino = self.current_project / f"{sanitized}.ino"
        try:
            if old_ino.exists():
                old_ino.rename(new_ino)
        except Exception as exc:
            self.log(f"Erro ao renomear .ino: {exc}")

        target_dir = self._resolve_unique_project_path(self.current_project.parent / sanitized)
        try:
            self.current_project.rename(target_dir)
        except Exception as exc:
            messagebox.showerror(self.t("error.title", "Error"), f"{self.t('error.rename_project', 'Could not rename project')}: {exc}")
            return

        self.current_project = target_dir
        self.current_config['name'] = sanitized
        self._save_config()
        self._add_to_recent(target_dir)
        self.code_project_name.config(text=sanitized)
        self.log(f"Projeto renomeado para: {sanitized}")
    
    def _edit_project_properties(self):
        """Abre modal para editar propriedades do projeto (autor, versao, colaboradores, descricao)"""
        if not self.current_project or not self.current_config:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project_first", "Select a project first"))
            return
        
        # Criar modal
        props_window = tk.Toplevel(self)
        self._apply_window_icon(props_window)
        props_window.title(self.t("props.title", "Project Properties"))
        props_window.geometry("500x400")
        props_window.resizable(False, False)
        props_window.transient(self)
        props_window.grab_set()
        
        # Frame com os campos
        frame = ttk.Frame(props_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # AUTOR
        ttk.Label(frame, text=self.t("props.author", "Author:"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 2))
        author_var = tk.StringVar(value=self.current_config.get("properties", {}).get("author", ""))
        author_entry = ttk.Entry(frame, textvariable=author_var, font=("Arial", 10))
        author_entry.pack(fill=tk.X, pady=(0, 10))
        
        # VERSÃO
        ttk.Label(frame, text=self.t("props.version", "Version:"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 2))
        version_var = tk.StringVar(value=self.current_config.get("properties", {}).get("version", "1.0.0"))
        version_entry = ttk.Entry(frame, textvariable=version_var, font=("Arial", 10))
        version_entry.pack(fill=tk.X, pady=(0, 10))
        
        # COLABORADORES
        ttk.Label(frame, text=self.t("props.contributors", "Contributors:"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 2))
        contributors_var = tk.StringVar(value=self.current_config.get("properties", {}).get("contributors", ""))
        contributors_entry = ttk.Entry(frame, textvariable=contributors_var, font=("Arial", 10))
        contributors_entry.pack(fill=tk.X, pady=(0, 10))
        
        # DESCRIÇÃO (multi-line)
        ttk.Label(frame, text=self.t("props.description", "Description:"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 2))
        desc_text = tk.Text(frame, height=6, font=("Arial", 10), wrap=tk.WORD)
        desc_text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        desc_text.insert(tk.END, self.current_config.get("properties", {}).get("description", ""))
        desc_text.config(state="disabled")
        
        # Botões
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        fields = [author_entry, version_entry, contributors_entry]
        edit_state = {"value": False}
        
        def set_editable(enabled: bool):
            state = "normal" if enabled else "disabled"
            for widget in fields:
                widget.config(state=state)
            desc_text.config(state=state)
            edit_state["value"] = enabled
            toggle_btn.config(text=self.t("props.view", "View") if enabled else self.t("props.edit", "Edit"))
        
        def toggle_edit_mode():
            set_editable(not edit_state["value"])
        
        def apply_changes():
            # Salvar as propriedades na configuração
            if "properties" not in self.current_config:
                self.current_config["properties"] = {}
            
            prev_desc_state = desc_text.cget("state")
            desc_text.config(state="normal")
            description_value = desc_text.get("1.0", tk.END).strip()
            desc_text.config(state=prev_desc_state)
            
            self.current_config["properties"]["author"] = author_var.get()
            self.current_config["properties"]["version"] = version_var.get()
            self.current_config["properties"]["contributors"] = contributors_var.get()
            self.current_config["properties"]["description"] = description_value
            
            self._save_config()
            self.log(f"[INFO] Propriedades do projeto atualizadas")
            props_window.destroy()
            messagebox.showinfo(self.t("info.title", "Info"), self.t("props.saved", "Properties saved successfully"))
        
        toggle_btn = ttk.Button(btn_frame, text=self.t("props.edit", "Edit"), command=toggle_edit_mode)
        toggle_btn.pack(side=tk.LEFT, expand=True, padx=5)
        ttk.Button(btn_frame, text=self.t("props.ok", "OK"), command=apply_changes).pack(side=tk.LEFT, expand=True, padx=5)
        ttk.Button(btn_frame, text=self.t("props.cancel", "Cancel"), command=props_window.destroy).pack(side=tk.LEFT, expand=True, padx=5)
        
        set_editable(False)
    
    def _update_board_details(self, variants, tools):
        """Atualiza widgets dinâmicos de variantes e ferramentas"""
        # Limpar widgets dinâmicos anteriores
        for widget in self.dynamic_config_frame.winfo_children():
            widget.destroy()
        
        self.settings_variant = None
        self.tools_widgets = {}

        # ===== VARIANTE =====
        if variants:
            self.variant_options = variants
            var_row = ttk.Frame(self.dynamic_config_frame)
            var_row.pack(fill=tk.X, pady=5)

            ttk.Label(var_row, text="Variante:", font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)
            selected_variant = self._find_option(variants, self.current_config.get('variant', ''))
            if not selected_variant and variants:
                selected_variant = variants[0]
                self.current_config['variant'] = selected_variant.get('id', '')
            variant_display = tk.Label(
                var_row,
                text=(selected_variant.get("name") or selected_variant.get("id", "Nenhuma")),
                font=("Courier", 9),
                relief=tk.GROOVE,
                bd=1,
                padx=4,
                pady=2,
                width=30,
                anchor="w"
            )
            variant_display.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            ttk.Button(var_row, text="...", width=3, command=lambda: self._open_option_modal(
                "Variante",
                self.variant_options,
                self.current_config.get('variant', ''),
                lambda opt: self._set_variant_value(opt, variant_display)
            )).pack(side=tk.LEFT, padx=5)

        # ===== FERRAMENTAS CUSTOMIZADAS =====
        if tools:
            self._create_tools_widgets(tools)
        
        self.log(f"✓ {len(variants)} variantes, {len(tools)} ferramentas carregadas")
    
    def _create_tools_widgets(self, tools):
        """Cria widgets das ferramentas customizadas no novo layout"""
        self.tool_display_labels = {}
        
        if not tools:
            return
        
        saved_tools = self.current_config.setdefault('tools', {})

        for tool in tools:
            tool_name = tool.get("name", tool.get("id", ""))
            tool_id = tool.get("id", "")
            if not tool_id:
                continue
            values = tool.get("values", [])
            default_value_id = saved_tools.get(tool_id) or tool.get("selected", "")

            # Row para cada ferramenta
            row = ttk.Frame(self.dynamic_config_frame)
            row.pack(fill=tk.X, pady=5)
            
            ttk.Label(row, text=f"{tool_name}:", font=("Arial", 9, "bold"), width=15).pack(side=tk.LEFT, padx=5)

            selected_option = self._find_option(values, default_value_id)
            if selected_option:
                saved_tools[tool_id] = selected_option.get("id", "")
            display_text = (selected_option.get("name") or selected_option.get("id")) if selected_option else "Automático"
            value_display = tk.Label(
                row,
                text=display_text,
                font=("Courier", 9),
                relief=tk.GROOVE,
                bd=1,
                padx=4,
                pady=2,
                width=30,
                anchor="w"
            )
            value_display.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            ttk.Button(row, text="...", width=3, command=lambda t=tool, tid=tool_id, tname=tool_name, label=value_display: self._open_option_modal(
                tname,
                t.get("values", []),
                saved_tools.get(tid, ""),
                lambda opt, tool_key=tid, lbl=label: self._set_tool_value(tool_key, opt, lbl)
            )).pack(side=tk.LEFT, padx=5)

        self._save_config()
    
    # BIBLIOTECAS
    def _load_libs(self):
        self.libs_tree.delete(*self.libs_tree.get_children())
        libs = self.backend.list_libraries_fixed()
        if not libs:
            libs = self.backend.list_libraries()
        self.loaded_libraries = libs
        for lib in libs:
            self.libs_tree.insert("", tk.END, text=lib.get("name", "?"), 
                                 values=(lib.get("version", ""), lib.get("sentence", "")[:40]))
        self.log("Bibliotecas carregadas")

    def _on_lib_double_click(self, event):
        sel = self.libs_tree.selection()
        if not sel:
            return
        item = self.libs_tree.item(sel[0])
        lib_name = item.get("text", "")
        version = item.get("values", [""])[0] if item.get("values") else ""
        library = None
        for lib in getattr(self, "loaded_libraries", []):
            if lib.get("name", "") == lib_name and lib.get("version", "") == version:
                library = lib
                break
        if library is None:
            library = {"name": lib_name, "version": version, "sentence": ""}
        self._show_library_modal(library)

    def _show_library_modal(self, library: dict):
        dialog = tk.Toplevel(self)
        dialog.title(f"Biblioteca: {library.get('name', '')}")
        self._apply_window_icon(dialog)
        dialog.geometry("520x280")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text=library.get("name", ""), font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(dialog, text=f"Versao: {library.get('version', 'N/A')}").pack(anchor="w", padx=12, pady=(0, 6))
        desc = library.get("sentence", "") or "Sem descricao"
        desc_box = scrolledtext.ScrolledText(dialog, height=7, font=("Courier", 8))
        desc_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        desc_box.insert("1.0", desc)
        desc_box.config(state=tk.DISABLED)

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))

        def open_in_vscode():
            name = library.get("name", "")
            lib_path = self.backend.find_library_path(name) if self.backend else None
            if lib_path and self.backend.open_code_editor(str(lib_path)):
                self.log(f"[LIB] Biblioteca aberta no VS Code: {name}")
                return
            messagebox.showwarning(self.t("libs.title", "Library"), f"{self.t('libs.path_not_found', 'Could not locate library path')}: '{name}'.")

        def remove_library():
            name = library.get("name", "")
            if not name:
                return
            if not messagebox.askyesno("Confirmar", f"Remover biblioteca '{name}'?"):
                return
            self.log(f"[LIB] Removendo biblioteca: {name}")
            output, ok, err = self.backend.uninstall_library(name)
            if not ok:
                self._show_error_modal("Biblioteca", err, output)
                return
            self.log(f"[LIB] Biblioteca removida: {name}")
            self._load_libs()
            dialog.destroy()

        ttk.Button(btn_row, text="Excluir", command=remove_library).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(btn_row, text="Abrir no VS Code", command=open_in_vscode).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(btn_row, text="Fechar", command=dialog.destroy).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
    
    def _install_lib_zip(self):
        path = filedialog.askopenfilename(filetypes=[("ZIP", "*.zip")])
        if path:
            self.backend.install_library_zip(path)
    
    def _search_lib(self):
        name = simpledialog.askstring("Buscar", "Nome:")
        if name:
            self.backend.install_library(name)
    
    # CONFIG
    def _save_config(self):
        if not self.current_project:
            return
        try:
            fuse_file = self.current_project / "project.fuzil"
            # Garantir UTF-8 explícito
            with open(fuse_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_config, f, indent=4, ensure_ascii=False)
            self.log(f"✓ Configuração salva")
        except UnicodeEncodeError as e:
            self.log(f"Erro de encoding ao salvar config: {e}")
            messagebox.showerror(self.t("error.title", "Error"), f"{self.t('error.save_config', 'Error saving configuration')}:\n{e}")
        except Exception as e:
            self.log(f"Erro: {e}")
            messagebox.showerror(self.t("error.title", "Error"), f"{self.t('error.save_config', 'Error saving configuration')}:\n{e}")
    
    def _auto_save_config(self):
        """Auto-salva configuração ao mudar valores na aba de Placas"""
        if not self.current_config or not self.current_project:
            return
        
        self.current_config['name'] = self.current_project.name

        self.current_config['fqbn'] = self.settings_board_var.get() or self.current_config.get('fqbn')
        self.current_config['variant'] = self.current_config.get('variant', '')
        self.current_config['port'] = self.settings_port_var.get() or "auto"
        self.current_config['baudrate'] = self.settings_baud_var.get() or "115200"
        self.current_config.setdefault('tools', {})
        
        # Salvar e atualizar UI
        self._save_config()
        self._update_project_info()
        self._update_serial_info()
    
    def _update_ports_combo(self):
        ports = self._get_serial_ports()
        self.available_ports = ports
    
    # SERIAL
    def _update_serial_info(self):
        """Atualiza status da serial no topo."""
        if self.current_config:
            self._refresh_serial_status(bool(self.serial_connection))

    def _refresh_serial_status(self, connected: bool, port: str = "", baud: str = ""):
        if connected:
            self.serial_toggle_button.config(text=self.t("serial.disconnect", "Disconnect"))
            self.serial_status_label.config(text=f"{self.t('serial.status_connected', 'Status: connected')} {port or 'auto'} @ {baud or '115200'}")
            return
        saved_port = self.current_config.get('port', 'auto') if self.current_config else 'auto'
        saved_baud = self.current_config.get('baudrate', '115200') if self.current_config else '115200'
        self.serial_toggle_button.config(text=self.t("serial.connect", "Connect"))
        self.serial_status_label.config(text=f"{self.t('serial.status', 'Status:')} {saved_port or 'auto'} @ {saved_baud}")

    def _toggle_serial_stamp(self):
        self.serial_stamp_enabled = not self.serial_stamp_enabled
        state = "ON" if self.serial_stamp_enabled else "OFF"
        self.serial_stamp_button.config(text=f"{self.t('serial.stamp_prefix', 'Stamp time')}: {state}")

    def _toggle_tx_log(self):
        self.serial_tx_enabled = not self.serial_tx_enabled
        state = "ON" if self.serial_tx_enabled else "OFF"
        self.serial_tx_button.config(text=f"{self.t('serial.tx_prefix', 'Log TX')}: {state}")

    def _serial_clear_log(self):
        self.serial_text.delete("1.0", tk.END)
        self.serial_tx_log.clear()

    def _serial_export_log(self):
        path = filedialog.asksaveasfilename(
            title=self.t("serial.export_title", "Export serial log"),
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            log_text = self.serial_text.get("1.0", tk.END)
            with open(path, "w", encoding="utf-8") as f:
                f.write(log_text)
            self.log(f"[SERIAL] {self.t('serial.export_ok', 'Log exported')}: {path}")
        except Exception as e:
            messagebox.showerror(self.t("error.title", "Error"), str(e))

    def _serial_toggle(self):
        if self.serial_connection:
            self._serial_disconnect()
        else:
            self._serial_connect()
    
    def _get_serial_ports(self):
        try:
            import serial.tools.list_ports
            return [p.device for p in serial.tools.list_ports.comports()]
        except:
            return ["COM3", "COM4"]
    
    def _serial_refresh_ports(self):
        """Atualiza apenas a lista de portas disponiveis sem conectar"""
        ports = self._get_serial_ports()
        self.log(f"Portas: {', '.join(ports) if ports else 'nenhuma'}")
    
    def _serial_connect(self):
        if self.serial_connection:
            messagebox.showinfo(self.t("info.title", "Info"), self.t("serial.already_connected", "Already connected"))
            return
        
        if not self.current_config:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("warn.select_project_first", "Select a project first"))
            return
        
        port = self.current_config.get('port', '')
        baud_str = self.current_config.get('baudrate', '115200')
        
        if not port or port == 'auto':
            ports = self._get_serial_ports()
            if ports:
                port = ports[0]
            else:
                messagebox.showerror(self.t("error.title", "Error"), self.t("serial.no_port", "No serial port available"))
                return
        
        try:
            baud = int(baud_str)
        except:
            baud = 115200
            
        try:
            import serial
            self.serial_connection = serial.Serial(port, baud, timeout=0.5)
            self.log(f"Serial: {port} @ {baud}")
            self._serial_monitor()
            self._refresh_serial_status(True, port, str(baud))
        except Exception as e:
            messagebox.showerror(self.t("error.title", "Error"), str(e))
    
    def _serial_disconnect(self):
        if self.serial_connection:
            self.serial_connection.close()
            self.serial_connection = None
            self.log("Serial desconectado")
            self._refresh_serial_status(False)
    
    def _serial_send(self):
        if not self.serial_connection:
            messagebox.showwarning(self.t("warn.title", "Warning"), self.t("serial.not_connected", "Not connected"))
            return
        data = self.serial_input.get()
        if data:
            try:
                self.serial_connection.write((data + "\n").encode())
                if self.serial_tx_enabled:
                    tx_line = f"TX {datetime.now().strftime('%H:%M:%S')} -> {data}"
                    self.serial_tx_log.append(tx_line)
                    self.serial_text.insert(tk.END, tx_line + "\n")
                    self.serial_text.see(tk.END)
                self.serial_input.delete(0, tk.END)
            except Exception as e:
                messagebox.showerror(self.t("error.title", "Error"), str(e))
    
    def _serial_monitor(self):
        def monitor():
            while self.serial_connection:
                try:
                    if self.serial_connection.in_waiting:
                        raw = self.serial_connection.readline()
                        if raw:
                            mode = self.serial_decode_var.get() if hasattr(self, "serial_decode_var") else "UTF-8"
                            if mode == "HEX":
                                payload = raw.hex(" ").upper().strip()
                            else:
                                payload = raw.decode(errors='ignore').rstrip("\r\n")
                            if self.serial_stamp_enabled:
                                payload = f"{datetime.now().strftime('%H:%M:%S')} -> {payload}"
                            self.serial_text.insert(tk.END, payload + "\n")
                            self.serial_text.see(tk.END)
                except:
                    break
        
        threading.Thread(target=monitor, daemon=True).start()
    
    # UTILS
    def _load_initial_data(self):
        self._load_boards()
        self._load_libs()
        self._serial_refresh_ports()
    
    def log(self, text: str):
        if self.console is None:
            return
        try:
            # Determina a cor baseado no conteúdo
            text_lower = text.lower()
            
            if any(word in text_lower for word in ["erro", "error", "fail", "falhou", ""]):
                tag = "error"
            elif any(word in text_lower for word in ["warn", "aviso", "atenção"]):
                tag = "warning"
            elif any(word in text_lower for word in ["sucesso", "ok", "concluído", "✓"]):
                tag = "success"
            elif any(word in text_lower for word in ["info", "carregadas", "serial:", "comando", "[ok]"]):
                tag = "info"
            else:
                tag = None
            
            # Insere o texto com a tag apropriada
            if tag:
                self.console.insert(tk.END, text + "\n", tag)
            else:
                self.console.insert(tk.END, text + "\n")
            
            self.console.see(tk.END)
        except:
            pass


if __name__ == "__main__":
    app = VCliApp()
    app.mainloop()

