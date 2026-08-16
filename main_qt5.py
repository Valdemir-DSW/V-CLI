import csv
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
import webbrowser
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from cli_backend import CLIBackend
from code_editor_tools import CodeEditorDialog
from serial_csv_tools import (
    CsvLogBrowserDialog,
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
        self.detail_installed = QtWidgets.QLabel("Instalada: -")
        self.detail_latest = QtWidgets.QLabel("Última: -")
        self.detail_author = QtWidgets.QLabel("Autor: -")
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

        for widget in [self.detail_title, self.detail_installed, self.detail_latest, self.detail_author]:
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
        for row, item in enumerate(self.items):
            values = [
                item.get("name", ""),
                item.get("installed_version", "") or "-",
                item.get("latest_version", "") or "-",
                item.get("category", "") or "-",
                str(item.get("match_score", "")),
            ]
            for col, value in enumerate(values):
                widget_item = QtWidgets.QTableWidgetItem(value)
                if col == 0 and item.get("has_update"):
                    widget_item.setForeground(QtGui.QColor("#0b6e4f"))
                self.table.setItem(row, col, widget_item)
        if self.items:
            self.table.selectRow(0)
        else:
            self.update_detail()

    def update_detail(self):
        item = self.selected_item()
        if not item:
            self.detail_title.setText("Selecione uma biblioteca")
            self.detail_installed.setText("Instalada: -")
            self.detail_latest.setText("Última: -")
            self.detail_author.setText("Autor: -")
            self.detail_desc.setPlainText("")
            self.detail_url.setText("")
            self.version_combo.clear()
            self.refresh_action_state()
            return

        self.detail_title.setText(item.get("name", ""))
        self.detail_installed.setText(f"Instalada: {item.get('installed_version') or '-'}")
        self.detail_latest.setText(f"Última: {item.get('latest_version') or '-'}")
        author = item.get("author") or item.get("maintainer") or "-"
        self.detail_author.setText(f"Autor: {author}")
        url = item.get("url", "")
        self.detail_url.setText(f'<a href="{url}">{url}</a>' if url else "")
        desc_parts = [item.get("sentence", ""), item.get("paragraph", "")]
        if item.get("match_reason"):
            desc_parts.append(f"Match: {item.get('match_reason')}")
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
        close_hint.setStyleSheet("color: #6b7280;")
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


class VCliQtApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        appdata_local = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.appdata_dir = appdata_local / "Arduino15" / "V-CLI"
        self.appdata_dir.mkdir(parents=True, exist_ok=True)
        self.app_settings_file = self.appdata_dir / "settings.json"
        self.app_settings = self._load_app_settings()
        self.locale_dir = Path.cwd() / "locales"
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
        self.app_icon_path = Path.cwd() / ".ico"
        self.recent_projects = []
        self._load_recent_projects()
        self.boards_cache = []
        self.boards_cache_time = 0
        self.loaded_libraries = []
        self.variant_options = []
        self.dynamic_tool_controls = {}
        self.startup_dialog = None
        self.default_project_icon_path = Path.cwd() / "project_padrao.png"
        self.board_updates_count = 0
        self.board_updates_flash_on = False
        self.board_updates_timer = QtCore.QTimer(self)
        self.board_updates_timer.setInterval(650)
        self.board_updates_timer.timeout.connect(self._toggle_board_updates_flash)

        self.setWindowTitle(self.t("app.title", "V CLI - VS Code Arduino plugin"))
        self.resize(1280, 820)
        self.setMinimumSize(1000, 680)
        if self.app_icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(self.app_icon_path)))
        self._apply_styles()
        self._build_ui()

        self.backend = CLIBackend(os.getcwd(), self.bridge.log_message.emit)
        self.load_recent_projects_widget()
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

    def _apply_styles(self):
        theme = str(self.app_settings.get("theme", "light") or "light").strip().lower()
        if theme == "dark":
            self.setStyleSheet(
                """
                QMainWindow, QDialog { background: #101418; color: #e5edf5; }
                QFrame#sidePanel { background: #16202a; border: 1px solid #293544; border-radius: 12px; }
                QListWidget#recentProjects { font-size: 13px; padding: 4px; }
                QListWidget#recentProjects::item { min-height: 28px; border-radius: 6px; padding: 4px 8px; }
                QListWidget#recentProjects::item:selected { background: #28435c; color: white; }
                QLabel#historyBanner { font-size: 14px; font-weight: 800; color: #f0f6fb; padding: 4px 8px; background: rgba(255,255,255,0.06); border: 1px solid #32465a; border-radius: 10px; }
                QLabel#boardUpdatesLabel { font-size: 12px; font-weight: 700; color: #9aa8b6; padding: 4px 8px; }
                QLabel#sectionTitle, QLabel#managerTitle { font-size: 15px; font-weight: 700; color: #f0f6fb; }
                QPushButton { background: #1a2530; color: #e5edf5; border: 1px solid #334355; border-radius: 8px; padding: 7px 12px; font-weight: 600; }
                QPushButton:hover { border-color: #6b8ba7; }
                QTabWidget::pane, QGroupBox { border: 1px solid #293544; border-radius: 10px; background: #141b23; }
                QTabBar::tab { background: #1e2a36; border: 1px solid #293544; padding: 8px 14px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; color: #e5edf5; }
                QTabBar::tab:selected { background: #141b23; }
                QPlainTextEdit#consoleBox, QPlainTextEdit#serialBox, QPlainTextEdit#cliBox { background: #050607; color: #00ff7f; border: 1px solid #111; border-radius: 10px; font-family: Consolas, Courier New, monospace; font-size: 12px; }
                QLineEdit, QComboBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit { border: 1px solid #334355; border-radius: 8px; padding: 6px; background: #0f151c; color: #e5edf5; }
                """
            )
            return
        self.setStyleSheet(
            """
            QMainWindow, QDialog {
                background: #f4f6f8;
                color: #1e2933;
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
            QLabel#boardUpdatesLabel {
                font-size: 12px;
                font-weight: 700;
                color: #6b7280;
                padding: 4px 8px;
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
            QLineEdit, QComboBox, QListWidget, QTableWidget, QTextEdit {
                border: 1px solid #c6d0da;
                border-radius: 8px;
                padding: 6px;
                background: white;
            }
            """
        )

    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        main = QtWidgets.QVBoxLayout(root)
        self._build_menu_bar()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main.addWidget(split, 1)

        side = QtWidgets.QFrame()
        side.setObjectName("sidePanel")
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
        self._build_boards_tab()
        self._build_libs_tab()
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

        self.new_btn.clicked.connect(self.create_project)
        self.open_btn.clicked.connect(self.open_project)
        self.recent_list.itemDoubleClicked.connect(lambda *_: self.open_recent_project())
        self.recent_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self.open_recent_context_menu)
        self.tabs.currentChanged.connect(lambda *_: self._refresh_board_updates_indicator())
        self._update_project_actions_enabled(False)
        self.apply_app_settings_to_ui()

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
        self.action_open_csv_log = QtWidgets.QAction("Ler Log CSV", self)
        self.action_code_editor = QtWidgets.QAction("Code Editor", self)
        link_arduino = QtWidgets.QAction("Arduino CLI", self)
        link_python = QtWidgets.QAction("Python", self)
        link_pyqt = QtWidgets.QAction("PyQt5", self)
        link_vscode = QtWidgets.QAction("VS Code", self)
        tools_menu.addAction(self.action_open_csv_log)
        tools_menu.addAction(self.action_code_editor)
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
        self.action_code_editor.triggered.connect(self.open_code_editor_dialog)
        settings_action.triggered.connect(self.open_settings_dialog)
        about_action.triggered.connect(self.show_about_dialog)
        link_arduino.triggered.connect(lambda: webbrowser.open("https://arduino.github.io/arduino-cli/latest/"))
        link_python.triggered.connect(lambda: webbrowser.open("https://www.python.org/"))
        link_pyqt.triggered.connect(lambda: webbrowser.open("https://pypi.org/project/PyQt5/"))
        link_vscode.triggered.connect(lambda: webbrowser.open("https://code.visualstudio.com/"))

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
        dynamic_title.setStyleSheet("font-style: italic; color: #2f4858;")
        form_layout.addWidget(dynamic_title)

        self.dynamic_scroll = QtWidgets.QScrollArea()
        self.dynamic_scroll.setWidgetResizable(True)
        self.dynamic_scroll.setMinimumHeight(240)
        self.dynamic_scroll_host = QtWidgets.QWidget()
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
        self.libs_refresh_btn.clicked.connect(self.load_installed_libraries)
        self.libs_zip_btn.clicked.connect(self.install_library_zip)
        self.libs_manager_btn.clicked.connect(self.open_library_manager)
        self.tabs.addTab(tab, self.t("tab.libs", "Libraries"))

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
        label.setStyleSheet("font-weight: 700;")
        value_label.setMinimumHeight(30)
        value_label.setStyleSheet("border: 1px solid #c6d0da; border-radius: 0px; background: white; padding: 6px;")
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
        cfg.setdefault("aux_library_repo", "")
        cfg.setdefault("command_open_template", "vcli.cmd open \"{project}\"")
        cfg.setdefault("command_vscode_template", "vcli.cmd vscode \"{project}\"")
        cfg.setdefault("command_compile_template", "vcli.cmd compile \"{project}\"")
        cfg.setdefault("command_export_template", "vcli.cmd export \"{project}\"")
        cfg.setdefault("command_upload_template", "vcli.cmd upload \"{project}\" --port {port}")
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
        self.btn_vscode.setText(editor_title)
        self.btn_vscode.setStyleSheet(
            f"background: {editor_color}; color: white; border: 1px solid {editor_color}; border-radius: 8px; padding: 7px 12px; font-weight: 600;"
        )
        if hasattr(self, "action_vscode"):
            self.action_vscode.setText(editor_title)

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
        base_dir = str(Path.cwd()).lower()
        for entry in path_env.split(os.pathsep):
            if entry.strip().lower() == base_dir:
                return True
        return False

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
            getattr(self, "action_open_csv_log", None),
            getattr(self, "action_code_editor", None),
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
        return props

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
            pixmap.fill(QtGui.QColor("#dbe7f3"))
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

    def show_error_dialog(self, title: str, error_msg: str, output: str = ""):
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

    def update_project_info(self):
        if not self.current_project or not self.current_config:
            return
        self._ensure_project_property_defaults()
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
        if not self.current_project or not self.current_config:
            return
        props = self._ensure_project_property_defaults()
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self.t("props.title", "Project Properties"))
        self.fit_dialog_to_screen(dialog, 900, 660)
        outer = QtWidgets.QVBoxLayout(dialog)
        body = QtWidgets.QHBoxLayout()
        nav = QtWidgets.QListWidget()
        nav.setFixedWidth(220)
        nav.addItems(["Geral", "Autoversionamento"])
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
        selected_file_label = QtWidgets.QLabel(props.get("autoversion_file", "") or "-")
        selected_file_label.setWordWrap(True)
        selected_var_label = QtWidgets.QLabel(props.get("autoversion_variable", "VERSION") or "VERSION")
        selected_var_label.setWordWrap(True)
        selected_kind_label = QtWidgets.QLabel(props.get("autoversion_kind", "string"))
        selected_kind_label.setWordWrap(True)
        auto_form.addRow(self.t("props.autoversion_mode", "Mode:"), autoversion_mode)
        auto_form.addRow(self.t("props.autoversion_file", "Selected file:"), selected_file_label)
        auto_form.addRow(self.t("props.autoversion_var", "Selected variable:"), selected_var_label)
        auto_form.addRow(self.t("props.autoversion_kind", "Detected type:"), selected_kind_label)
        auto_layout.addLayout(auto_form)

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

        available_files = self._list_project_source_files()
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

        files_list.currentItemChanged.connect(lambda *_: refresh_variables_for_selected_file())
        vars_list.currentItemChanged.connect(lambda *_: choose_variable())
        file_search.textChanged.connect(lambda *_: refresh_file_list())
        var_search.textChanged.connect(lambda *_: refresh_var_list())
        if files_list.count() and files_list.currentRow() < 0:
            files_list.setCurrentRow(0)
        refresh_variables_for_selected_file()

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
            props["autoversion_file"] = selected_autoversion_file["value"]
            props["autoversion_variable"] = selected_autoversion_variable["value"] or "VERSION"
            props["autoversion_kind"] = selected_kind_label.text().strip() or "string"
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
        body = QtWidgets.QHBoxLayout()
        nav = QtWidgets.QListWidget()
        nav.setFixedWidth(220)
        nav.addItems(["Geral", "Editor", "Bibliotecas", "Placas / JSON", "Comandos"])
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
        language_note = QtWidgets.QLabel("Idioma e tema são aplicados após salvar. O idioma pode exigir reiniciar a aplicação para refletir tudo.")
        language_note.setWordWrap(True)
        general_form.addRow("Tema:", theme_combo)
        general_form.addRow("Idioma:", language_combo)
        general_form.addRow(language_note)
        stack.addWidget(general_page)

        editor_page = QtWidgets.QWidget()
        editor_form = QtWidgets.QFormLayout(editor_page)
        editor_title = QtWidgets.QLineEdit(settings.get("editor_title", "VS Code"))
        editor_command = QtWidgets.QLineEdit(settings.get("editor_command", "code"))
        editor_color = QtWidgets.QLineEdit(settings.get("editor_button_color", "#0078d4"))
        choose_color_btn = QtWidgets.QPushButton("Cor...")
        color_row = QtWidgets.QHBoxLayout()
        color_row.addWidget(editor_color, 1)
        color_row.addWidget(choose_color_btn)
        editor_form.addRow("Título do editor:", editor_title)
        editor_form.addRow("Comando do editor:", editor_command)
        editor_form.addRow("Cor do botão:", self._wrap_layout(color_row))
        editor_form.addRow(QtWidgets.QLabel("Exemplos de comando: `code`, `cursor`, caminho completo do editor."))
        stack.addWidget(editor_page)

        libs_page = QtWidgets.QWidget()
        libs_form = QtWidgets.QFormLayout(libs_page)
        aux_repo = QtWidgets.QLineEdit(settings.get("aux_library_repo", ""))
        aux_info = QtWidgets.QLabel("Repositório auxiliar de bibliotecas (experimental). Use URL direta quando necessário.")
        aux_info.setWordWrap(True)
        default_lib_info = QtWidgets.QLabel("Padrão atual: índice padrão do Arduino CLI em Arduino15/library_index.json.")
        default_lib_info.setWordWrap(True)
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

        commands_page = QtWidgets.QWidget()
        commands_layout = QtWidgets.QFormLayout(commands_page)
        command_status = QtWidgets.QLabel(
            "Registrado no PATH do Windows." if self._is_vcli_registered_on_path() else "Ainda não registrado no PATH do Windows."
        )
        command_status.setWordWrap(True)
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
            self.app_settings["theme"] = theme_combo.currentData() or "light"
            self.app_settings["language"] = language_combo.currentData() or "auto"
            self.app_settings["editor_title"] = editor_title.text().strip() or "VS Code"
            self.app_settings["editor_command"] = editor_command.text().strip() or "code"
            self.app_settings["editor_button_color"] = editor_color.text().strip() or "#0078d4"
            self.app_settings["aux_library_repo"] = aux_repo.text().strip()
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

        choose_color_btn.clicked.connect(choose_color)
        board_add_btn.clicked.connect(add_board_url)
        board_remove_btn.clicked.connect(remove_board_url)
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)
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
        old_version = str(props.get("version", "1.0.0") or "1.0.0").strip()
        new_version = self._increment_version(old_version)
        value_kind = str(props.get("autoversion_kind", "string") or "string").strip()
        if value_kind == "number":
            digits = "".join(ch for ch in old_version if ch.isdigit())
            current_number = int(digits or "0") + 1
            new_version = str(current_number)
        props["version"] = new_version
        version_file = str(props.get("autoversion_file", "") or "").strip()
        version_variable = str(props.get("autoversion_variable", "VERSION") or "VERSION").strip()
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
        self.fit_dialog_to_screen(dialog, 640, 420)
        layout = QtWidgets.QVBoxLayout(dialog)
        title = QtWidgets.QLabel("V CLI")
        title.setObjectName("managerTitle")
        subtitle = QtWidgets.QLabel("Interface Qt 5 para arduino-cli")
        subtitle.setStyleSheet("font-size: 13px; color: #4b5563;")
        info = QtWidgets.QLabel(
            "Tecnologias principais:\n"
            "- Python\n"
            "- PyQt5\n"
            "- Arduino CLI\n"
            "- VS Code\n"
        )
        info.setWordWrap(True)
        links = QtWidgets.QLabel(
            '<a href="https://arduino.github.io/arduino-cli/latest/">Arduino CLI</a><br>'
            '<a href="https://www.python.org/">Python</a><br>'
            '<a href="https://pypi.org/project/PyQt5/">PyQt5</a><br>'
            '<a href="https://code.visualstudio.com/">VS Code</a>'
        )
        links.setOpenExternalLinks(True)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addWidget(info)
        layout.addWidget(links)
        layout.addStretch(1)
        layout.addWidget(buttons)
        dialog.exec_()

    def compile_project(self):
        if not self.current_project or not self.current_config:
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
        ok_version, version_info = self._run_autoversion("export")
        if not ok_version:
            self.show_error_dialog("Autoversionamento", version_info)
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
            try:
                boards = self.backend.list_boards()
                libs = self.backend.list_libraries_fixed()
                ports = self.get_serial_ports()
                updates = self.backend.list_core_updates()
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
                self.populate_installed_libraries(libs)
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

    def _toggle_board_updates_flash(self):
        self.board_updates_flash_on = not self.board_updates_flash_on
        self._refresh_board_updates_indicator()

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
        loading.setStyleSheet("font-style: italic; color: #355c7d;")
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

            def done():
                self.populate_installed_libraries(libs)

            self.bridge.invoke.emit(done)

        threading.Thread(target=worker, daemon=True).start()

    def populate_installed_libraries(self, libs):
        self.loaded_libraries = libs or []
        self.libs_table.setRowCount(len(self.loaded_libraries))
        for row, lib in enumerate(self.loaded_libraries):
            self.libs_table.setItem(row, 0, QtWidgets.QTableWidgetItem(lib.get("name", "")))
            self.libs_table.setItem(row, 1, QtWidgets.QTableWidgetItem(lib.get("version", "")))
            self.libs_table.setItem(row, 2, QtWidgets.QTableWidgetItem((lib.get("sentence", "") or "")[:120]))

    def install_library_zip(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Selecionar biblioteca ZIP", "", "ZIP (*.zip)")
        if not path:
            return
        self.run_project_action("Instalando biblioteca ZIP", lambda: self.backend.install_library_zip_sync(path))

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

    def _serial_logs_dir(self) -> Path:
        base = self.current_project if self.current_project else Path.cwd()
        logs_dir = base / "logs"
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
        logs_dir = self._serial_logs_dir()
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
        logs_dir = self._serial_logs_dir()
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
        dialog = CsvLogBrowserDialog(self, self._serial_logs_dir())
        dialog.exec_()

    def open_code_editor_dialog(self):
        if not self.current_project:
            QtWidgets.QMessageBox.information(self, "Code Editor", "Abra um projeto para usar o Code Editor.")
            return
        dialog = CodeEditorDialog(self, self.current_project)
        dialog.exec_()

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
                except Exception:
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


def run():
    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = VCliQtApp()
    window.show()
    return qt_app.exec_()


if __name__ == "__main__":
    sys.exit(run())
