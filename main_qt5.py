import base64
import csv
import ctypes
import html
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import zlib
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets
try:
    import PyQt5.QtWebEngineWidgets as QtWebEngineWidgets
except ImportError:
    QtWebEngineWidgets = None
    print("PyQtWebEngine não está instalado")
try:
    import winreg
except Exception:
    winreg = None

try:
    from lupa import LuaRuntime
except Exception:
    LuaRuntime = None

try:
    import yaml
except Exception:
    yaml = None

from cli_backend import CLIBackend
from code_editor_tools import CodeEditorDialog
from serial_csv_tools import (
    CsvLogBrowserDialog,
    CsvLogViewerDialog,
    RecordingSaveDialog,
    SerialPlotWidget,
    extract_numeric_series,
    is_error_line,
    parse_csv_line,
)


class UiBridge(QtCore.QObject):
    invoke = QtCore.pyqtSignal(object)
    log_message = QtCore.pyqtSignal(str)
    serial_data = QtCore.pyqtSignal(object)


class LibraryManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.app = parent
        self.backend = parent.backend
        self.setWindowTitle(self.app.t("mgr.lib.title", "Library Manager"))
        self.app.fit_dialog_to_screen(self, 1040, 620)
        self.items = []
        self.runtime_busy = False
        self._build_ui()
        self.refresh_data()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Pesquise no catalogo completo, filtre instaladas e enxergue rapidamente o que esta defasado."
        )
        intro.setWordWrap(True)
        self.app._mark_muted_label(intro)
        layout.addWidget(intro)

        top = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText(self.app.t("mgr.search", "Search:"))
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItem(self.app.t("mgr.filter.all", "All"), "all")
        self.filter_combo.addItem(self.app.t("mgr.filter.installed", "Installed"), "installed")
        self.filter_combo.addItem(self.app.t("mgr.filter.updates", "Pending updates"), "updates")
        self.reload_btn = QtWidgets.QPushButton(self.app.t("mgr.reload", "Reload"))
        self.install_zip_btn = QtWidgets.QPushButton("ZIP")
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.filter_combo)
        top.addWidget(self.reload_btn)
        top.addWidget(self.install_zip_btn)
        layout.addLayout(top)
        self.summary_label = QtWidgets.QLabel("Resumo: carregando catalogo...")
        self.summary_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.summary_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, 1)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Biblioteca", "Instalada", "Última", "Categoria", "Match"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        splitter.addWidget(self.table)

        detail_host = QtWidgets.QWidget()
        detail_layout = QtWidgets.QVBoxLayout(detail_host)
        self.detail_title = QtWidgets.QLabel("Selecione uma biblioteca")
        self.detail_title.setObjectName("managerTitle")
        self.detail_badge = QtWidgets.QLabel("Status: aguardando selecao")
        self.detail_badge.setWordWrap(True)
        self.detail_installed = QtWidgets.QLabel("Instalada: -")
        self.detail_latest = QtWidgets.QLabel("Última: -")
        self.detail_author = QtWidgets.QLabel("Autor: -")
        self.detail_category = QtWidgets.QLabel("Categoria: -")
        self.detail_desc = QtWidgets.QTextEdit()
        self.detail_desc.setReadOnly(True)
        self.detail_desc.setMinimumHeight(180)
        self.detail_url = QtWidgets.QLabel()
        self.detail_url.setOpenExternalLinks(True)

        version_row = QtWidgets.QHBoxLayout()
        self.version_combo = QtWidgets.QComboBox()
        self.action_btn = QtWidgets.QPushButton(self.app.t("mgr.install", "Install"))
        self.uninstall_btn = QtWidgets.QPushButton(self.app.t("mgr.uninstall", "Uninstall"))
        version_row.addWidget(QtWidgets.QLabel(self.app.t("mgr.version", "Version:")))
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.action_btn)
        version_row.addWidget(self.uninstall_btn)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.hide()
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(180)

        for widget in [self.detail_title, self.detail_badge, self.detail_installed, self.detail_latest, self.detail_author, self.detail_category]:
            detail_layout.addWidget(widget)
        detail_layout.addWidget(self.detail_url)
        detail_layout.addWidget(self.detail_desc, 1)
        detail_layout.addLayout(version_row)
        detail_layout.addWidget(self.progress)
        detail_layout.addWidget(self.log_box)
        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.search_edit.textChanged.connect(self.refresh_data)
        self.filter_combo.currentIndexChanged.connect(self.refresh_data)
        self.reload_btn.clicked.connect(self.refresh_data)
        self.install_zip_btn.clicked.connect(self.install_zip)
        self.table.itemSelectionChanged.connect(self.update_detail)
        self.version_combo.currentIndexChanged.connect(self.refresh_action_state)
        self.action_btn.clicked.connect(self.run_action)
        self.uninstall_btn.clicked.connect(self.run_uninstall)

    def append_log(self, text: str):
        self.log_box.appendPlainText(text)

    def set_busy(self, flag: bool):
        self.runtime_busy = flag
        self.progress.setVisible(flag)
        self.reload_btn.setEnabled(not flag)
        self.install_zip_btn.setEnabled(not flag)
        self.action_btn.setEnabled(not flag and self.table.currentRow() >= 0)
        self.uninstall_btn.setEnabled(not flag and self.table.currentRow() >= 0)

    def current_filter(self) -> str:
        return self.filter_combo.currentData() or "all"

    def selected_item(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items):
            return None
        return self.items[row]

    def refresh_data(self):
        if self.runtime_busy:
            return
        term = self.search_edit.text().strip()
        mode = self.current_filter()
        self.set_busy(True)
        self.append_log(self.app.t("mgr.loading", "Loading data..."))

        def worker():
            results = self.backend.search_libraries_advanced(
                term=term,
                limit=0 if term else 120,
                installed_only=mode == "installed",
                updates_only=mode == "updates",
            )

            def done():
                self.items = results
                self.populate_table()
                self.set_busy(False)
                self.append_log(self.app.t("mgr.loaded", "Data loaded."))

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def populate_table(self):
        self.table.setRowCount(len(self.items))
        update_count = 0
        for row, item in enumerate(self.items):
            has_update = bool(item.get("has_update"))
            if has_update:
                update_count += 1
            values = [
                item.get("name", ""),
                item.get("installed_version", "") or "-",
                item.get("latest_version", "") or "-",
                item.get("category", "") or "-",
                "Atualizacao pendente" if has_update else ("Instalada" if item.get("installed_version") else "Catalogo"),
            ]
            for col, value in enumerate(values):
                widget_item = QtWidgets.QTableWidgetItem(value)
                if has_update:
                    widget_item.setBackground(QtGui.QColor("#fff3cd"))
                    widget_item.setForeground(QtGui.QColor("#7a4f01"))
                    font = widget_item.font()
                    font.setBold(True)
                    widget_item.setFont(font)
                    widget_item.setToolTip(
                        f"Biblioteca com update pendente: {item.get('installed_version') or '-'} -> {item.get('latest_version') or '-'}"
                    )
                elif item.get("installed_version"):
                    widget_item.setToolTip("Biblioteca instalada e alinhada com o catalogo atual.")
                else:
                    widget_item.setToolTip("Biblioteca disponivel no catalogo, ainda nao instalada.")
                self.table.setItem(row, col, widget_item)
        installed_count = sum(1 for item in self.items if item.get("installed_version"))
        self.summary_label.setText(
            f"Resumo: {len(self.items)} bibliotecas visiveis  •  {installed_count} instaladas  •  {update_count} com update pendente"
        )
        if self.items:
            self.table.selectRow(0)
        else:
            self.update_detail()

    def update_detail(self):
        item = self.selected_item()
        if not item:
            self.detail_title.setText("Selecione uma biblioteca")
            self.detail_badge.setText("Status: aguardando selecao")
            self.detail_installed.setText("Instalada: -")
            self.detail_latest.setText("Última: -")
            self.detail_author.setText("Autor: -")
            self.detail_category.setText("Categoria: -")
            self.detail_desc.setPlainText("")
            self.detail_url.setText("")
            self.version_combo.clear()
            self.refresh_action_state()
            return

        self.detail_title.setText(item.get("name", ""))
        self.detail_badge.setStyleSheet("")
        self.detail_installed.setText(f"Instalada: {item.get('installed_version') or '-'}")
        self.detail_latest.setText(f"Última: {item.get('latest_version') or '-'}")
        author = item.get("author") or item.get("maintainer") or "-"
        self.detail_author.setText(f"Autor: {author}")
        self.detail_category.setText(f"Categoria: {item.get('category') or '-'}")
        if item.get("has_update"):
            self.detail_badge.setText(
                f"Status: update pendente  •  {item.get('installed_version') or '-'} -> {item.get('latest_version') or '-'}"
            )
            self.detail_badge.setStyleSheet(
                "padding: 6px 10px; border-radius: 10px; background: #fff3cd; color: #7a4f01; font-weight: 700;"
            )
        elif item.get("installed_version"):
            self.detail_badge.setText("Status: instalada e alinhada")
            self.detail_badge.setStyleSheet(
                "padding: 6px 10px; border-radius: 10px; background: #e8f5ee; color: #0b6e4f; font-weight: 700;"
            )
        else:
            self.detail_badge.setText("Status: disponivel para instalar")
            self.detail_badge.setStyleSheet(
                "padding: 6px 10px; border-radius: 10px; background: #eef4fb; color: #355c7d; font-weight: 700;"
            )
        url = item.get("url", "")
        self.detail_url.setText(f'<a href="{url}">{url}</a>' if url else "")
        desc_parts = [item.get("sentence", ""), item.get("paragraph", "")]
        if item.get("match_reason"):
            desc_parts.append(f"Sinal de busca: {item.get('match_reason')}")
        self.detail_desc.setPlainText("\n\n".join([x for x in desc_parts if x]))

        versions = item.get("versions", []) or [item.get("latest_version", "")]
        current = item.get("latest_version") or item.get("installed_version") or ""
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.addItems([v for v in versions if v])
        index = self.version_combo.findText(current)
        self.version_combo.setCurrentIndex(index if index >= 0 else 0)
        self.version_combo.blockSignals(False)
        self.refresh_action_state()

    def refresh_action_state(self):
        item = self.selected_item()
        if not item:
            self.action_btn.setEnabled(False)
            self.uninstall_btn.setEnabled(False)
            return
        installed = item.get("installed_version", "")
        selected = self.version_combo.currentText().strip()
        if not installed:
            self.action_btn.setText(self.app.t("mgr.install", "Install"))
            self.action_btn.setEnabled(not self.runtime_busy)
        else:
            cmp = self.app.compare_versions(selected, installed)
            if cmp > 0:
                self.action_btn.setText(f"{self.app.t('mgr.update', 'Update')} para {selected}")
                self.action_btn.setEnabled(not self.runtime_busy)
            elif cmp < 0:
                self.action_btn.setText(f"{self.app.t('mgr.downgrade', 'Downgrade')} para {selected}")
                self.action_btn.setEnabled(not self.runtime_busy)
            else:
                self.action_btn.setText(self.app.t("mgr.installed_state", "Installed"))
                self.action_btn.setEnabled(False)
        self.uninstall_btn.setEnabled(bool(installed) and not self.runtime_busy)

    def run_action(self):
        item = self.selected_item()
        if not item or self.runtime_busy:
            return
        version = self.version_combo.currentText().strip()
        name = item.get("name", "")
        self.set_busy(True)
        self.append_log(f"{self.action_btn.text()}: {name} {version}".strip())

        def worker():
            out, ok, err = self.backend.install_library_sync(name, version)

            def done():
                self.set_busy(False)
                if ok:
                    self.app.load_installed_libraries()
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha na instalação", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def run_uninstall(self):
        item = self.selected_item()
        if not item or self.runtime_busy or not item.get("installed_version"):
            return
        if QtWidgets.QMessageBox.question(
            self,
            self.app.t("mgr.uninstall", "Uninstall"),
            f"{self.app.t('mgr.uninstall_confirm', 'Remove library?')}\n{item.get('name', '')}",
        ) != QtWidgets.QMessageBox.Yes:
            return
        name = item.get("name", "")
        self.set_busy(True)
        self.append_log(f"{self.app.t('mgr.uninstall', 'Uninstall')}: {name}")

        def worker():
            out, ok, err = self.backend.uninstall_library(name)

            def done():
                self.set_busy(False)
                if ok:
                    self.app.load_installed_libraries()
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao desinstalar", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def install_zip(self):
        if self.runtime_busy:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Selecionar biblioteca ZIP", "", "ZIP (*.zip)")
        if not path:
            return
        self.set_busy(True)
        self.append_log(f"ZIP: {path}")

        def worker():
            out, ok, err = self.backend.install_library_zip_sync(path)

            def done():
                self.set_busy(False)
                if ok:
                    self.app.load_installed_libraries()
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao instalar ZIP", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()


class BoardManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.app = parent
        self.backend = parent.backend
        self.setWindowTitle(self.app.t("mgr.board.title", "Board Manager"))
        self.app.fit_dialog_to_screen(self, 1040, 620)
        self.items = []
        self.urls = []
        self.runtime_busy = False
        self._build_ui()
        self.refresh_data()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)

        manager_tab = QtWidgets.QWidget()
        manager_layout = QtWidgets.QVBoxLayout(manager_tab)
        top = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText(self.app.t("mgr.search", "Search:"))
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItem(self.app.t("mgr.filter.updates", "Pending updates"), "updates")
        self.filter_combo.addItem(self.app.t("mgr.filter.installed", "Installed"), "installed")
        self.filter_combo.addItem(self.app.t("mgr.filter.all", "All"), "all")
        self.reload_btn = QtWidgets.QPushButton(self.app.t("mgr.reload", "Reload"))
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.filter_combo)
        top.addWidget(self.reload_btn)
        manager_layout.addLayout(top)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        manager_layout.addWidget(splitter, 1)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Plataforma", "Instalada", "Última", "ID"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        splitter.addWidget(self.table)

        detail_host = QtWidgets.QWidget()
        detail_layout = QtWidgets.QVBoxLayout(detail_host)
        self.detail_title = QtWidgets.QLabel("Selecione uma plataforma")
        self.detail_title.setObjectName("managerTitle")
        self.detail_id = QtWidgets.QLabel("ID: -")
        self.detail_installed = QtWidgets.QLabel("Instalada: -")
        self.detail_latest = QtWidgets.QLabel("Última: -")
        self.detail_url = QtWidgets.QLabel()
        self.detail_url.setOpenExternalLinks(True)
        version_row = QtWidgets.QHBoxLayout()
        self.version_combo = QtWidgets.QComboBox()
        self.action_btn = QtWidgets.QPushButton(self.app.t("mgr.install", "Install"))
        self.uninstall_btn = QtWidgets.QPushButton(self.app.t("mgr.uninstall", "Uninstall"))
        version_row.addWidget(QtWidgets.QLabel(self.app.t("mgr.version", "Version:")))
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.action_btn)
        version_row.addWidget(self.uninstall_btn)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.hide()
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(180)
        for widget in [self.detail_title, self.detail_id, self.detail_installed, self.detail_latest, self.detail_url]:
            detail_layout.addWidget(widget)
        detail_layout.addLayout(version_row)
        detail_layout.addWidget(self.progress)
        detail_layout.addWidget(self.log_box)
        splitter.addWidget(detail_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        aux_tab = QtWidgets.QWidget()
        aux_layout = QtWidgets.QVBoxLayout(aux_tab)
        aux_layout.addWidget(QtWidgets.QLabel(self.app.t("mgr.board.json_urls", "Board JSON URLs")))
        self.urls_list = QtWidgets.QListWidget()
        aux_layout.addWidget(self.urls_list, 1)
        urls_btns = QtWidgets.QHBoxLayout()
        self.add_url_btn = QtWidgets.QPushButton(self.app.t("mgr.board.json_add", "Add JSON"))
        self.remove_url_btn = QtWidgets.QPushButton(self.app.t("mgr.board.json_remove", "Remove JSON"))
        urls_btns.addWidget(self.add_url_btn)
        urls_btns.addWidget(self.remove_url_btn)
        aux_layout.addLayout(urls_btns)
        aux_layout.addWidget(QtWidgets.QLabel(self.app.t("mgr.board.default_info", "Default URLs include ESP32 and STM32 indexes.")))

        self.tabs.addTab(manager_tab, self.app.t("mgr.tab.manager", "Manager"))
        self.tabs.addTab(aux_tab, self.app.t("mgr.tab.aux", "Auxiliary Settings"))

        self.search_edit.textChanged.connect(self.render_table)
        self.filter_combo.currentIndexChanged.connect(self.render_table)
        self.reload_btn.clicked.connect(self.refresh_data)
        self.table.itemSelectionChanged.connect(self.update_detail)
        self.version_combo.currentIndexChanged.connect(self.refresh_action_state)
        self.action_btn.clicked.connect(self.run_action)
        self.uninstall_btn.clicked.connect(self.run_uninstall)
        self.add_url_btn.clicked.connect(self.add_url)
        self.remove_url_btn.clicked.connect(self.remove_url)

    def append_log(self, text: str):
        self.log_box.appendPlainText(text)

    def set_busy(self, flag: bool):
        self.runtime_busy = flag
        self.progress.setVisible(flag)
        for widget in [self.reload_btn, self.add_url_btn, self.remove_url_btn, self.search_edit, self.filter_combo]:
            widget.setEnabled(not flag)
        self.refresh_action_state()

    def current_filter(self) -> str:
        return self.filter_combo.currentData() or "updates"

    def selected_item(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        filtered = self.filtered_items()
        if row >= len(filtered):
            return None
        return filtered[row]

    def filtered_items(self):
        mode = self.current_filter()
        if mode == "installed":
            base = [item for item in self.items if item.get("installed_version")]
        elif mode == "all":
            base = list(self.items)
        else:
            base = [item for item in self.items if item.get("has_update")]
        term = self.search_edit.text().strip().lower()
        if term:
            base = [item for item in base if term in f"{item.get('name','')} {item.get('id','')}".lower()]
        return base

    def refresh_data(self):
        if self.runtime_busy:
            return
        self.set_busy(True)
        self.append_log(self.app.t("mgr.loading", "Loading data..."))

        def worker():
            all_board_entries = self.backend.list_boards_all_versions()
            platforms_by_id = {}
            for entry in all_board_entries:
                platform_id = entry.get("platform_id", "")
                version = entry.get("platform_version", "")
                if platform_id and version:
                    if platform_id not in platforms_by_id:
                        platforms_by_id[platform_id] = {
                            "id": platform_id,
                            "name": platform_id,
                            "url": "",
                            "versions": set(),
                        }
                    platforms_by_id[platform_id]["versions"].add(version)

            installed_cores = self.backend.list_installed_cores()
            installed_map = {}
            for core in installed_cores:
                core_id = core.get("id", "")
                version = core.get("installed_version", "")
                if core_id and version:
                    installed_map[core_id] = version

            all_cores = []
            for platform_id, plat_data in platforms_by_id.items():
                versions_list = sorted(list(plat_data["versions"]), key=lambda v: self.backend._normalize_version(v), reverse=True)
                installed_version = installed_map.get(platform_id, "")
                latest_version = versions_list[0] if versions_list else ""
                all_cores.append({
                    "id": platform_id,
                    "name": plat_data.get("name", platform_id),
                    "installed_version": installed_version,
                    "latest_version": latest_version,
                    "versions": versions_list,
                    "url": plat_data.get("url", ""),
                    "has_update": bool(installed_version and latest_version and self.app.compare_versions(latest_version, installed_version) > 0),
                })

            urls = self.backend.get_additional_board_urls()

            def done():
                self.items = sorted(all_cores, key=lambda x: x.get("id", "").lower())
                self.urls = urls
                self.urls_list.clear()
                self.urls_list.addItems(urls)
                self.render_table()
                self.set_busy(False)
                self.append_log(self.app.t("mgr.loaded", "Data loaded."))

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def render_table(self):
        filtered = self.filtered_items()
        self.table.setRowCount(len(filtered))
        for row, item in enumerate(filtered):
            values = [
                item.get("name", ""),
                item.get("installed_version", "") or "-",
                item.get("latest_version", "") or "-",
                item.get("id", ""),
            ]
            for col, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if col == 0 and item.get("has_update"):
                    cell.setForeground(QtGui.QColor("#0b6e4f"))
                self.table.setItem(row, col, cell)
        if filtered:
            self.table.selectRow(0)
        else:
            self.update_detail()

    def update_detail(self):
        item = self.selected_item()
        if not item:
            self.detail_title.setText("Selecione uma plataforma")
            self.detail_id.setText("ID: -")
            self.detail_installed.setText("Instalada: -")
            self.detail_latest.setText("Última: -")
            self.detail_url.setText("")
            self.version_combo.clear()
            self.refresh_action_state()
            return
        self.detail_title.setText(item.get("name", ""))
        self.detail_id.setText(f"ID: {item.get('id', '')}")
        self.detail_installed.setText(f"Instalada: {item.get('installed_version') or '-'}")
        self.detail_latest.setText(f"Última: {item.get('latest_version') or '-'}")
        url = item.get("url", "")
        self.detail_url.setText(f'<a href="{url}">{url}</a>' if url else "")
        versions = list(item.get("versions", []))
        if not versions:
            versions = [item.get("latest_version", "") or item.get("installed_version", "")]
        current = item.get("latest_version") or item.get("installed_version") or ""
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.addItems([value for value in versions if value])
        index = self.version_combo.findText(current)
        self.version_combo.setCurrentIndex(index if index >= 0 else 0)
        self.version_combo.blockSignals(False)
        self.refresh_action_state()

    def refresh_action_state(self):
        item = self.selected_item()
        if not item:
            self.action_btn.setEnabled(False)
            self.uninstall_btn.setEnabled(False)
            return
        installed = item.get("installed_version", "")
        selected = self.version_combo.currentText().strip()
        if not installed:
            self.action_btn.setText(self.app.t("mgr.install", "Install"))
            self.action_btn.setEnabled(not self.runtime_busy)
        else:
            cmp = self.app.compare_versions(selected, installed)
            if cmp > 0:
                self.action_btn.setText(self.app.t("mgr.update", "Update"))
                self.action_btn.setEnabled(not self.runtime_busy)
            elif cmp < 0:
                self.action_btn.setText(self.app.t("mgr.downgrade", "Downgrade"))
                self.action_btn.setEnabled(not self.runtime_busy)
            else:
                self.action_btn.setText(self.app.t("mgr.installed_state", "Installed"))
                self.action_btn.setEnabled(False)
        self.uninstall_btn.setEnabled(bool(installed) and not self.runtime_busy)

    def run_action(self):
        item = self.selected_item()
        if not item or self.runtime_busy:
            return
        core_id = item.get("id", "")
        version = self.version_combo.currentText().strip()
        self.set_busy(True)
        self.append_log(f"{self.action_btn.text()}: {core_id} {version}".strip())

        def worker():
            out, ok, err = self.backend.install_core_sync(core_id, version)

            def done():
                self.set_busy(False)
                if ok:
                    self.app.load_boards()
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao instalar plataforma", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def run_uninstall(self):
        item = self.selected_item()
        if not item or self.runtime_busy or not item.get("installed_version"):
            return
        if QtWidgets.QMessageBox.question(
            self,
            self.app.t("mgr.uninstall", "Uninstall"),
            f"{self.app.t('mgr.uninstall_confirm', 'Remove item?')}\n{item.get('name', '')}",
        ) != QtWidgets.QMessageBox.Yes:
            return
        core_id = item.get("id", "")
        self.set_busy(True)
        self.append_log(f"{self.app.t('mgr.uninstall', 'Uninstall')}: {core_id}")

        def worker():
            out, ok, err = self.backend.uninstall_core_sync(core_id)

            def done():
                self.set_busy(False)
                if ok:
                    self.app.load_boards()
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao remover plataforma", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def add_url(self):
        if self.runtime_busy:
            return
        url, ok = QtWidgets.QInputDialog.getText(
            self,
            self.app.t("mgr.board.json_add", "Add JSON"),
            self.app.t("mgr.board.json_prompt", "Board index URL:"),
        )
        if not ok or not url.strip():
            return
        self.set_busy(True)
        self.append_log(f"{self.app.t('mgr.board.json_add', 'Add JSON')}: {url.strip()}")

        def worker():
            out, ok_result, err = self.backend.add_board_json_sync(url.strip())

            def done():
                self.set_busy(False)
                if ok_result:
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao adicionar URL", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def remove_url(self):
        if self.runtime_busy:
            return
        item = self.urls_list.currentItem()
        if not item:
            return
        url = item.text()
        if QtWidgets.QMessageBox.question(
            self,
            self.app.t("mgr.board.json_remove", "Remove JSON"),
            f"{self.app.t('mgr.board.json_remove_q', 'Remove URL?')}\n{url}",
        ) != QtWidgets.QMessageBox.Yes:
            return
        self.set_busy(True)
        self.append_log(f"{self.app.t('mgr.board.json_remove', 'Remove JSON')}: {url}")

        def worker():
            out, ok_result, err = self.backend.remove_board_json_sync(url)

            def done():
                self.set_busy(False)
                if ok_result:
                    self.refresh_data()
                else:
                    self.app.show_error_dialog(self.windowTitle(), err or "Falha ao remover URL", out)

            self.app.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()


class ActionProgressDialog(QtWidgets.QDialog):
    def __init__(self, parent, title: str, subtitle: str, debug_lines: list, abort_callback=None):
        super().__init__(parent)
        self.allow_close = False
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(680, 420)
        layout = QtWidgets.QVBoxLayout(self)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("managerTitle")
        self.subtitle_label = QtWidgets.QLabel(subtitle)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.debug_box = QtWidgets.QPlainTextEdit()
        self.debug_box.setReadOnly(True)
        self.debug_box.setPlainText("\n".join(debug_lines))
        self.debug_box.setMaximumBlockCount(2000)
        self.abort_btn = QtWidgets.QPushButton("Abortar")
        self.abort_btn.setObjectName("warningOrange")
        self.abort_btn.setEnabled(abort_callback is not None)
        close_hint = QtWidgets.QLabel("Fechamento bloqueado até a operação terminar ou ser abortada.")
        close_hint.setObjectName("mutedLabel")
        layout.addWidget(title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.debug_box, 1)
        layout.addWidget(close_hint)
        layout.addWidget(self.abort_btn, 0, QtCore.Qt.AlignRight)
        if abort_callback:
            self.abort_btn.clicked.connect(abort_callback)

    def set_subtitle(self, text: str):
        self.subtitle_label.setText(text)

    def append_debug(self, text: str):
        self.debug_box.appendPlainText(text)
        bar = self.debug_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        else:
            event.ignore()

    def finish(self):
        self.allow_close = True
        self.accept()


class ActionResultDialog(QtWidgets.QDialog):
    def __init__(self, parent, title: str, flash_pct: float, ram_pct: float, flash_line: str, ram_line: str, warning_lines: list):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 520)
        layout = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel(title)
        header.setObjectName("managerTitle")
        layout.addWidget(header)

        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel(f"Flash: {flash_pct:.1f}%"), 0, 0)
        flash_bar = QtWidgets.QProgressBar()
        flash_bar.setRange(0, 100)
        flash_bar.setValue(max(0, min(100, int(flash_pct))))
        grid.addWidget(flash_bar, 0, 1)
        if flash_line:
            flash_lbl = QtWidgets.QLabel(flash_line)
            flash_lbl.setWordWrap(True)
            grid.addWidget(flash_lbl, 1, 0, 1, 2)

        grid.addWidget(QtWidgets.QLabel(f"RAM: {ram_pct:.1f}%"), 2, 0)
        ram_bar = QtWidgets.QProgressBar()
        ram_bar.setRange(0, 100)
        ram_bar.setValue(max(0, min(100, int(ram_pct))))
        grid.addWidget(ram_bar, 2, 1)
        if ram_line:
            ram_lbl = QtWidgets.QLabel(ram_line)
            ram_lbl.setWordWrap(True)
            grid.addWidget(ram_lbl, 3, 0, 1, 2)
        layout.addLayout(grid)

        layout.addWidget(QtWidgets.QLabel("Warnings"))
        warnings = QtWidgets.QPlainTextEdit()
        warnings.setReadOnly(True)
        warnings.setPlainText("\n".join(warning_lines) if warning_lines else "Sem warnings")
        layout.addWidget(warnings, 1)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class WebBrowserQt(QtWidgets.QDialog):
    def __init__(self, parent, title: str = "Pesquisar"):
        super().__init__(parent)
        self.setWindowTitle(title)
        if hasattr(parent, "fit_dialog_to_screen"):
            parent.fit_dialog_to_screen(self, 1180, 760)
        else:
            self.resize(1180, 760)
        layout = QtWidgets.QVBoxLayout(self)
        self.url_label = QtWidgets.QLineEdit()
        self.url_label.setReadOnly(True)
        layout.addWidget(self.url_label)

        compiled_runtime = bool(getattr(sys, "frozen", False)) or bool(globals().get("__compiled__"))
        use_internal_browser = QtWebEngineWidgets is not None and not compiled_runtime
        self.browser = QtWebEngineWidgets.QWebEngineView(self) if use_internal_browser else None
        if self.browser is not None:
            layout.addWidget(self.browser, 1)
        else:
            fallback = QtWidgets.QTextBrowser()
            fallback.setOpenExternalLinks(True)
            fallback.setHtml(
                "<h3>Navegador interno indisponível</h3>"
                "<p>QtWebEngine não está disponível nesta instalação.</p>"
                "<p>Use o botão abaixo para abrir a pesquisa no navegador padrão.</p>"
            )
            self.browser = fallback
            layout.addWidget(fallback, 1)

        buttons_row = QtWidgets.QHBoxLayout()
        self.open_external_btn = QtWidgets.QPushButton("Abrir no navegador")
        close_btn = QtWidgets.QPushButton("Fechar")
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.open_external_btn)
        buttons_row.addWidget(close_btn)
        layout.addLayout(buttons_row)
        close_btn.clicked.connect(self.accept)
        self.open_external_btn.clicked.connect(self._open_external)
        self._current_url = ""

    def load_url(self, url: str):
        self._current_url = str(url or "").strip()
        self.url_label.setText(self._current_url)
        if hasattr(self.browser, "load"):
            self.browser.load(QtCore.QUrl(self._current_url))
        elif isinstance(self.browser, QtWidgets.QTextBrowser):
            safe_url = html.escape(self._current_url)
            self.browser.setHtml(
                f"<h3>Pesquisa pronta</h3><p><a href=\"{safe_url}\">{safe_url}</a></p>"
            )

    def _open_external(self):
        if self._current_url:
            parent = self.parent()
            if parent and hasattr(parent, "open_external_url"):
                parent.open_external_url(self._current_url)
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._current_url))


class VCliQtApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_base_dir = Path(__file__).resolve().parent
        appdata_local = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.appdata_dir = appdata_local / "Arduino15" / "V-CLI"
        self.appdata_dir.mkdir(parents=True, exist_ok=True)
        self.app_settings_file = self.appdata_dir / "settings.json"
        self.app_settings = self._load_app_settings()
        self.locale_dir = self.app_base_dir / "locales"
        self.translations = {}
        self.lang = "en"
        self._load_i18n()
        self.bridge = UiBridge()
        self.bridge.invoke.connect(lambda fn: fn())
        self.bridge.log_message.connect(self.log)
        self.bridge.serial_data.connect(self._handle_serial_payload)

        self.current_project = None
        self.current_config = None
        self.backend = None
        self.git_available = bool(shutil.which("git"))
        self.serial_connection = None
        self.serial_stamp_enabled = False
        self.serial_tx_enabled = False
        self.serial_tx_log = []
        self.serial_live_headers = []
        self.serial_live_records = []
        self.serial_live_errors = []
        self.serial_plot_series = {}
        self.serial_recording_active = False
        self.serial_recording_session = None
        self.serial_last_rx_ts = None
        self.serial_line_counter = 0
        self.available_ports = []
        self.baud_options = ["9600", "19200", "38400", "57600", "115200"]
        self.recent_projects_file = self.appdata_dir / "recent_projects.json"
        self.app_icon_path = self.app_base_dir / ".ico"
        self.recent_projects = []
        self._load_recent_projects()
        self.boards_cache = []
        self.boards_cache_time = 0
        self.loaded_libraries = []
        self.variant_options = []
        self.dynamic_tool_controls = {}
        self.startup_dialog = None
        self.default_project_icon_path = self.app_base_dir / "project_padrao.png"
        self.board_updates_count = 0
        self.board_updates_flash_on = False
        self.board_updates_timer = QtCore.QTimer(self)
        self.board_updates_timer.setInterval(650)
        self.board_updates_timer.timeout.connect(self._toggle_board_updates_flash)
        self.libs_updates_count = 0
        self.libs_updates_flash_on = False
        self.libs_updates_timer = QtCore.QTimer(self)
        self.libs_updates_timer.setInterval(650)
        self.libs_updates_timer.timeout.connect(self._toggle_libs_updates_flash)

        self.setWindowTitle(self.t("app.title", "V CLI - VS Code Arduino plugin"))
        self.setMinimumSize(1000, 680)
        if self.app_icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(self.app_icon_path)))
        self._apply_styles()
        self._build_ui()
        self._sanitize_widget_texts(self)
        self.tray_icon = None
        self._quitting_from_tray = False
        self._create_tray_icon()

        self.backend = CLIBackend(os.getcwd(), self.bridge.log_message.emit)
        self.load_recent_projects_widget()
        self.apply_app_settings_to_ui()
        QtCore.QTimer.singleShot(0, self.start_initial_loading)

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
        preferred = str(getattr(self, "app_settings", {}).get("language", "auto") or "auto").strip().lower()
        if preferred in {"pt", "en"}:
            return preferred
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

    def _fix_mojibake_text(self, text: str) -> str:
        value = str(text or "")
        replacements = {
            "ÃƒÂ§": "ç",
            "ÃƒÂ£": "ã",
            "ÃƒÂ¡": "á",
            "ÃƒÂ©": "é",
            "ÃƒÂ­": "í",
            "ÃƒÂ³": "ó",
            "ÃƒÂº": "ú",
            "ÃƒÂµ": "õ",
            "ÃƒÂª": "ê",
            "ÃƒÂ´": "ô",
            "ÃƒÂ¢": "â",
            "ÃƒÂ‰": "É",
            "ÃƒÂ“": "Ó",
            "ÃƒÂš": "Ú",
            "ÃƒÂ€": "À",
            "ÃƒÂ ": "à",
            "ÃƒÂ": "",
            "Ã§": "ç",
            "Ã£": "ã",
            "Ã¡": "á",
            "Ã©": "é",
            "Ã­": "í",
            "Ã³": "ó",
            "Ãº": "ú",
            "Ãµ": "õ",
            "Ãª": "ê",
            "Ã´": "ô",
            "Ã¢": "â",
            "Ã‰": "É",
            "Ã“": "Ó",
            "Ãš": "Ú",
            "Ã€": "À",
            "Ã ": "à",
            "Â¿": "¿",
            "Âº": "º",
            "Âª": "ª",
            "â€“": "-",
            "â€”": "-",
            "â€˜": "'",
            "â€™": "'",
            "â€œ": "\"",
            "â€\x9d": "\"",
            "â†’": "->",
        }
        for wrong, right in replacements.items():
            value = value.replace(wrong, right)
        return value

    def _sanitize_widget_texts(self, root):
        if root is None:
            return
        widgets = [root]
        widgets.extend(root.findChildren(QtWidgets.QWidget))
        for widget in widgets:
            try:
                if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QPushButton, QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                    widget.setText(self._fix_mojibake_text(widget.text()))
                elif isinstance(widget, QtWidgets.QGroupBox):
                    widget.setTitle(self._fix_mojibake_text(widget.title()))
                elif isinstance(widget, QtWidgets.QLineEdit):
                    widget.setPlaceholderText(self._fix_mojibake_text(widget.placeholderText()))
                elif isinstance(widget, QtWidgets.QPlainTextEdit):
                    widget.setPlaceholderText(self._fix_mojibake_text(widget.placeholderText()))
                elif isinstance(widget, QtWidgets.QTextEdit):
                    widget.setPlaceholderText(self._fix_mojibake_text(widget.placeholderText()))
                elif isinstance(widget, QtWidgets.QComboBox):
                    for index in range(widget.count()):
                        widget.setItemText(index, self._fix_mojibake_text(widget.itemText(index)))
                elif isinstance(widget, QtWidgets.QTabWidget):
                    for index in range(widget.count()):
                        widget.setTabText(index, self._fix_mojibake_text(widget.tabText(index)))
                elif isinstance(widget, QtWidgets.QListWidget):
                    for index in range(widget.count()):
                        item = widget.item(index)
                        if item:
                            item.setText(self._fix_mojibake_text(item.text()))
                elif isinstance(widget, QtWidgets.QTreeWidget):
                    for index in range(widget.columnCount()):
                        widget.headerItem().setText(index, self._fix_mojibake_text(widget.headerItem().text(index)))
                elif isinstance(widget, QtWidgets.QTableWidget):
                    for index in range(widget.columnCount()):
                        header = widget.horizontalHeaderItem(index)
                        if header:
                            header.setText(self._fix_mojibake_text(header.text()))
            except Exception:
                pass

    def _apply_styles(self):
        theme = str(self.app_settings.get("theme", "light") or "light").strip().lower()
        if theme == "dark":
            self.setStyleSheet(
                """
                QMainWindow, QDialog, QWidget { background: #101418; color: #e5edf5; }
                QMenuBar { background: #0d1319; color: #e5edf5; border-bottom: 1px solid #293544; }
                QMenuBar::item { background: transparent; color: #d8e7f5; padding: 4px 8px; }
                QMenuBar::item:selected { background: #223447; color: #ffffff; border-radius: 4px; }
                QMenuBar::item:pressed { background: #2b4359; color: #ffffff; border-radius: 4px; }
                QFrame#sidePanel { background: #16202a; border: 1px solid #293544; border-radius: 12px; }
                QListWidget#recentProjects { font-size: 13px; padding: 4px; }
                QListWidget#recentProjects::item { min-height: 28px; border-radius: 6px; padding: 4px 8px; }
                QListWidget#recentProjects::item:selected { background: #28435c; color: white; }
                QLabel#historyBanner { font-size: 14px; font-weight: 800; color: #f0f6fb; padding: 4px 8px; background: rgba(255,255,255,0.06); border: 1px solid #32465a; border-radius: 10px; }
                QLabel#historyIcon { background: transparent; border: none; }
                QLabel#boardUpdatesLabel { font-size: 12px; font-weight: 700; color: #9aa8b6; padding: 4px 8px; }
                QLabel#mutedLabel { color: #9fb4c7; background: transparent; }
                QLabel#sectionTitle, QLabel#managerTitle { font-size: 15px; font-weight: 700; color: #f0f6fb; }
                QPushButton { background: #1a2530; color: #e5edf5; border: 1px solid #334355; border-radius: 8px; padding: 7px 12px; font-weight: 600; }
                QPushButton:hover { border-color: #6b8ba7; }
                QTabWidget::pane, QGroupBox { border: 1px solid #293544; border-radius: 10px; background: #141b23; }
                QGroupBox::title { color: #d8e7f5; subcontrol-origin: margin; left: 12px; padding: 0 4px; }
                QTabBar::tab { background: #1e2a36; border: 1px solid #293544; padding: 8px 14px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; color: #e5edf5; }
                QTabBar::tab:selected { background: #141b23; }
                QPlainTextEdit#consoleBox, QPlainTextEdit#serialBox, QPlainTextEdit#cliBox { background: #050607; color: #00ff7f; border: 1px solid #111; border-radius: 10px; font-family: Consolas, Courier New, monospace; font-size: 12px; }
                QLineEdit, QComboBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit, QTextBrowser, QTreeWidget { border: 1px solid #334355; border-radius: 8px; padding: 6px; background: #0f151c; color: #e5edf5; }
                QTableWidget { alternate-background-color: #18222d; gridline-color: #334355; selection-background-color: #35506d; selection-color: #ffffff; }
                QTableWidget::item { background: transparent; color: #e5edf5; }
                QTableWidget::item:selected { background: #35506d; color: #ffffff; }
                QComboBox::drop-down { border: none; background: #1a2530; width: 24px; border-top-right-radius: 8px; border-bottom-right-radius: 8px; }
                QComboBox QAbstractItemView, QListView, QAbstractItemView { background: #16202a; color: #e5edf5; selection-background-color: #28435c; selection-color: white; border: 1px solid #334355; }
                QCheckBox, QRadioButton { color: #e5edf5; background: transparent; }
                QDialogButtonBox { background: transparent; }
                QHeaderView::section { background: #1e2a36; color: #e5edf5; border: 1px solid #334355; padding: 6px; }
                QMenu { background: #16202a; color: #e5edf5; border: 1px solid #334355; }
                QMenu::item:selected { background: #28435c; color: white; }
                QSplitter::handle { background: #293544; }
                QScrollBar:vertical, QScrollBar:horizontal { background: #141b23; border: none; }
                QScrollBar:vertical { width: 14px; margin: 2px; }
                QScrollBar:horizontal { height: 14px; margin: 2px; }
                QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #42576c; border-radius: 7px; min-height: 28px; min-width: 28px; }
                QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #5f7a95; }
                QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }
                QTableCornerButton::section { background: #1e2a36; border: 1px solid #334355; }
                """
            )
            self._apply_theme_accents(dark=True)
            return
        self.setStyleSheet(
            """
            QMainWindow, QDialog, QWidget {
                background: #f4f6f8;
                color: #1e2933;
            }
            QMenuBar {
                background: #ffffff;
                color: #1e2933;
                border-bottom: 1px solid #c8d3df;
            }
            QMenuBar::item {
                background: transparent;
                color: #1e2933;
                padding: 4px 8px;
            }
            QMenuBar::item:selected {
                background: #cfe5ff;
                color: #12344d;
                border-radius: 4px;
            }
            QMenuBar::item:pressed {
                background: #b9d8ff;
                color: #12344d;
                border-radius: 4px;
            }
            QFrame#sidePanel {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #eef3f7, stop:1 #dfe7ef);
                border: 1px solid #c8d3df;
                border-radius: 12px;
            }
            QListWidget#recentProjects {
                font-size: 13px;
                padding: 4px;
            }
            QListWidget#recentProjects::item {
                min-height: 28px;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QListWidget#recentProjects::item:selected {
                background: #cfe5ff;
                color: #12344d;
            }
            QLabel#sectionTitle, QLabel#managerTitle {
                font-size: 15px;
                font-weight: 700;
                color: #12344d;
            }
            QLabel#historyBanner {
                font-size: 14px;
                font-weight: 800;
                color: #0f3554;
                padding: 4px 8px;
                background: rgba(255,255,255,0.55);
                border: 1px solid #c8d3df;
                border-radius: 10px;
            }
            QLabel#historyIcon {
                background: transparent;
                border: none;
            }
            QLabel#boardUpdatesLabel {
                font-size: 12px;
                font-weight: 700;
                color: #6b7280;
                padding: 4px 8px;
            }
            QLabel#mutedLabel {
                color: #5b7288;
                background: transparent;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c6d0da;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                border-color: #6b8ba7;
            }
            QPushButton#primaryBlue { background: #0078d4; color: white; border-color: #0078d4; }
            QPushButton#successLight { background: #90ee90; color: #102a12; border-color: #79d87a; }
            QPushButton#successDark { background: #228b22; color: white; border-color: #228b22; }
            QPushButton#warningOrange { background: #ff8c00; color: white; border-color: #ff8c00; }
            QPushButton#neutralGray { background: #808080; color: white; border-color: #808080; }
            QTabWidget::pane, QGroupBox {
                border: 1px solid #c8d3df;
                border-radius: 10px;
                background: white;
            }
            QGroupBox::title {
                color: #12344d;
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QTabBar::tab {
                background: #dde7f0;
                border: 1px solid #c8d3df;
                padding: 8px 14px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: white;
            }
            QPlainTextEdit#consoleBox, QPlainTextEdit#serialBox, QPlainTextEdit#cliBox {
                background: #1e1e1e;
                color: #00ff7f;
                border: 1px solid #111;
                border-radius: 10px;
                font-family: Consolas, Courier New, monospace;
                font-size: 12px;
            }
            QLineEdit, QComboBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit, QTextBrowser, QTreeWidget {
                border: 1px solid #c6d0da;
                border-radius: 8px;
                padding: 6px;
                background: white;
                color: #1e2933;
            }
            QTableWidget {
                alternate-background-color: #f5f8fb;
                gridline-color: #c8d3df;
                selection-background-color: #cfe5ff;
                selection-color: #12344d;
            }
            QTableWidget::item {
                background: transparent;
                color: #1e2933;
            }
            QTableWidget::item:selected {
                background: #cfe5ff;
                color: #12344d;
            }
            QComboBox::drop-down {
                border: none;
                background: #eef3f7;
                width: 24px;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QComboBox QAbstractItemView, QListView, QAbstractItemView {
                background: white;
                color: #1e2933;
                selection-background-color: #cfe5ff;
                selection-color: #12344d;
                border: 1px solid #c8d3df;
            }
            QCheckBox, QRadioButton { color: #1e2933; background: transparent; }
            QDialogButtonBox { background: transparent; }
            QHeaderView::section { background: #eef3f7; color: #12344d; border: 1px solid #c8d3df; padding: 6px; }
            QMenu { background: white; color: #1e2933; border: 1px solid #c8d3df; }
            QMenu::item:selected { background: #cfe5ff; color: #12344d; }
            QSplitter::handle { background: #d8e1ea; }
            QScrollBar:vertical, QScrollBar:horizontal { background: #eef3f7; border: none; }
            QScrollBar:vertical { width: 14px; margin: 2px; }
            QScrollBar:horizontal { height: 14px; margin: 2px; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #b7c7d8; border-radius: 7px; min-height: 28px; min-width: 28px; }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #90a8c1; }
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }
            QTableCornerButton::section { background: #eef3f7; border: 1px solid #c8d3df; }
            """
        )
        self._apply_theme_accents(dark=False)

    def _apply_theme_accents(self, dark: bool):
        title_color = "#c7d7e7" if dark else "#2f4858"
        summary_style = (
            "padding: 8px 10px; border: 1px solid #334355; border-radius: 10px; background: rgba(64,114,158,0.16); color: #d8e7f5;"
            if dark
            else "padding: 8px 10px; border: 1px solid #c6d0da; border-radius: 10px; background: rgba(40,120,180,0.06); color: #1e2933;"
        )
        if hasattr(self, "dynamic_title_label"):
            self.dynamic_title_label.setStyleSheet(f"font-style: italic; color: {title_color};")
        if hasattr(self, "serial_csv_summary"):
            self.serial_csv_summary.setStyleSheet(summary_style)
        if hasattr(self, "libs_updates_label"):
            self._refresh_libs_updates_indicator()
        self._apply_windows_titlebar_theme(dark)

    def _mark_muted_label(self, widget):
        if widget is not None:
            widget.setObjectName("mutedLabel")

    def _apply_windows_titlebar_theme(self, dark: bool):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _apply_windows_titlebar_theme_to_widget(self, widget, dark: bool):
        if sys.platform != "win32" or widget is None:
            return
        try:
            hwnd = int(widget.winId())
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        main = QtWidgets.QVBoxLayout(root)
        self._build_menu_bar()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main.addWidget(split, 1)

        side = QtWidgets.QFrame()
        side.setObjectName("sidePanel")
        side.setMinimumWidth(340)
        side_layout = QtWidgets.QVBoxLayout(side)
        btn_row = QtWidgets.QHBoxLayout()
        self.new_btn = QtWidgets.QPushButton(self.t("btn.new", "New"))
        self.open_btn = QtWidgets.QPushButton(self.t("btn.open", "Open"))
        btn_row.addWidget(self.new_btn)
        btn_row.addWidget(self.open_btn)
        side_layout.addLayout(btn_row)
        history_row = QtWidgets.QHBoxLayout()
        self.history_icon_label = QtWidgets.QLabel()
        self.history_icon_label.setFixedSize(28, 28)
        self.history_icon_label.setObjectName("historyIcon")
        history = QtWidgets.QLabel(self.t("nav.history", "HISTORY"))
        history.setObjectName("historyBanner")
        history_row.addWidget(self.history_icon_label, 0, QtCore.Qt.AlignVCenter)
        history_row.addWidget(history, 1)
        side_layout.addLayout(history_row)
        self.recent_list = QtWidgets.QListWidget()
        self.recent_list.setObjectName("recentProjects")
        side_layout.addWidget(self.recent_list, 1)
        hint = QtWidgets.QLabel(self.t("hint.history", "dblclick: open\nright: remove"))
        hint.setWordWrap(True)
        side_layout.addWidget(hint)
        split.addWidget(side)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.tabs = QtWidgets.QTabWidget()
        right_layout.addWidget(self.tabs, 1)
        self._build_code_tab()
        self._build_docs_tab()
        self._build_boards_tab()
        self._build_libs_tab()
        if self.git_available:
            self._build_git_tab()
        self._build_serial_tab()
        self._build_cli_tab()

        console_group = QtWidgets.QGroupBox(self.t("panel.output", "OUTPUT"))
        console_layout = QtWidgets.QVBoxLayout(console_group)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setObjectName("consoleBox")
        self.console.setReadOnly(True)
        console_layout.addWidget(self.console)
        right_layout.addWidget(console_group, 0)

        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 10)
        split.setSizes([360, 1040])

        self.new_btn.clicked.connect(self.create_project)
        self.open_btn.clicked.connect(self.open_project)
        self.recent_list.itemDoubleClicked.connect(lambda *_: self.open_recent_project())
        self.recent_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self.open_recent_context_menu)
        self.tabs.currentChanged.connect(lambda *_: (self._refresh_board_updates_indicator(), self._refresh_libs_updates_indicator()))
        self._update_project_actions_enabled(False)
        self.apply_app_settings_to_ui()
        self._refresh_docs_ui()
        if self.git_available:
            self._refresh_git_ui()

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Arquivo")
        self.action_new = QtWidgets.QAction("Novo", self)
        self.action_open = QtWidgets.QAction("Abrir", self)
        self.action_open_folder = QtWidgets.QAction("Abrir pasta", self)
        self.action_vscode = QtWidgets.QAction("VS Code", self)
        self.action_compile = QtWidgets.QAction("Compilar", self)
        self.action_upload = QtWidgets.QAction("Upload", self)
        self.action_export = QtWidgets.QAction("Exportar binário", self)
        self.action_properties = QtWidgets.QAction("Propriedades", self)
        file_menu.addAction(self.action_new)
        file_menu.addAction(self.action_open)
        file_menu.addSeparator()
        file_menu.addAction(self.action_open_folder)
        file_menu.addAction(self.action_vscode)
        file_menu.addAction(self.action_compile)
        file_menu.addAction(self.action_upload)
        file_menu.addAction(self.action_export)
        file_menu.addAction(self.action_properties)

        vcli_menu = menu_bar.addMenu("V CLI")
        tools_menu = menu_bar.addMenu("Ferramentas")
        settings_action = QtWidgets.QAction("Configurações", self)
        about_action = QtWidgets.QAction("About", self)
        self.action_lib_backup = QtWidgets.QAction("Backup de bibliotecas", self)
        self.action_lib_restore = QtWidgets.QAction("Restaurar backup de bibliotecas", self)
        self.action_export_project_settings = QtWidgets.QAction("Exportar configurações do projeto", self)
        self.action_import_project_settings = QtWidgets.QAction("Importar configurações do projeto", self)
        self.action_open_csv_log = QtWidgets.QAction("Lista de logs do projeto", self)
        self.action_open_external_log = QtWidgets.QAction("Abrir log externo", self)
        self.action_code_editor = QtWidgets.QAction("Code Editor", self)
        self.action_docs_editor = QtWidgets.QAction("Editor da documentação", self)
        link_arduino = QtWidgets.QAction("Arduino CLI", self)
        link_python = QtWidgets.QAction("Python", self)
        link_pyqt = QtWidgets.QAction("PyQt5", self)
        link_vscode = QtWidgets.QAction("VS Code", self)
        tools_menu.addAction(self.action_open_csv_log)
        tools_menu.addAction(self.action_open_external_log)
        tools_menu.addAction(self.action_code_editor)
        tools_menu.addAction(self.action_docs_editor)
        tools_menu.addSeparator()
        tools_menu.addAction(self.action_export_project_settings)
        tools_menu.addAction(self.action_import_project_settings)
        tools_menu.addSeparator()
        tools_menu.addAction(self.action_lib_backup)
        tools_menu.addAction(self.action_lib_restore)
        vcli_menu.addAction(settings_action)
        vcli_menu.addAction(about_action)
        vcli_menu.addSeparator()
        vcli_menu.addAction(link_arduino)
        vcli_menu.addAction(link_python)
        vcli_menu.addAction(link_pyqt)
        vcli_menu.addAction(link_vscode)

        self.action_new.triggered.connect(self.create_project)
        self.action_open.triggered.connect(self.open_project)
        self.action_open_folder.triggered.connect(self.open_project_folder)
        self.action_vscode.triggered.connect(self.open_vscode)
        self.action_compile.triggered.connect(self.compile_project)
        self.action_upload.triggered.connect(self.upload_project)
        self.action_export.triggered.connect(self.export_binary)
        self.action_properties.triggered.connect(self.edit_project_properties)
        self.action_open_csv_log.triggered.connect(self.open_csv_log_viewer)
        self.action_open_external_log.triggered.connect(self.open_external_csv_log_viewer)
        self.action_code_editor.triggered.connect(self.open_code_editor_dialog)
        self.action_docs_editor.triggered.connect(self.open_docs_editor_dialog)
        self.action_export_project_settings.triggered.connect(self.export_project_settings_bundle)
        self.action_import_project_settings.triggered.connect(self.import_project_settings_bundle)
        self.action_lib_backup.triggered.connect(self.backup_installed_libraries)
        self.action_lib_restore.triggered.connect(self.restore_libraries_backup)
        settings_action.triggered.connect(self.open_settings_dialog)
        about_action.triggered.connect(self.show_about_dialog)
        link_arduino.triggered.connect(lambda: self.open_external_url("https://arduino.github.io/arduino-cli/latest/"))
        link_python.triggered.connect(lambda: self.open_external_url("https://www.python.org/"))
        link_pyqt.triggered.connect(lambda: self.open_external_url("https://pypi.org/project/PyQt5/"))
        link_vscode.triggered.connect(lambda: self.open_external_url("https://code.visualstudio.com/"))

    def _build_code_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        actions = QtWidgets.QHBoxLayout()
        self.btn_folder = QtWidgets.QPushButton("...")
        self.btn_folder.setObjectName("neutralGray")
        self.btn_vscode = QtWidgets.QPushButton(self.t("btn.vscode", "VS Code"))
        self.btn_vscode.setObjectName("primaryBlue")
        self.btn_compile = QtWidgets.QPushButton(self.t("btn.compile", "Compile"))
        self.btn_compile.setObjectName("successLight")
        self.btn_upload = QtWidgets.QPushButton(self.t("btn.upload", "Upload"))
        self.btn_upload.setObjectName("successDark")
        self.btn_export = QtWidgets.QPushButton(self.t("btn.export_binary", "Export binary"))
        self.btn_export.setObjectName("warningOrange")
        for btn in [self.btn_folder, self.btn_vscode, self.btn_compile, self.btn_upload, self.btn_export]:
            actions.addWidget(btn)
        layout.addLayout(actions)

        form_box = QtWidgets.QGroupBox(self.t("cfg.project_and_settings", "Project and Settings"))
        form_layout = QtWidgets.QVBoxLayout(form_box)

        self.project_name_label = QtWidgets.QLabel("...")
        self.project_name_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.board_display = QtWidgets.QLabel("-")
        self.board_display.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.port_display = QtWidgets.QLabel("auto")
        self.port_display.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.baud_display = QtWidgets.QLabel("115200")
        self.baud_display.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.addItem("auto")
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(self.baud_options)
        self.baud_combo.setCurrentText("115200")
        self.port_combo.hide()
        self.baud_combo.hide()

        form_layout.addLayout(self._build_setting_row(self.t("cfg.name", "Name:"), self.project_name_label, [
            ("...", self.edit_project_name),
            (self.t("btn.properties", "Properties"), self.edit_project_properties),
            ("README", self.show_project_readme),
        ]))
        form_layout.addLayout(self._build_setting_row(self.t("cfg.board", "Board:"), self.board_display, [
            ("...", self.open_boards_dialog),
        ]))
        form_layout.addLayout(self._build_setting_row(self.t("cfg.port_label", "Serial Port:"), self.port_display, [
            ("...", self.open_port_modal),
        ]))
        form_layout.addLayout(self._build_setting_row(self.t("cfg.baud_label", "Baud rate (bps):"), self.baud_display, [
            ("...", self.open_baud_modal),
        ]))

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        form_layout.addWidget(line)

        dynamic_title = QtWidgets.QLabel(self.t("cfg.dynamic_board_settings", "Board settings (loaded dynamically):"))
        self.dynamic_title_label = dynamic_title
        dynamic_title.setStyleSheet("font-style: italic;")
        form_layout.addWidget(dynamic_title)

        self.dynamic_scroll = QtWidgets.QScrollArea()
        self.dynamic_scroll.setWidgetResizable(True)
        self.dynamic_scroll.setMinimumHeight(240)
        self.dynamic_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.dynamic_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.dynamic_scroll_host = QtWidgets.QWidget()
        self.dynamic_scroll_host.setStyleSheet("background: transparent;")
        self.dynamic_form = QtWidgets.QVBoxLayout(self.dynamic_scroll_host)
        self.dynamic_form.setContentsMargins(4, 4, 4, 4)
        self.dynamic_form.setSpacing(8)
        self.dynamic_form.addStretch(1)
        self.dynamic_scroll.setWidget(self.dynamic_scroll_host)
        form_layout.addWidget(self.dynamic_scroll, 1)

        layout.addWidget(form_box, 1)

        self.btn_folder.clicked.connect(self.open_project_folder)
        self.btn_vscode.clicked.connect(self.open_vscode)
        self.btn_compile.clicked.connect(self.compile_project)
        self.btn_upload.clicked.connect(self.upload_project)
        self.btn_export.clicked.connect(self.export_binary)
        self.select_board_btn = None
        self.port_combo.currentTextChanged.connect(lambda *_: self.save_config())
        self.baud_combo.currentTextChanged.connect(lambda *_: self.save_config())
        self.tabs.addTab(tab, self.t("tab.code", "Code"))

    def _build_boards_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        buttons = QtWidgets.QHBoxLayout()
        self.boards_refresh_btn = QtWidgets.QPushButton("Atualizar")
        self.boards_select_btn = QtWidgets.QPushButton("Selecionar")
        self.boards_manager_btn = QtWidgets.QPushButton("Gerenciador")
        buttons.addWidget(self.boards_refresh_btn)
        buttons.addWidget(self.boards_select_btn)
        buttons.addWidget(self.boards_manager_btn)
        layout.addLayout(buttons)
        self.boards_table = QtWidgets.QTableWidget(0, 2)
        self.boards_table.setHorizontalHeaderLabels(["Placa", "FQBN"])
        self.boards_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.boards_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.boards_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.boards_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.boards_table, 1)
        self.board_updates_label = QtWidgets.QLabel("Atualizações pendentes: 0")
        self.board_updates_label.setObjectName("boardUpdatesLabel")
        layout.addWidget(self.board_updates_label)
        self.boards_refresh_btn.clicked.connect(self.load_boards)
        self.boards_select_btn.clicked.connect(self.select_board_from_table)
        self.boards_manager_btn.clicked.connect(self.open_board_manager)
        self.boards_table.itemDoubleClicked.connect(lambda *_: self.apply_selected_board())
        self.tabs.addTab(tab, self.t("tab.boards", "Boards"))

    def _build_docs_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        header = QtWidgets.QHBoxLayout()
        self.docs_refresh_btn = QtWidgets.QPushButton("Atualizar")
        self.docs_open_folder_btn = QtWidgets.QPushButton("Abrir DOCS")
        for widget in [self.docs_refresh_btn, self.docs_open_folder_btn]:
            header.addWidget(widget)
        header.addStretch(1)
        layout.addLayout(header)

        docs_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.docs_list = QtWidgets.QListWidget()
        self.docs_list.setIconSize(QtCore.QSize(28, 28))
        self.docs_list.setMinimumWidth(220)
        docs_split.addWidget(self.docs_list)
        docs_right = QtWidgets.QWidget()
        docs_right_layout = QtWidgets.QVBoxLayout(docs_right)
        self.docs_content = QtWidgets.QTextBrowser()
        self.docs_content.setOpenExternalLinks(True)
        docs_right_layout.addWidget(self.docs_content, 1)
        docs_split.addWidget(docs_right)
        docs_split.setStretchFactor(0, 2)
        docs_split.setStretchFactor(1, 7)
        docs_split.setSizes([260, 900])
        layout.addWidget(docs_split, 1)
        self.tabs.addTab(tab, "Docs")

        self.docs_refresh_btn.clicked.connect(self._refresh_docs_ui)
        self.docs_open_folder_btn.clicked.connect(self._open_docs_folder)
        self.docs_list.currentItemChanged.connect(lambda *_: self._show_selected_doc())

    def _build_libs_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        buttons = QtWidgets.QHBoxLayout()
        self.libs_refresh_btn = QtWidgets.QPushButton("Atualizar")
        self.libs_zip_btn = QtWidgets.QPushButton("ZIP")
        self.libs_manager_btn = QtWidgets.QPushButton("Gerenciador")
        buttons.addWidget(self.libs_refresh_btn)
        buttons.addWidget(self.libs_zip_btn)
        buttons.addWidget(self.libs_manager_btn)
        layout.addLayout(buttons)
        self.libs_table = QtWidgets.QTableWidget(0, 3)
        self.libs_table.setHorizontalHeaderLabels(["Biblioteca", "Versão", "Descrição"])
        self.libs_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.libs_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.libs_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.libs_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.libs_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.libs_table, 1)
        self.libs_updates_label = QtWidgets.QLabel("AtualizaÃ§Ãµes pendentes: 0")
        self.libs_updates_label.setObjectName("boardUpdatesLabel")
        self.libs_updates_label.setText("Atualizações pendentes: 0")
        layout.addWidget(self.libs_updates_label)
        self.libs_refresh_btn.clicked.connect(self.load_installed_libraries)
        self.libs_zip_btn.clicked.connect(self.install_library_zip)
        self.libs_manager_btn.clicked.connect(self.open_library_manager)
        self.tabs.addTab(tab, self.t("tab.libs", "Libraries"))

    def _build_git_tab(self):
        return self._build_git_tab_v2()

    def _build_git_tab_v2(self):
        return self._build_git_tab_v3()

    def _build_git_tab_v3(self):
        return self._build_git_tab_v4()

    def _build_git_tab_v4(self):
        return self._build_git_tab_v5()

    def _build_git_tab_v5(self):
        return self._build_git_tab_v6()

    def _build_git_tab_v6(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        info = QtWidgets.QLabel("Git do projeto atual com branch, arquivos alterados, histórico e diff.")
        self._mark_muted_label(info)
        layout.addWidget(info)

        self.git_views = QtWidgets.QTabWidget()
        self.git_views.setTabPosition(QtWidgets.QTabWidget.West)

        main_page = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main_page)
        top_row = QtWidgets.QHBoxLayout()
        self.git_branch_label = QtWidgets.QLabel("Branch: -")
        self.git_state_label = QtWidgets.QLabel("Estado: -")
        self.git_refresh_btn = QtWidgets.QPushButton("Atualizar")
        self.git_branch_combo = QtWidgets.QComboBox()
        self.git_checkout_branch_btn = QtWidgets.QPushButton("Trocar")
        self.git_new_branch_btn = QtWidgets.QPushButton("Nova branch")
        top_row.addWidget(self.git_branch_label)
        top_row.addWidget(self.git_state_label, 1)
        top_row.addWidget(QtWidgets.QLabel("Branch:"))
        top_row.addWidget(self.git_branch_combo, 1)
        top_row.addWidget(self.git_checkout_branch_btn)
        top_row.addWidget(self.git_new_branch_btn)
        top_row.addWidget(self.git_refresh_btn)
        main_layout.addLayout(top_row)

        commit_row = QtWidgets.QHBoxLayout()
        self.git_commit_edit = QtWidgets.QLineEdit()
        self.git_commit_edit.setPlaceholderText("Mensagem de commit")
        self.git_add_all_btn = QtWidgets.QPushButton("Add All")
        self.git_commit_btn = QtWidgets.QPushButton("Commit")
        commit_row.addWidget(QtWidgets.QLabel("Commit:"))
        commit_row.addWidget(self.git_commit_edit, 1)
        commit_row.addWidget(self.git_add_all_btn)
        commit_row.addWidget(self.git_commit_btn)
        main_layout.addLayout(commit_row)

        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_host = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_host)
        left_layout.addWidget(QtWidgets.QLabel("Arquivos alterados"))
        self.git_changed_files = QtWidgets.QTreeWidget()
        self.git_changed_files.setHeaderLabels(["Arquivo", "Estado"])
        self.git_changed_files.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.git_changed_files.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        left_layout.addWidget(self.git_changed_files, 1)
        left_layout.addWidget(QtWidgets.QLabel("Histórico"))
        self.git_commit_table = QtWidgets.QTableWidget(0, 4)
        self.git_commit_table.setHorizontalHeaderLabels(["Estado", "Hash", "Quando", "Mensagem"])
        self.git_commit_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.git_commit_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.git_commit_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        left_layout.addWidget(self.git_commit_table, 1)
        main_split.addWidget(left_host)

        right_host = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_host)
        self.git_selected_hash_label = QtWidgets.QLabel("Hash selecionada: -")
        diff_top = QtWidgets.QHBoxLayout()
        self.git_expand_diff_btn = QtWidgets.QPushButton("Expandir")
        diff_top.addWidget(self.git_selected_hash_label, 1)
        diff_top.addWidget(self.git_expand_diff_btn)
        right_layout.addLayout(diff_top)
        self.git_diff_view = QtWidgets.QTextBrowser()
        self.git_diff_view.setOpenExternalLinks(False)
        right_layout.addWidget(self.git_diff_view, 1)
        main_split.addWidget(right_host)
        main_split.setStretchFactor(0, 4)
        main_split.setStretchFactor(1, 5)
        main_split.setSizes([520, 680])
        main_layout.addWidget(main_split, 1)
        self.git_views.addTab(main_page, "Main")

        remote_page = QtWidgets.QWidget()
        remote_layout = QtWidgets.QVBoxLayout(remote_page)
        remote_summary = QtWidgets.QGridLayout()
        self.git_remote_label = QtWidgets.QLabel("Remote atual: -")
        self.git_remote_state_label = QtWidgets.QLabel("Remote: -")
        self.git_sync_label = QtWidgets.QLabel("Sincronização: -")
        self.git_sync_counts_label = QtWidgets.QLabel("Pendências: -")
        remote_summary.addWidget(self.git_remote_label, 0, 0, 1, 2)
        remote_summary.addWidget(self.git_remote_state_label, 1, 0)
        remote_summary.addWidget(self.git_sync_label, 1, 1)
        remote_summary.addWidget(self.git_sync_counts_label, 2, 0, 1, 2)
        remote_layout.addLayout(remote_summary)
        self.git_remote_edit = QtWidgets.QLineEdit()
        self.git_remote_edit.setPlaceholderText("Remote URL")
        self.git_set_remote_btn = QtWidgets.QPushButton("Set Remote")
        remote_row = QtWidgets.QHBoxLayout()
        remote_row.addWidget(QtWidgets.QLabel("Remote:"))
        remote_row.addWidget(self.git_remote_edit, 1)
        remote_row.addWidget(self.git_set_remote_btn)
        remote_layout.addLayout(remote_row)
        remote_buttons = QtWidgets.QHBoxLayout()
        self.git_status_btn = QtWidgets.QPushButton("Status")
        self.git_pull_btn = QtWidgets.QPushButton("Pull")
        self.git_push_btn = QtWidgets.QPushButton("Push")
        self.git_force_push_btn = QtWidgets.QPushButton("Force Push")
        self.git_fetch_btn = QtWidgets.QPushButton("Fetch")
        for widget in [self.git_status_btn, self.git_pull_btn, self.git_push_btn, self.git_force_push_btn, self.git_fetch_btn]:
            remote_buttons.addWidget(widget)
        remote_buttons.addStretch(1)
        remote_layout.addLayout(remote_buttons)
        remote_layout.addWidget(QtWidgets.QLabel("Histórico de operações"))
        self.git_remote_history = QtWidgets.QPlainTextEdit()
        self.git_remote_history.setReadOnly(True)
        remote_layout.addWidget(self.git_remote_history, 1)
        self.git_views.addTab(remote_page, "Remote")

        admin_page = QtWidgets.QWidget()
        admin_layout = QtWidgets.QVBoxLayout(admin_page)
        self.git_admin_views = QtWidgets.QTabWidget()
        self.git_admin_views.setTabPosition(QtWidgets.QTabWidget.West)

        admin_local_page = QtWidgets.QWidget()
        admin_local_layout = QtWidgets.QVBoxLayout(admin_local_page)
        admin_local_buttons = QtWidgets.QHBoxLayout()
        self.git_init_btn = QtWidgets.QPushButton("Init")
        self.git_delete_branch_btn = QtWidgets.QPushButton("Excluir branch")
        admin_local_buttons.addWidget(self.git_init_btn)
        admin_local_buttons.addWidget(self.git_delete_branch_btn)
        admin_local_buttons.addStretch(1)
        admin_local_layout.addLayout(admin_local_buttons)
        self.git_admin_target_combo = QtWidgets.QComboBox()
        self.git_admin_target_combo.setEditable(False)
        admin_local_layout.addWidget(self.git_admin_target_combo)
        self.git_admin_views.addTab(admin_local_page, "Local")

        admin_remote_page = QtWidgets.QWidget()
        admin_remote_layout = QtWidgets.QVBoxLayout(admin_remote_page)
        self.git_delete_remote_branch_btn = QtWidgets.QPushButton("Excluir branch remota")
        self.git_admin_remote_target_combo = QtWidgets.QComboBox()
        self.git_admin_remote_target_combo.setEditable(False)
        admin_remote_layout.addWidget(self.git_admin_remote_target_combo)
        admin_remote_layout.addWidget(self.git_delete_remote_branch_btn, 0, QtCore.Qt.AlignLeft)
        self.git_admin_views.addTab(admin_remote_page, "Remote")

        admin_reset_page = QtWidgets.QWidget()
        admin_reset_layout = QtWidgets.QVBoxLayout(admin_reset_page)
        self.git_reset_hard_btn = QtWidgets.QPushButton("Reset hard")
        self.git_reset_target_combo = QtWidgets.QComboBox()
        self.git_reset_target_combo.setEditable(True)
        self.git_reset_target_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        admin_reset_layout.addWidget(QtWidgets.QLabel("Hash/commit alvo"))
        admin_reset_layout.addWidget(self.git_reset_target_combo)
        admin_reset_layout.addWidget(self.git_reset_hard_btn, 0, QtCore.Qt.AlignLeft)
        self.git_admin_views.addTab(admin_reset_page, "Reset")

        admin_layout.addWidget(self.git_admin_views, 1)
        self.git_views.addTab(admin_page, "Admin")

        host_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.git_output = QtWidgets.QPlainTextEdit()
        self.git_output.setReadOnly(True)
        self.git_output.setMaximumWidth(360)
        host_split.addWidget(self.git_views)
        host_split.addWidget(self.git_output)
        host_split.setStretchFactor(0, 8)
        host_split.setStretchFactor(1, 3)
        host_split.setSizes([980, 320])
        layout.addWidget(host_split, 1)

        self.git_init_btn.clicked.connect(lambda: self._run_git_command(["init"]))
        self.git_status_btn.clicked.connect(lambda: self._run_git_command(["status", "--short", "--branch"]))
        self.git_add_all_btn.clicked.connect(lambda: self._run_git_command(["add", "."]))
        self.git_commit_btn.clicked.connect(self._git_commit)
        self.git_pull_btn.clicked.connect(lambda: self._run_git_command(["pull"]))
        self.git_push_btn.clicked.connect(lambda: self._run_git_command(["push"]))
        self.git_force_push_btn.clicked.connect(lambda: self._run_git_command(["push", "--force-with-lease"]))
        self.git_fetch_btn.clicked.connect(lambda: self._run_git_command(["fetch", "--all", "--prune"]))
        self.git_set_remote_btn.clicked.connect(self._git_set_remote)
        self.git_refresh_btn.clicked.connect(self._refresh_git_ui)
        self.git_commit_table.itemSelectionChanged.connect(self._update_git_selection_details)
        self.git_commit_table.customContextMenuRequested.connect(self._open_git_commit_context_menu)
        self.git_changed_files.itemSelectionChanged.connect(self._update_git_file_diff)
        self.git_new_branch_btn.clicked.connect(self._git_create_branch_from_selected)
        self.git_checkout_branch_btn.clicked.connect(self._git_checkout_branch)
        self.git_expand_diff_btn.clicked.connect(self._open_git_diff_dialog)
        self.git_delete_branch_btn.clicked.connect(self._git_delete_branch)
        self.git_delete_remote_branch_btn.clicked.connect(self._git_delete_remote_branch)
        self.git_reset_hard_btn.clicked.connect(self._git_reset_hard)
        self.tabs.addTab(tab, "Git")
        return

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        info = QtWidgets.QLabel("Git do projeto atual com branch, arquivos alterados, histórico e diff.")
        self._mark_muted_label(info)
        layout.addWidget(info)

        self.git_views = QtWidgets.QTabWidget()
        self.git_views.setTabPosition(QtWidgets.QTabWidget.West)

        main_page = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(main_page)
        top_row = QtWidgets.QHBoxLayout()
        self.git_branch_label = QtWidgets.QLabel("Branch: -")
        self.git_state_label = QtWidgets.QLabel("Estado: -")
        self.git_refresh_btn = QtWidgets.QPushButton("Atualizar")
        self.git_branch_combo = QtWidgets.QComboBox()
        self.git_checkout_branch_btn = QtWidgets.QPushButton("Trocar")
        self.git_new_branch_btn = QtWidgets.QPushButton("Nova branch")
        top_row.addWidget(self.git_branch_label)
        top_row.addWidget(self.git_state_label, 1)
        top_row.addWidget(QtWidgets.QLabel("Branch:"))
        top_row.addWidget(self.git_branch_combo, 1)
        top_row.addWidget(self.git_checkout_branch_btn)
        top_row.addWidget(self.git_new_branch_btn)
        top_row.addWidget(self.git_refresh_btn)
        main_layout.addLayout(top_row)

        commit_row = QtWidgets.QHBoxLayout()
        self.git_commit_edit = QtWidgets.QLineEdit()
        self.git_commit_edit.setPlaceholderText("Mensagem de commit")
        self.git_add_all_btn = QtWidgets.QPushButton("Add All")
        self.git_commit_btn = QtWidgets.QPushButton("Commit")
        commit_row.addWidget(QtWidgets.QLabel("Commit:"))
        commit_row.addWidget(self.git_commit_edit, 1)
        commit_row.addWidget(self.git_add_all_btn)
        commit_row.addWidget(self.git_commit_btn)
        main_layout.addLayout(commit_row)

        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_host = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_host)
        left_layout.addWidget(QtWidgets.QLabel("Arquivos alterados"))
        self.git_changed_files = QtWidgets.QTreeWidget()
        self.git_changed_files.setHeaderLabels(["Arquivo", "Estado"])
        self.git_changed_files.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.git_changed_files.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        left_layout.addWidget(self.git_changed_files, 1)
        left_layout.addWidget(QtWidgets.QLabel("Histórico"))
        self.git_commit_table = QtWidgets.QTableWidget(0, 4)
        self.git_commit_table.setHorizontalHeaderLabels(["Estado", "Hash", "Quando", "Mensagem"])
        self.git_commit_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.git_commit_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.git_commit_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        left_layout.addWidget(self.git_commit_table, 1)
        main_split.addWidget(left_host)

        right_host = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_host)
        hash_row = QtWidgets.QHBoxLayout()
        self.git_selected_hash_label = QtWidgets.QLabel("Hash selecionada: -")
        self.git_copy_hash_btn = QtWidgets.QPushButton("Copiar hash")
        self.git_copy_short_hash_btn = QtWidgets.QPushButton("Copiar curta")
        hash_row.addWidget(self.git_selected_hash_label, 1)
        hash_row.addWidget(self.git_copy_hash_btn)
        hash_row.addWidget(self.git_copy_short_hash_btn)
        right_layout.addLayout(hash_row)
        self.git_diff_mode_combo = QtWidgets.QComboBox()
        self.git_diff_mode_combo.addItem("Diff do arquivo", "file")
        self.git_diff_mode_combo.addItem("Diff do commit", "commit")
        right_layout.addWidget(self.git_diff_mode_combo, 0)
        self.git_diff_view = QtWidgets.QPlainTextEdit()
        self.git_diff_view.setReadOnly(True)
        right_layout.addWidget(self.git_diff_view, 1)
        main_split.addWidget(right_host)
        main_split.setStretchFactor(0, 4)
        main_split.setStretchFactor(1, 5)
        main_split.setSizes([520, 680])
        main_layout.addWidget(main_split, 1)
        self.git_views.addTab(main_page, "Main")

        remote_page = QtWidgets.QWidget()
        remote_layout = QtWidgets.QVBoxLayout(remote_page)
        remote_summary = QtWidgets.QGridLayout()
        self.git_remote_label = QtWidgets.QLabel("Remote atual: -")
        self.git_remote_state_label = QtWidgets.QLabel("Remote: -")
        self.git_sync_label = QtWidgets.QLabel("Sincronização: -")
        self.git_sync_counts_label = QtWidgets.QLabel("Pendências: -")
        remote_summary.addWidget(self.git_remote_label, 0, 0, 1, 2)
        remote_summary.addWidget(self.git_remote_state_label, 1, 0)
        remote_summary.addWidget(self.git_sync_label, 1, 1)
        remote_summary.addWidget(self.git_sync_counts_label, 2, 0, 1, 2)
        remote_layout.addLayout(remote_summary)
        self.git_remote_edit = QtWidgets.QLineEdit()
        self.git_remote_edit.setPlaceholderText("Remote URL")
        self.git_set_remote_btn = QtWidgets.QPushButton("Set Remote")
        remote_row = QtWidgets.QHBoxLayout()
        remote_row.addWidget(QtWidgets.QLabel("Remote:"))
        remote_row.addWidget(self.git_remote_edit, 1)
        remote_row.addWidget(self.git_set_remote_btn)
        remote_layout.addLayout(remote_row)
        remote_buttons = QtWidgets.QHBoxLayout()
        self.git_status_btn = QtWidgets.QPushButton("Status")
        self.git_pull_btn = QtWidgets.QPushButton("Pull")
        self.git_push_btn = QtWidgets.QPushButton("Push")
        self.git_force_push_btn = QtWidgets.QPushButton("Force Push")
        self.git_fetch_btn = QtWidgets.QPushButton("Fetch")
        for widget in [self.git_status_btn, self.git_pull_btn, self.git_push_btn, self.git_force_push_btn, self.git_fetch_btn]:
            remote_buttons.addWidget(widget)
        remote_buttons.addStretch(1)
        remote_layout.addLayout(remote_buttons)
        self.git_views.addTab(remote_page, "Remote")

        admin_page = QtWidgets.QWidget()
        admin_layout = QtWidgets.QVBoxLayout(admin_page)
        admin_buttons = QtWidgets.QHBoxLayout()
        self.git_init_btn = QtWidgets.QPushButton("Init")
        self.git_delete_branch_btn = QtWidgets.QPushButton("Excluir branch")
        self.git_delete_remote_branch_btn = QtWidgets.QPushButton("Excluir branch remota")
        self.git_reset_hard_btn = QtWidgets.QPushButton("Reset hard")
        for widget in [self.git_init_btn, self.git_delete_branch_btn, self.git_delete_remote_branch_btn, self.git_reset_hard_btn]:
            admin_buttons.addWidget(widget)
        admin_buttons.addStretch(1)
        admin_layout.addLayout(admin_buttons)
        self.git_admin_target_edit = QtWidgets.QLineEdit()
        self.git_admin_target_edit.setPlaceholderText("Branch, remote branch ou hash alvo")
        admin_layout.addWidget(self.git_admin_target_edit)
        admin_layout.addWidget(QtWidgets.QLabel("Área administrativa: ações perigosas, sempre com confirmação."))
        self.git_views.addTab(admin_page, "Admin")

        layout.addWidget(self.git_views, 1)

        self.git_output = QtWidgets.QPlainTextEdit()
        self.git_output.setReadOnly(True)
        layout.addWidget(self.git_output, 1)

        self.git_init_btn.setToolTip("Inicializa um repositório Git neste projeto.")
        self.git_status_btn.setToolTip("Mostra o status atual do repositório.")
        self.git_add_all_btn.setToolTip("Adiciona todas as alterações ao stage.")
        self.git_commit_btn.setToolTip("Cria um commit com a mensagem informada.")
        self.git_pull_btn.setToolTip("Busca e integra alterações do remote.")
        self.git_push_btn.setToolTip("Envia commits locais para o remote.")
        self.git_force_push_btn.setToolTip("Envia commits com --force-with-lease.")
        self.git_fetch_btn.setToolTip("Atualiza referências do remote sem mesclar.")
        self.git_set_remote_btn.setToolTip("Define ou substitui o remote origin.")
        self.git_delete_branch_btn.setToolTip("Exclui uma branch local.")
        self.git_delete_remote_branch_btn.setToolTip("Exclui uma branch no remote.")
        self.git_reset_hard_btn.setToolTip("Faz git reset --hard para a hash informada.")

        self.git_init_btn.clicked.connect(lambda: self._run_git_command(["init"]))
        self.git_status_btn.clicked.connect(lambda: self._run_git_command(["status", "--short", "--branch"]))
        self.git_add_all_btn.clicked.connect(lambda: self._run_git_command(["add", "."]))
        self.git_commit_btn.clicked.connect(self._git_commit)
        self.git_pull_btn.clicked.connect(lambda: self._run_git_command(["pull"]))
        self.git_push_btn.clicked.connect(lambda: self._run_git_command(["push"]))
        self.git_force_push_btn.clicked.connect(lambda: self._run_git_command(["push", "--force-with-lease"]))
        self.git_fetch_btn.clicked.connect(lambda: self._run_git_command(["fetch", "--all", "--prune"]))
        self.git_set_remote_btn.clicked.connect(self._git_set_remote)
        self.git_refresh_btn.clicked.connect(self._refresh_git_ui)
        self.git_commit_table.itemSelectionChanged.connect(self._update_git_selection_details)
        self.git_changed_files.itemSelectionChanged.connect(self._update_git_file_diff)
        self.git_copy_hash_btn.clicked.connect(lambda: self._copy_selected_git_hash(short=False))
        self.git_copy_short_hash_btn.clicked.connect(lambda: self._copy_selected_git_hash(short=True))
        self.git_new_branch_btn.clicked.connect(self._git_create_branch_from_selected)
        self.git_checkout_branch_btn.clicked.connect(self._git_checkout_branch)
        self.git_diff_mode_combo.currentIndexChanged.connect(self._refresh_git_diff_view)
        self.git_delete_branch_btn.clicked.connect(self._git_delete_branch)
        self.git_delete_remote_branch_btn.clicked.connect(self._git_delete_remote_branch)
        self.git_reset_hard_btn.clicked.connect(self._git_reset_hard)
        self.tabs.addTab(tab, "Git")
        return

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        info = QtWidgets.QLabel("Controle Git simples do projeto atual.")
        self._mark_muted_label(info)
        layout.addWidget(info)
        top = QtWidgets.QHBoxLayout()
        self.git_init_btn = QtWidgets.QPushButton("Init")
        self.git_status_btn = QtWidgets.QPushButton("Status")
        self.git_add_all_btn = QtWidgets.QPushButton("Add All")
        self.git_commit_btn = QtWidgets.QPushButton("Commit")
        self.git_pull_btn = QtWidgets.QPushButton("Pull")
        self.git_push_btn = QtWidgets.QPushButton("Push")
        self.git_force_push_btn = QtWidgets.QPushButton("Force Push")
        for widget in [self.git_init_btn, self.git_status_btn, self.git_add_all_btn, self.git_commit_btn, self.git_pull_btn, self.git_push_btn]:
            top.addWidget(widget)
        top.addWidget(self.git_force_push_btn)
        top.addStretch(1)
        layout.addLayout(top)

        remote_row = QtWidgets.QHBoxLayout()
        self.git_remote_edit = QtWidgets.QLineEdit()
        self.git_remote_edit.setPlaceholderText("Remote URL")
        self.git_set_remote_btn = QtWidgets.QPushButton("Set Remote")
        self.git_remote_label = QtWidgets.QLabel("Remote atual: -")
        remote_row.addWidget(QtWidgets.QLabel("Remote:"))
        remote_row.addWidget(self.git_remote_edit, 1)
        remote_row.addWidget(self.git_set_remote_btn)
        remote_row.addWidget(self.git_remote_label)
        layout.addLayout(remote_row)

        commit_row = QtWidgets.QHBoxLayout()
        self.git_commit_edit = QtWidgets.QLineEdit()
        self.git_commit_edit.setPlaceholderText("Mensagem de commit")
        commit_row.addWidget(QtWidgets.QLabel("Commit:"))
        commit_row.addWidget(self.git_commit_edit, 1)
        layout.addLayout(commit_row)

        self.git_output = QtWidgets.QPlainTextEdit()
        self.git_output.setReadOnly(True)
        layout.addWidget(self.git_output, 1)

        self.git_init_btn.setToolTip("Inicializa um repositório Git neste projeto.")
        self.git_status_btn.setToolTip("Mostra o status atual do repositório.")
        self.git_add_all_btn.setToolTip("Adiciona todas as alterações ao stage.")
        self.git_commit_btn.setToolTip("Cria um commit com a mensagem informada.")
        self.git_pull_btn.setToolTip("Busca e integra alterações do remote.")
        self.git_push_btn.setToolTip("Envia commits locais para o remote.")
        self.git_force_push_btn.setToolTip("Envia commits com --force-with-lease.")
        self.git_set_remote_btn.setToolTip("Define ou substitui o remote origin.")

        self.git_init_btn.clicked.connect(lambda: self._run_git_command(["init"]))
        self.git_status_btn.clicked.connect(lambda: self._run_git_command(["status", "--short", "--branch"]))
        self.git_add_all_btn.clicked.connect(lambda: self._run_git_command(["add", "."]))
        self.git_commit_btn.clicked.connect(self._git_commit)
        self.git_pull_btn.clicked.connect(lambda: self._run_git_command(["pull"]))
        self.git_push_btn.clicked.connect(lambda: self._run_git_command(["push"]))
        self.git_force_push_btn.clicked.connect(lambda: self._run_git_command(["push", "--force-with-lease"]))
        self.git_set_remote_btn.clicked.connect(self._git_set_remote)
        self.tabs.addTab(tab, "Git")

    def _build_serial_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.serial_views = QtWidgets.QTabWidget()
        self.serial_views.setTabPosition(QtWidgets.QTabWidget.West)
        monitor_page = QtWidgets.QWidget()
        monitor_layout = QtWidgets.QVBoxLayout(monitor_page)
        monitor_controls = QtWidgets.QHBoxLayout()
        self.serial_toggle_btn = QtWidgets.QPushButton(self.t("serial.connect", "Connect"))
        self.serial_stamp_btn = QtWidgets.QPushButton(self.t("serial.stamp_off", "Stamp time: OFF"))
        self.serial_clear_btn = QtWidgets.QPushButton(self.t("serial.clear", "Clear"))
        self.serial_export_btn = QtWidgets.QPushButton(self.t("serial.export", "Export"))
        self.serial_tx_btn = QtWidgets.QPushButton(self.t("serial.tx_off", "Log TX: OFF"))
        self.serial_decode_combo = QtWidgets.QComboBox()
        self.serial_decode_combo.addItems(["UTF-8", "HEX"])
        self.serial_status = QtWidgets.QLabel(self.t("serial.status_disconnected", "Status: disconnected"))
        for widget in [self.serial_toggle_btn, self.serial_stamp_btn, self.serial_clear_btn, self.serial_export_btn, self.serial_tx_btn]:
            monitor_controls.addWidget(widget)
        monitor_controls.addWidget(QtWidgets.QLabel(self.t("serial.decode", "Decode:")))
        monitor_controls.addWidget(self.serial_decode_combo)
        monitor_controls.addWidget(self.serial_status, 1)
        monitor_layout.addLayout(monitor_controls)

        self.serial_text = QtWidgets.QPlainTextEdit()
        self.serial_text.setObjectName("serialBox")
        self.serial_text.setReadOnly(True)
        monitor_layout.addWidget(self.serial_text, 1)

        send_row = QtWidgets.QHBoxLayout()
        self.serial_input = QtWidgets.QLineEdit()
        self.serial_send_btn = QtWidgets.QPushButton(">>")
        send_row.addWidget(QtWidgets.QLabel(self.t("serial.send", "Send:")))
        send_row.addWidget(self.serial_input, 1)
        send_row.addWidget(self.serial_send_btn)
        monitor_layout.addLayout(send_row)
        self.serial_views.addTab(monitor_page, "Monitor Serial")

        plot_page = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_page)
        plot_controls = QtWidgets.QHBoxLayout()
        self.serial_plot_toggle_btn = QtWidgets.QPushButton(self.t("serial.connect", "Connect"))
        self.serial_plot_clear_btn = QtWidgets.QPushButton("Limpar plot")
        self.serial_plot_decode_combo = QtWidgets.QComboBox()
        self.serial_plot_decode_combo.addItems(["UTF-8", "HEX"])
        self.serial_plot_type_combo = QtWidgets.QComboBox()
        self.serial_plot_type_combo.addItems(["line", "step", "scatter", "bar"])
        self.serial_plot_series_limit = QtWidgets.QSpinBox()
        self.serial_plot_series_limit.setRange(1, 12)
        self.serial_plot_series_limit.setValue(4)
        self.serial_plot_status = QtWidgets.QLabel(self.t("serial.status_disconnected", "Status: disconnected"))
        self.serial_plot_fps_label = QtWidgets.QLabel("RX FPS: 0.00")
        plot_controls.addWidget(self.serial_plot_toggle_btn)
        plot_controls.addWidget(self.serial_plot_clear_btn)
        plot_controls.addWidget(QtWidgets.QLabel(self.t("serial.decode", "Decode:")))
        plot_controls.addWidget(self.serial_plot_decode_combo)
        plot_controls.addWidget(QtWidgets.QLabel("Plot:"))
        plot_controls.addWidget(self.serial_plot_type_combo)
        plot_controls.addWidget(QtWidgets.QLabel("Variáveis:"))
        plot_controls.addWidget(self.serial_plot_series_limit)
        plot_controls.addWidget(self.serial_plot_fps_label)
        plot_controls.addWidget(self.serial_plot_status, 1)
        plot_layout.addLayout(plot_controls)

        plot_content = QtWidgets.QHBoxLayout()
        plot_side = QtWidgets.QVBoxLayout()
        self.serial_series_list = QtWidgets.QListWidget()
        plot_side.addWidget(QtWidgets.QLabel("Séries disponíveis"))
        plot_side.addWidget(self.serial_series_list, 1)
        self.serial_plot_widget = SerialPlotWidget()
        plot_content.addLayout(plot_side, 1)
        plot_content.addWidget(self.serial_plot_widget, 4)
        plot_layout.addLayout(plot_content, 1)
        self.serial_views.addTab(plot_page, "Plotter Serial")

        csv_page = QtWidgets.QWidget()
        csv_layout = QtWidgets.QVBoxLayout(csv_page)
        csv_controls = QtWidgets.QHBoxLayout()
        self.serial_csv_toggle_btn = QtWidgets.QPushButton(self.t("serial.connect", "Connect"))
        self.serial_rec_btn = QtWidgets.QPushButton("Gravar CSV")
        self.serial_stop_rec_btn = QtWidgets.QPushButton("Parar gravação")
        self.serial_csv_clear_btn = QtWidgets.QPushButton("Limpar CSV")
        self.serial_csv_decode_combo = QtWidgets.QComboBox()
        self.serial_csv_decode_combo.addItems(["UTF-8", "HEX"])
        self.serial_csv_status = QtWidgets.QLabel(self.t("serial.status_disconnected", "Status: disconnected"))
        self.serial_fps_label = QtWidgets.QLabel("RX FPS: 0.00")
        for widget in [self.serial_csv_toggle_btn, self.serial_rec_btn, self.serial_stop_rec_btn, self.serial_csv_clear_btn]:
            csv_controls.addWidget(widget)
        csv_controls.addWidget(QtWidgets.QLabel(self.t("serial.decode", "Decode:")))
        csv_controls.addWidget(self.serial_csv_decode_combo)
        csv_controls.addWidget(self.serial_fps_label)
        csv_controls.addWidget(self.serial_csv_status, 1)
        csv_layout.addLayout(csv_controls)

        self.serial_csv_summary = QtWidgets.QLabel("Aguardando CSV...")
        self.serial_csv_summary.setWordWrap(True)
        self.serial_csv_summary.setStyleSheet("padding: 8px 10px; border: 1px solid #c6d0da; border-radius: 10px; background: rgba(40,120,180,0.06);")
        csv_layout.addWidget(self.serial_csv_summary)
        csv_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.serial_csv_table = QtWidgets.QTableWidget(0, 0)
        self.serial_csv_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.serial_csv_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.serial_csv_table.horizontalHeader().setStretchLastSection(True)
        self.serial_csv_errors = QtWidgets.QPlainTextEdit()
        self.serial_csv_errors.setReadOnly(True)
        csv_split.addWidget(self.serial_csv_table)
        csv_split.addWidget(self.serial_csv_errors)
        csv_split.setStretchFactor(0, 4)
        csv_split.setStretchFactor(1, 1)
        csv_layout.addWidget(csv_split, 1)
        self.serial_views.addTab(csv_page, "Log Serial CSV")

        layout.addWidget(self.serial_views, 1)
        self.serial_toggle_btn.clicked.connect(self.serial_toggle)
        self.serial_plot_toggle_btn.clicked.connect(self.serial_toggle)
        self.serial_csv_toggle_btn.clicked.connect(self.serial_toggle)
        self.serial_stamp_btn.clicked.connect(self.toggle_serial_stamp)
        self.serial_clear_btn.clicked.connect(self.serial_clear)
        self.serial_plot_clear_btn.clicked.connect(self.serial_clear)
        self.serial_csv_clear_btn.clicked.connect(self.serial_clear)
        self.serial_export_btn.clicked.connect(self.serial_export)
        self.serial_tx_btn.clicked.connect(self.toggle_serial_tx)
        self.serial_rec_btn.clicked.connect(self.start_serial_recording)
        self.serial_stop_rec_btn.clicked.connect(self.stop_serial_recording)
        self.serial_send_btn.clicked.connect(self.serial_send)
        self.serial_input.returnPressed.connect(self.serial_send)
        self.serial_decode_combo.currentTextChanged.connect(self._sync_serial_decode_mode)
        self.serial_plot_decode_combo.currentTextChanged.connect(self._sync_serial_decode_mode)
        self.serial_csv_decode_combo.currentTextChanged.connect(self._sync_serial_decode_mode)
        self.serial_plot_type_combo.currentTextChanged.connect(self._refresh_live_plot)
        self.serial_plot_series_limit.valueChanged.connect(self._refresh_live_plot)
        self.serial_series_list.itemChanged.connect(lambda *_: self._refresh_live_plot())
        self.serial_stop_rec_btn.setEnabled(False)
        self.tabs.addTab(tab, self.t("tab.serial", "Serial"))

    def _build_cli_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        row = QtWidgets.QHBoxLayout()
        self.cli_input = QtWidgets.QLineEdit()
        self.cli_help_btn = QtWidgets.QPushButton(self.t("cli.help", "Help"))
        self.cli_execute_btn = QtWidgets.QPushButton(self.t("cli.execute", "Execute"))
        row.addWidget(QtWidgets.QLabel(self.t("cli.command", "Command:")))
        row.addWidget(self.cli_input, 1)
        row.addWidget(self.cli_help_btn)
        row.addWidget(self.cli_execute_btn)
        layout.addLayout(row)
        self.cli_text = QtWidgets.QPlainTextEdit()
        self.cli_text.setObjectName("cliBox")
        self.cli_text.setReadOnly(True)
        layout.addWidget(self.cli_text, 1)
        self.cli_help_btn.clicked.connect(self.show_cli_help)
        self.cli_execute_btn.clicked.connect(self.execute_cli)
        self.cli_input.returnPressed.connect(self.execute_cli)
        self.tabs.addTab(tab, "CLI")

    def _wrap_layout(self, layout):
        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        return widget

    def _build_setting_row(self, label_text: str, value_label: QtWidgets.QLabel, buttons: list):
        row = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(label_text)
        label.setMinimumWidth(150)
        if str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark":
            label.setStyleSheet("font-weight: 700; background: transparent; color: #f0f6fb; padding: 2px 0;")
        else:
            label.setStyleSheet("font-weight: 700; background: transparent; color: #12344d; padding: 2px 0;")
        value_label.setMinimumHeight(30)
        if str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark":
            value_label.setStyleSheet("border: 1px solid #334355; border-radius: 8px; background: #0f151c; color: #e5edf5; padding: 6px;")
        else:
            value_label.setStyleSheet("border: 1px solid #c6d0da; border-radius: 8px; background: white; color: #1e2933; padding: 6px;")
        row.addWidget(label)
        row.addWidget(value_label, 1)
        for text, callback in buttons:
            btn = QtWidgets.QPushButton(text)
            if text == "...":
                btn.setFixedWidth(36)
            row.addWidget(btn)
            btn.clicked.connect(callback)
        return row

    def _load_app_settings(self) -> dict:
        try:
            if self.app_settings_file.exists():
                with open(self.app_settings_file, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                    if isinstance(data, dict):
                        return self._ensure_app_setting_defaults(data)
        except Exception:
            pass
        return self._ensure_app_setting_defaults({})

    def _ensure_app_setting_defaults(self, settings: dict) -> dict:
        cfg = dict(settings or {})
        cfg.setdefault("editor_title", "VS Code")
        cfg.setdefault("editor_command", "code")
        cfg.setdefault("editor_button_color", "#0078d4")
        cfg.setdefault("theme", "light")
        cfg.setdefault("language", "auto")
        cfg.setdefault("tray_enabled", False)
        cfg.setdefault("minimize_to_tray", False)
        cfg.setdefault("close_to_tray", False)
        cfg.setdefault("startup_to_tray", False)
        cfg.setdefault("startup_width", 1280)
        cfg.setdefault("startup_height", 820)
        cfg.setdefault("single_instance", True)
        cfg.setdefault("aux_library_repo", "")
        cfg.setdefault("command_open_template", "vcli.cmd open \"{project}\"")
        cfg.setdefault("command_vscode_template", "vcli.cmd vscode \"{project}\"")
        cfg.setdefault("command_compile_template", "vcli.cmd compile \"{project}\"")
        cfg.setdefault("command_export_template", "vcli.cmd export \"{project}\"")
        cfg.setdefault("command_upload_template", "vcli.cmd upload \"{project}\" --port {port}")
        cfg.setdefault("inocli_mode", "bundled")
        cfg.setdefault("inocli_custom_path", "")
        return cfg

    def _save_app_settings(self):
        self.app_settings = self._ensure_app_setting_defaults(self.app_settings)
        try:
            with open(self.app_settings_file, "w", encoding="utf-8") as handle:
                json.dump(self.app_settings, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log(f"[WARN] Falha ao salvar settings: {exc}")

    def apply_app_settings_to_ui(self):
        settings = self._ensure_app_setting_defaults(self.app_settings)
        editor_title = str(settings.get("editor_title", "VS Code") or "VS Code").strip()
        editor_color = str(settings.get("editor_button_color", "#0078d4") or "#0078d4").strip()
        self._apply_startup_window_size(settings)
        self.btn_vscode.setText(editor_title)
        self.btn_vscode.setStyleSheet(
            f"background: {editor_color}; color: white; border: 1px solid {editor_color}; border-radius: 8px; padding: 7px 12px; font-weight: 600;"
        )
        if hasattr(self, "action_vscode"):
            self.action_vscode.setText(editor_title)
        self._apply_backend_cli_settings()
        self._sync_tray_settings()
        self._sanitize_widget_texts(self)

    def _apply_startup_window_size(self, settings: dict | None = None):
        settings = self._ensure_app_setting_defaults(settings or self.app_settings)
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
        width_limit = max(1000, available.width() - 40)
        height_limit = max(680, available.height() - 40)
        width = max(1000, min(int(settings.get("startup_width", 1280) or 1280), width_limit))
        height = max(680, min(int(settings.get("startup_height", 820) or 820), height_limit))
        self.resize(width, height)

    def _create_tray_icon(self):
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QtWidgets.QSystemTrayIcon(self)
        tray.setIcon(self.windowIcon() if not self.windowIcon().isNull() else QtGui.QIcon(str(self.app_icon_path)))
        tray.setToolTip("V CLI")
        menu = QtWidgets.QMenu(self)
        tray.setContextMenu(menu)
        self.tray_menu = menu
        self._refresh_tray_menu()
        tray.activated.connect(lambda reason: self._restore_from_tray() if reason == QtWidgets.QSystemTrayIcon.Trigger else None)
        self.tray_icon = tray
        self._sync_tray_settings()

    def _sync_tray_settings(self):
        if not getattr(self, "tray_icon", None):
            return
        settings = self._ensure_app_setting_defaults(self.app_settings)
        enabled = bool(settings.get("tray_enabled", True))
        self.tray_icon.setVisible(enabled)
        self._refresh_tray_menu()

    def _refresh_tray_menu(self):
        menu = getattr(self, "tray_menu", None)
        if menu is None:
            return
        menu.clear()
        show_action = menu.addAction("Mostrar janela")
        hide_action = menu.addAction("Ocultar na bandeja")
        show_action.triggered.connect(self._restore_from_tray)
        hide_action.triggered.connect(self.hide)

        menu.addSeparator()
        project_name = self.current_project.name if self.current_project else "Nenhum projeto aberto"
        project_action = menu.addAction(f"Projeto atual: {project_name}")
        project_action.setEnabled(False)

        compile_action = menu.addAction("Compilar")
        export_action = menu.addAction("Exportar binario")
        upload_action = menu.addAction("Upload")
        open_folder_action = menu.addAction("Abrir pasta do projeto")
        for action in [compile_action, export_action, upload_action, open_folder_action]:
            action.setEnabled(bool(self.current_project))
        compile_action.triggered.connect(self.compile_project)
        export_action.triggered.connect(self.export_binary)
        upload_action.triggered.connect(self.upload_project)
        open_folder_action.triggered.connect(self.open_project_folder)

        recent_menu = menu.addMenu("Projetos recentes")
        if self.recent_projects:
            for path in self.recent_projects[:8]:
                project_path = Path(path)
                label = project_path.name
                if self.current_project and project_path == self.current_project:
                    label = f"{label}  [aberto]"
                action = recent_menu.addAction(label)
                action.setToolTip(str(project_path))
                action.triggered.connect(lambda checked=False, p=str(project_path): self.load_project_path(p))
        else:
            empty_action = recent_menu.addAction("Sem projetos recentes")
            empty_action.setEnabled(False)

        menu.addSeparator()
        quit_action = menu.addAction("Sair")
        quit_action.triggered.connect(self._quit_from_tray)

    def _restore_from_tray(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting_from_tray = True
        if getattr(self, "tray_icon", None):
            self.tray_icon.hide()
        self.close()

    def _bundled_inocli_path(self) -> Path:
        return self.app_base_dir / "arduino-cli.exe"

    def _resolve_inocli_path(self) -> Path:
        settings = self._ensure_app_setting_defaults(self.app_settings)
        mode = str(settings.get("inocli_mode", "bundled") or "bundled").strip()
        if mode == "path":
            found = shutil.which("arduino-cli")
            if found:
                return Path(found)
        if mode == "custom":
            custom = str(settings.get("inocli_custom_path", "") or "").strip()
            if custom:
                return Path(custom)
        return self._bundled_inocli_path()

    def _apply_backend_cli_settings(self):
        if not self.backend:
            return
        self.backend.cli_path = self._resolve_inocli_path()

    def _editor_command(self) -> str:
        return str(self.app_settings.get("editor_command", "code") or "code").strip()

    def _command_templates(self) -> dict:
        return {
            "open": str(self.app_settings.get("command_open_template", 'vcli.cmd open "{project}"')),
            "vscode": str(self.app_settings.get("command_vscode_template", 'vcli.cmd vscode "{project}"')),
            "compile": str(self.app_settings.get("command_compile_template", 'vcli.cmd compile "{project}"')),
            "export": str(self.app_settings.get("command_export_template", 'vcli.cmd export "{project}"')),
            "upload": str(self.app_settings.get("command_upload_template", 'vcli.cmd upload "{project}" --port {port}')),
        }

    def _is_vcli_registered_on_path(self) -> bool:
        path_env = os.getenv("PATH", "")
        base_dir = str(self.app_base_dir).lower()
        for entry in path_env.split(os.pathsep):
            if entry.strip().lower() == base_dir:
                return True
        return False

    def _set_vcli_path_registration(self, enabled: bool) -> tuple[bool, str]:
        if winreg is None:
            return False, "Registro do Windows não disponível."
        try:
            base_dir = str(self.app_base_dir)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                current_path, _ = winreg.QueryValueEx(key, "Path")
                entries = [entry for entry in str(current_path or "").split(os.pathsep) if entry.strip()]
                normalized = [entry.lower() for entry in entries]
                if enabled and base_dir.lower() not in normalized:
                    entries.append(base_dir)
                if not enabled:
                    entries = [entry for entry in entries if entry.strip().lower() != base_dir.lower()]
                new_value = os.pathsep.join(entries)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_value)
            os.environ["PATH"] = new_value
            return True, "PATH atualizado com sucesso."
        except FileNotFoundError:
            return False, "Chave de ambiente não encontrada."
        except Exception as exc:
            return False, str(exc)

    def _list_project_source_files(self) -> list:
        if not self.current_project or not self.current_project.exists():
            return []
        allowed = {".ino", ".h", ".hpp", ".c", ".cpp", ".txt"}
        files = []
        for path in self.current_project.rglob("*"):
            if path.is_file() and path.suffix.lower() in allowed:
                try:
                    rel = path.relative_to(self.current_project)
                except Exception:
                    rel = path.name
                files.append(str(rel).replace("\\", "/"))
        return sorted(files)

    def _extract_version_variables(self, relative_file: str) -> list:
        if not self.current_project or not relative_file:
            return []
        target = self.current_project / relative_file
        if not target.exists():
            return []
        content = target.read_text(encoding="utf-8", errors="replace")
        found = []
        patterns = [
            (r'#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+"([^"]*)"', "string"),
            (r'#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([0-9]+)', "number"),
            (r'\b(?:static\s+)?(?:const\s+)?char\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\d*\s*\]\s*=\s*"([^"]*)"', "string"),
            (r'\b(?:const\s+)?char\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*\]\s*=\s*"([^"]*)"', "string"),
            (r'\b(?:const\s+char\s*\*|char\s*\*|String|std::string)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', "string"),
            (r'\b(?:constexpr\s+auto|const\s+auto|auto)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', "string"),
            (r'\bString\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*"([^"]*)"\s*\)', "string"),
            (r'\b(?:int|long|unsigned\s+int|unsigned\s+long)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([0-9]+)', "number"),
            (r'\b(?:uint8_t|uint16_t|uint32_t|size_t)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([0-9]+)', "number"),
            (r'\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*?(?:\d+\.\d+\.\d+|\d+)[^"]*)"', "string"),
            (r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([0-9]+)", "number"),
        ]
        for pattern, kind in patterns:
            for match in re.finditer(pattern, content):
                name = match.group(1)
                value = match.group(2)
                found.append({
                    "name": name,
                    "kind": kind,
                    "preview": f"{name} = {value}",
                })
        unique = []
        seen = set()
        for item in found:
            key = (item["name"], item["kind"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        scored = []
        for item in unique:
            score = 0
            name_upper = item["name"].upper()
            if "VER" in name_upper:
                score += 4
            if "VERSION" in name_upper:
                score += 6
            if "FW" in name_upper or "ECU" in name_upper:
                score += 2
            if re.search(r"\d+\.\d+\.\d+|\d+", item["preview"]):
                score += 3
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
        return [item for _, item in scored]

    def _update_project_actions_enabled(self, enabled: bool):
        for widget in [self.btn_folder, self.btn_vscode, self.btn_compile, self.btn_upload, self.btn_export]:
            widget.setEnabled(enabled)
        for action in [
            getattr(self, "action_open_folder", None),
            getattr(self, "action_vscode", None),
            getattr(self, "action_compile", None),
            getattr(self, "action_upload", None),
            getattr(self, "action_export", None),
            getattr(self, "action_properties", None),
            getattr(self, "action_export_project_settings", None),
            getattr(self, "action_import_project_settings", None),
            getattr(self, "action_open_csv_log", None),
            getattr(self, "action_code_editor", None),
            getattr(self, "action_docs_editor", None),
        ]:
            if action:
                action.setEnabled(enabled)

    def _ensure_project_property_defaults(self):
        if not self.current_config:
            return {}
        props = self.current_config.setdefault("properties", {})
        props.setdefault("author", "")
        props.setdefault("version", "1.0.0")
        props.setdefault("contributors", "")
        props.setdefault("description", "")
        props.setdefault("icon", "")
        props.setdefault("autoversion_mode", "disabled")
        props.setdefault("autoversion_file", "")
        props.setdefault("autoversion_variable", "VERSION")
        props.setdefault("autoversion_kind", "string")
        props.setdefault("autoversion_value_mode", "increment")
        props.setdefault("autoversion_lua_script", self._default_autoversion_lua_script())
        props.setdefault("batch_mode", "disabled")
        props.setdefault("batch_file", "")
        props.setdefault("batch_variable", "LOT")
        props.setdefault("batch_kind", "string")
        props.setdefault("batch_value_mode", "preset")
        props.setdefault("batch_pattern", "date_time")
        props.setdefault("batch_lua_script", self._default_batch_lua_script())
        props["compile_questions"] = self._normalize_compile_questions(props.get("compile_questions", []))
        return props

    def _default_batch_lua_script(self) -> str:
        return (
            "-- Use a tabela ctx para montar o lote.\n"
            "-- Campos úteis: ctx.date_compact, ctx.time_compact, ctx.timestamp_compact,\n"
            "-- ctx.year, ctx.month, ctx.day, ctx.hour, ctx.minute, ctx.second,\n"
            "-- ctx.iso_week, ctx.project_name, ctx.version, ctx.action.\n"
            "return ctx.timestamp_compact\n"
        )

    def _default_autoversion_lua_script(self) -> str:
        return (
            "-- Recebe ctx.current_value, ctx.kind, ctx.action e dados de tempo.\n"
            "-- Retorne a nova versão como string.\n"
            "return ctx.current_value\n"
        )

    def _autoversion_strategy_label(self, mode: str) -> str:
        labels = {
            "increment": "Incremento simples",
            "year_semver": "Ano + revisao incremental",
            "iso_week": "Ano + semana ISO + revisao",
            "lua": "Script Lua personalizado",
        }
        return labels.get(str(mode or "").strip(), str(mode or "increment"))

    def _generate_year_semver_value(self, old_value: str, now: datetime | None = None) -> str:
        current = now or datetime.now()
        current_year = current.year
        parts = [int(piece) for piece in re.findall(r"\d+", str(old_value or ""))]
        if parts and parts[0] == current_year:
            revision = parts[-1] + 1 if len(parts) > 1 else 1
        else:
            revision = 1
        return f"{current_year}.{revision}"

    def _generate_iso_week_value(self, old_value: str, now: datetime | None = None) -> str:
        current = now or datetime.now()
        iso_year, iso_week, _ = current.isocalendar()
        match = re.search(r"(\d{4})[.\-_W]*(\d{1,2})[.\-_]*(\d+)$", str(old_value or ""))
        revision = 1
        if match:
            old_year = int(match.group(1))
            old_week = int(match.group(2))
            if old_year == iso_year and old_week == iso_week:
                revision = int(match.group(3)) + 1
        return f"{iso_year}.W{iso_week:02d}.{revision}"

    def _describe_autoversion_delta(self, old_value: str, new_value: str) -> str:
        if not old_value:
            return f"Novo valor inicial: {new_value}"
        if old_value == new_value:
            return "Sem alteracao detectada para o proximo passo."
        return f"Proximo incremento esperado: {old_value} -> {new_value}"

    def _normalize_compile_questions(self, questions) -> list:
        items = list(questions or [])
        normalized = []
        for index in range(4):
            raw = items[index] if index < len(items) and isinstance(items[index], dict) else {}
            normalized.append({
                "enabled": bool(raw.get("enabled", False)),
                "label": str(raw.get("label", "") or "").strip(),
                "file": str(raw.get("file", "") or "").strip(),
                "variable": str(raw.get("variable", "") or "").strip(),
                "kind": str(raw.get("kind", "string") or "string").strip(),
                "options_text": str(raw.get("options_text", "") or "").strip(),
                "allow_keep": bool(raw.get("allow_keep", True)),
            })
        return normalized

    def _split_configured_values(self, raw_text: str) -> list[str]:
        values = []
        for token in re.split(r"[\r\n,;|]+", str(raw_text or "")):
            cleaned = token.strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
        return values

    def _build_time_context(self, action_name: str = "", now: datetime | None = None) -> dict:
        props = self._ensure_project_property_defaults()
        current = now or datetime.now()
        iso_year, iso_week, iso_weekday = current.isocalendar()
        return {
            "action": str(action_name or "").strip(),
            "project_name": self.current_project.name if self.current_project else "",
            "version": str(props.get("version", "1.0.0") or "1.0.0").strip(),
            "year": current.year,
            "month": current.month,
            "day": current.day,
            "hour": current.hour,
            "minute": current.minute,
            "second": current.second,
            "microsecond": current.microsecond,
            "weekday": current.isoweekday(),
            "day_of_year": int(current.strftime("%j")),
            "iso_year": iso_year,
            "iso_week": iso_week,
            "iso_weekday": iso_weekday,
            "date_compact": current.strftime("%Y%m%d"),
            "time_compact": current.strftime("%H%M%S"),
            "timestamp_compact": current.strftime("%Y%m%d-%H%M%S"),
            "iso_stamp": current.isoformat(timespec="seconds"),
        }

    def _generate_batch_value(self, action_name: str = "export", now: datetime | None = None) -> str:
        props = self._ensure_project_property_defaults()
        current = now or datetime.now()
        value_mode = str(props.get("batch_value_mode", "preset") or "preset").strip()
        pattern = str(props.get("batch_pattern", "date_time") or "date_time").strip()
        if value_mode != "lua":
            if pattern == "date":
                return current.strftime("%Y%m%d")
            if pattern == "time":
                return current.strftime("%H%M%S")
            if pattern == "iso_week":
                return current.strftime("%Y-W%W-%u")
            return current.strftime("%Y%m%d-%H%M%S")
        if LuaRuntime is None:
            raise RuntimeError("Lupa/Lua não está disponível nesta instalação.")
        script = str(props.get("batch_lua_script", "") or "").strip() or self._default_batch_lua_script()
        context = self._build_time_context(action_name=action_name, now=current)
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals()["ctx"] = lua.table_from(context)
        lua.globals()["strftime"] = lambda fmt: current.strftime(str(fmt))
        result = lua.execute(script)
        if result is None:
            generator = getattr(lua.globals(), "generate", None)
            if generator is not None:
                result = generator(lua.table_from(context))
        if result is None:
            raise ValueError("O script Lua não retornou nenhum valor.")
        return str(result).strip()

    def _run_batch_autofill(self, action_name: str) -> tuple[bool, str]:
        if not self.current_project or not self.current_config:
            return True, ""
        props = self._ensure_project_property_defaults()
        mode = str(props.get("batch_mode", "disabled") or "disabled").strip()
        allowed = {
            "always_export": {"export"},
            "always_upload": {"upload"},
            "always_both": {"export", "upload"},
            "ask_export": {"export"},
            "ask_upload": {"upload"},
            "ask_both": {"export", "upload"},
        }.get(mode, set())
        if action_name not in allowed:
            return True, ""
        if mode.startswith("ask_"):
            label = "exportar binário" if action_name == "export" else "fazer upload"
            answer = QtWidgets.QMessageBox.question(self, "Lote", f"Deseja atualizar o lote antes de {label}?")
            if answer != QtWidgets.QMessageBox.Yes:
                return True, ""
        batch_file = str(props.get("batch_file", "") or "").strip()
        batch_variable = str(props.get("batch_variable", "LOT") or "LOT").strip()
        batch_kind = str(props.get("batch_kind", "string") or "string").strip()
        if not batch_file or not batch_variable:
            return False, "Configure o arquivo e a variável do lote antes de exportar."
        try:
            batch_value = self._generate_batch_value(action_name=action_name)
        except Exception as exc:
            return False, f"Falha ao gerar o lote: {exc}"
        if batch_kind == "number" and not str(batch_value).isdigit():
            return False, f"O lote está configurado como número, mas o script gerou '{batch_value}'."
        target_file = (self.current_project / batch_file).resolve()
        try:
            updated = self._update_version_in_source_file(target_file, batch_variable, batch_value, value_kind=batch_kind)
        except Exception as exc:
            return False, f"Falha ao atualizar o lote: {exc}"
        if not updated:
            return False, f"Não encontrei a variável '{batch_variable}' em '{batch_file}' para atualizar o lote."
        self.log(f"[LOTE] {batch_variable} -> {batch_value}")
        return True, batch_value

    def _run_precompile_questions(self) -> tuple[bool, str]:
        if not self.current_project or not self.current_config:
            return True, ""
        props = self._ensure_project_property_defaults()
        questions = self._normalize_compile_questions(props.get("compile_questions", []))
        changed = False
        for index, question in enumerate(questions, start=1):
            if not question.get("enabled"):
                continue
            relative_file = str(question.get("file", "") or "").strip()
            variable_name = str(question.get("variable", "") or "").strip()
            value_kind = str(question.get("kind", "string") or "string").strip()
            option_values = self._split_configured_values(question.get("options_text", ""))
            if not relative_file or not variable_name or not option_values:
                continue
            if value_kind == "number":
                invalid_choices = [value for value in option_values if not str(value).isdigit()]
                if invalid_choices:
                    return False, (
                        f"A pergunta '{question.get('label') or f'Pergunta {index}'}' esta marcada como numerica, "
                        f"mas possui valores invalidos: {', '.join(invalid_choices[:5])}"
                    )
            current_value = self._read_version_from_source_file((self.current_project / relative_file).resolve(), variable_name, value_kind=value_kind)
            prompt_label = str(question.get("label", "") or "").strip() or f"Pergunta {index}"
            choices = list(option_values)
            keep_label = f"Manter valor atual ({current_value or 'vazio'})"
            default_index = 0
            if question.get("allow_keep", True):
                choices.insert(0, keep_label)
            elif current_value and current_value in choices:
                default_index = choices.index(current_value)
            prompt_text = (
                f"{prompt_label}\n\n"
                f"Arquivo: {relative_file}\n"
                f"Campo: {variable_name}\n"
                f"Tipo: {value_kind}\n"
                f"Valor atual: {current_value or 'vazio'}"
            )
            selected, ok = QtWidgets.QInputDialog.getItem(
                self,
                "Perguntas de compilação",
                prompt_text,
                choices,
                default_index,
                False,
            )
            if not ok:
                return False, "Compilação cancelada nas perguntas pré-compilação."
            if question.get("allow_keep", True) and selected == keep_label:
                continue
            target_file = (self.current_project / relative_file).resolve()
            try:
                updated = self._update_version_in_source_file(target_file, variable_name, selected, value_kind=value_kind)
            except Exception as exc:
                return False, f"Falha ao aplicar '{prompt_label}': {exc}"
            if not updated:
                return False, f"Não encontrei o campo configurado para '{prompt_label}' em '{relative_file}'."
            self.log(f"[BUILD-QUESTION] {prompt_label} -> {selected}")
            changed = True
        if changed:
            self.save_config()
        return True, ""

    def _read_version_from_source_file(self, target_file: Path, variable_name: str, value_kind: str = "string") -> str:
        if not target_file.exists():
            return ""
        content = target_file.read_text(encoding="utf-8", errors="replace")
        escaped_var = re.escape(variable_name.strip() or "VERSION")
        patterns = [
            rf'((?:const\s+)?char\s+{escaped_var}\s*\[\s*\]\s*=\s*")([^"]*)(")',
            rf'(#define\s+{escaped_var}\s+")([^"]*)(")',
            rf'(\b{escaped_var}\b\s*=\s*")([^"]*)(")',
            rf"(\b{escaped_var}\b\s*=\s*')([^']*)(')",
            rf'(#define\s+{escaped_var}\s+)([0-9]+)',
            rf'(\b{escaped_var}\b\s*=\s*)([0-9]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return str(match.group(2)).strip()
        return ""

    def _sync_version_from_source(self):
        if not self.current_project or not self.current_config:
            return
        props = self._ensure_project_property_defaults()
        version_file = str(props.get("autoversion_file", "") or "").strip()
        version_variable = str(props.get("autoversion_variable", "VERSION") or "VERSION").strip()
        value_kind = str(props.get("autoversion_kind", "string") or "string").strip()
        if not version_file:
            return
        source_value = self._read_version_from_source_file(self.current_project / version_file, version_variable, value_kind=value_kind)
        if source_value:
            props["version"] = source_value

    def _refresh_git_ui(self):
        if not self.git_available or not hasattr(self, "git_init_btn"):
            return
        project_ready = bool(self.current_project)
        repo_ready = False
        if project_ready:
            ok_git, _, _ = self._git_capture(["rev-parse", "--git-dir"])
            repo_ready = ok_git
        current_remote = "-"
        if repo_ready:
            _, current_remote_out, _ = self._git_capture(["remote", "get-url", "origin"])
            current_remote = current_remote_out or "-"

        self.git_init_btn.setVisible(not repo_ready)
        for widget in [
            self.git_status_btn,
            self.git_add_all_btn,
            self.git_commit_btn,
            self.git_pull_btn,
            self.git_push_btn,
            self.git_force_push_btn,
            self.git_set_remote_btn,
            self.git_remote_edit,
            getattr(self, "git_fetch_btn", None),
            getattr(self, "git_refresh_btn", None),
        ]:
            if widget:
                widget.setVisible(repo_ready or widget in {getattr(self, "git_refresh_btn", None)})
                widget.setEnabled(project_ready)
        self.git_remote_label.setText(f"Remote atual: {current_remote}")
        if hasattr(self, "git_remote_history"):
            self.git_remote_history.clear()
            self.git_remote_history.appendPlainText(f"Remote: {current_remote}")

        if hasattr(self, "git_branch_combo"):
            self.git_branch_combo.blockSignals(True)
            self.git_branch_combo.clear()
            ok_branches, branches_out, _ = self._git_capture(["branch", "--format", "%(refname:short)"])
            if ok_branches:
                branches = [line.strip() for line in branches_out.splitlines() if line.strip()]
                for branch in branches:
                    self.git_branch_combo.addItem(branch)
            self.git_branch_combo.blockSignals(False)
        if hasattr(self, "git_admin_target_combo"):
            self.git_admin_target_combo.clear()
        if hasattr(self, "git_admin_remote_target_combo"):
            self.git_admin_remote_target_combo.clear()

        if hasattr(self, "git_commit_table"):
            self.git_commit_table.setRowCount(0)
        if hasattr(self, "git_changed_files"):
            self.git_changed_files.clear()
        if hasattr(self, "git_diff_view"):
            self.git_diff_view.clear()
        if hasattr(self, "git_branch_label"):
            self.git_branch_label.setText("Branch: -")
            self.git_state_label.setText("Estado: sem repositório")
            self.git_selected_hash_label.setText("Hash selecionada: -")
            self.git_state_label.setStyleSheet("color: #7a4f01; font-weight: 600;")
        if hasattr(self, "git_reset_target_combo"):
            self.git_reset_target_combo.clear()
        if hasattr(self, "git_sync_label"):
            self.git_sync_label.setText("Sincronização: -")
            self.git_sync_counts_label.setText("Pendências: -")
            self.git_remote_state_label.setText(f"Remote: {current_remote}")

        if not repo_ready:
            return

        _, branch_name, _ = self._git_capture(["branch", "--show-current"])
        _, status_out, _ = self._git_capture(["status", "--short", "--branch"])
        changed_lines = [line for line in status_out.splitlines() if line and not line.startswith("##")]
        upstream = ""
        ok_upstream, upstream_out, _ = self._git_capture(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if ok_upstream:
            upstream = upstream_out.strip()
        ahead = behind = 0
        if upstream:
            ok_counts, counts_out, _ = self._git_capture(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
            if ok_counts:
                parts = counts_out.split()
                if len(parts) >= 2:
                    behind = int(parts[0] or "0")
                    ahead = int(parts[1] or "0")

        sync_text = "sem upstream"
        if upstream:
            if ahead == 0 and behind == 0:
                sync_text = "sincronizado com o remote"
            else:
                sync_text = f"{ahead} local / {behind} remoto"
        self.git_branch_label.setText(f"Branch: {branch_name or '-'}")
        if hasattr(self, "git_branch_combo"):
            self.git_branch_combo.setCurrentIndex(max(0, self.git_branch_combo.findText(branch_name or "")))
        if hasattr(self, "git_admin_target_combo"):
            for idx in range(self.git_branch_combo.count()):
                branch = self.git_branch_combo.itemText(idx)
                if branch and branch != branch_name:
                    self.git_admin_target_combo.addItem(branch)
        repo_clean = not changed_lines
        self.git_state_label.setText("Estado: limpo" if repo_clean else f"Estado: {len(changed_lines)} alteração(ões)")
        self.git_state_label.setStyleSheet(
            "color: #0b6e4f; font-weight: 700;" if repo_clean else "color: #7a4f01; font-weight: 700;"
        )
        self.git_sync_label.setText(f"Sincronização: {sync_text}")
        self.git_sync_counts_label.setText(f"Pendências: {ahead} push / {behind} pull")
        self.git_sync_counts_label.setStyleSheet(
            "color: #0b6e4f; font-weight: 600;" if ahead == 0 and behind == 0 else "color: #355c7d; font-weight: 600;"
        )
        self.git_remote_state_label.setText(f"Remote: {current_remote}")
        if hasattr(self, "git_remote_history"):
            self.git_remote_history.appendPlainText(f"Branch atual: {branch_name or '-'}")
            self.git_remote_history.appendPlainText(f"Sincronização: {sync_text}")
            self.git_remote_history.appendPlainText(f"Pendências: {ahead} push / {behind} pull")
        if hasattr(self, "git_admin_remote_target_combo") and upstream:
            upstream_branch = upstream.split("/", 1)[-1] if "/" in upstream else upstream
            if upstream_branch:
                self.git_admin_remote_target_combo.addItem(upstream_branch)

        if hasattr(self, "git_changed_files"):
            ok_name_status, name_status_out, _ = self._git_capture(["status", "--short"])
            if ok_name_status:
                for raw_line in name_status_out.splitlines():
                    if not raw_line.strip():
                        continue
                    status_code = raw_line[:2].strip() or "?"
                    file_name = raw_line[3:].strip()
                    item = QtWidgets.QTreeWidgetItem([file_name, status_code])
                    item.setData(0, QtCore.Qt.UserRole, file_name)
                    item.setToolTip(0, f"{status_code}  {file_name}")
                    item.setToolTip(1, "Estado Git do arquivo selecionado.")
                    if any(flag in status_code for flag in ["M", "A", "R", "D", "??"]):
                        item.setForeground(1, QtGui.QColor("#7a4f01"))
                    self.git_changed_files.addTopLevelItem(item)

        if hasattr(self, "git_commit_table"):
            _, log_out, _ = self._git_capture(["log", "--pretty=format:%H%x1f%h%x1f%cr%x1f%s", "--max-count", "30", "HEAD"])
            remote_shas = set()
            if upstream:
                _, remote_log, _ = self._git_capture(["rev-list", "--max-count", "200", upstream])
                remote_shas = {line.strip() for line in remote_log.splitlines() if line.strip()}
            rows = [line for line in log_out.splitlines() if line.strip()]
            self.git_commit_table.setRowCount(len(rows))
            for row_index, line in enumerate(rows):
                sha, short_sha, rel_time, subject = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
                state = "Sim"
                if upstream and sha not in remote_shas:
                    state = "Só local"
                elif not upstream:
                    state = "Sem remote"
                values = [state, short_sha, rel_time, subject]
                for col_index, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    if col_index == 1:
                        item.setData(QtCore.Qt.UserRole, sha)
                    self.git_commit_table.setItem(row_index, col_index, item)
                if hasattr(self, "git_reset_target_combo"):
                    self.git_reset_target_combo.addItem(f"{short_sha}  {subject}", sha)
        self._refresh_git_diff_view()

    def _git_capture(self, args: list[str], timeout: int = 20) -> tuple[bool, str, str]:
        if not self.current_project:
            return False, "", "Projeto não aberto"
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.current_project),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0, (result.stdout or "").strip(), (result.stderr or "").strip()
        except Exception as exc:
            return False, "", str(exc)

    def _docs_dir(self, create: bool = False) -> Path | None:
        if not self.current_project:
            return None
        docs_dir = self.current_project / "DOCS"
        if create:
            docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir

    def _doc_image_for_markdown(self, md_path: Path) -> Path | None:
        for ext in [".png", ".ico", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]:
            candidate = md_path.with_suffix(ext)
            if candidate.exists():
                return candidate
        return None

    def _parse_doc_file(self, md_path: Path) -> tuple[dict, str]:
        raw = md_path.read_text(encoding="utf-8", errors="replace")
        metadata = {}
        body = raw
        if raw.startswith("---") and "\n---" in raw[3:]:
            try:
                second_sep = raw.find("\n---", 3)
                if second_sep > 0:
                    meta_text = raw[4:second_sep]
                    body = raw[second_sep + 4:].lstrip("\r\n")
                    if yaml is not None:
                        parsed = yaml.safe_load(meta_text) or {}
                        if isinstance(parsed, dict):
                            metadata = parsed
            except Exception:
                metadata = {}
                body = raw
        return metadata, body

    def _markdown_inline_to_html(self, text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img alt="\1" src="\2" style="max-width:100%; border-radius:10px; margin:8px 0;">', escaped)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", escaped)
        escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
        return escaped

    def _markdown_to_html_simple(self, markdown_text: str) -> str:
        lines = markdown_text.replace("\r\n", "\n").split("\n")
        html_parts = []
        in_code = False
        code_lang = ""
        in_list = False
        paragraph = []

        def flush_paragraph():
            nonlocal paragraph
            if paragraph:
                joined = " ".join(part.strip() for part in paragraph if part.strip())
                if joined:
                    html_parts.append(f"<p>{self._markdown_inline_to_html(joined)}</p>")
                paragraph = []

        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("```"):
                flush_paragraph()
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                if in_code:
                    if code_lang == "mindmap":
                        html_parts.append("</div>")
                    else:
                        html_parts.append("</pre>")
                    in_code = False
                    code_lang = ""
                else:
                    code_lang = stripped[3:].strip().lower()
                    if code_lang == "mindmap":
                        html_parts.append('<div class="mindmap-block">')
                    else:
                        html_parts.append("<pre>")
                    in_code = True
                continue
            if in_code:
                if code_lang == "mindmap":
                    indent = len(raw_line) - len(raw_line.lstrip(" "))
                    safe_line = self._markdown_inline_to_html(stripped)
                    html_parts.append(
                        f'<div class="mindmap-line" style="margin-left:{indent * 10}px;">'
                        f'<span class="mindmap-node">{safe_line}</span></div>'
                    )
                else:
                    html_parts.append(html.escape(line))
                continue
            if not stripped:
                flush_paragraph()
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                continue
            if stripped.startswith("#"):
                flush_paragraph()
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                level = min(len(stripped) - len(stripped.lstrip("#")), 6)
                html_parts.append(f"<h{level}>{self._markdown_inline_to_html(stripped[level:].strip())}</h{level}>")
                continue
            if stripped.startswith(("- ", "* ")):
                flush_paragraph()
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                html_parts.append(f"<li>{self._markdown_inline_to_html(stripped[2:].strip())}</li>")
                continue
            paragraph.append(stripped)

        flush_paragraph()
        if in_list:
            html_parts.append("</ul>")
        if in_code:
            html_parts.append("</div>" if code_lang == "mindmap" else "</pre>")
        return "\n".join(html_parts)

    def _mindmap_blocks_from_markdown(self, markdown_text: str) -> tuple[str, dict]:
        placeholders = {}
        pattern = re.compile(r"```mindmap\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
        index = 0

        def repl(match):
            nonlocal index
            content = match.group(1).replace("\r\n", "\n")
            lines = []
            for raw_line in content.split("\n"):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                indent = len(raw_line) - len(raw_line.lstrip(" "))
                lines.append(
                    f'<div class="mindmap-line" style="margin-left:{indent * 10}px;">'
                    f'<span class="mindmap-node">{html.escape(stripped)}</span></div>'
                )
            token = f"__MINDMAP_BLOCK_{index}__"
            placeholders[token] = '<div class="mindmap-block">' + "".join(lines) + "</div>"
            index += 1
            return token

        processed = pattern.sub(repl, markdown_text)
        return processed, placeholders

    def _render_doc_metadata_html(self, metadata: dict | None) -> str:
        if not isinstance(metadata, dict) or not metadata:
            return ""
        blocks = []
        title = str(metadata.get("title") or "").strip()
        if title:
            blocks.append(f'<div class="doc-meta-title">{html.escape(title)}</div>')
        items = []
        for key, value in metadata.items():
            if str(key).strip().lower() == "title":
                continue
            text_value = ", ".join(str(x) for x in value) if isinstance(value, list) else str(value or "").strip()
            if not text_value:
                continue
            items.append(
                f'<span class="doc-meta-chip"><b>{html.escape(str(key))}:</b> {html.escape(text_value)}</span>'
            )
        if items:
            blocks.append('<div class="doc-meta-row">' + "".join(items) + "</div>")
        if not blocks:
            return ""
        return '<div class="doc-meta-box">' + "".join(blocks) + "</div>"

    def _render_markdown_html(self, markdown_text: str, metadata: dict | None = None) -> str:
        processed, placeholders = self._mindmap_blocks_from_markdown(markdown_text)
        doc = QtGui.QTextDocument()
        doc.setMarkdown(processed)
        html_text = doc.toHtml()
        for token, html_block in placeholders.items():
            html_text = html_text.replace(html.escape(token), html_block).replace(token, html_block)
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        metadata_html = self._render_doc_metadata_html(metadata)
        if dark:
            style = (
                "<style>"
                "body{font-family:Segoe UI,Arial,sans-serif;padding:8px;color:#d8e7f5;background:#101418;}"
                "h1,h2,h3,h4,h5,h6{color:#f0f6fb;}"
                "a{color:#79c0ff;}"
                "pre{background:#141b23;border:1px solid #334355;padding:10px;border-radius:8px;color:#d8e7f5;}"
                "code{background:#1b2632;color:#d8e7f5;padding:1px 4px;border-radius:4px;}"
                "li{margin:4px 0;} p{line-height:1.45;color:#d8e7f5;}"
                ".doc-meta-box{background:#141b23;border:1px solid #334355;border-radius:12px;padding:12px;margin:0 0 14px 0;}"
                ".doc-meta-title{font-size:22px;font-weight:800;color:#f0f6fb;margin-bottom:8px;}"
                ".doc-meta-row{display:block;}"
                ".doc-meta-chip{display:inline-block;background:#1b2632;border:1px solid #3a4c60;border-radius:999px;padding:5px 10px;margin:4px 8px 0 0;color:#d8e7f5;}"
                ".mindmap-block{background:#141b23;border:1px solid #334355;border-radius:12px;padding:10px;margin:12px 0;}"
                ".mindmap-line{margin:6px 0;}"
                ".mindmap-node{display:inline-block;background:#1b2632;border:1px solid #3a4c60;border-radius:999px;padding:5px 10px;font-weight:600;color:#f0f6fb;}"
                "img{max-width:100%;}"
                "</style>"
            )
        else:
            style = (
                "<style>"
                "body{font-family:Segoe UI,Arial,sans-serif;padding:8px;}"
                "h1,h2,h3{color:#17324d;}"
                "pre{background:#f4f7fa;border:1px solid #d6e0ea;padding:10px;border-radius:8px;}"
                "code{background:#eef3f7;padding:1px 4px;border-radius:4px;}"
                "li{margin:4px 0;} p{line-height:1.45;}"
                ".doc-meta-box{background:#f7fbff;border:1px solid #d6e0ea;border-radius:12px;padding:12px;margin:0 0 14px 0;}"
                ".doc-meta-title{font-size:22px;font-weight:800;color:#17324d;margin-bottom:8px;}"
                ".doc-meta-row{display:block;}"
                ".doc-meta-chip{display:inline-block;background:#eef6ff;border:1px solid #c7d9ee;border-radius:999px;padding:5px 10px;margin:4px 8px 0 0;color:#17324d;}"
                ".mindmap-block{background:#fbfcfe;border:1px solid #d6e0ea;border-radius:12px;padding:10px;margin:12px 0;}"
                ".mindmap-line{margin:6px 0;}"
                ".mindmap-node{display:inline-block;background:#eef6ff;border:1px solid #c7d9ee;border-radius:999px;padding:5px 10px;font-weight:600;color:#17324d;}"
                "img{max-width:100%;}"
                "</style>"
            )
        return (
            style + metadata_html + html_text
        )

    def _render_git_diff_html(self, diff_text: str) -> str:
        lines = []
        for raw_line in str(diff_text or "").splitlines():
            escaped = html.escape(raw_line)
            style = "color:#c9d1d9;"
            if raw_line.startswith("diff --git") or raw_line.startswith("@@"):
                style = "color:#7ee787; font-weight:700;"
            elif raw_line.startswith("+++ ") or raw_line.startswith("--- "):
                style = "color:#79c0ff; font-weight:700;"
            elif raw_line.startswith("+") and not raw_line.startswith("+++"):
                style = "background:#123524; color:#7ee787;"
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                style = "background:#3b1f22; color:#ffa198;"
            lines.append(f'<div style="{style}; white-space:pre;">{escaped}</div>')
        return (
            "<style>body{background:#0d1117;color:#c9d1d9;font-family:Consolas, 'Courier New', monospace;"
            "font-size:12px;padding:8px;} div{padding:1px 6px;border-radius:4px;}</style>"
            + "".join(lines)
        )

    def show_docs_help_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Ajuda da documentação")
        self.fit_dialog_to_screen(dialog, 760, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        browser = QtWidgets.QTextBrowser()
        browser.setHtml(
            "<h2>Metadados disponíveis</h2>"
            "<p>Use front matter YAML no topo do arquivo:</p>"
            "<pre>---\ntitle: Documento\nupdated: 2026-08-16\nowner: Equipe\nstatus: ativo\n---</pre>"
            "<h2>MindMap</h2>"
            "<p>Use um bloco <code>mindmap</code>:</p>"
            "<pre>```mindmap\nProjeto\n  Firmware\n    Lote\n    Autoversionamento\n  Docs\n    README\n```</pre>"
            "<p>Imagens locais e links externos também são suportados no Markdown.</p>"
        )
        layout.addWidget(browser, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def _refresh_docs_ui(self):
        if not hasattr(self, "docs_list"):
            return
        self.docs_list.clear()
        self.docs_content.clear()
        docs_dir = self._docs_dir(create=False)
        buttons_enabled = bool(self.current_project)
        for widget in [self.docs_refresh_btn, self.docs_open_folder_btn]:
            widget.setEnabled(buttons_enabled)
        if not docs_dir or not docs_dir.exists():
            self.docs_content.setHtml("<b>DOCS</b><br>Abra um projeto e crie a pasta <code>DOCS</code> para usar a documentação.")
            return
        doc_files = sorted(docs_dir.glob("*.md"))
        for md_path in doc_files:
            icon_path = self._doc_image_for_markdown(md_path)
            icon = QtGui.QIcon(self._pixmap_for_icon_path(icon_path, size=28)) if icon_path else self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
            item = QtWidgets.QListWidgetItem(icon, md_path.stem)
            item.setData(QtCore.Qt.UserRole, str(md_path))
            self.docs_list.addItem(item)
        if doc_files:
            self.docs_list.setCurrentRow(0)
        else:
            self.docs_content.setHtml("<p>Use <b>Novo MD</b> para começar.</p>")

    def _show_selected_doc(self):
        item = self.docs_list.currentItem() if hasattr(self, "docs_list") else None
        if not item:
            return
        md_path = Path(item.data(QtCore.Qt.UserRole))
        if not md_path.exists():
            return
        metadata, body = self._parse_doc_file(md_path)
        self.docs_content.document().setBaseUrl(QtCore.QUrl.fromLocalFile(str(md_path.parent.resolve()) + os.sep))
        self.docs_content.setHtml(self._render_markdown_html(body, metadata))

    def show_project_readme(self):
        if not self.current_project:
            return
        readme_path = self.current_project / "README.md"
        if not readme_path.exists():
            QtWidgets.QMessageBox.information(self, "README", "Este projeto não possui README.md na raiz.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("README")
        self.fit_dialog_to_screen(dialog, 900, 720)
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.document().setBaseUrl(QtCore.QUrl.fromLocalFile(str(readme_path.parent.resolve()) + os.sep))
        metadata, body = self._parse_doc_file(readme_path)
        browser.setHtml(self._render_markdown_html(body, metadata))
        layout.addWidget(browser, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def open_docs_editor_dialog(self):
        if not self.current_project:
            QtWidgets.QMessageBox.information(self, "Documentação", "Abra um projeto para editar a documentação.")
            return
        docs_dir = self._docs_dir(create=True)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Editor da documentação")
        self.fit_dialog_to_screen(dialog, 980, 720)
        layout = QtWidgets.QVBoxLayout(dialog)
        top = QtWidgets.QHBoxLayout()
        new_btn = QtWidgets.QPushButton("Novo")
        edit_btn = QtWidgets.QPushButton("Editar")
        delete_btn = QtWidgets.QPushButton("Excluir")
        icon_btn = QtWidgets.QPushButton("Ícone")
        help_btn = QtWidgets.QPushButton("?")
        help_btn.setFixedWidth(36)
        refresh_btn = QtWidgets.QPushButton("Atualizar")
        for widget in [new_btn, edit_btn, delete_btn, icon_btn, help_btn, refresh_btn]:
            top.addWidget(widget)
        top.addStretch(1)
        layout.addLayout(top)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        docs_list = QtWidgets.QListWidget()
        docs_list.setIconSize(QtCore.QSize(28, 28))
        preview = QtWidgets.QTextBrowser()
        preview.setOpenExternalLinks(True)
        split.addWidget(docs_list)
        split.addWidget(preview)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 6)
        split.setSizes([260, 760])
        layout.addWidget(split, 1)

        def reload_docs():
            docs_list.clear()
            for md_path in sorted(docs_dir.glob("*.md")):
                icon_path = self._doc_image_for_markdown(md_path)
                icon = QtGui.QIcon(self._pixmap_for_icon_path(icon_path, size=28)) if icon_path else self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
                item = QtWidgets.QListWidgetItem(icon, md_path.stem)
                item.setData(QtCore.Qt.UserRole, str(md_path))
                docs_list.addItem(item)
            if docs_list.count():
                docs_list.setCurrentRow(0)
            else:
                preview.setHtml("<p>Nenhum documento encontrado.</p>")

        def selected_doc() -> Path | None:
            item = docs_list.currentItem()
            return Path(item.data(QtCore.Qt.UserRole)) if item else None

        def refresh_preview():
            md_path = selected_doc()
            if not md_path or not md_path.exists():
                preview.setHtml("<p>Nenhum documento selecionado.</p>")
                return
            preview.document().setBaseUrl(QtCore.QUrl.fromLocalFile(str(md_path.parent.resolve()) + os.sep))
            metadata, body = self._parse_doc_file(md_path)
            preview.setHtml(self._render_markdown_html(body, metadata))

        def create_doc():
            self._create_doc_file()
            reload_docs()
            self._refresh_docs_ui()

        def edit_doc():
            md_path = selected_doc()
            if not md_path:
                return
            self._edit_doc_file(md_path)
            reload_docs()
            self._refresh_docs_ui()

        def delete_doc():
            md_path = selected_doc()
            if not md_path:
                return
            self._delete_doc_file(md_path)
            reload_docs()
            self._refresh_docs_ui()

        def set_icon():
            md_path = selected_doc()
            if not md_path:
                return
            self._set_doc_icon(md_path)
            reload_docs()
            self._refresh_docs_ui()

        docs_list.currentItemChanged.connect(lambda *_: refresh_preview())
        new_btn.clicked.connect(create_doc)
        edit_btn.clicked.connect(edit_doc)
        delete_btn.clicked.connect(delete_doc)
        icon_btn.clicked.connect(set_icon)
        help_btn.clicked.connect(self.show_docs_help_dialog)
        refresh_btn.clicked.connect(reload_docs)
        reload_docs()
        dialog.exec_()

    def _open_docs_folder(self):
        docs_dir = self._docs_dir(create=True)
        if docs_dir:
            subprocess.Popen(["explorer", str(docs_dir)])

    def _doc_from_selection(self) -> Path | None:
        item = self.docs_list.currentItem() if hasattr(self, "docs_list") else None
        if not item:
            return None
        return Path(item.data(QtCore.Qt.UserRole))

    def _doc_editor_dialog(self, title: str, filename_text: str = "", content_text: str = "") -> tuple[int, str, str]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        self.fit_dialog_to_screen(dialog, 860, 680)
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        filename_edit = QtWidgets.QLineEdit(filename_text)
        content_edit = QtWidgets.QPlainTextEdit()
        content_edit.setPlainText(content_text)
        form.addRow("Arquivo .md:", filename_edit)
        layout.addLayout(form)
        layout.addWidget(content_edit, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        result = dialog.exec_()
        return result, filename_edit.text().strip(), content_edit.toPlainText()

    def _create_doc_file(self):
        docs_dir = self._docs_dir(create=True)
        if not docs_dir:
            return
        result, filename, content = self._doc_editor_dialog(
            "Novo documento",
            "novo_documento.md",
            "---\ntitle: Novo documento\nupdated: 2026-08-16\n---\n\n# Novo documento\n",
        )
        if result != 1:
            return
        safe_name = filename if filename.lower().endswith(".md") else f"{filename}.md"
        safe_name = re.sub(r"[^\w\-.]+", "_", safe_name).strip("._") or "novo_documento.md"
        (docs_dir / safe_name).write_text(content, encoding="utf-8")
        self._refresh_docs_ui()

    def _edit_doc_file(self, md_path: Path | None = None):
        md_path = md_path or self._doc_from_selection()
        if not md_path or not md_path.exists():
            return
        current_content = md_path.read_text(encoding="utf-8", errors="replace")
        result, filename, content = self._doc_editor_dialog("Editar documento", md_path.name, current_content)
        if result != 1:
            return
        safe_name = filename if filename.lower().endswith(".md") else f"{filename}.md"
        safe_name = re.sub(r"[^\w\-.]+", "_", safe_name).strip("._") or md_path.name
        target = md_path.with_name(safe_name)
        if target != md_path and md_path.exists():
            md_path.rename(target)
            icon_path = self._doc_image_for_markdown(md_path)
            if icon_path and icon_path.exists():
                icon_path.rename(target.with_suffix(icon_path.suffix))
        target.write_text(content, encoding="utf-8")
        self._refresh_docs_ui()

    def _delete_doc_file(self, md_path: Path | None = None):
        md_path = md_path or self._doc_from_selection()
        if not md_path or not md_path.exists():
            return
        answer = QtWidgets.QMessageBox.question(self, "Excluir documento", f"Deseja excluir '{md_path.name}'? Se existir ícone pareado, ele também será removido.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        icon_path = self._doc_image_for_markdown(md_path)
        md_path.unlink(missing_ok=True)
        if icon_path:
            icon_path.unlink(missing_ok=True)
        self._refresh_docs_ui()

    def _set_doc_icon(self, md_path: Path | None = None):
        md_path = md_path or self._doc_from_selection()
        if not md_path or not md_path.exists():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Escolher ícone do documento", "", "Imagens (*.png *.jpg *.jpeg *.bmp *.ico *.gif *.webp)")
        if not path:
            return
        src = Path(path)
        dest = md_path.with_suffix(src.suffix.lower())
        for ext in [".png", ".ico", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]:
            candidate = md_path.with_suffix(ext)
            if candidate.exists() and candidate != dest:
                candidate.unlink(missing_ok=True)
        shutil.copy2(src, dest)
        self._refresh_docs_ui()

    def _update_git_selection_details(self):
        if not hasattr(self, "git_commit_table"):
            return
        row = self.git_commit_table.currentRow()
        if row < 0:
            self.git_selected_hash_label.setText("Hash selecionada: -")
            self.git_diff_source = "file"
            self._refresh_git_diff_view()
            return
        item = self.git_commit_table.item(row, 1)
        if item:
            self.git_selected_hash_label.setText(f"Hash selecionada: {item.data(QtCore.Qt.UserRole) or item.text()}")
        self.git_diff_source = "commit"
        self._refresh_git_diff_view()

    def _update_git_file_diff(self):
        self.git_diff_source = "file"
        self._refresh_git_diff_view()

    def _refresh_git_diff_view(self):
        if hasattr(self, "git_diff_view") and self.current_project:
            mode = getattr(self, "git_diff_source", "file")
            if mode == "commit":
                row = self.git_commit_table.currentRow() if hasattr(self, "git_commit_table") else -1
                if row < 0:
                    self.git_diff_plain_text = "Selecione um commit para ver o diff."
                    self.git_diff_view.setHtml(self._render_git_diff_html(self.git_diff_plain_text))
                    return
                item = self.git_commit_table.item(row, 1)
                if not item:
                    self.git_diff_plain_text = "Selecione um commit para ver o diff."
                    self.git_diff_view.setHtml(self._render_git_diff_html(self.git_diff_plain_text))
                    return
                commit_hash = str(item.data(QtCore.Qt.UserRole) or item.text())
                _, diff_out, diff_err = self._git_capture(["show", "--stat", "--patch", "--format=medium", commit_hash], timeout=60)
                self.git_diff_plain_text = diff_out or diff_err or "Sem diff disponivel."
                self.git_diff_view.setHtml(self._render_git_diff_html(self.git_diff_plain_text))
                return

            current_item = self.git_changed_files.currentItem() if hasattr(self, "git_changed_files") else None
            if not current_item:
                self.git_diff_plain_text = "Selecione um arquivo alterado para ver o diff."
                self.git_diff_view.setHtml(self._render_git_diff_html(self.git_diff_plain_text))
                return
            file_name = str(current_item.data(0, QtCore.Qt.UserRole) or current_item.text(0))
            _, diff_out, diff_err = self._git_capture(["diff", "--", file_name], timeout=60)
            if not diff_out:
                _, diff_out, diff_err = self._git_capture(["diff", "--cached", "--", file_name], timeout=60)
            self.git_diff_plain_text = diff_out or diff_err or "Sem diff disponivel para este arquivo."
            self.git_diff_view.setHtml(self._render_git_diff_html(self.git_diff_plain_text))
            return
        if not hasattr(self, "git_diff_view") or not self.current_project:
            return
        mode = getattr(self, "git_diff_source", "file")
        if mode == "commit":
            row = self.git_commit_table.currentRow() if hasattr(self, "git_commit_table") else -1
            if row < 0:
                self.git_diff_view.setHtml(self._render_git_diff_html("Selecione um commit para ver o diff."))
                return
            item = self.git_commit_table.item(row, 1)
            if not item:
                self.git_diff_view.setHtml(self._render_git_diff_html("Selecione um commit para ver o diff."))
                return
            commit_hash = str(item.data(QtCore.Qt.UserRole) or item.text())
            _, diff_out, diff_err = self._git_capture(["show", "--stat", "--patch", "--format=medium", commit_hash], timeout=60)
            self.git_diff_view.setHtml(self._render_git_diff_html(diff_out or diff_err or "Sem diff disponível."))
            return

        current_item = self.git_changed_files.currentItem() if hasattr(self, "git_changed_files") else None
        if not current_item:
            self.git_diff_view.setHtml(self._render_git_diff_html("Selecione um arquivo alterado para ver o diff."))
            return
        file_name = str(current_item.data(0, QtCore.Qt.UserRole) or current_item.text(0))
        _, diff_out, diff_err = self._git_capture(["diff", "--", file_name], timeout=60)
        if not diff_out:
            _, diff_out, diff_err = self._git_capture(["diff", "--cached", "--", file_name], timeout=60)
        self.git_diff_view.setHtml(self._render_git_diff_html(diff_out or diff_err or "Sem diff disponível para este arquivo."))

    def _open_git_diff_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Diff Expandido")
        self.fit_dialog_to_screen(dialog, 1100, 760)
        layout = QtWidgets.QVBoxLayout(dialog)
        info = QtWidgets.QLabel("Visualizacao ampliada do patch atual, com render colorido e diff bruto.")
        info.setWordWrap(True)
        self._mark_muted_label(info)
        layout.addWidget(info)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        browser = QtWidgets.QTextBrowser()
        browser.setHtml(self.git_diff_view.toHtml() if hasattr(self, "git_diff_view") else "")
        split.addWidget(browser)
        raw_box = QtWidgets.QPlainTextEdit()
        raw_box.setReadOnly(True)
        raw_box.setPlainText(getattr(self, "git_diff_plain_text", ""))
        split.addWidget(raw_box)
        split.setSizes([430, 290])
        layout.addWidget(split, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()
        return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Diff")
        self.fit_dialog_to_screen(dialog, 1100, 760)
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser()
        browser.setHtml(self.git_diff_view.toHtml() if hasattr(self, "git_diff_view") else "")
        layout.addWidget(browser, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def _copy_selected_git_hash(self, short: bool = False):
        row = self.git_commit_table.currentRow() if hasattr(self, "git_commit_table") else -1
        if row < 0:
            return
        item = self.git_commit_table.item(row, 1)
        if not item:
            return
        full_hash = str(item.data(QtCore.Qt.UserRole) or item.text())
        QtWidgets.QApplication.clipboard().setText(full_hash[:7] if short else full_hash)

    def _open_git_commit_context_menu(self, pos):
        row = self.git_commit_table.currentRow() if hasattr(self, "git_commit_table") else -1
        if row < 0:
            return
        menu = QtWidgets.QMenu(self)
        copy_hash_action = menu.addAction("Copiar hash")
        copy_short_action = menu.addAction("Copiar hash curta")
        action = menu.exec_(self.git_commit_table.viewport().mapToGlobal(pos))
        if action == copy_hash_action:
            self._copy_selected_git_hash(short=False)
        elif action == copy_short_action:
            self._copy_selected_git_hash(short=True)

    def _git_create_branch_from_selected(self):
        branch_name, ok = QtWidgets.QInputDialog.getText(self, "Criar branch", "Nome da nova branch:")
        if not ok or not branch_name.strip():
            return
        row = self.git_commit_table.currentRow() if hasattr(self, "git_commit_table") else -1
        target = "HEAD"
        if row >= 0:
            item = self.git_commit_table.item(row, 1)
            if item:
                target = str(item.data(QtCore.Qt.UserRole) or target)
        self._run_git_command(["branch", branch_name.strip(), target])

    def _git_checkout_branch(self):
        branch_name = self.git_branch_combo.currentText().strip() if hasattr(self, "git_branch_combo") else ""
        if not branch_name:
            QtWidgets.QMessageBox.information(self, "Git", "Selecione uma branch para trocar.")
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Git",
            f"Trocar para a branch '{branch_name}'?\n\nConfirme se você já tratou alterações locais não commitadas.",
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._run_git_command(["checkout", branch_name])

    def _git_delete_branch(self):
        target = self.git_admin_target_combo.currentText().strip() if hasattr(self, "git_admin_target_combo") else ""
        if not target:
            QtWidgets.QMessageBox.information(self, "Git", "Digite a branch local para excluir.")
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Git Admin",
            (
                f"Excluir a branch local '{target}'?\n\n"
                "Consequência: ela some desta máquina. O histórico pode continuar existindo em commits e no remote."
            ),
        ) == QtWidgets.QMessageBox.Yes:
            self._run_git_command(["branch", "-D", target])

    def _git_delete_remote_branch(self):
        target = self.git_admin_remote_target_combo.currentText().strip() if hasattr(self, "git_admin_remote_target_combo") else ""
        if not target:
            QtWidgets.QMessageBox.information(self, "Git", "Digite a branch remota para excluir.")
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Git Admin",
            (
                f"Excluir a branch remota '{target}' do origin?\n\n"
                "Consequência: outras pessoas deixam de ver essa branch no remote após fetch/pull."
            ),
        ) == QtWidgets.QMessageBox.Yes:
            self._run_git_command(["push", "origin", "--delete", target])

    def _git_reset_hard(self):
        target = self.git_reset_target_combo.currentData() if hasattr(self, "git_reset_target_combo") and self.git_reset_target_combo.currentIndex() >= 0 else ""
        if not target and hasattr(self, "git_reset_target_combo"):
            target = self.git_reset_target_combo.currentText().strip()
        if not target:
            QtWidgets.QMessageBox.information(self, "Git", "Digite a hash alvo para o reset --hard.")
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Git Admin",
            (
                f"Fazer git reset --hard para '{target}'?\n\n"
                "Consequência: alterações locais não commitadas serão descartadas sem voltar sozinhas."
            ),
        ) == QtWidgets.QMessageBox.Yes:
            self._run_git_command(["reset", "--hard", target])

    def _project_icon_path_from_config(self, project_path: Path, config: dict | None = None) -> Path:
        cfg = config or {}
        props = cfg.get("properties", {}) if isinstance(cfg.get("properties", {}), dict) else {}
        stored = str(props.get("icon", "") or "").strip()
        if stored:
            candidate = project_path / stored
            if candidate.exists():
                return candidate
        return self.default_project_icon_path if self.default_project_icon_path.exists() else self.app_icon_path

    def _pixmap_for_icon_path(self, icon_path: Path, size: int = 24) -> QtGui.QPixmap:
        pixmap = QtGui.QPixmap(str(icon_path))
        if pixmap.isNull() and self.default_project_icon_path.exists():
            pixmap = QtGui.QPixmap(str(self.default_project_icon_path))
        if pixmap.isNull():
            pixmap = QtGui.QPixmap(size, size)
            pixmap.fill(QtCore.Qt.transparent)
        return pixmap.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def _icon_for_project(self, project_path: Path, config: dict | None = None, size: int = 24) -> QtGui.QIcon:
        return QtGui.QIcon(self._pixmap_for_icon_path(self._project_icon_path_from_config(project_path, config), size=size))

    def _load_recent_projects(self):
        try:
            if self.recent_projects_file.exists():
                with open(self.recent_projects_file, "r", encoding="utf-8") as handle:
                    self.recent_projects = json.load(handle)
            else:
                self.recent_projects = []
        except Exception:
            self.recent_projects = []

    def _save_recent_projects(self):
        try:
            with open(self.recent_projects_file, "w", encoding="utf-8") as handle:
                json.dump(self.recent_projects, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log(f"[WARN] Falha ao salvar histórico: {exc}")

    def load_recent_projects_widget(self):
        self.recent_list.clear()
        for path in self.recent_projects[:20]:
            project_path = Path(path)
            config = self.backend.load_project(str(project_path)) if (hasattr(self, "backend") and self.backend and project_path.exists()) else {}
            item = QtWidgets.QListWidgetItem(self._icon_for_project(project_path, config), project_path.name)
            self.recent_list.addItem(item)
        self._update_history_icon()

    def add_to_recent(self, path):
        path_str = str(path)
        if path_str in self.recent_projects:
            self.recent_projects.remove(path_str)
        self.recent_projects.insert(0, path_str)
        self.recent_projects = self.recent_projects[:20]
        self._save_recent_projects()
        self.load_recent_projects_widget()
        self._refresh_tray_menu()

    def _update_history_icon(self):
        icon_path = self.default_project_icon_path
        if self.current_project:
            icon_path = self._project_icon_path_from_config(self.current_project, self.current_config or {})
        self.history_icon_label.setPixmap(self._pixmap_for_icon_path(icon_path, size=24))

    def open_recent_project(self):
        row = self.recent_list.currentRow()
        if row < 0 or row >= len(self.recent_projects):
            return
        path = self.recent_projects[row]
        if Path(path).exists():
            self.load_project_path(path)
        else:
            QtWidgets.QMessageBox.warning(self, self.t("error.title", "Error"), self.t("error.project_missing", "Project does not exist"))

    def open_recent_context_menu(self, pos):
        row = self.recent_list.currentRow()
        if row < 0:
            return
        menu = QtWidgets.QMenu(self)
        remove_action = menu.addAction("Remover do histórico")
        action = menu.exec_(self.recent_list.mapToGlobal(pos))
        if action == remove_action:
            self.recent_projects.pop(row)
            self._save_recent_projects()
            self.load_recent_projects_widget()

    def compare_versions(self, a: str, b: str) -> int:
        def normalize(value: str):
            parts = []
            for token in str(value or "").replace("-", ".").split("."):
                digits = "".join(ch for ch in token if ch.isdigit())
                parts.append(int(digits) if digits else 0)
            return parts or [0]
        va = normalize(a)
        vb = normalize(b)
        if va > vb:
            return 1
        if va < vb:
            return -1
        return 0

    def _build_resolution_prompt(self, title: str, error_msg: str, output: str = "") -> str:
        project_name = self.current_project.name if self.current_project else "sem_projeto"
        raw_error_block = "\n\n".join(
            part for part in [str(error_msg or "").strip(), str(output or "").strip()] if part
        ).strip()
        parts = [
            "Analise este erro de compilação/upload do Arduino CLI e proponha uma resolução objetiva.",
            f"Tarefa: {title}",
            f"Projeto: {project_name}",
            "",
            "Erro principal:",
            str(error_msg or "Erro não informado").strip(),
        ]
        clean_output = str(output or "").strip()
        if clean_output:
            parts.extend(
                [
                    "",
                    "Output completo relevante:",
                    clean_output[:12000],
                ]
            )
        if raw_error_block:
            parts.extend(
                [
                    "",
                    "Erro bruto para consulta/copia:",
                    raw_error_block[:12000],
                ]
            )
        parts.extend(
            [
                "",
                "Quero:",
                "1. causa provável",
                "2. passos para corrigir",
                "3. se possível, o comando ou ajuste exato",
            ]
        )
        return "\n".join(parts)

    def _open_error_search_dialog(self, title: str, error_msg: str, output: str = ""):
        query = f"Arduino CLI {title} {error_msg} {output[:400]}"
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
        dialog = WebBrowserQt(self, f"Pesquisar erro - {title}")
        dialog.load_url(url)
        dialog.exec_()

    def open_external_url(self, url: str) -> bool:
        target = str(url or "").strip()
        if not target:
            return False
        try:
            opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl(target))
        except Exception as exc:
            self.log(f"[BROWSER] Falha ao abrir URL externa: {exc}")
            QtWidgets.QMessageBox.warning(self, "Navegador", f"Não consegui abrir o navegador:\n{exc}")
            return False
        if not opened:
            self.log(f"[BROWSER] Sistema recusou abrir URL: {target}")
            QtWidgets.QMessageBox.warning(self, "Navegador", "O sistema não conseguiu abrir o navegador padrão.")
            return False
        return True

    def _is_abort_message(self, text: str) -> bool:
        normalized = self._fix_mojibake_text(str(text or "")).strip().lower()
        normalized = normalized.replace("ç", "c").replace("ã", "a").replace("á", "a")
        return normalized in {
            "operacao abortada pelo usuario",
            "operacao cancelada pelo usuario",
            "abortado pelo usuario",
            "cancelado pelo usuario",
        }

    def show_error_dialog(self, title: str, error_msg: str, output: str = ""):
        if self._is_abort_message(error_msg):
            self.log(output or f"[ABORT] {title}")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Erro em {title}")
        self.fit_dialog_to_screen(dialog, 760, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        header = QtWidgets.QLabel(f"Erro durante {title.lower()}:")
        header.setObjectName("managerTitle")
        layout.addWidget(header)
        msg_box = QtWidgets.QPlainTextEdit()
        msg_box.setReadOnly(True)
        msg_box.setMaximumHeight(180)
        msg_box.setPlainText(error_msg or "Erro")
        layout.addWidget(msg_box)
        if output:
            layout.addWidget(QtWidgets.QLabel("Output completo"))
            out_box = QtWidgets.QPlainTextEdit()
            out_box.setReadOnly(True)
            out_box.setPlainText(output[:12000])
            layout.addWidget(out_box, 1)
        buttons_row = QtWidgets.QHBoxLayout()
        copy_error_btn = QtWidgets.QPushButton("Copiar erro")
        copy_prompt_btn = QtWidgets.QPushButton("Copiar prompt")
        search_btn = QtWidgets.QPushButton("Pesquisar")
        buttons_row.addWidget(copy_error_btn)
        buttons_row.addWidget(copy_prompt_btn)
        buttons_row.addWidget(search_btn)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        def copy_error():
            QtWidgets.QApplication.clipboard().setText(
                "\n\n".join(part for part in [str(error_msg or "").strip(), str(output or "").strip()] if part)
            )
            self.log(f"[COPY] Erro copiado: {title}")

        def copy_prompt():
            QtWidgets.QApplication.clipboard().setText(self._build_resolution_prompt(title, error_msg, output))
            self.log(f"[COPY] Prompt de resolução copiado: {title}")

        copy_error_btn.clicked.connect(copy_error)
        copy_prompt_btn.clicked.connect(copy_prompt)
        search_btn.clicked.connect(lambda: self._open_error_search_dialog(title, error_msg, output))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def log(self, text: str):
        self.console.appendPlainText(str(text))
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def fit_dialog_to_screen(self, dialog, preferred_width: int, preferred_height: int):
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
        width = min(preferred_width, max(520, available.width() - 80))
        height = min(preferred_height, max(360, available.height() - 80))
        dialog.resize(width, height)
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        self._apply_windows_titlebar_theme_to_widget(dialog, dark)

    def log_build_summary(self, output: str):
        if not output:
            return
        summary_lines = []
        for raw in output.splitlines():
            line = raw.strip()
            lowered = line.lower()
            if not line:
                continue
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

    def extract_compile_metrics(self, output: str):
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

    def show_compile_success_dialog(self, output: str, title: str):
        flash_pct, ram_pct, flash_line, ram_line, warning_lines = self.extract_compile_metrics(output)
        dialog = ActionResultDialog(self, title, flash_pct, ram_pct, flash_line, ram_line, warning_lines)
        self.fit_dialog_to_screen(dialog, 700, 560)
        dialog.exec_()

    def _request_abort_action(self, progress_dialog=None):
        aborted = self.backend.abort_current_action() if self.backend else False
        if progress_dialog:
            progress_dialog.set_subtitle("Abortando ação em andamento...")
            progress_dialog.append_debug("[ABORT] Solicitação de aborto enviada")
        self.log("[ABORT] Solicitação de aborto enviada" if aborted else "[ABORT] Nenhuma ação em execução para abortar")

    def create_project(self):
        parent_folder = QtWidgets.QFileDialog.getExistingDirectory(self, "[+] Selecione a pasta base")
        if not parent_folder:
            return
        project_name, ok = QtWidgets.QInputDialog.getText(self, "Novo projeto", "Nome da pasta:")
        if not ok or not project_name.strip():
            return
        sanitized = self.sanitize_project_name(project_name)
        target = Path(parent_folder) / sanitized
        if target.exists():
            QtWidgets.QMessageBox.warning(self, self.t("warn.title", "Warning"), f"A pasta '{sanitized}' já existe.")
            return
        if self.backend.create_project(str(target), project_name=sanitized, template_key="clean"):
            self.add_to_recent(target)
            self.load_project_path(str(target))

    def sanitize_project_name(self, name: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(name or "").strip())
        return cleaned.strip("._-") or "novo_projeto"

    def open_project(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Abrir projeto")
        if folder:
            self.load_project_path(folder)

    def load_project_path(self, path):
        config = self.backend.load_project(str(path))
        if not config:
            self.log(f"[ERRO] Falha ao carregar projeto: {path}")
            return
        self.current_project = Path(path)
        config.setdefault("name", self.current_project.name)
        self.current_config = config
        self._ensure_project_property_defaults()
        self.add_to_recent(path)
        self.update_project_info()
        self._refresh_tray_menu()

    def update_project_info(self):
        if not self.current_project or not self.current_config:
            return
        self._ensure_project_property_defaults()
        self._sync_version_from_source()
        self.project_name_label.setText(self.current_config.get("name", self.current_project.name))
        saved_fqbn = self.current_config.get("fqbn", "")
        saved_port = self.current_config.get("port", "auto") or "auto"
        saved_baud = self.current_config.get("baudrate", "115200") or "115200"
        self.board_display.setText(saved_fqbn or "-")
        self.port_display.setText(saved_port)
        self.baud_display.setText(saved_baud)
        self._set_combo_value(self.port_combo, saved_port)
        self._set_combo_value(self.baud_combo, saved_baud)
        self.refresh_serial_status(bool(self.serial_connection))
        self._update_history_icon()
        self._update_project_actions_enabled(True)
        self._refresh_docs_ui()
        self._refresh_git_ui()
        if saved_fqbn:
            self.load_board_details_async(saved_fqbn)
        else:
            self.clear_dynamic_board_details()

    def save_config(self):
        if not self.current_project or not self.current_config:
            return
        self.current_config["port"] = self.port_combo.currentText()
        self.current_config["baudrate"] = self.baud_combo.currentText()
        self.port_display.setText(self.current_config["port"])
        self.baud_display.setText(self.current_config["baudrate"])
        fuse_file = self.current_project / "project.fuzil"
        try:
            with open(fuse_file, "w", encoding="utf-8") as handle:
                json.dump(self.current_config, handle, ensure_ascii=False, indent=4)
        except Exception as exc:
            self.log(f"[ERRO] Falha ao salvar config: {exc}")

    def _clone_jsonable(self, value):
        return json.loads(json.dumps(value, ensure_ascii=False))

    def _bundle_prefix(self) -> str:
        return "VCLI-PROJECT-BUNDLE-1:"

    def _build_project_settings_payload(self) -> dict:
        self.save_config()
        config = self._clone_jsonable(self.current_config or {})
        payload = {
            "schema": "vcli_project_bundle",
            "schema_version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "project_name": self.current_project.name if self.current_project else config.get("name", ""),
            "config": config,
            "assets": {},
        }
        icon_rel = str(config.get("properties", {}).get("icon", "") or "").strip()
        if self.current_project and icon_rel:
            icon_path = (self.current_project / icon_rel).resolve()
            try:
                if icon_path.is_file():
                    payload["assets"]["project_icon"] = {
                        "relative_path": icon_rel,
                        "data_b64": base64.b64encode(icon_path.read_bytes()).decode("ascii"),
                    }
            except Exception as exc:
                self.log(f"[EXPORT] Não consegui embutir o ícone do projeto: {exc}")
        return payload

    def _encode_project_settings_payload(self, payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        packed = zlib.compress(raw, level=9)
        return self._bundle_prefix() + base64.urlsafe_b64encode(packed).decode("ascii")

    def _decode_project_settings_payload(self, text: str) -> dict:
        raw_text = str(text or "").strip()
        if not raw_text:
            raise ValueError("Cole um pacote exportado ou carregue um arquivo.")
        compact = "".join(raw_text.split())
        if compact.startswith(self._bundle_prefix()):
            compact = compact[len(self._bundle_prefix()):]
        try:
            decoded = zlib.decompress(base64.urlsafe_b64decode(compact.encode("ascii")))
        except Exception as exc:
            raise ValueError(f"Pacote inválido ou corrompido: {exc}") from exc
        try:
            payload = json.loads(decoded.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Não consegui decodificar o conteúdo: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
            raise ValueError("O pacote não contém uma configuração de projeto válida.")
        return payload

    def _import_project_label_map(self) -> dict:
        return {
            "name": "Nome do projeto",
            "fqbn": "Placa (FQBN)",
            "port": "Porta padrão",
            "baudrate": "Baudrate",
            "variant": "Variante da placa",
            "custom_libs": "Bibliotecas customizadas",
        }

    def _project_import_label(self, path: str) -> str:
        label_map = self._import_project_label_map()
        if path in label_map:
            return label_map[path]
        if path.startswith("tools."):
            return f"Ferramenta da placa: {path.split('.', 1)[1]}"
        if path.startswith("properties."):
            return f"Propriedade: {path.split('.', 1)[1]}"
        if path == "asset.project_icon":
            return "Ícone do projeto"
        return path

    def _project_import_preview(self, value) -> str:
        if value in (None, "", [], {}):
            return "(vazio)"
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        text = text.replace("\r", " ").replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        return text

    def _build_project_import_changes(self, imported_config: dict, assets: dict | None = None) -> list:
        current = self._clone_jsonable(self.current_config or {})
        incoming = self._clone_jsonable(imported_config or {})
        changes = []

        def add_change(path: str, new_value):
            current_value = current.get(path) if "." not in path else None
            changes.append(
                {
                    "path": path,
                    "label": self._project_import_label(path),
                    "current": current_value,
                    "new": new_value,
                }
            )

        for key in ["name", "fqbn", "port", "baudrate", "variant", "custom_libs"]:
            current_value = current.get(key)
            new_value = incoming.get(key)
            if current_value != new_value:
                changes.append(
                    {
                        "path": key,
                        "label": self._project_import_label(key),
                        "current": current_value,
                        "new": new_value,
                    }
                )

        current_tools = current.get("tools", {}) if isinstance(current.get("tools"), dict) else {}
        incoming_tools = incoming.get("tools", {}) if isinstance(incoming.get("tools"), dict) else {}
        for tool_key in sorted(set(current_tools) | set(incoming_tools)):
            current_value = current_tools.get(tool_key)
            new_value = incoming_tools.get(tool_key)
            if current_value != new_value:
                changes.append(
                    {
                        "path": f"tools.{tool_key}",
                        "label": self._project_import_label(f"tools.{tool_key}"),
                        "current": current_value,
                        "new": new_value,
                    }
                )

        current_props = current.get("properties", {}) if isinstance(current.get("properties"), dict) else {}
        incoming_props = incoming.get("properties", {}) if isinstance(incoming.get("properties"), dict) else {}
        for prop_key in sorted(set(current_props) | set(incoming_props)):
            if prop_key == "icon":
                continue
            current_value = current_props.get(prop_key)
            new_value = incoming_props.get(prop_key)
            if current_value != new_value:
                changes.append(
                    {
                        "path": f"properties.{prop_key}",
                        "label": self._project_import_label(f"properties.{prop_key}"),
                        "current": current_value,
                        "new": new_value,
                    }
                )

        current_icon = str(current_props.get("icon", "") or "").strip()
        imported_icon = str(incoming_props.get("icon", "") or "").strip()
        has_icon_asset = bool((assets or {}).get("project_icon", {}).get("data_b64"))
        if current_icon != imported_icon or (imported_icon and has_icon_asset):
            changes.append(
                {
                    "path": "asset.project_icon",
                    "label": self._project_import_label("asset.project_icon"),
                    "current": current_icon,
                    "new": imported_icon or "(remover)",
                }
            )
        return changes

    def _set_project_value_by_path(self, path: str, value, assets: dict | None = None):
        assets = assets or {}
        if path == "name":
            self.current_config["name"] = str(value or self.current_project.name).strip() or self.current_project.name
            return
        if path in {"fqbn", "port", "baudrate", "variant"}:
            self.current_config[path] = str(value or "").strip()
            return
        if path == "custom_libs":
            self.current_config["custom_libs"] = list(value or [])
            return
        if path.startswith("tools."):
            tool_key = path.split(".", 1)[1]
            tools = self.current_config.setdefault("tools", {})
            if value in (None, ""):
                tools.pop(tool_key, None)
            else:
                tools[tool_key] = value
            return
        if path.startswith("properties."):
            prop_key = path.split(".", 1)[1]
            props = self.current_config.setdefault("properties", {})
            props[prop_key] = value
            return
        if path == "asset.project_icon":
            props = self.current_config.setdefault("properties", {})
            asset = assets.get("project_icon", {}) if isinstance(assets, dict) else {}
            icon_name = str(asset.get("relative_path") or value or "").strip()
            if not icon_name:
                props["icon"] = ""
                return
            data_b64 = str(asset.get("data_b64", "") or "").strip()
            suffix = Path(icon_name).suffix.lower() or ".png"
            target_name = f"project_icon{suffix}"
            target_path = self.current_project / target_name
            if data_b64:
                target_path.write_bytes(base64.b64decode(data_b64.encode("ascii")))
                props["icon"] = target_name
            else:
                raise ValueError("O pacote referencia um ícone, mas não trouxe o arquivo embutido.")

    def _show_project_import_review_dialog(self, payload: dict) -> bool:
        imported_config = payload.get("config", {})
        assets = payload.get("assets", {}) if isinstance(payload.get("assets"), dict) else {}
        changes = self._build_project_import_changes(imported_config, assets)
        if not changes:
            QtWidgets.QMessageBox.information(self, "Importar configurações", "Esse pacote não traz mudanças para o projeto atual.")
            return False

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Revisar importação de configurações")
        self.fit_dialog_to_screen(dialog, 980, 680)
        layout = QtWidgets.QVBoxLayout(dialog)
        intro = QtWidgets.QLabel(
            "Revise o que vai mudar no projeto atual. Você pode desmarcar qualquer item antes de aplicar."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        source_name = str(payload.get("project_name", "") or "-").strip()
        exported_at = str(payload.get("exported_at", "") or "-").strip()
        meta = QtWidgets.QLabel(f"Origem: {source_name} | Exportado em: {exported_at}")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        table = QtWidgets.QTreeWidget()
        table.setColumnCount(3)
        table.setHeaderLabels(["Configuração", "Atual", "Importado"])
        table.setRootIsDecorated(False)
        table.setAlternatingRowColors(True)
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        if dark:
            table.setStyleSheet(
                "QTreeWidget { background: #111821; color: #e5edf5; border: 1px solid #334355; alternate-background-color: #18222d; }"
                "QTreeWidget::item { background: transparent; color: #e5edf5; }"
                "QTreeWidget::item:selected { background: #2b6cb0; color: white; }"
                "QHeaderView::section { background: #1b2632; color: #e5edf5; border: 1px solid #334355; padding: 6px; }"
            )
        else:
            table.setStyleSheet(
                "QTreeWidget { background: #ffffff; color: #1e2933; border: 1px solid #c8d3df; alternate-background-color: #f5f8fb; }"
                "QTreeWidget::item { background: transparent; color: #1e2933; }"
                "QTreeWidget::item:selected { background: #cfe5ff; color: #12344d; }"
                "QHeaderView::section { background: #eef3f7; color: #12344d; border: 1px solid #c8d3df; padding: 6px; }"
            )
        layout.addWidget(table, 1)

        for change in changes:
            item = QtWidgets.QTreeWidgetItem(
                [
                    change["label"],
                    self._project_import_preview(change["current"]),
                    self._project_import_preview(change["new"]),
                ]
            )
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked)
            item.setData(0, QtCore.Qt.UserRole, change["path"])
            table.addTopLevelItem(item)

        status_label = QtWidgets.QLabel("")
        layout.addWidget(status_label)

        def refresh_status():
            selected = 0
            for index in range(table.topLevelItemCount()):
                if table.topLevelItem(index).checkState(0) == QtCore.Qt.Checked:
                    selected += 1
            status_label.setText(f"Itens marcados para aplicar: {selected} de {table.topLevelItemCount()}")

        table.itemChanged.connect(lambda *_: refresh_status())
        refresh_status()

        action_row = QtWidgets.QHBoxLayout()
        mark_all_btn = QtWidgets.QPushButton("Marcar tudo")
        clear_all_btn = QtWidgets.QPushButton("Desmarcar tudo")
        action_row.addWidget(mark_all_btn)
        action_row.addWidget(clear_all_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        def set_all(state):
            for index in range(table.topLevelItemCount()):
                table.topLevelItem(index).setCheckState(0, state)

        mark_all_btn.clicked.connect(lambda: set_all(QtCore.Qt.Checked))
        clear_all_btn.clicked.connect(lambda: set_all(QtCore.Qt.Unchecked))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Aplicar selecionados")
        buttons.rejected.connect(dialog.reject)

        def apply_selected():
            selected_paths = []
            for index in range(table.topLevelItemCount()):
                item = table.topLevelItem(index)
                if item.checkState(0) == QtCore.Qt.Checked:
                    selected_paths.append(item.data(0, QtCore.Qt.UserRole))
            if not selected_paths:
                QtWidgets.QMessageBox.information(dialog, "Importar configurações", "Nenhuma alteração foi selecionada.")
                return
            try:
                for path in selected_paths:
                    if path == "asset.project_icon":
                        imported_value = imported_config.get("properties", {}).get("icon", "")
                    elif path.startswith("tools."):
                        imported_value = imported_config.get("tools", {}).get(path.split(".", 1)[1])
                    elif path.startswith("properties."):
                        imported_value = imported_config.get("properties", {}).get(path.split(".", 1)[1])
                    else:
                        imported_value = imported_config.get(path)
                    self._set_project_value_by_path(path, imported_value, assets=assets)
                self._ensure_project_property_defaults()
                self.save_config()
                self.update_project_info()
                self.log(f"[IMPORT] Configurações importadas de '{source_name}'")
                dialog.accept()
            except Exception as exc:
                self.show_error_dialog("Importar configurações", str(exc))

        buttons.accepted.connect(apply_selected)
        layout.addWidget(buttons)
        return dialog.exec_() == QtWidgets.QDialog.Accepted

    def export_project_settings_bundle(self):
        if not self.current_project or not self.current_config:
            QtWidgets.QMessageBox.warning(self, "Exportar configurações", "Abra um projeto antes de exportar as configurações.")
            return
        payload = self._build_project_settings_payload()
        encoded = self._encode_project_settings_payload(payload)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Exportar configurações do projeto")
        self.fit_dialog_to_screen(dialog, 880, 540)
        layout = QtWidgets.QVBoxLayout(dialog)
        info = QtWidgets.QLabel(
            "Esse pacote contém as configurações do projeto atual, incluindo placa, propriedades, versionamento e ícone embutido quando existir."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        editor = QtWidgets.QPlainTextEdit()
        editor.setPlainText(encoded)
        layout.addWidget(editor, 1)
        row = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton("Copiar pacote")
        save_btn = QtWidgets.QPushButton("Salvar em arquivo")
        close_btn = QtWidgets.QPushButton("Fechar")
        row.addWidget(copy_btn)
        row.addWidget(save_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(editor.toPlainText().strip()))

        def save_file():
            suggested = f"{self.current_project.name}_config.vcli"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                dialog,
                "Salvar pacote de configurações",
                str(self.current_project / suggested),
                "Pacote V CLI (*.vcli);;Texto (*.txt)",
            )
            if not path:
                return
            try:
                Path(path).write_text(editor.toPlainText().strip(), encoding="utf-8")
                self.log(f"[EXPORT] Pacote salvo em {path}")
            except Exception as exc:
                self.show_error_dialog("Exportar configurações", str(exc))

        save_btn.clicked.connect(save_file)
        close_btn.clicked.connect(dialog.accept)
        dialog.exec_()

    def import_project_settings_bundle(self):
        if not self.current_project or not self.current_config:
            QtWidgets.QMessageBox.warning(self, "Importar configurações", "Abra um projeto antes de importar configurações.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Importar configurações do projeto")
        self.fit_dialog_to_screen(dialog, 880, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        info = QtWidgets.QLabel(
            "Cole o pacote inline exportado por outro projeto ou carregue um arquivo. Antes de aplicar, o V CLI vai mostrar exatamente o que será alterado."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        editor = QtWidgets.QPlainTextEdit()
        layout.addWidget(editor, 1)
        row = QtWidgets.QHBoxLayout()
        load_btn = QtWidgets.QPushButton("Carregar arquivo")
        analyze_btn = QtWidgets.QPushButton("Analisar mudanças")
        close_btn = QtWidgets.QPushButton("Fechar")
        row.addWidget(load_btn)
        row.addStretch(1)
        row.addWidget(analyze_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

        def load_file():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                dialog,
                "Abrir pacote de configurações",
                str(self.current_project),
                "Pacote V CLI (*.vcli *.txt);;Todos (*.*)",
            )
            if not path:
                return
            try:
                editor.setPlainText(Path(path).read_text(encoding="utf-8"))
            except Exception as exc:
                self.show_error_dialog("Importar configurações", str(exc))

        def analyze_bundle():
            try:
                payload = self._decode_project_settings_payload(editor.toPlainText())
            except Exception as exc:
                self.show_error_dialog("Importar configurações", str(exc))
                return
            self._show_project_import_review_dialog(payload)

        load_btn.clicked.connect(load_file)
        analyze_btn.clicked.connect(analyze_bundle)
        close_btn.clicked.connect(dialog.accept)
        dialog.exec_()

    def clear_dynamic_board_details(self):
        self.variant_options = []
        self.dynamic_tool_controls = {}
        while self.dynamic_form.count():
            item = self.dynamic_form.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self._clear_layout(child_layout)
        self.dynamic_form.addStretch(1)

    def _clear_layout(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            inner = child.layout()
            if widget:
                widget.deleteLater()
            elif inner:
                self._clear_layout(inner)

    def _find_option(self, options: list, option_id: str) -> dict:
        for option in options:
            if option.get("id") == option_id:
                return option
        return options[0] if options else {}

    def open_option_modal(self, title: str, options: list, current_id: str, on_select):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(420, 360)
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(title))
        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Filtrar...")
        layout.addWidget(search)
        list_widget = QtWidgets.QListWidget()
        layout.addWidget(list_widget, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        filtered = list(options)

        def refresh():
            current_text = search.text().strip().lower()
            list_widget.clear()
            filtered.clear()
            for option in options:
                text = option.get("name", option.get("id", ""))
                if current_text and current_text not in text.lower() and current_text not in option.get("id", "").lower():
                    continue
                filtered.append(option)
                list_widget.addItem(text)
            for index, option in enumerate(filtered):
                if option.get("id") == current_id:
                    list_widget.setCurrentRow(index)
                    break

        def confirm():
            row = list_widget.currentRow()
            if row < 0 or row >= len(filtered):
                return
            on_select(filtered[row])
            dialog.accept()

        search.textChanged.connect(lambda *_: refresh())
        list_widget.itemDoubleClicked.connect(lambda *_: confirm())
        buttons.accepted.connect(confirm)
        buttons.rejected.connect(dialog.reject)
        refresh()
        dialog.exec_()

    def open_port_modal(self):
        ports = self.get_serial_ports()
        self.available_ports = ports
        options = [{"id": "auto", "name": "auto"}] + [{"id": port, "name": port} for port in ports]
        self.open_option_modal(
            self.t("cfg.port", "Serial Port"),
            options,
            self.port_combo.currentText() or "auto",
            self.set_port_value,
        )

    def set_port_value(self, option: dict):
        if not option:
            return
        self._set_combo_value(self.port_combo, option.get("id", "auto"))
        self.port_display.setText(option.get("id", "auto"))
        self.save_config()

    def open_baud_modal(self):
        options = [{"id": baud, "name": baud} for baud in self.baud_options]
        self.open_option_modal(
            self.t("cfg.baud", "Baud rate"),
            options,
            self.baud_combo.currentText() or "115200",
            self.set_baud_value,
        )

    def set_baud_value(self, option: dict):
        if not option:
            return
        self._set_combo_value(self.baud_combo, option.get("id", "115200"))
        self.baud_display.setText(option.get("id", "115200"))
        self.save_config()

    def edit_project_name(self):
        if not self.current_project or not self.current_config:
            return
        new_name, ok = QtWidgets.QInputDialog.getText(self, "Editar Nome", "Nome do projeto:", text=self.current_project.name)
        if not ok or not new_name.strip():
            return
        sanitized = self.sanitize_project_name(new_name)
        if sanitized == self.current_project.name:
            return
        old_ino = self.current_project / f"{self.current_project.name}.ino"
        new_ino = self.current_project / f"{sanitized}.ino"
        try:
            if old_ino.exists():
                old_ino.rename(new_ino)
            target_dir = self.current_project.parent / sanitized
            suffix = 1
            while target_dir.exists():
                target_dir = self.current_project.parent / f"{sanitized}_{suffix}"
                suffix += 1
            self.current_project.rename(target_dir)
            self.current_project = target_dir
            self.current_config["name"] = target_dir.name
            self.save_config()
            self.add_to_recent(target_dir)
            self.project_name_label.setText(target_dir.name)
        except Exception as exc:
            self.show_error_dialog(self.t("error.title", "Error"), str(exc))

    def edit_project_properties(self):
        return self._edit_project_properties_v2()

    def _edit_project_properties_v2(self):
        if not self.current_project or not self.current_config:
            return
        props = self._ensure_project_property_defaults()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.t("props.title", "Project Properties"))
        self.fit_dialog_to_screen(dialog, 1040, 760)
        outer = QtWidgets.QVBoxLayout(dialog)
        body = QtWidgets.QHBoxLayout()
        nav = QtWidgets.QListWidget()
        nav.setFixedWidth(220)
        nav.addItems(["Geral", "Autoversionamento", "Lote", "Perguntas de compilação"])
        stack = QtWidgets.QStackedWidget()
        body.addWidget(nav)
        body.addWidget(stack, 1)

        general_page = QtWidgets.QWidget()
        general_form = QtWidgets.QFormLayout(general_page)
        author = QtWidgets.QLineEdit(props.get("author", ""))
        version = QtWidgets.QLineEdit(props.get("version", "1.0.0"))
        contributors = QtWidgets.QLineEdit(props.get("contributors", ""))
        description = QtWidgets.QTextEdit(props.get("description", ""))
        autoversion_options = [
            ("Desativado", "disabled"),
            ("Sempre ao exportar binário", "always_export"),
            ("Sempre no upload", "always_upload"),
            ("Sempre em ambos", "always_both"),
            ("Perguntar ao exportar binário", "ask_export"),
            ("Perguntar no upload", "ask_upload"),
            ("Perguntar em ambos", "ask_both"),
        ]
        icon_preview = QtWidgets.QLabel()
        icon_preview.setFixedSize(48, 48)
        icon_preview.setPixmap(self._pixmap_for_icon_path(self._project_icon_path_from_config(self.current_project, self.current_config), size=44))
        selected_icon_path = {"value": ""}
        choose_icon_btn = QtWidgets.QPushButton("Alterar ícone")
        reset_icon_btn = QtWidgets.QPushButton("Padrão")
        icon_row = QtWidgets.QHBoxLayout()
        icon_row.addWidget(icon_preview)
        icon_row.addWidget(choose_icon_btn)
        icon_row.addWidget(reset_icon_btn)
        icon_row.addStretch(1)
        general_form.addRow(self.t("props.author", "Author:"), author)
        general_form.addRow(self.t("props.version", "Version:"), version)
        general_form.addRow(self.t("props.contributors", "Contributors:"), contributors)
        general_form.addRow(self.t("props.description", "Description:"), description)
        general_form.addRow("Ícone:", self._wrap_layout(icon_row))
        stack.addWidget(general_page)

        auto_page = QtWidgets.QWidget()
        auto_layout = QtWidgets.QVBoxLayout(auto_page)
        auto_form = QtWidgets.QFormLayout()
        autoversion_mode = QtWidgets.QComboBox()
        for label, value in autoversion_options:
            autoversion_mode.addItem(label, value)
        current_mode_index = autoversion_mode.findData(props.get("autoversion_mode", "disabled"))
        autoversion_mode.setCurrentIndex(current_mode_index if current_mode_index >= 0 else 0)
        autoversion_value_mode = QtWidgets.QComboBox()
        autoversion_value_mode.addItem("Incremento simples", "increment")
        autoversion_value_mode.addItem("Ano + revisão", "year_semver")
        autoversion_value_mode.addItem("Ano + semana ISO + revisão", "iso_week")
        autoversion_value_mode.addItem("Script Lua", "lua")
        autoversion_value_mode.setCurrentIndex(max(0, autoversion_value_mode.findData(props.get("autoversion_value_mode", "increment"))))
        autoversion_script = QtWidgets.QPlainTextEdit()
        autoversion_script.setPlainText(props.get("autoversion_lua_script", self._default_autoversion_lua_script()) or self._default_autoversion_lua_script())
        autoversion_script.setMinimumHeight(120)
        autoversion_help = QtWidgets.QLabel(
            "Incrementador: se encontrar números, incrementa o último grupo numérico.\n"
            "Ex.: 1.2.9 -> 1.2.10, FW_A09 -> FW_A10, release -> release.1.\n"
            "Lua: use ctx.current_value, ctx.kind, ctx.action e dados de tempo."
        )
        autoversion_help.setWordWrap(True)
        selected_file_label = QtWidgets.QLabel(props.get("autoversion_file", "") or "-")
        selected_file_label.setWordWrap(True)
        selected_var_label = QtWidgets.QLabel(props.get("autoversion_variable", "VERSION") or "VERSION")
        selected_var_label.setWordWrap(True)
        selected_kind_label = QtWidgets.QLabel(props.get("autoversion_kind", "string"))
        selected_kind_label.setWordWrap(True)
        autoversion_preview_label = QtWidgets.QLabel("-")
        autoversion_preview_label.setWordWrap(True)
        autoversion_delta_label = QtWidgets.QLabel("-")
        autoversion_delta_label.setWordWrap(True)
        auto_form.addRow(self.t("props.autoversion_mode", "Mode:"), autoversion_mode)
        auto_form.addRow("Lógica:", autoversion_value_mode)
        auto_form.addRow(self.t("props.autoversion_file", "Selected file:"), selected_file_label)
        auto_form.addRow(self.t("props.autoversion_var", "Selected variable:"), selected_var_label)
        auto_form.addRow(self.t("props.autoversion_kind", "Detected type:"), selected_kind_label)
        auto_form.addRow("Estratégia:", autoversion_preview_label)
        auto_form.addRow("Próximo passo:", autoversion_delta_label)
        auto_layout.addLayout(auto_form)
        auto_layout.addWidget(autoversion_help)
        auto_layout.addWidget(QtWidgets.QLabel("Script Lua:"))
        auto_layout.addWidget(autoversion_script)

        files_help = QtWidgets.QLabel(self.t("props.autoversion_files", "Detected project files:"))
        vars_help = QtWidgets.QLabel(self.t("props.autoversion_vars", "Detected file variables:"))
        file_search = QtWidgets.QLineEdit()
        file_search.setPlaceholderText(self.t("props.autoversion_files_filter", "Filter files..."))
        var_search = QtWidgets.QLineEdit()
        var_search.setPlaceholderText(self.t("props.autoversion_vars_filter", "Filter variables..."))
        files_list = QtWidgets.QListWidget()
        vars_list = QtWidgets.QListWidget()
        files_and_vars = QtWidgets.QHBoxLayout()
        left_col = QtWidgets.QVBoxLayout()
        right_col = QtWidgets.QVBoxLayout()
        left_col.addWidget(files_help)
        left_col.addWidget(file_search)
        left_col.addWidget(files_list, 1)
        right_col.addWidget(vars_help)
        right_col.addWidget(var_search)
        right_col.addWidget(vars_list, 1)
        files_and_vars.addLayout(left_col, 1)
        files_and_vars.addLayout(right_col, 1)
        auto_layout.addLayout(files_and_vars, 1)
        stack.addWidget(auto_page)

        batch_page = QtWidgets.QWidget()
        batch_layout = QtWidgets.QFormLayout(batch_page)
        batch_file_label = QtWidgets.QLabel(props.get("batch_file", "") or "-")
        batch_var_edit = QtWidgets.QLineEdit(props.get("batch_variable", "LOT"))
        batch_pattern = QtWidgets.QComboBox()
        batch_pattern.addItem("Data + Hora", "date_time")
        batch_pattern.addItem("Data", "date")
        batch_pattern.addItem("Hora", "time")
        batch_pattern.addItem("Ano + Semana + Dia", "iso_week")
        batch_pattern.setCurrentIndex(max(0, batch_pattern.findData(props.get("batch_pattern", "date_time"))))
        batch_preview = QtWidgets.QLabel("-")
        batch_file_btn = QtWidgets.QPushButton("Usar arquivo do autoversionamento")
        batch_refresh_btn = QtWidgets.QPushButton("Gerar preview")
        batch_btn_row = QtWidgets.QHBoxLayout()
        batch_btn_row.addWidget(batch_file_btn)
        batch_btn_row.addWidget(batch_refresh_btn)
        batch_btn_row.addStretch(1)
        batch_layout.addRow("Arquivo:", batch_file_label)
        batch_layout.addRow("Variável de lote:", batch_var_edit)
        batch_layout.addRow("Padrão:", batch_pattern)
        batch_layout.addRow("Preview:", batch_preview)
        batch_layout.addRow("", self._wrap_layout(batch_btn_row))
        stack.addWidget(batch_page)

        batch_mode = QtWidgets.QComboBox()
        batch_mode.addItem("Desativado", "disabled")
        batch_mode.addItem("Sempre ao exportar binário", "always_export")
        batch_mode.addItem("Sempre no upload", "always_upload")
        batch_mode.addItem("Sempre em ambos", "always_both")
        batch_mode.addItem("Perguntar ao exportar binário", "ask_export")
        batch_mode.addItem("Perguntar no upload", "ask_upload")
        batch_mode.addItem("Perguntar em ambos", "ask_both")
        batch_mode.setCurrentIndex(max(0, batch_mode.findData(props.get("batch_mode", "disabled"))))
        batch_file_combo = QtWidgets.QComboBox()
        batch_file_combo.addItem("-", "")
        batch_var_combo = QtWidgets.QComboBox()
        batch_kind_label = QtWidgets.QLabel(props.get("batch_kind", "string"))
        batch_value_mode = QtWidgets.QComboBox()
        batch_value_mode.addItem("Preset de tempo", "preset")
        batch_value_mode.addItem("Script Lua", "lua")
        batch_value_mode.setCurrentIndex(max(0, batch_value_mode.findData(props.get("batch_value_mode", "preset"))))
        batch_script = QtWidgets.QPlainTextEdit()
        batch_script.setPlainText(props.get("batch_lua_script", self._default_batch_lua_script()) or self._default_batch_lua_script())
        batch_script.setPlaceholderText("return ctx.timestamp_compact")
        batch_script.setMinimumHeight(140)
        batch_help = QtWidgets.QLabel(
            "O lote pode usar um preset simples de tempo ou um script Lua. "
            "No Lua você recebe `ctx` com data/hora, versão, projeto e ação."
        )
        batch_help.setWordWrap(True)
        batch_layout.insertRow(0, "Modo:", batch_mode)
        batch_layout.insertRow(1, "Arquivo alvo:", batch_file_combo)
        batch_layout.insertRow(2, "Variável / macro:", batch_var_combo)
        batch_layout.insertRow(3, "Tipo detectado:", batch_kind_label)
        batch_layout.insertRow(4, "Algoritmo:", batch_value_mode)
        batch_layout.insertRow(5, "Ajuda:", batch_help)
        batch_layout.addRow("Script Lua:", batch_script)

        questions_page = QtWidgets.QWidget()
        questions_layout = QtWidgets.QHBoxLayout(questions_page)
        question_slots_list = QtWidgets.QListWidget()
        question_slots_list.setFixedWidth(190)
        for slot_number in range(4):
            question_slots_list.addItem(f"Pergunta {slot_number + 1}")
        questions_layout.addWidget(question_slots_list)
        question_editor = QtWidgets.QWidget()
        question_editor_layout = QtWidgets.QVBoxLayout(question_editor)
        question_form = QtWidgets.QFormLayout()
        question_enabled = QtWidgets.QCheckBox("Ativar esta pergunta")
        question_label = QtWidgets.QLineEdit()
        question_label.setPlaceholderText("Ex.: Modelo do produto")
        question_file_combo = QtWidgets.QComboBox()
        question_file_combo.addItem("-", "")
        question_var_combo = QtWidgets.QComboBox()
        question_kind_label = QtWidgets.QLabel("string")
        question_allow_keep = QtWidgets.QCheckBox("Permitir manter o valor atual")
        question_allow_keep.setChecked(True)
        question_options = QtWidgets.QPlainTextEdit()
        question_options.setPlaceholderText("Um valor por linha, ou separado por vírgula.\nEx.: 0\n1\n2")
        question_help = QtWidgets.QLabel(
            "Essas perguntas aparecem antes de qualquer compilação, exportação ou upload.\n"
            "O nome exibido é cosmético; o nome real da variável não aparece para o usuário."
        )
        question_help.setWordWrap(True)
        question_form.addRow("", question_enabled)
        question_form.addRow("Nome exibido:", question_label)
        question_form.addRow("Arquivo:", question_file_combo)
        question_form.addRow("Variável / macro:", question_var_combo)
        question_form.addRow("Tipo detectado:", question_kind_label)
        question_form.addRow("", question_allow_keep)
        question_editor_layout.addLayout(question_form)
        question_editor_layout.addWidget(QtWidgets.QLabel("Valores possíveis:"))
        question_editor_layout.addWidget(question_options, 1)
        question_editor_layout.addWidget(question_help)
        questions_layout.addWidget(question_editor, 1)
        stack.addWidget(questions_page)

        available_files = self._list_project_source_files()
        for file_name in available_files:
            batch_file_combo.addItem(file_name, file_name)
            question_file_combo.addItem(file_name, file_name)
        filtered_files = {"items": list(available_files)}
        current_variables = {"items": []}
        files_list.addItems(filtered_files["items"])
        selected_autoversion_file = {"value": props.get("autoversion_file", "")}
        selected_autoversion_variable = {"value": props.get("autoversion_variable", "VERSION")}
        for index in range(files_list.count()):
            if files_list.item(index).text() == selected_autoversion_file["value"]:
                files_list.setCurrentRow(index)
                break

        def refresh_file_list():
            term = file_search.text().strip().lower()
            filtered_files["items"] = [item for item in available_files if not term or term in item.lower()]
            files_list.clear()
            files_list.addItems(filtered_files["items"])
            for index in range(files_list.count()):
                if files_list.item(index).text() == selected_autoversion_file["value"]:
                    files_list.setCurrentRow(index)
                    break

        def refresh_var_list():
            term = var_search.text().strip().lower()
            vars_list.clear()
            for item in current_variables["items"]:
                text = f"{item['name']} [{item['kind']}]  {item['preview']}"
                if term and term not in text.lower():
                    continue
                list_item = QtWidgets.QListWidgetItem(text)
                list_item.setData(QtCore.Qt.UserRole, item)
                vars_list.addItem(list_item)
            for index in range(vars_list.count()):
                item_data = vars_list.item(index).data(QtCore.Qt.UserRole)
                if item_data and item_data.get("name") == selected_autoversion_variable["value"]:
                    vars_list.setCurrentRow(index)
                    break

        def refresh_variables_for_selected_file():
            current_file_item = files_list.currentItem()
            current_file = current_file_item.text() if current_file_item else ""
            selected_autoversion_file["value"] = current_file
            selected_file_label.setText(current_file or "-")
            current_variables["items"] = self._extract_version_variables(current_file)
            refresh_var_list()

        def choose_variable():
            current_item = vars_list.currentItem()
            if not current_item:
                return
            data = current_item.data(QtCore.Qt.UserRole) or {}
            selected_autoversion_variable["value"] = data.get("name", "VERSION")
            selected_var_label.setText(selected_autoversion_variable["value"])
            selected_kind_label.setText(data.get("kind", "string"))

        def refresh_autoversion_preview():
            props["autoversion_value_mode"] = autoversion_value_mode.currentData() or "increment"
            props["autoversion_lua_script"] = autoversion_script.toPlainText().strip() or self._default_autoversion_lua_script()
            current_value = version.text().strip() or props.get("version", "1.0.0") or "1.0.0"
            kind = selected_kind_label.text().strip() or "string"
            autoversion_preview_label.setText(self._autoversion_strategy_label(props["autoversion_value_mode"]))
            try:
                preview_value = self._generate_autoversion_value(current_value, kind, "preview")
                autoversion_delta_label.setText(self._describe_autoversion_delta(current_value, preview_value))
            except Exception as exc:
                autoversion_delta_label.setText(f"Preview com erro: {exc}")

        files_list.currentItemChanged.connect(lambda *_: refresh_variables_for_selected_file())
        vars_list.currentItemChanged.connect(lambda *_: (choose_variable(), refresh_autoversion_preview()))
        file_search.textChanged.connect(lambda *_: refresh_file_list())
        var_search.textChanged.connect(lambda *_: refresh_var_list())
        if files_list.count() and files_list.currentRow() < 0:
            files_list.setCurrentRow(0)
        refresh_variables_for_selected_file()
        autoversion_value_mode.currentIndexChanged.connect(lambda *_: refresh_autoversion_preview())
        autoversion_script.textChanged.connect(refresh_autoversion_preview)
        version.textChanged.connect(lambda *_: refresh_autoversion_preview())
        refresh_autoversion_preview()

        def refresh_batch_kind():
            data = batch_var_combo.currentData() or {}
            batch_kind_label.setText(str(data.get("kind", "string") or "string"))

        def refresh_batch_variables():
            batch_var_combo.blockSignals(True)
            batch_var_combo.clear()
            current_file = str(batch_file_combo.currentData() or "").strip()
            items = self._extract_version_variables(current_file)
            if not items:
                batch_var_combo.addItem("-", {"name": "", "kind": "string"})
            for item in items:
                batch_var_combo.addItem(f"{item['name']} [{item['kind']}]  {item['preview']}", item)
            selected_name = str(props.get("batch_variable", "LOT") or "LOT").strip()
            selected_index = 0
            for idx in range(batch_var_combo.count()):
                data = batch_var_combo.itemData(idx) or {}
                if data.get("name") == selected_name:
                    selected_index = idx
                    break
            batch_var_combo.setCurrentIndex(selected_index)
            batch_var_combo.blockSignals(False)
            refresh_batch_kind()

        def sync_batch_visibility():
            is_lua = (batch_value_mode.currentData() or "preset") == "lua"
            batch_pattern.setEnabled(not is_lua)
            batch_script.setEnabled(is_lua)

        def generate_batch_preview():
            props["batch_value_mode"] = batch_value_mode.currentData() or "preset"
            props["batch_pattern"] = batch_pattern.currentData() or "date_time"
            props["batch_lua_script"] = batch_script.toPlainText().strip() or self._default_batch_lua_script()
            try:
                value = self._generate_batch_value(action_name="export")
                batch_preview.setText(f"Preview: {value}")
            except Exception as exc:
                batch_preview.setText(f"Preview com erro: {exc}")

        batch_file_btn.hide()
        batch_var_edit.hide()
        batch_file_combo.currentIndexChanged.connect(lambda *_: (batch_file_label.setText(str(batch_file_combo.currentData() or "") or "-"), refresh_batch_variables()))
        batch_var_combo.currentIndexChanged.connect(lambda *_: (batch_var_edit.setText(str((batch_var_combo.currentData() or {}).get("name", ""))), refresh_batch_kind()))
        batch_refresh_btn.clicked.connect(generate_batch_preview)
        batch_pattern.currentIndexChanged.connect(lambda *_: generate_batch_preview())
        batch_value_mode.currentIndexChanged.connect(lambda *_: (sync_batch_visibility(), generate_batch_preview()))
        batch_script.textChanged.connect(generate_batch_preview)
        batch_file_combo.setCurrentIndex(max(0, batch_file_combo.findData(props.get("batch_file", ""))))
        batch_file_label.setText(str(batch_file_combo.currentData() or "") or "-")
        refresh_batch_variables()
        sync_batch_visibility()
        generate_batch_preview()

        question_slots = self._normalize_compile_questions(props.get("compile_questions", []))
        question_state = {"index": 0}
        question_loading = {"value": False}

        def update_question_kind():
            data = question_var_combo.currentData() or {}
            question_kind_label.setText(str(data.get("kind", "string") or "string"))

        def populate_question_variables(relative_file: str, selected_name: str = ""):
            items = self._extract_version_variables(relative_file)
            question_var_combo.blockSignals(True)
            question_var_combo.clear()
            if not items:
                question_var_combo.addItem("-", {"name": "", "kind": "string"})
            for item in items:
                question_var_combo.addItem(f"{item['name']} [{item['kind']}]  {item['preview']}", item)
            selected_index = 0
            for idx in range(question_var_combo.count()):
                data = question_var_combo.itemData(idx) or {}
                if data.get("name") == selected_name:
                    selected_index = idx
                    break
            question_var_combo.setCurrentIndex(selected_index)
            question_var_combo.blockSignals(False)
            update_question_kind()

        def store_question_editor():
            if question_loading["value"]:
                return
            slot = question_slots[question_state["index"]]
            slot["enabled"] = question_enabled.isChecked()
            slot["label"] = question_label.text().strip()
            slot["file"] = str(question_file_combo.currentData() or "").strip()
            variable_data = question_var_combo.currentData() or {}
            slot["variable"] = str(variable_data.get("name", "") or "").strip()
            slot["kind"] = str(variable_data.get("kind", "string") or "string").strip()
            slot["allow_keep"] = question_allow_keep.isChecked()
            slot["options_text"] = question_options.toPlainText().strip()
            question_slots_list.item(question_state["index"]).setText(slot["label"] or f"Pergunta {question_state['index'] + 1}")

        def load_question_editor(index: int):
            question_loading["value"] = True
            slot = question_slots[index]
            question_enabled.setChecked(slot.get("enabled", False))
            question_label.setText(slot.get("label", ""))
            question_allow_keep.setChecked(slot.get("allow_keep", True))
            question_options.setPlainText(slot.get("options_text", ""))
            file_index = max(0, question_file_combo.findData(slot.get("file", "")))
            question_file_combo.blockSignals(True)
            question_file_combo.setCurrentIndex(file_index)
            question_file_combo.blockSignals(False)
            populate_question_variables(slot.get("file", ""), slot.get("variable", ""))
            question_kind_label.setText(slot.get("kind", "string") or "string")
            question_loading["value"] = False

        def on_question_slot_changed(index: int):
            if index < 0:
                return
            if question_slots_list.count():
                store_question_editor()
            question_state["index"] = index
            load_question_editor(index)

        question_file_combo.currentIndexChanged.connect(
            lambda *_: populate_question_variables(str(question_file_combo.currentData() or "").strip())
        )
        question_var_combo.currentIndexChanged.connect(lambda *_: update_question_kind())
        question_label.textChanged.connect(lambda *_: store_question_editor())
        question_enabled.toggled.connect(lambda *_: store_question_editor())
        question_allow_keep.toggled.connect(lambda *_: store_question_editor())
        question_options.textChanged.connect(lambda *_: store_question_editor())
        question_slots_list.currentRowChanged.connect(on_question_slot_changed)
        load_question_editor(0)
        question_slots_list.setCurrentRow(0)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        outer.addLayout(body)
        outer.addWidget(buttons)

        def choose_icon():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(dialog, "Escolher ícone do projeto", "", "Imagens (*.png *.jpg *.jpeg *.bmp *.ico)")
            if not path:
                return
            selected_icon_path["value"] = path
            icon_preview.setPixmap(self._pixmap_for_icon_path(Path(path), size=44))

        def reset_icon():
            selected_icon_path["value"] = "__DEFAULT__"
            icon_preview.setPixmap(self._pixmap_for_icon_path(self.default_project_icon_path, size=44))

        choose_icon_btn.clicked.connect(choose_icon)
        reset_icon_btn.clicked.connect(reset_icon)
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)

        def save_props():
            props["author"] = author.text().strip()
            props["version"] = version.text().strip()
            props["contributors"] = contributors.text().strip()
            props["description"] = description.toPlainText().strip()
            props["autoversion_mode"] = autoversion_mode.currentData() or "disabled"
            props["autoversion_value_mode"] = autoversion_value_mode.currentData() or "increment"
            props["autoversion_file"] = selected_autoversion_file["value"]
            props["autoversion_variable"] = selected_autoversion_variable["value"] or "VERSION"
            props["autoversion_kind"] = selected_kind_label.text().strip() or "string"
            props["autoversion_lua_script"] = autoversion_script.toPlainText().strip() or self._default_autoversion_lua_script()
            props["batch_mode"] = batch_mode.currentData() or "disabled"
            props["batch_file"] = str(batch_file_combo.currentData() or "").strip()
            batch_data = batch_var_combo.currentData() or {}
            props["batch_variable"] = str(batch_data.get("name", "LOT") or "LOT").strip()
            props["batch_kind"] = str(batch_data.get("kind", "string") or "string").strip()
            props["batch_value_mode"] = batch_value_mode.currentData() or "preset"
            props["batch_pattern"] = batch_pattern.currentData() or "date_time"
            props["batch_lua_script"] = batch_script.toPlainText().strip() or self._default_batch_lua_script()
            store_question_editor()
            props["compile_questions"] = self._normalize_compile_questions(question_slots)
            version_file = str(props.get("autoversion_file", "") or "").strip()
            version_variable = str(props.get("autoversion_variable", "VERSION") or "VERSION").strip()
            version_kind = str(props.get("autoversion_kind", "string") or "string").strip()
            if version_file and props["version"]:
                target_file = (self.current_project / version_file).resolve()
                try:
                    self._update_version_in_source_file(target_file, version_variable, props["version"], value_kind=version_kind)
                except Exception as exc:
                    self.show_error_dialog("Autoversionamento", f"Falha ao salvar versão manual: {exc}")
                    return
            icon_choice = selected_icon_path["value"]
            if icon_choice == "__DEFAULT__":
                props["icon"] = ""
            elif icon_choice:
                src = Path(icon_choice)
                if src.exists():
                    safe_name = f"project_icon{src.suffix.lower()}"
                    dest = self.current_project / safe_name
                    shutil.copy2(src, dest)
                    props["icon"] = safe_name
            self.save_config()
            self.load_recent_projects_widget()
            self._update_history_icon()
            dialog.accept()

        buttons.accepted.connect(save_props)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def open_vscode(self):
        if not self.current_project:
            return
        self.backend.open_code_editor(str(self.current_project), editor=self._editor_command())

    def open_settings_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Configurações")
        self.fit_dialog_to_screen(dialog, 980, 680)
        outer = QtWidgets.QVBoxLayout(dialog)
        intro = QtWidgets.QLabel(
            "Ajuste o comportamento global do V CLI. Estas opcoes afetam a experiencia inteira, nao so o projeto atual."
        )
        intro.setWordWrap(True)
        self._mark_muted_label(intro)
        outer.addWidget(intro)
        body = QtWidgets.QHBoxLayout()
        nav = QtWidgets.QListWidget()
        nav.setFixedWidth(220)
        nav.addItems(["Geral", "Editor", "Bibliotecas", "Placas / JSON", "inoCli", "Comandos"])
        stack = QtWidgets.QStackedWidget()
        body.addWidget(nav)
        body.addWidget(stack, 1)

        settings = self._ensure_app_setting_defaults(self.app_settings)

        general_page = QtWidgets.QWidget()
        general_form = QtWidgets.QFormLayout(general_page)
        theme_combo = QtWidgets.QComboBox()
        theme_combo.addItem("Claro", "light")
        theme_combo.addItem("Escuro", "dark")
        theme_index = theme_combo.findData(settings.get("theme", "light"))
        theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        language_combo = QtWidgets.QComboBox()
        language_combo.addItem("Automático", "auto")
        language_combo.addItem("Português", "pt")
        language_combo.addItem("English", "en")
        lang_index = language_combo.findData(settings.get("language", "auto"))
        language_combo.setCurrentIndex(lang_index if lang_index >= 0 else 0)
        tray_enabled = QtWidgets.QCheckBox("Ativar bandeja do Windows")
        tray_enabled.setChecked(bool(settings.get("tray_enabled", False)))
        minimize_to_tray = QtWidgets.QCheckBox("Ao minimizar, enviar para a bandeja")
        minimize_to_tray.setChecked(bool(settings.get("minimize_to_tray", False)))
        close_to_tray = QtWidgets.QCheckBox("Ao fechar, ocultar na bandeja em vez de encerrar")
        close_to_tray.setChecked(bool(settings.get("close_to_tray", False)))
        startup_to_tray = QtWidgets.QCheckBox("Iniciar oculto na bandeja")
        startup_to_tray.setChecked(bool(settings.get("startup_to_tray", False)))
        single_instance = QtWidgets.QCheckBox("Bloquear múltiplas instâncias da aplicação")
        single_instance.setChecked(bool(settings.get("single_instance", True)))
        startup_width = QtWidgets.QSpinBox()
        startup_width.setRange(1000, 4096)
        startup_width.setSingleStep(20)
        startup_width.setSuffix(" px")
        startup_width.setValue(int(settings.get("startup_width", 1280) or 1280))
        startup_height = QtWidgets.QSpinBox()
        startup_height.setRange(680, 2160)
        startup_height.setSingleStep(20)
        startup_height.setSuffix(" px")
        startup_height.setValue(int(settings.get("startup_height", 820) or 820))
        language_note = QtWidgets.QLabel("Idioma e tema são aplicados após salvar. O idioma pode exigir reiniciar a aplicação para refletir tudo.")
        language_note.setWordWrap(True)
        self._mark_muted_label(language_note)
        general_intro = QtWidgets.QLabel("Tema e idioma deixam o ambiente mais coerente para quem usa a ferramenta no dia a dia.")
        self._mark_muted_label(general_intro)
        general_form.addRow(general_intro)
        general_form.addRow("Tema:", theme_combo)
        general_form.addRow("Idioma:", language_combo)
        general_form.addRow("", tray_enabled)
        general_form.addRow("", minimize_to_tray)
        general_form.addRow("", close_to_tray)
        general_form.addRow("", startup_to_tray)
        general_form.addRow("Largura inicial:", startup_width)
        general_form.addRow("Altura inicial:", startup_height)
        general_form.addRow("", single_instance)
        general_form.addRow(language_note)
        stack.addWidget(general_page)

        editor_page = QtWidgets.QWidget()
        editor_form = QtWidgets.QFormLayout(editor_page)
        editor_title = QtWidgets.QLineEdit(settings.get("editor_title", "VS Code"))
        editor_command = QtWidgets.QLineEdit(settings.get("editor_command", "code"))
        editor_color = QtWidgets.QLineEdit(settings.get("editor_button_color", "#0078d4"))
        editor_title.setPlaceholderText("Como o botao vai aparecer")
        editor_command.setPlaceholderText("Ex.: code, cursor ou caminho completo")
        editor_color.setPlaceholderText("#0078d4")
        choose_color_btn = QtWidgets.QPushButton("Cor...")
        color_row = QtWidgets.QHBoxLayout()
        color_row.addWidget(editor_color, 1)
        color_row.addWidget(choose_color_btn)
        editor_form.addRow("Título do editor:", editor_title)
        editor_form.addRow("Comando do editor:", editor_command)
        editor_form.addRow("Cor do botão:", self._wrap_layout(color_row))
        editor_help = QtWidgets.QLabel("Exemplos de comando: `code`, `cursor`, caminho completo do editor.")
        self._mark_muted_label(editor_help)
        editor_form.addRow(editor_help)
        stack.addWidget(editor_page)

        libs_page = QtWidgets.QWidget()
        libs_form = QtWidgets.QFormLayout(libs_page)
        aux_repo = QtWidgets.QLineEdit(settings.get("aux_library_repo", ""))
        aux_repo.setPlaceholderText("URL opcional de um library_index.json alternativo")
        aux_info = QtWidgets.QLabel("Repositório auxiliar de bibliotecas (experimental). Use URL direta quando necessário.")
        aux_info.setWordWrap(True)
        self._mark_muted_label(aux_info)
        default_lib_info = QtWidgets.QLabel("Padrão atual: índice padrão do Arduino CLI em Arduino15/library_index.json.")
        default_lib_info.setWordWrap(True)
        self._mark_muted_label(default_lib_info)
        libs_form.addRow("Servidor / URL auxiliar:", aux_repo)
        libs_form.addRow(aux_info)
        libs_form.addRow(default_lib_info)
        stack.addWidget(libs_page)

        boards_page = QtWidgets.QWidget()
        boards_layout = QtWidgets.QVBoxLayout(boards_page)
        boards_layout.addWidget(QtWidgets.QLabel("JSONs auxiliares de placas"))
        board_urls_list = QtWidgets.QListWidget()
        for url in self.backend.get_additional_board_urls():
            board_urls_list.addItem(url)
        boards_layout.addWidget(board_urls_list, 1)
        board_url_entry = QtWidgets.QLineEdit()
        board_url_entry.setPlaceholderText("URL do índice de placas")
        boards_btns = QtWidgets.QHBoxLayout()
        board_add_btn = QtWidgets.QPushButton("Adicionar")
        board_remove_btn = QtWidgets.QPushButton("Remover selecionado")
        boards_btns.addWidget(board_add_btn)
        boards_btns.addWidget(board_remove_btn)
        boards_layout.addWidget(board_url_entry)
        boards_layout.addLayout(boards_btns)
        stack.addWidget(boards_page)

        inocli_page = QtWidgets.QWidget()
        inocli_layout = QtWidgets.QFormLayout(inocli_page)
        inocli_mode = QtWidgets.QComboBox()
        inocli_mode.addItem("Usar o da pasta do V CLI", "bundled")
        inocli_mode.addItem("Usar o pré-instalado no PATH", "path")
        inocli_mode.addItem("Usar caminho personalizado", "custom")
        inocli_mode.setCurrentIndex(max(0, inocli_mode.findData(settings.get("inocli_mode", "bundled"))))
        inocli_custom_path = QtWidgets.QLineEdit(settings.get("inocli_custom_path", ""))
        inocli_browse_btn = QtWidgets.QPushButton("...")
        inocli_path_row = QtWidgets.QHBoxLayout()
        inocli_path_row.addWidget(inocli_custom_path, 1)
        inocli_path_row.addWidget(inocli_browse_btn)
        inocli_effective_path = QtWidgets.QLabel(str(self._resolve_inocli_path()))
        inocli_effective_path.setWordWrap(True)
        inocli_version_label = QtWidgets.QLabel("-")
        inocli_version_label.setWordWrap(True)
        inocli_path_buttons = QtWidgets.QHBoxLayout()
        inocli_register_btn = QtWidgets.QPushButton("Registrar no PATH")
        inocli_unregister_btn = QtWidgets.QPushButton("Retirar do PATH")
        inocli_refresh_btn = QtWidgets.QPushButton("Atualizar info")
        for widget in [inocli_register_btn, inocli_unregister_btn, inocli_refresh_btn]:
            inocli_path_buttons.addWidget(widget)
        inocli_path_buttons.addStretch(1)
        inocli_help = QtWidgets.QLabel(
            "Padrão: usa o arduino-cli.exe da pasta onde o V CLI está rodando.\n"
            "Você também pode usar o pré-instalado no PATH do Windows ou apontar um executável personalizado."
        )
        inocli_help.setWordWrap(True)
        self._mark_muted_label(inocli_help)
        inocli_layout.addRow("Origem:", inocli_mode)
        inocli_layout.addRow("Caminho personalizado:", self._wrap_layout(inocli_path_row))
        inocli_layout.addRow("Caminho em uso:", inocli_effective_path)
        inocli_layout.addRow("Versão:", inocli_version_label)
        inocli_layout.addRow("", self._wrap_layout(inocli_path_buttons))
        inocli_layout.addRow(inocli_help)
        stack.addWidget(inocli_page)

        commands_page = QtWidgets.QWidget()
        commands_layout = QtWidgets.QFormLayout(commands_page)
        command_status = QtWidgets.QLabel(
            "Registrado no PATH do Windows." if self._is_vcli_registered_on_path() else "Ainda não registrado no PATH do Windows."
        )
        command_status.setWordWrap(True)
        path_buttons = QtWidgets.QHBoxLayout()
        register_path_btn = QtWidgets.QPushButton("Registrar")
        unregister_path_btn = QtWidgets.QPushButton("Retirar registro")
        path_buttons.addWidget(register_path_btn)
        path_buttons.addWidget(unregister_path_btn)
        path_buttons.addStretch(1)
        cmd_open = QtWidgets.QLineEdit(settings.get("command_open_template", 'vcli.cmd open "{project}"'))
        cmd_vscode = QtWidgets.QLineEdit(settings.get("command_vscode_template", 'vcli.cmd vscode "{project}"'))
        cmd_compile = QtWidgets.QLineEdit(settings.get("command_compile_template", 'vcli.cmd compile "{project}"'))
        cmd_export = QtWidgets.QLineEdit(settings.get("command_export_template", 'vcli.cmd export "{project}"'))
        cmd_upload = QtWidgets.QLineEdit(settings.get("command_upload_template", 'vcli.cmd upload "{project}" --port {port}'))
        commands_help = QtWidgets.QLabel(
            "Marcadores: {project} e {port}. Você pode personalizar os comandos exibidos/copiados para o terminal."
        )
        commands_help.setWordWrap(True)
        commands_layout.addRow("Status:", command_status)
        commands_layout.addRow("", self._wrap_layout(path_buttons))
        commands_layout.addRow("Abrir projeto:", cmd_open)
        commands_layout.addRow("Abrir editor:", cmd_vscode)
        commands_layout.addRow("Compilar:", cmd_compile)
        commands_layout.addRow("Exportar:", cmd_export)
        commands_layout.addRow("Upload:", cmd_upload)
        commands_layout.addRow(commands_help)
        stack.addWidget(commands_page)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        outer.addLayout(body)
        outer.addWidget(buttons)

        def choose_color():
            color = QtWidgets.QColorDialog.getColor(QtGui.QColor(editor_color.text() or "#0078d4"), dialog)
            if color.isValid():
                editor_color.setText(color.name())

        def add_board_url():
            url = board_url_entry.text().strip()
            if not url:
                return
            out, ok_result, err = self.backend.add_board_json_sync(url)
            if ok_result:
                board_urls_list.addItem(url)
                board_url_entry.clear()
            else:
                self.show_error_dialog("JSONs de placas", err or "Falha ao adicionar URL", out)

        def remove_board_url():
            current = board_urls_list.currentItem()
            if not current:
                return
            url = current.text()
            out, ok_result, err = self.backend.remove_board_json_sync(url)
            if ok_result:
                board_urls_list.takeItem(board_urls_list.row(current))
            else:
                self.show_error_dialog("JSONs de placas", err or "Falha ao remover URL", out)

        def save_settings():
            screen = QtWidgets.QApplication.primaryScreen()
            available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
            width_limit = max(1000, available.width() - 40)
            height_limit = max(680, available.height() - 40)
            self.app_settings["theme"] = theme_combo.currentData() or "light"
            self.app_settings["language"] = language_combo.currentData() or "auto"
            self.app_settings["tray_enabled"] = tray_enabled.isChecked()
            self.app_settings["minimize_to_tray"] = minimize_to_tray.isChecked()
            self.app_settings["close_to_tray"] = close_to_tray.isChecked()
            self.app_settings["startup_to_tray"] = startup_to_tray.isChecked()
            self.app_settings["startup_width"] = max(1000, min(int(startup_width.value()), width_limit))
            self.app_settings["startup_height"] = max(680, min(int(startup_height.value()), height_limit))
            self.app_settings["single_instance"] = single_instance.isChecked()
            self.app_settings["editor_title"] = editor_title.text().strip() or "VS Code"
            self.app_settings["editor_command"] = editor_command.text().strip() or "code"
            self.app_settings["editor_button_color"] = editor_color.text().strip() or "#0078d4"
            self.app_settings["aux_library_repo"] = aux_repo.text().strip()
            self.app_settings["inocli_mode"] = inocli_mode.currentData() or "bundled"
            self.app_settings["inocli_custom_path"] = inocli_custom_path.text().strip()
            self.app_settings["command_open_template"] = cmd_open.text().strip() or 'vcli.cmd open "{project}"'
            self.app_settings["command_vscode_template"] = cmd_vscode.text().strip() or 'vcli.cmd vscode "{project}"'
            self.app_settings["command_compile_template"] = cmd_compile.text().strip() or 'vcli.cmd compile "{project}"'
            self.app_settings["command_export_template"] = cmd_export.text().strip() or 'vcli.cmd export "{project}"'
            self.app_settings["command_upload_template"] = cmd_upload.text().strip() or 'vcli.cmd upload "{project}" --port {port}'
            self._save_app_settings()
            self.lang = self._detect_system_lang()
            self._load_i18n()
            self._apply_styles()
            self.apply_app_settings_to_ui()
            dialog.accept()

        def update_path_status():
            command_status.setText(
                "Registrado no PATH do Windows." if self._is_vcli_registered_on_path() else "Ainda não registrado no PATH do Windows."
            )

        def register_path():
            ok_result, message = self._set_vcli_path_registration(True)
            if not ok_result:
                self.show_error_dialog("PATH do Windows", message)
                return
            update_path_status()

        def unregister_path():
            ok_result, message = self._set_vcli_path_registration(False)
            if not ok_result:
                self.show_error_dialog("PATH do Windows", message)
                return
            update_path_status()

        def refresh_inocli_info():
            if inocli_mode.currentData() == "custom":
                self.app_settings["inocli_custom_path"] = inocli_custom_path.text().strip()
            self.app_settings["inocli_mode"] = inocli_mode.currentData() or "bundled"
            effective = self._resolve_inocli_path()
            inocli_effective_path.setText(str(effective))
            if effective.exists():
                try:
                    result = subprocess.run(
                        [str(effective), "version"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=20,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    version_text = ((result.stdout or "") + (result.stderr or "")).strip() or "Sem saída"
                    inocli_version_label.setText(version_text)
                except Exception as exc:
                    inocli_version_label.setText(str(exc))
            else:
                inocli_version_label.setText("Executável não encontrado.")
            inocli_custom_path.setEnabled((inocli_mode.currentData() or "bundled") == "custom")
            inocli_browse_btn.setEnabled((inocli_mode.currentData() or "bundled") == "custom")

        def browse_inocli():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(dialog, "Escolher arduino-cli", "", "Executável (*.exe);;Todos (*.*)")
            if not path:
                return
            inocli_custom_path.setText(path)
            refresh_inocli_info()

        def register_inocli_path():
            effective = self._resolve_inocli_path()
            if not effective.exists():
                self.show_error_dialog("inoCli", "Executável não encontrado para registrar no PATH.")
                return
            cli_dir = effective.parent
            original = Path.cwd()
            try:
                os.chdir(cli_dir)
                ok_result, message = self._set_vcli_path_registration(True)
            finally:
                os.chdir(original)
            if not ok_result:
                self.show_error_dialog("inoCli", message)
                return
            update_path_status()

        def unregister_inocli_path():
            effective = self._resolve_inocli_path()
            cli_dir = effective.parent
            original = Path.cwd()
            try:
                os.chdir(cli_dir)
                ok_result, message = self._set_vcli_path_registration(False)
            finally:
                os.chdir(original)
            if not ok_result:
                self.show_error_dialog("inoCli", message)
                return
            update_path_status()

        choose_color_btn.clicked.connect(choose_color)
        board_add_btn.clicked.connect(add_board_url)
        board_remove_btn.clicked.connect(remove_board_url)
        register_path_btn.clicked.connect(register_path)
        unregister_path_btn.clicked.connect(unregister_path)
        inocli_mode.currentIndexChanged.connect(lambda *_: refresh_inocli_info())
        inocli_custom_path.textChanged.connect(lambda *_: refresh_inocli_info())
        inocli_browse_btn.clicked.connect(browse_inocli)
        inocli_register_btn.clicked.connect(register_inocli_path)
        inocli_unregister_btn.clicked.connect(unregister_inocli_path)
        inocli_refresh_btn.clicked.connect(refresh_inocli_info)
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)
        refresh_inocli_info()
        buttons.accepted.connect(save_settings)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def _increment_version(self, version_text: str) -> str:
        parts = [segment for segment in str(version_text or "1.0.0").split(".")]
        normalized = []
        for segment in parts[:3]:
            digits = "".join(ch for ch in segment if ch.isdigit())
            normalized.append(int(digits) if digits else 0)
        while len(normalized) < 3:
            normalized.append(0)
        normalized[2] += 1
        return ".".join(str(value) for value in normalized[:3])

    def _increment_mixed_version(self, version_text: str) -> str:
        text = str(version_text or "").strip()
        match = re.search(r"(\d+)(?!.*\d)", text)
        if match:
            digits = match.group(1)
            bumped = str(int(digits) + 1).zfill(len(digits))
            return f"{text[:match.start(1)]}{bumped}{text[match.end(1):]}"
        if text:
            return f"{text}.1"
        return "1.0.1"

    def _generate_autoversion_value(self, old_value: str, value_kind: str, action_name: str) -> str:
        props = self._ensure_project_property_defaults()
        mode = str(props.get("autoversion_value_mode", "increment") or "increment").strip()
        if mode == "lua":
            if LuaRuntime is None:
                raise RuntimeError("Lupa/Lua não está disponível nesta instalação.")
            current = datetime.now()
            context = self._build_time_context(action_name=action_name, now=current)
            context["current_value"] = str(old_value or "")
            context["kind"] = str(value_kind or "string")
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.globals()["ctx"] = lua.table_from(context)
            lua.globals()["strftime"] = lambda fmt: current.strftime(str(fmt))
            script = str(props.get("autoversion_lua_script", "") or "").strip() or self._default_autoversion_lua_script()
            result = lua.execute(script)
            if result is None:
                result = getattr(lua.globals(), "generate", lambda *_: None)(lua.table_from(context))
            if result is None:
                raise ValueError("O script Lua do autoversionamento não retornou valor.")
            return str(result).strip()
        if value_kind == "number":
            digits = "".join(ch for ch in str(old_value or "0") if ch.isdigit())
            return str(int(digits or "0") + 1)
        if mode == "year_semver":
            return self._generate_year_semver_value(old_value)
        if mode == "iso_week":
            return self._generate_iso_week_value(old_value)
        return self._increment_mixed_version(old_value)

    def _update_version_in_source_file(self, target_file: Path, variable_name: str, new_version: str, value_kind: str = "string") -> bool:
        if not target_file.exists():
            raise FileNotFoundError(f"Arquivo de versão não encontrado: {target_file}")
        content = target_file.read_text(encoding="utf-8", errors="replace")
        escaped_var = re.escape(variable_name.strip() or "VERSION")
        if value_kind == "number":
            if not str(new_version).isdigit():
                raise ValueError("Versão configurada como número, mas o valor atual não é numérico.")
            patterns = [
                rf'(#define\s+{escaped_var}\s+)([0-9]+)',
                rf'(\b{escaped_var}\b\s*=\s*)([0-9]+)',
            ]
            replacement = rf"\g<1>{new_version}"
        else:
            patterns = [
                rf'((?:const\s+)?char\s+{escaped_var}\s*\[\s*\]\s*=\s*")([^"]*)(")',
                rf'(#define\s+{escaped_var}\s+")([^"]*)(")',
                rf'(\b{escaped_var}\b\s*=\s*")([^"]*)(")',
                rf"(\b{escaped_var}\b\s*=\s*')([^']*)(')",
            ]
            replacement = rf"\g<1>{new_version}\g<3>"
        replaced = False
        updated_content = content
        for pattern in patterns:
            updated_content, count = re.subn(pattern, replacement, updated_content, count=1)
            if count:
                replaced = True
                break
        if not replaced:
            return False
        target_file.write_text(updated_content, encoding="utf-8")
        return True

    def _should_run_autoversion(self, action_name: str):
        if not self.current_project or not self.current_config:
            return False
        props = self._ensure_project_property_defaults()
        mode = str(props.get("autoversion_mode", "disabled") or "disabled")
        if mode == "disabled":
            return False
        always_matches = {
            "always_export": {"export"},
            "always_upload": {"upload"},
            "always_both": {"export", "upload"},
            "ask_export": {"export"},
            "ask_upload": {"upload"},
            "ask_both": {"export", "upload"},
        }
        allowed = always_matches.get(mode, set())
        if action_name not in allowed:
            return False
        if mode.startswith("ask_"):
            label = "exportar binário" if action_name == "export" else "fazer upload"
            answer = QtWidgets.QMessageBox.question(
                self,
                "Autoversionamento",
                f"Deseja incrementar a versão antes de {label}?\n\nUse 'Não' quando for apenas um teste.",
            )
            return answer == QtWidgets.QMessageBox.Yes
        return True

    def _run_autoversion(self, action_name: str) -> tuple:
        if not self._should_run_autoversion(action_name):
            return True, ""
        props = self._ensure_project_property_defaults()
        version_file = str(props.get("autoversion_file", "") or "").strip()
        version_variable = str(props.get("autoversion_variable", "VERSION") or "VERSION").strip()
        value_kind = str(props.get("autoversion_kind", "string") or "string").strip()
        old_version = "1.0.0"
        if version_file:
            source_value = self._read_version_from_source_file((self.current_project / version_file).resolve(), version_variable, value_kind=value_kind)
            old_version = str(source_value or props.get("version", "1.0.0") or "1.0.0").strip()
        else:
            old_version = str(props.get("version", "1.0.0") or "1.0.0").strip()
        try:
            new_version = self._generate_autoversion_value(old_version, value_kind, action_name)
        except Exception as exc:
            return False, f"Falha ao gerar a nova versão: {exc}"
        props["version"] = new_version
        if version_file:
            target_file = (self.current_project / version_file).resolve()
            try:
                updated = self._update_version_in_source_file(target_file, version_variable, new_version, value_kind=value_kind)
            except Exception as exc:
                return False, f"Falha ao atualizar arquivo de versão: {exc}"
            if not updated:
                return False, (
                    f"Não encontrei a variável '{version_variable}' em '{version_file}' "
                    f"para atualizar para {new_version}."
                )
        self.save_config()
        self.log(f"[VERSION] {old_version} -> {new_version}")
        return True, new_version

    def open_project_folder(self):
        if not self.current_project:
            return
        try:
            subprocess.Popen(["explorer", str(self.current_project)])
        except Exception as exc:
            self.show_error_dialog(self.t("error.title", "Error"), str(exc))

    def show_about_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("About V CLI")
        self.fit_dialog_to_screen(dialog, 820, 620)
        layout = QtWidgets.QVBoxLayout(dialog)
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        hero = QtWidgets.QFrame()
        if dark:
            hero.setStyleSheet("QFrame { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #162534, stop:1 #0f1b27); border: 1px solid #334355; border-radius: 16px; }")
        else:
            hero.setStyleSheet("QFrame { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #edf6ff, stop:1 #d8ebff); border: 1px solid #c9ddf5; border-radius: 16px; }")
        hero_layout = QtWidgets.QHBoxLayout(hero)
        icon_label = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(str(self.app_icon_path))
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap.scaled(92, 92, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        icon_label.setMinimumWidth(110)
        hero_layout.addWidget(icon_label)
        text_col = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("V CLI")
        title.setStyleSheet(f"font-size: 28px; font-weight: 800; color: {'#f0f6fb' if dark else '#17324d'};")
        subtitle = QtWidgets.QLabel("Compilador profissionalizado de código aberto baseado em Arduino CLI")
        subtitle.setStyleSheet(f"font-size: 13px; color: {'#b8cadb' if dark else '#4b5563'};")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        hero_layout.addLayout(text_col, 1)
        layout.addWidget(hero)

        tech = QtWidgets.QLabel(
            "Tecnologias principais:\n"
            "Python 3 • PyQt5 • Arduino CLI • VS Code • pyserial"
        )
        tech.setWordWrap(True)
        if dark:
            tech.setStyleSheet("padding: 10px 12px; background: #141b23; border: 1px solid #334355; border-radius: 12px; color: #d8e7f5;")
        else:
            tech.setStyleSheet("padding: 10px 12px; background: #f7fbff; border: 1px solid #d7e6f3; border-radius: 12px;")
        layout.addWidget(tech)

        licenses = QtWidgets.QTextBrowser()
        licenses.setOpenExternalLinks(True)
        licenses.setHtml(
            "<h3 style='color:#17324d'>Licenças e links</h3>"
            "<p><b>Autor:</b> Valdemir DSW</p>"
            "<p><b>Repositório oficial:</b> <a href='https://github.com/Valdemir-DSW/V-CLI'>github.com/Valdemir-DSW/V-CLI</a></p>"
            "<ul>"
            "<li><b>Python</b>: PSF License - <a href='https://www.python.org/'>python.org</a></li>"
            "<li><b>PyQt5</b>: GPL/comercial (bindings Qt for Python) - <a href='https://pypi.org/project/PyQt5/'>PyQt5</a></li>"
            "<li><b>Qt</b>: LGPL/GPL/comercial - <a href='https://www.qt.io/'>qt.io</a></li>"
            "<li><b>Arduino CLI</b>: GPL v3 - <a href='https://arduino.github.io/arduino-cli/latest/'>arduino-cli</a></li>"
            "<li><b>VS Code</b>: licença Microsoft - <a href='https://code.visualstudio.com/'>VS Code</a></li>"
            "<li><b>pyserial</b>: BSD-style - <a href='https://pypi.org/project/pyserial/'>pyserial</a></li>"
            "</ul>"
        )
        licenses.setMinimumHeight(260)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(licenses, 1)
        layout.addStretch(1)
        layout.addWidget(buttons)
        dialog.exec_()

    def compile_project(self):
        if not self.current_project or not self.current_config:
            return
        ok_questions, question_msg = self._run_precompile_questions()
        if not ok_questions:
            if question_msg:
                self.show_error_dialog("Perguntas de compilação", question_msg)
            return
        fqbn = self.current_config.get("fqbn", "arduino:avr:uno")
        debug_lines = [
            "[PRE-DEBUG]",
            f"Projeto: {self.current_project.name}",
            f"Placa (FQBN): {fqbn}",
            f"Caminho: {self.current_project}",
            f"Comando: arduino-cli compile --fqbn {fqbn} {self.current_project}",
        ]
        dialog = ActionProgressDialog(self, "Compilando Projeto", "Aguarde, a compilação está em andamento...", debug_lines, abort_callback=lambda: self._request_abort_action(dialog))
        self.fit_dialog_to_screen(dialog, 720, 520)

        def worker():
            output, success, error_msg = self.backend.compile_action(str(self.current_project), fqbn, config=self.current_config)

            def done():
                dialog.finish()
                if not success:
                    self.show_error_dialog("Compilação", error_msg or "Compilação falhou", output)
                else:
                    self.log("[OK] Compilação concluída com sucesso")
                    self.log(f"[FILE] Binário em: {self.current_project}\\build")
                    self.log_build_summary(output)
                    self.show_compile_success_dialog(output, "Compilação concluída com sucesso")

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()
        dialog.exec_()

    def upload_project(self):
        if not self.current_project or not self.current_config:
            return
        ok_questions, question_msg = self._run_precompile_questions()
        if not ok_questions:
            if question_msg:
                self.show_error_dialog("Perguntas de compilação", question_msg)
            return
        ok_version, version_info = self._run_autoversion("upload")
        if not ok_version:
            self.show_error_dialog("Autoversionamento", version_info)
            return
        port = self.current_config.get("port", "auto")
        if not port or port == "auto":
            port = self.port_combo.currentText()
        if not port or port == "auto":
            QtWidgets.QMessageBox.warning(self, self.t("warn.title", "Warning"), "Defina uma porta serial antes do upload.")
            return
        fqbn = self.current_config.get("fqbn", "arduino:avr:uno")
        debug_lines = [
            "[PRE-DEBUG]",
            f"Projeto: {self.current_project.name}",
            f"Placa (FQBN): {fqbn}",
            f"Porta: {port}",
            f"Versão do projeto: {self.current_config.get('properties', {}).get('version', '1.0.0')}",
            f"Comando 1: arduino-cli compile --fqbn {fqbn} {self.current_project}",
            f"Comando 2: arduino-cli upload -p {port} --fqbn {fqbn} {self.current_project}",
        ]
        dialog = ActionProgressDialog(self, "Upload de Firmware", "Compilando antes do envio...", debug_lines, abort_callback=lambda: self._request_abort_action(dialog))
        self.fit_dialog_to_screen(dialog, 720, 540)

        def worker():
            compile_output, compile_ok, compile_err = self.backend.compile_action(str(self.current_project), fqbn, config=self.current_config)
            if not compile_ok:
                def compile_fail():
                    dialog.finish()
                    self.show_error_dialog("Upload (compilação)", compile_err or "Compilação falhou", compile_output)
                self.bridge.invoke.emit(compile_fail)
                return

            def update_step():
                dialog.set_subtitle("Compilação concluída. Enviando para a placa...")
                dialog.append_debug("[OK] Compilação concluída")
            self.bridge.invoke.emit(update_step)

            upload_output, upload_ok, upload_err = self.backend.upload_action(str(self.current_project), fqbn, port, config=self.current_config)

            def done():
                dialog.finish()
                if not upload_ok:
                    self.show_error_dialog("Upload", upload_err or "Upload falhou", upload_output)
                else:
                    self.log("[OK] Upload concluído com sucesso")
                    self.log("[SUCCESS] Placa reprogramada!")
                    self.log_build_summary(compile_output)
                    self.show_compile_success_dialog(compile_output, "Upload concluído com sucesso")

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()
        dialog.exec_()

    def export_binary(self):
        if not self.current_project or not self.current_config:
            return
        ok_questions, question_msg = self._run_precompile_questions()
        if not ok_questions:
            if question_msg:
                self.show_error_dialog("Perguntas de compilação", question_msg)
            return
        ok_version, version_info = self._run_autoversion("export")
        if not ok_version:
            self.show_error_dialog("Autoversionamento", version_info)
            return
        ok_batch, batch_info = self._run_batch_autofill("export")
        if not ok_batch:
            self.show_error_dialog("Lote", batch_info)
            return
        fqbn = self.current_config.get("fqbn", "arduino:avr:uno")
        debug_lines = [
            "[PRE-DEBUG]",
            f"Projeto: {self.current_project.name}",
            f"Placa (FQBN): {fqbn}",
            f"Versão do projeto: {self.current_config.get('properties', {}).get('version', '1.0.0')}",
            f"Comando: arduino-cli compile --fqbn {fqbn} --export-binaries {self.current_project}",
        ]
        dialog = ActionProgressDialog(self, "Exportar Binário", "Gerando binários da compilação...", debug_lines, abort_callback=lambda: self._request_abort_action(dialog))
        self.fit_dialog_to_screen(dialog, 720, 500)

        def worker():
            output, success, error_msg = self.backend.export_binary_action(str(self.current_project), fqbn, config=self.current_config)

            def done():
                dialog.finish()
                if not success:
                    self.show_error_dialog("Exportar binário", error_msg or "Exportação falhou", output)
                else:
                    self.log(f"[SUCCESS] Binário exportado em: {self.current_project}\\build")
                    self.log_build_summary(output)
                    self.show_compile_success_dialog(output, "Exportação concluída com sucesso")

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()
        dialog.exec_()

    def load_initial_data(self):
        self.load_boards()
        self.load_installed_libraries()
        self.refresh_serial_ports()
        self.show_cli_help()

    def start_initial_loading(self):
        self.startup_dialog = QtWidgets.QProgressDialog("Carregando placas, bibliotecas e portas...", None, 0, 0, self)
        self.startup_dialog.setWindowTitle("Carregando")
        self.startup_dialog.setCancelButton(None)
        self.startup_dialog.setMinimumDuration(0)
        self.startup_dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setEnabled(False)
        self.startup_dialog.show()

        def worker():
            error_msg = ""
            boards = []
            libs = []
            ports = []
            updates = []
            lib_updates = []
            try:
                boards = self.backend.list_boards()
                libs = self.backend.list_libraries_fixed()
                ports = self.get_serial_ports()
                updates = self.backend.list_core_updates()
                lib_updates = self.backend.list_library_updates()
            except Exception as exc:
                error_msg = str(exc)

            def done():
                self.setEnabled(True)
                if self.startup_dialog:
                    self.startup_dialog.close()
                    self.startup_dialog = None
                if error_msg:
                    self.log(f"[WARN] Falha no carregamento inicial: {error_msg}")
                self.populate_boards_table(boards)
                self.populate_installed_libraries(libs, lib_updates)
                self.available_ports = ports
                self.update_board_updates_count(len(updates or []))
                self.refresh_serial_ports()
                self.show_cli_help()

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def load_boards(self):
        self.log("[BOARDS] Carregando...")

        def worker():
            boards = self.backend.list_boards()
            updates = self.backend.list_core_updates()

            def done():
                self.populate_boards_table(boards)
                self.update_board_updates_count(len(updates or []))

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def populate_boards_table(self, boards):
        self.boards_cache = boards or []
        self.boards_cache_time = time.time()
        self.boards_table.setRowCount(len(self.boards_cache))
        for row, board in enumerate(self.boards_cache):
            self.boards_table.setItem(row, 0, QtWidgets.QTableWidgetItem(board.get("name", "")))
            self.boards_table.setItem(row, 1, QtWidgets.QTableWidgetItem(board.get("fqbn", "")))

    def update_board_updates_count(self, count: int):
        self.board_updates_count = int(count or 0)
        self._refresh_board_updates_indicator()

    def update_libs_updates_count(self, count: int):
        self.libs_updates_count = int(count or 0)
        self._refresh_libs_updates_indicator()

    def _toggle_board_updates_flash(self):
        self.board_updates_flash_on = not self.board_updates_flash_on
        self._refresh_board_updates_indicator()

    def _toggle_libs_updates_flash(self):
        self.libs_updates_flash_on = not self.libs_updates_flash_on
        self._refresh_libs_updates_indicator()

    def _refresh_board_updates_indicator(self):
        if not hasattr(self, "board_updates_label"):
            return
        count = self.board_updates_count
        self.board_updates_label.setText(f"{self.t('mgr.filter.updates', 'Pending updates')}: {count}")
        is_boards_tab = self.tabs.currentWidget() is self.boards_table.parentWidget()
        if count > 0 and is_boards_tab:
            if not self.board_updates_timer.isActive():
                self.board_updates_flash_on = True
                self.board_updates_timer.start()
            color = "#c62828" if self.board_updates_flash_on else "#7f1d1d"
            self.board_updates_label.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: 800; padding: 4px 8px;"
            )
        elif count > 0:
            if self.board_updates_timer.isActive():
                self.board_updates_timer.stop()
            self.board_updates_label.setStyleSheet(
                "color: #9a3412; font-size: 12px; font-weight: 700; padding: 4px 8px;"
            )
        else:
            if self.board_updates_timer.isActive():
                self.board_updates_timer.stop()
            self.board_updates_label.setStyleSheet(
                "color: #6b7280; font-size: 12px; font-weight: 600; padding: 4px 8px;"
            )

    def _refresh_libs_updates_indicator(self):
        if not hasattr(self, "libs_updates_label"):
            return
        count = self.libs_updates_count
        self.libs_updates_label.setText(f"{self.t('mgr.filter.updates', 'Pending updates')}: {count}")
        is_libs_tab = self.tabs.currentWidget() is self.libs_table.parentWidget()
        dark = str(self.app_settings.get("theme", "light") or "light").strip().lower() == "dark"
        neutral = "#9aa8b6" if dark else "#6b7280"
        warn = "#ff8a65" if dark else "#9a3412"
        flash_a = "#ff6b6b" if dark else "#c62828"
        flash_b = "#d9485f" if dark else "#7f1d1d"
        if count > 0 and is_libs_tab:
            if not self.libs_updates_timer.isActive():
                self.libs_updates_flash_on = True
                self.libs_updates_timer.start()
            color = flash_a if self.libs_updates_flash_on else flash_b
            self.libs_updates_label.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: 800; padding: 4px 8px;"
            )
        elif count > 0:
            if self.libs_updates_timer.isActive():
                self.libs_updates_timer.stop()
            self.libs_updates_label.setStyleSheet(
                f"color: {warn}; font-size: 12px; font-weight: 700; padding: 4px 8px;"
            )
        else:
            if self.libs_updates_timer.isActive():
                self.libs_updates_timer.stop()
            self.libs_updates_label.setStyleSheet(
                f"color: {neutral}; font-size: 12px; font-weight: 600; padding: 4px 8px;"
            )

    def select_board_from_table(self):
        self.tabs.setCurrentWidget(self.boards_table.parentWidget())

    def open_boards_dialog(self):
        if not self.current_project:
            QtWidgets.QMessageBox.warning(self, self.t("warn.title", "Warning"), self.t("warn.select_project_first", "Select a project first"))
            return
        if not self.boards_cache:
            self.load_boards()
            QtWidgets.QMessageBox.information(self, self.t("info.title", "Info"), self.t("info.loading_boards", "Loading boards..."))
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Selecionar Placa")
        dialog.resize(720, 480)
        layout = QtWidgets.QVBoxLayout(dialog)
        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Pesquisar placa...")
        layout.addWidget(search)
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Placa", "FQBN"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(table, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        filtered = []

        def refresh():
            term = search.text().strip().lower()
            filtered.clear()
            for board in self.boards_cache:
                name = board.get("name", "")
                fqbn = board.get("fqbn", "")
                if term and term not in name.lower() and term not in fqbn.lower():
                    continue
                filtered.append(board)
            table.setRowCount(len(filtered))
            for row, board in enumerate(filtered):
                table.setItem(row, 0, QtWidgets.QTableWidgetItem(board.get("name", "")))
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(board.get("fqbn", "")))
            if filtered:
                table.selectRow(0)

        def confirm():
            row = table.currentRow()
            if row < 0 or row >= len(filtered):
                return
            self.apply_board_selection(filtered[row].get("fqbn", ""), filtered[row].get("name", ""))
            dialog.accept()

        search.textChanged.connect(lambda *_: refresh())
        table.itemDoubleClicked.connect(lambda *_: confirm())
        buttons.accepted.connect(confirm)
        buttons.rejected.connect(dialog.reject)
        refresh()
        dialog.exec_()

    def apply_selected_board(self):
        row = self.boards_table.currentRow()
        if row < 0:
            return
        fqbn_item = self.boards_table.item(row, 1)
        name_item = self.boards_table.item(row, 0)
        fqbn = fqbn_item.text().strip() if fqbn_item else ""
        name = name_item.text().strip() if name_item else ""
        if not fqbn or not self.current_config:
            return
        self.apply_board_selection(fqbn, name)
        self.tabs.setCurrentIndex(0)

    def apply_board_selection(self, fqbn: str, name: str = ""):
        if not fqbn or not self.current_config:
            return
        old_fqbn = self.current_config.get("fqbn", "")
        if old_fqbn and old_fqbn != fqbn:
            self.current_config["variant"] = ""
            self.current_config.pop("tools", None)
            self.log("⚠ Configurações resetadas (placa mudou)")
        self.current_config["fqbn"] = fqbn
        self.board_display.setText(fqbn)
        self.save_config()
        self.log(f"Placa selecionada: {fqbn}")
        if name:
            self.log(name)
        self.load_board_details_async(fqbn)

    def load_board_details_async(self, fqbn: str):
        if not fqbn or not self.current_config:
            return
        self.clear_dynamic_board_details()
        loading = QtWidgets.QLabel("Carregando configurações da placa...")
        loading.setObjectName("mutedLabel")
        loading.setStyleSheet("font-style: italic;")
        self.dynamic_form.insertWidget(0, loading)

        def worker():
            variants = self.backend.get_board_variants(fqbn)
            tools = self.backend.get_platform_tools(fqbn)

            def done():
                self.update_board_details(variants, tools)

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def update_board_details(self, variants, tools):
        self.clear_dynamic_board_details()
        self.variant_options = variants or []
        self.dynamic_tool_controls = {}

        if variants:
            selected_variant = self._find_option(variants, self.current_config.get("variant", ""))
            if not selected_variant and variants:
                selected_variant = variants[0]
                self.current_config["variant"] = selected_variant.get("id", "")
            label = QtWidgets.QLabel(selected_variant.get("name") or selected_variant.get("id", "Nenhuma"))
            row = self._build_setting_row("Variante:", label, [
                ("...", lambda: self.open_option_modal("Variante", self.variant_options, self.current_config.get("variant", ""), lambda opt: self.set_variant_value(opt, label))),
            ])
            self.dynamic_form.insertLayout(self.dynamic_form.count() - 1, row)

        saved_tools = self.current_config.setdefault("tools", {})
        for tool in tools or []:
            tool_name = tool.get("name", tool.get("id", ""))
            tool_id = tool.get("id", "")
            if not tool_id:
                continue
            values = tool.get("values", [])
            selected_option = self._find_option(values, saved_tools.get(tool_id) or tool.get("selected", ""))
            if selected_option:
                saved_tools[tool_id] = selected_option.get("id", "")
            display = QtWidgets.QLabel((selected_option.get("name") or selected_option.get("id")) if selected_option else "Automático")
            row = self._build_setting_row(
                f"{tool_name}:",
                display,
                [("...", lambda _checked=False, t=tool, tid=tool_id, lbl=display, tname=tool_name: self.open_option_modal(tname, t.get("values", []), saved_tools.get(tid, ""), lambda opt, tool_key=tid, label_widget=lbl: self.set_tool_value(tool_key, opt, label_widget)))]
            )
            self.dynamic_form.insertLayout(self.dynamic_form.count() - 1, row)

        self.save_config()

    def set_variant_value(self, option: dict, display_label: QtWidgets.QLabel):
        if not option:
            return
        self.current_config["variant"] = option.get("id", "")
        display_label.setText(option.get("name", option.get("id", "")))
        self.save_config()

    def set_tool_value(self, tool_id: str, option: dict, display_label: QtWidgets.QLabel):
        if not option:
            return
        tools = self.current_config.setdefault("tools", {})
        tools[tool_id] = option.get("id", "")
        display_label.setText(option.get("name", option.get("id", "")))
        self.save_config()

    def load_installed_libraries(self):
        self.log("[LIBS] Carregando bibliotecas instaladas...")

        def worker():
            libs = self.backend.list_libraries_fixed()
            updates = self.backend.list_library_updates()

            def done():
                self.populate_installed_libraries(libs, updates)

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def populate_installed_libraries(self, libs, updates=None):
        self.loaded_libraries = libs or []
        updates_by_name = {str(item.get("name", "")).strip().lower(): item for item in (updates or [])}
        self.update_libs_updates_count(len(updates_by_name))
        self.libs_table.setRowCount(len(self.loaded_libraries))
        for row, lib in enumerate(self.loaded_libraries):
            name = lib.get("name", "")
            version = lib.get("version", "")
            desc = (lib.get("sentence", "") or "")[:120]
            update_info = updates_by_name.get(str(name).strip().lower())
            version_text = version
            if update_info:
                version_text = f"{version}  [update: {update_info.get('latest_version', '')}]"
            items = [
                QtWidgets.QTableWidgetItem(name),
                QtWidgets.QTableWidgetItem(version_text),
                QtWidgets.QTableWidgetItem(desc),
            ]
            if update_info:
                for item in items:
                    item.setBackground(QtGui.QColor("#fff3cd"))
                    item.setForeground(QtGui.QColor("#7a4f01"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip(f"Atualizacao disponivel para {name}")
            else:
                for item in items:
                    item.setToolTip("Biblioteca instalada sem atualizacao pendente no catalogo atual.")
            self.libs_table.setItem(row, 0, items[0])
            self.libs_table.setItem(row, 1, items[1])
            self.libs_table.setItem(row, 2, items[2])
        return
        self.loaded_libraries = libs or []
        updates_by_name = {str(item.get("name", "")).strip().lower(): item for item in (updates or [])}
        self.libs_table.setRowCount(len(self.loaded_libraries))
        for row, lib in enumerate(self.loaded_libraries):
            name = lib.get("name", "")
            version = lib.get("version", "")
            desc = (lib.get("sentence", "") or "")[:120]
            items = [
                QtWidgets.QTableWidgetItem(name),
                QtWidgets.QTableWidgetItem(version),
                QtWidgets.QTableWidgetItem(desc),
            ]
            update_info = updates_by_name.get(str(name).strip().lower())
            if update_info:
                for item in items:
                    item.setBackground(QtGui.QColor("#fff3cd"))
                    item.setForeground(QtGui.QColor("#7a4f01"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                items[1].setText(f"{version} → {update_info.get('latest_version', '')}")
                for item in items:
                    item.setToolTip(f"Atualização disponível para {name}")
            self.libs_table.setItem(row, 0, items[0])
            self.libs_table.setItem(row, 1, items[1])
            self.libs_table.setItem(row, 2, items[2])

    def install_library_zip(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Selecionar biblioteca ZIP", "", "ZIP (*.zip)")
        if not path:
            return
        self.run_project_action("Instalando biblioteca ZIP", lambda: self.backend.install_library_zip_sync(path))

    def run_project_action(self, title: str, action, success_title: str = "", on_success=None, abortable: bool = False):
        debug_lines = [
            f"[ACTION] {title}",
            f"Projeto: {self.current_project.name if self.current_project else 'N/A'}",
        ]
        dialog = ActionProgressDialog(
            self,
            title,
            "Executando operaÃ§Ã£o...",
            debug_lines,
            abort_callback=(lambda: self._request_abort_action(dialog)) if abortable else None,
        )
        self.fit_dialog_to_screen(dialog, 700, 480)

        def worker():
            result = action()
            if not isinstance(result, tuple):
                result = ("", False, "Retorno invÃ¡lido da operaÃ§Ã£o")
            output = result[0] if len(result) > 0 else ""
            success = bool(result[1]) if len(result) > 1 else False
            error_msg = result[2] if len(result) > 2 else ""

            def done():
                dialog.finish()
                if not success:
                    if str(error_msg or "").strip().lower() == "operacao abortada pelo usuario":
                        self.log(output or f"[ABORT] {title}")
                        QtWidgets.QMessageBox.information(self, title, output or "OperaÃ§Ã£o abortada pelo usuÃ¡rio.")
                        return
                    self.show_error_dialog(title, error_msg or "OperaÃ§Ã£o falhou", output)
                    return
                self.log(output or f"[OK] {title}")
                if callable(on_success):
                    on_success(output)
                if success_title:
                    QtWidgets.QMessageBox.information(self, success_title, output or "OperaÃ§Ã£o concluÃ­da com sucesso.")

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()
        dialog.exec_()

    def backup_installed_libraries(self):
        if not self.current_project:
            QtWidgets.QMessageBox.information(self, "Backup de bibliotecas", "Abra um projeto para salvar o backup das bibliotecas.")
            return

        def after_success(_output):
            self.load_installed_libraries()

        self.run_project_action(
            "Backup de bibliotecas",
            lambda: self.backend.create_libraries_backup(str(self.current_project)),
            success_title="Backup de bibliotecas",
            on_success=after_success,
            abortable=True,
        )

    def restore_libraries_backup(self):
        start_dir = str(self.current_project if self.current_project else Path.cwd())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Selecionar backup de bibliotecas", start_dir, "ZIP (*.zip)")
        if not path:
            return

        info, ok, err = self.backend.inspect_libraries_backup(path)
        if not ok:
            self.show_error_dialog("Restaurar backup de bibliotecas", err or "NÃ£o foi possÃ­vel ler o backup.")
            return

        conflicts = info.get("conflicts", [])
        overwrite_existing = False
        if conflicts:
            lines = [
                "Foram encontradas bibliotecas jÃ¡ instaladas.",
                "",
                "VersÃµes em conflito:",
            ]
            for item in conflicts[:15]:
                lines.append(
                    f"- {item.get('name', '?')}: instalada {item.get('installed_version', 'N/A')} | backup {item.get('backup_version', 'N/A')}"
                )
            if len(conflicts) > 15:
                lines.append(f"- ... e mais {len(conflicts) - 15} conflito(s)")
            lines.extend(
                [
                    "",
                    "Sim: sobrescrever as instaladas",
                    "NÃ£o: manter as instaladas e restaurar apenas as faltantes",
                    "Cancelar: abortar agora",
                ]
            )
            answer = QtWidgets.QMessageBox.question(
                self,
                "Restaurar backup de bibliotecas",
                "\n".join(lines),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.No,
            )
            if answer == QtWidgets.QMessageBox.Cancel:
                return
            overwrite_existing = answer == QtWidgets.QMessageBox.Yes
        else:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Restaurar backup de bibliotecas",
                f"Restaurar {info.get('library_count', 0)} biblioteca(s) do backup agora?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return

        def after_success(_output):
            self.load_installed_libraries()

        self.run_project_action(
            "Restaurando backup de bibliotecas",
            lambda: self.backend.restore_libraries_backup(path, overwrite_existing=overwrite_existing),
            success_title="Restaurar backup de bibliotecas",
            on_success=after_success,
            abortable=True,
        )

    def open_library_manager(self):
        dialog = LibraryManagerDialog(self)
        dialog.exec_()

    def open_board_manager(self):
        dialog = BoardManagerDialog(self)
        dialog.exec_()

    def show_cli_help(self):
        lines = [
            "Arduino-CLI Examples:",
            "=" * 60,
            "",
            "core list              - List installed boards",
            "core search --all      - Search all available boards",
            "lib list               - List installed libraries",
            "lib search <name>      - Search for a library",
            "board listall          - List all supported boards",
            "version                - Show Arduino-CLI version",
            "",
            "Tip: Type a command above and press Enter or click Execute",
        ]
        self.cli_text.setPlainText("\n".join(lines))

    def execute_cli(self):
        cmd = self.cli_input.text().strip()
        if not cmd:
            return
        self.cli_text.setPlainText(f"$ arduino-cli {cmd}\n{'=' * 60}\n")
        self.cli_execute_btn.setEnabled(False)

        def worker():
            output = self.backend.run_cli_sync(cmd.split())

            def done():
                self.cli_text.appendPlainText(output or "(no output)")
                self.cli_execute_btn.setEnabled(True)

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def get_serial_ports(self):
        try:
            import serial.tools.list_ports

            return [port.device for port in serial.tools.list_ports.comports()]
        except Exception:
            return ["COM3", "COM4"]

    def refresh_serial_ports(self):
        self.available_ports = self.get_serial_ports()
        current = self.port_combo.currentText() or "auto"
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem("auto")
        self.port_combo.addItems(self.available_ports)
        self._set_combo_value(self.port_combo, current)
        self.port_combo.blockSignals(False)

    def _serial_logs_dir(self, create: bool = False) -> Path:
        base = self.current_project if self.current_project else Path.cwd()
        logs_dir = base / "logs"
        if create:
            logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def _sync_serial_decode_mode(self, text: str):
        combos = [self.serial_decode_combo, self.serial_plot_decode_combo, self.serial_csv_decode_combo]
        for combo in combos:
            if combo.currentText() == text:
                continue
            combo.blockSignals(True)
            combo.setCurrentText(text)
            combo.blockSignals(False)

    def _selected_plot_series(self):
        selected = []
        for index in range(self.serial_series_list.count()):
            item = self.serial_series_list.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                selected.append(item.text())
        return selected

    def _sync_series_selector(self):
        existing = {}
        for index in range(self.serial_series_list.count()):
            item = self.serial_series_list.item(index)
            existing[item.text()] = item.checkState()
        visible_limit = self.serial_plot_series_limit.value() if hasattr(self, "serial_plot_series_limit") else 4
        self.serial_series_list.blockSignals(True)
        self.serial_series_list.clear()
        for index, name in enumerate(self.serial_plot_series.keys()):
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            state = existing.get(name)
            if state is None:
                state = QtCore.Qt.Checked if index < visible_limit else QtCore.Qt.Unchecked
            item.setCheckState(state)
            self.serial_series_list.addItem(item)
        self.serial_series_list.blockSignals(False)

    def _refresh_live_plot(self):
        self.serial_plot_widget.set_data(
            self.serial_plot_series,
            selected_series=self._selected_plot_series(),
            plot_type=self.serial_plot_type_combo.currentText(),
        )

    def _refresh_csv_table(self):
        if not self.serial_live_records:
            self.serial_csv_table.setRowCount(0)
            self.serial_csv_table.setColumnCount(0)
            self.serial_csv_summary.setText("Aguardando CSV e eventos do stream serial...")
            self.serial_csv_errors.setPlainText("\n\n".join(self.serial_live_errors[-20:]) or "Sem erros no stream.")
            return
        base_headers = ["timestamp", "elapsed_ms", "rx_fps"]
        headers = base_headers + list(self.serial_live_headers)
        self.serial_csv_table.setColumnCount(len(headers))
        self.serial_csv_table.setHorizontalHeaderLabels(headers)
        self.serial_csv_table.setRowCount(len(self.serial_live_records))
        for row_index, record in enumerate(self.serial_live_records):
            for col_index, header in enumerate(headers):
                value = record.get(header, "")
                self.serial_csv_table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(str(value)))
        self.serial_csv_table.scrollToBottom()
        self.serial_csv_summary.setText(
            f"Pacotes CSV {len(self.serial_live_records)}   •   Colunas {len(self.serial_live_headers)}   •   "
            f"Erros {len(self.serial_live_errors)}   •   FPS {self.serial_fps_label.text().replace('RX FPS: ', '')}"
        )
        self.serial_csv_errors.setPlainText("\n\n".join(self.serial_live_errors[-20:]) or "Sem erros no stream.")

    def _reset_live_serial_views(self):
        self.serial_live_headers = []
        self.serial_live_records = []
        self.serial_live_errors = []
        self.serial_plot_series = {}
        self.serial_last_rx_ts = None
        self.serial_line_counter = 0
        self.serial_fps_label.setText("RX FPS: 0.00")
        self.serial_plot_fps_label.setText("RX FPS: 0.00")
        self._sync_series_selector()
        self._refresh_live_plot()
        self._refresh_csv_table()

    def _sanitize_log_name(self, text: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in str(text or "").strip())
        return cleaned.strip("._-") or datetime.now().strftime("log_%Y%m%d_%H%M%S")

    def start_serial_recording(self):
        if self.serial_recording_active:
            return
        logs_dir = self._serial_logs_dir(create=True)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_path = logs_dir / f".recording_{session_id}.jsonl"
        self.serial_recording_session = {
            "id": session_id,
            "temp_path": temp_path,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "project": self.current_project.name if self.current_project else "",
            "description": "",
        }
        temp_path.write_text("", encoding="utf-8")
        self.serial_recording_active = True
        self.serial_rec_btn.setEnabled(False)
        self.serial_stop_rec_btn.setEnabled(True)
        self.log(f"[SERIAL] Gravação CSV iniciada: {temp_path.name}")

    def _write_recording_event(self, record: dict):
        if not self.serial_recording_active or not self.serial_recording_session:
            return
        temp_path = self.serial_recording_session["temp_path"]
        try:
            with open(temp_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.log(f"[ERRO] Falha ao gravar log temporário: {exc}")

    def stop_serial_recording(self):
        if not self.serial_recording_active or not self.serial_recording_session:
            return
        dialog = RecordingSaveDialog(self, f"log_{self.serial_recording_session['id']}")
        result = dialog.exec_()
        name, description = dialog.values()
        temp_path = self.serial_recording_session["temp_path"]
        self.serial_recording_active = False
        self.serial_rec_btn.setEnabled(True)
        self.serial_stop_rec_btn.setEnabled(False)
        if result == 2:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.log("[SERIAL] Gravação descartada.")
            self.serial_recording_session = None
            return
        if result != 1:
            self.serial_recording_active = True
            self.serial_rec_btn.setEnabled(False)
            self.serial_stop_rec_btn.setEnabled(True)
            return
        records = []
        try:
            with open(temp_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception as exc:
            self.show_error_dialog("Log CSV", str(exc))
            self.serial_recording_session = None
            return

        csv_headers = []
        for record in records:
            for key in record.keys():
                if key not in {"event_type", "timestamp", "date", "time", "elapsed_ms", "rx_fps", "line_index", "error_flag", "error_message", "raw"} and key not in csv_headers:
                    csv_headers.append(key)
        final_name = self._sanitize_log_name(name)
        logs_dir = self._serial_logs_dir(create=True)
        csv_path = logs_dir / f"{final_name}.csv"
        meta_path = logs_dir / f"{final_name}.meta.json"
        fieldnames = ["event_type", "timestamp", "date", "time", "elapsed_ms", "rx_fps", "line_index", "error_flag", "error_message", "raw"] + csv_headers
        try:
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    row = {key: record.get(key, "") for key in fieldnames}
                    writer.writerow(row)
            meta = {
                "name": final_name,
                "description": description,
                "started_at": self.serial_recording_session.get("started_at"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "project": self.serial_recording_session.get("project", ""),
                "row_count": len(records),
                "error_count": sum(1 for item in records if item.get("error_flag")),
                "headers": csv_headers,
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.unlink(missing_ok=True)
            self.log(f"[SERIAL] Log CSV salvo: {csv_path}")
        except Exception as exc:
            self.show_error_dialog("Log CSV", str(exc))
        self.serial_recording_session = None

    def open_csv_log_viewer(self):
        dialog = CsvLogBrowserDialog(self, self._serial_logs_dir(create=False))
        dialog.exec_()

    def open_external_csv_log_viewer(self):
        logs_dir = self._serial_logs_dir(create=False)
        start_dir = str(logs_dir if logs_dir.exists() else (self.current_project or Path.cwd()))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Abrir log externo", start_dir, "CSV (*.csv)")
        if not path:
            return
        dialog = CsvLogViewerDialog(self, Path(path))
        dialog.exec_()

    def open_code_editor_dialog(self):
        if not self.current_project:
            QtWidgets.QMessageBox.information(self, "Code Editor", "Abra um projeto para usar o Code Editor.")
            return
        dialog = CodeEditorDialog(self, self.current_project)
        dialog.exec_()

    def _run_git_command(self, args: list[str]):
        if not self.current_project:
            QtWidgets.QMessageBox.information(self, "Git", "Abra um projeto para usar a aba Git.")
            return
        self.git_output.appendPlainText(f"$ git {' '.join(args)}")
        if hasattr(self, "git_remote_history"):
            self.git_remote_history.appendPlainText(f"$ git {' '.join(args)}")
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.current_project),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = (result.stdout or "") + (result.stderr or "")
            self.git_output.appendPlainText(output.strip() or "(sem saída)")
            if hasattr(self, "git_remote_history"):
                self.git_remote_history.appendPlainText(output.strip() or "(sem saída)")
        except Exception as exc:
            self.git_output.appendPlainText(str(exc))
            if hasattr(self, "git_remote_history"):
                self.git_remote_history.appendPlainText(str(exc))
        self.git_output.appendPlainText("")
        if hasattr(self, "git_remote_history"):
            self.git_remote_history.appendPlainText("")
        self._refresh_git_ui()

    def _git_commit(self):
        message = self.git_commit_edit.text().strip()
        if not message:
            QtWidgets.QMessageBox.information(self, "Git", "Digite uma mensagem de commit.")
            return
        self._run_git_command(["commit", "-m", message])

    def _git_set_remote(self):
        remote = self.git_remote_edit.text().strip()
        if not remote:
            QtWidgets.QMessageBox.information(self, "Git", "Digite a URL do remote.")
            return
        self._run_git_command(["remote", "remove", "origin"])
        self._run_git_command(["remote", "add", "origin", remote])

    def refresh_serial_status(self, connected: bool, port: str = "", baud: str = ""):
        if connected:
            connect_text = self.t("serial.disconnect", "Disconnect")
            status_text = f"{self.t('serial.status_connected', 'Status: connected')} {port or 'auto'} @ {baud or '115200'}"
        else:
            saved_port = self.port_combo.currentText() or "auto"
            saved_baud = self.baud_combo.currentText() or "115200"
            connect_text = self.t("serial.connect", "Connect")
            status_text = f"{self.t('serial.status', 'Status:')} {saved_port} @ {saved_baud}"
        for button in [self.serial_toggle_btn, self.serial_plot_toggle_btn, self.serial_csv_toggle_btn]:
            button.setText(connect_text)
        for label in [self.serial_status, self.serial_plot_status, self.serial_csv_status]:
            label.setText(status_text)

    def toggle_serial_stamp(self):
        self.serial_stamp_enabled = not self.serial_stamp_enabled
        state = "ON" if self.serial_stamp_enabled else "OFF"
        self.serial_stamp_btn.setText(f"{self.t('serial.stamp_prefix', 'Stamp time')}: {state}")

    def toggle_serial_tx(self):
        self.serial_tx_enabled = not self.serial_tx_enabled
        state = "ON" if self.serial_tx_enabled else "OFF"
        self.serial_tx_btn.setText(f"{self.t('serial.tx_prefix', 'Log TX')}: {state}")

    def serial_clear(self):
        self.serial_text.clear()
        self.serial_tx_log.clear()
        self._reset_live_serial_views()

    def serial_export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, self.t("serial.export_title", "Export serial log"), "", "Log (*.log);;Text (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.serial_text.toPlainText(), encoding="utf-8")
        except Exception as exc:
            self.show_error_dialog(self.t("error.title", "Error"), str(exc))

    def serial_toggle(self):
        if self.serial_connection:
            self.serial_disconnect()
        else:
            self.serial_connect()

    def serial_connect(self):
        self._reset_live_serial_views()
        port = self.port_combo.currentText()
        if not port or port == "auto":
            ports = self.get_serial_ports()
            if not ports:
                self.show_error_dialog(self.t("error.title", "Error"), self.t("serial.no_port", "No serial port available"))
                return
            port = ports[0]
        baud = int(self.baud_combo.currentText() or "115200")
        try:
            import serial

            self.serial_connection = serial.Serial(port, baud, timeout=0.5)
            self.refresh_serial_status(True, port, str(baud))
            self.start_serial_monitor()
        except Exception as exc:
            self.show_error_dialog(self.t("error.title", "Error"), str(exc))

    def serial_disconnect(self):
        if self.serial_recording_active:
            self.stop_serial_recording()
            if self.serial_recording_active:
                return
        if self.serial_connection:
            try:
                self.serial_connection.close()
            except Exception:
                pass
            self.serial_connection = None
        self.refresh_serial_status(False)

    def serial_send(self):
        if not self.serial_connection:
            return
        data = self.serial_input.text()
        if not data:
            return
        try:
            self.serial_connection.write((data + "\n").encode())
            if self.serial_tx_enabled:
                line = f"TX {datetime.now().strftime('%H:%M:%S')} -> {data}"
                self.serial_tx_log.append(line)
                self._append_serial_text(line)
            self.serial_input.clear()
        except Exception as exc:
            self.show_error_dialog(self.t("error.title", "Error"), str(exc))

    def start_serial_monitor(self):
        def worker():
            while self.serial_connection:
                try:
                    if self.serial_connection.in_waiting:
                        raw = self.serial_connection.readline()
                        if not raw:
                            continue
                        now = datetime.now()
                        if self.serial_decode_combo.currentText() == "HEX":
                            payload = raw.hex(" ").upper().strip()
                        else:
                            payload = raw.decode(errors="ignore").rstrip("\r\n")
                        delta = (now - self.serial_last_rx_ts).total_seconds() if self.serial_last_rx_ts else 0
                        rx_fps = (1.0 / delta) if delta > 0 else 0.0
                        self.serial_last_rx_ts = now
                        if self.serial_stamp_enabled:
                            payload = f"{now.strftime('%H:%M:%S')} -> {payload}"
                        self.serial_line_counter += 1
                        self.bridge.serial_data.emit({
                            "text": payload,
                            "raw_text": raw.decode(errors="ignore").rstrip("\r\n"),
                            "timestamp": now,
                            "rx_fps": rx_fps,
                            "line_index": self.serial_line_counter,
                        })
                    else:
                        time.sleep(0.02)
                except Exception:
                    self.log("[SERIAL] Monitor interrompido por erro na leitura ou desconexao da porta.")
                    break

        threading.Thread(target=worker, daemon=True).start()

    def _handle_serial_payload(self, payload):
        if isinstance(payload, dict):
            self._append_serial_text(payload.get("text", ""))
            self._process_serial_line(payload)
            return
        self._append_serial_text(str(payload))

    def _process_serial_line(self, payload: dict):
        raw_text = str(payload.get("raw_text", ""))
        timestamp = payload.get("timestamp") or datetime.now()
        line_index = int(payload.get("line_index", 0))
        rx_fps = float(payload.get("rx_fps", 0.0) or 0.0)
        self.serial_fps_label.setText(f"RX FPS: {rx_fps:.2f}")
        self.serial_plot_fps_label.setText(f"RX FPS: {rx_fps:.2f}")
        elapsed_ms = 0.0
        if self.serial_recording_session:
            started_at = datetime.fromisoformat(self.serial_recording_session["started_at"])
            elapsed_ms = round((timestamp - started_at).total_seconds() * 1000.0, 3)
        elif self.serial_live_records:
            first_ts = self.serial_live_records[0].get("timestamp_obj")
            if isinstance(first_ts, datetime):
                elapsed_ms = round((timestamp - first_ts).total_seconds() * 1000.0, 3)

        parsed = parse_csv_line(raw_text, self.serial_live_headers)
        record = {
            "event_type": "text",
            "timestamp": timestamp.isoformat(timespec="seconds"),
            "date": timestamp.strftime("%Y-%m-%d"),
            "time": timestamp.strftime("%H:%M:%S"),
            "elapsed_ms": elapsed_ms,
            "rx_fps": round(rx_fps, 4),
            "line_index": line_index,
            "error_flag": False,
            "error_message": "",
            "raw": raw_text,
        }
        if parsed:
            if parsed["kind"] == "header":
                self.serial_live_headers = parsed["headers"]
                self._refresh_csv_table()
            else:
                record["event_type"] = "csv"
                for key, value in parsed["mapping"].items():
                    record[key] = value
                live_record = dict(record)
                live_record["timestamp_obj"] = timestamp
                self.serial_live_records.append(live_record)
                self.serial_live_records = self.serial_live_records[-500:]
                self.serial_plot_series = extract_numeric_series(self.serial_live_records)
                self._sync_series_selector()
                self._refresh_live_plot()
                self._refresh_csv_table()
        if is_error_line(raw_text):
            record["event_type"] = "error" if record["event_type"] == "text" else record["event_type"]
            record["error_flag"] = True
            record["error_message"] = raw_text
            self.serial_live_errors.append(f"[{record['timestamp']}] {raw_text}")
            self.serial_live_errors = self.serial_live_errors[-200:]
            self._refresh_csv_table()
        self._write_recording_event(record)

    def _append_serial_text(self, text: str):
        self.serial_text.appendPlainText(text)
        self.serial_text.verticalScrollBar().setValue(self.serial_text.verticalScrollBar().maximum())

    def _set_combo_value(self, combo: QtWidgets.QComboBox, value: str):
        text = str(value or "")
        index = combo.findText(text)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text)
        combo.setCurrentIndex(max(index, 0))

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.WindowStateChange:
            settings = self._ensure_app_setting_defaults(self.app_settings)
            if (
                bool(settings.get("tray_enabled", True))
                and bool(settings.get("minimize_to_tray", True))
                and self.isMinimized()
                and getattr(self, "tray_icon", None)
            ):
                QtCore.QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event):
        settings = self._ensure_app_setting_defaults(self.app_settings)
        if self._quitting_from_tray:
            event.accept()
            return
        if (
            bool(settings.get("tray_enabled", True))
            and bool(settings.get("close_to_tray", True))
            and getattr(self, "tray_icon", None)
        ):
            event.ignore()
            self.hide()
            return
        if getattr(self, "tray_icon", None):
            self.tray_icon.hide()
        event.accept()


def run(initial_project: str | None = None):
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = VCliQtApp()
    if initial_project:
        try:
            target = Path(initial_project)
            if target.is_file():
                target = target.parent
            if target.exists():
                window.load_project_path(str(target))
        except Exception:
            pass
    settings = window._ensure_app_setting_defaults(window.app_settings)
    startup_to_tray = (
        bool(settings.get("tray_enabled", False))
        and bool(settings.get("startup_to_tray", False))
        and getattr(window, "tray_icon", None)
    )
    if startup_to_tray:
        window.hide()
    else:
        window.show()
    return qt_app.exec_()


if __name__ == "__main__":
    sys.exit(run())
