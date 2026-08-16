import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets


BASE_LOG_FIELDS = [
    "event_type",
    "timestamp",
    "date",
    "time",
    "elapsed_ms",
    "rx_fps",
    "line_index",
    "error_flag",
    "error_message",
    "raw",
]

ERROR_PATTERN = re.compile(r"\b(error|erro|exception|fail|failed|panic|fatal|traceback)\b", re.IGNORECASE)
NUMERIC_PATTERN = re.compile(r"^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)$")


def safe_float(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def looks_numeric(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(NUMERIC_PATTERN.match(text))


def sanitize_header_name(value: str, index: int) -> str:
    text = re.sub(r"\s+", "_", str(value or "").strip())
    text = re.sub(r"[^0-9A-Za-z_]", "", text)
    text = text.strip("_")
    if not text:
        text = f"col_{index + 1}"
    if text[0].isdigit():
        text = f"col_{text}"
    return text


def is_error_line(text: str) -> bool:
    return bool(ERROR_PATTERN.search(str(text or "")))


def parse_csv_line(text: str, current_headers=None):
    line = str(text or "").strip()
    if not line or "," not in line:
        return None
    try:
        row = next(csv.reader([line]))
    except Exception:
        return None
    row = [cell.strip() for cell in row]
    if len(row) < 2:
        return None

    has_letters = any(any(ch.isalpha() for ch in cell) for cell in row)
    numeric_count = sum(1 for cell in row if looks_numeric(cell))
    if current_headers and len(current_headers) == len(row):
        return {
            "kind": "data",
            "headers": list(current_headers),
            "values": row,
            "mapping": dict(zip(current_headers, row)),
        }
    if has_letters and numeric_count < len(row):
        headers = [sanitize_header_name(cell, index) for index, cell in enumerate(row)]
        return {"kind": "header", "headers": headers, "values": row}
    headers = list(current_headers or [])
    if not headers or len(headers) != len(row):
        headers = [f"value_{index + 1}" for index in range(len(row))]
    return {
        "kind": "data",
        "headers": headers,
        "values": row,
        "mapping": dict(zip(headers, row)),
    }


def extract_numeric_series(records):
    series = defaultdict(list)
    for record in records:
        if record.get("event_type") != "csv":
            continue
        x_value = safe_float(record.get("elapsed_ms"))
        if x_value is None:
            x_value = float(record.get("line_index", 0))
        for key, value in record.items():
            if key in BASE_LOG_FIELDS:
                continue
            numeric = safe_float(value)
            if numeric is None:
                continue
            series[key].append((x_value, numeric))
    return dict(series)


def format_duration_ms(value):
    total_ms = max(0, int(float(value or 0)))
    seconds, ms = divmod(total_ms, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minute:02d}:{sec:02d}.{ms:03d}"
    return f"{minute:02d}:{sec:02d}.{ms:03d}"


def fit_dialog_to_screen(dialog, preferred_width: int, preferred_height: int, min_width: int = 960, min_height: int = 620):
    screen = QtWidgets.QApplication.primaryScreen()
    available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
    width = min(preferred_width, max(min_width, available.width() - 36))
    height = min(preferred_height, max(min_height, available.height() - 36))
    dialog.resize(width, height)


class SerialPlotWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.series = {}
        self.selected_series = []
        self.plot_type = "line"
        self.color_map = {}

    def set_data(self, series: dict, selected_series=None, plot_type: str = "line", color_map=None):
        self.series = series or {}
        self.plot_type = plot_type or "line"
        self.color_map = dict(color_map or {})
        if selected_series is None:
            self.selected_series = list(self.series.keys())[:4]
        else:
            self.selected_series = [name for name in selected_series if name in self.series]
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#0b1016"))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        plot_rect = self.rect().adjusted(56, 16, -16, -40)
        if plot_rect.width() < 60 or plot_rect.height() < 60:
            return

        painter.setPen(QtGui.QPen(QtGui.QColor("#2f3b46"), 1))
        painter.drawRect(plot_rect)

        visible = [(name, self.series.get(name, [])) for name in self.selected_series if self.series.get(name)]
        if not visible:
            painter.setPen(QtGui.QColor("#a9bbca"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Sem dados numéricos para plotar")
            return

        xs = [point[0] for _, points in visible for point in points]
        ys = [point[1] for _, points in visible for point in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if math.isclose(min_x, max_x):
            max_x += 1.0
        if math.isclose(min_y, max_y):
            max_y += 1.0

        def to_screen(x_value, y_value):
            x_norm = (x_value - min_x) / (max_x - min_x)
            y_norm = (y_value - min_y) / (max_y - min_y)
            x = plot_rect.left() + x_norm * plot_rect.width()
            y = plot_rect.bottom() - y_norm * plot_rect.height()
            return QtCore.QPointF(x, y)

        painter.setPen(QtGui.QPen(QtGui.QColor("#33414d"), 1, QtCore.Qt.DashLine))
        for step in range(1, 5):
            y = plot_rect.top() + plot_rect.height() * step / 5.0
            painter.drawLine(plot_rect.left(), int(y), plot_rect.right(), int(y))

        colors = ["#43aa8b", "#ffb703", "#8ecae6", "#fb8500", "#ff6b6b", "#b388eb"]
        for index, (name, points) in enumerate(visible):
            color = QtGui.QColor(self.color_map.get(name, colors[index % len(colors)]))
            pen = QtGui.QPen(color, 2)
            painter.setPen(pen)
            mapped = [to_screen(x_value, y_value) for x_value, y_value in points]
            if self.plot_type == "scatter":
                for point in mapped:
                    painter.setBrush(color)
                    painter.drawEllipse(point, 3, 3)
            elif self.plot_type == "bar":
                bar_width = max(3, int(plot_rect.width() / max(len(mapped), 24) * 0.7))
                painter.setBrush(color)
                for point in mapped:
                    painter.drawRect(int(point.x() - bar_width / 2), int(point.y()), bar_width, plot_rect.bottom() - int(point.y()))
            elif self.plot_type == "step":
                path = QtGui.QPainterPath(mapped[0])
                previous = mapped[0]
                for point in mapped[1:]:
                    path.lineTo(point.x(), previous.y())
                    path.lineTo(point)
                    previous = point
                painter.drawPath(path)
            else:
                path = QtGui.QPainterPath(mapped[0])
                for point in mapped[1:]:
                    path.lineTo(point)
                painter.drawPath(path)

        painter.setPen(QtGui.QColor("#d7e3ee"))
        painter.drawText(8, plot_rect.top() + 4, f"max {max_y:.3f}")
        painter.drawText(8, plot_rect.bottom(), f"min {min_y:.3f}")
        painter.drawText(plot_rect.left(), self.height() - 12, f"{min_x:.0f} ms")
        painter.drawText(plot_rect.right() - 80, self.height() - 12, f"{max_x:.0f} ms")

        legend_x = plot_rect.left()
        legend_y = self.height() - 18
        for index, (name, _) in enumerate(visible):
            color = QtGui.QColor(self.color_map.get(name, colors[index % len(colors)]))
            painter.fillRect(legend_x, legend_y - 8, 10, 10, color)
            painter.setPen(QtGui.QColor("#d7e3ee"))
            painter.drawText(legend_x + 14, legend_y, name)
            legend_x += 110


class TimelineWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []
        self.window_start = 0
        self.window_end = 0
        self.setMinimumHeight(72)

    def set_records(self, records, window_start: int = 0, window_end: int | None = None):
        self.records = list(records or [])
        self.window_start = max(0, int(window_start or 0))
        self.window_end = len(self.records) if window_end is None else max(self.window_start, int(window_end))
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#f4f8fc"))
        bar_rect = self.rect().adjusted(18, 30, -18, -20)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#dbe7f3"))
        painter.drawRoundedRect(bar_rect, 12, 12)
        if not self.records:
            painter.setPen(QtGui.QColor("#61758a"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Timeline vazia")
            return

        max_elapsed = max(safe_float(row.get("elapsed_ms")) or 0 for row in self.records) or max(len(self.records) - 1, 1)
        painter.setPen(QtGui.QColor("#3b4d61"))
        painter.drawText(18, 18, "⏱ Timeline")
        painter.drawText(self.width() - 120, 18, format_duration_ms(max_elapsed))

        for step in range(5):
            x = bar_rect.left() + (bar_rect.width() * step / 4.0)
            painter.setPen(QtGui.QPen(QtGui.QColor("#c5d4e2"), 1))
            painter.drawLine(int(x), bar_rect.top() - 4, int(x), bar_rect.bottom() + 4)
            painter.setPen(QtGui.QColor("#70869a"))
            painter.drawText(int(x) - 18, self.height() - 4, format_duration_ms(max_elapsed * step / 4.0))

        for row in self.records:
            elapsed = safe_float(row.get("elapsed_ms"))
            x_ratio = (elapsed / max_elapsed) if max_elapsed and elapsed is not None else 0
            x = bar_rect.left() + x_ratio * bar_rect.width()
            is_error = str(row.get("error_flag", "")).lower() in {"1", "true", "yes"}
            color = QtGui.QColor("#ef476f" if is_error else "#219ebc")
            height = bar_rect.height() if is_error else int(bar_rect.height() * 0.6)
            top = bar_rect.bottom() - height
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QtCore.QRectF(x - 2, top, 4, height), 2, 2)

        if self.records:
            start_elapsed = safe_float(self.records[min(self.window_start, len(self.records) - 1)].get("elapsed_ms")) or 0
            end_index = min(max(self.window_end - 1, self.window_start), len(self.records) - 1)
            end_elapsed = safe_float(self.records[end_index].get("elapsed_ms")) or max_elapsed
            start_x = bar_rect.left() + ((start_elapsed / max_elapsed) if max_elapsed else 0) * bar_rect.width()
            end_x = bar_rect.left() + ((end_elapsed / max_elapsed) if max_elapsed else 1) * bar_rect.width()
            painter.setPen(QtGui.QPen(QtGui.QColor("#0f6d9b"), 2))
            painter.setBrush(QtGui.QColor(33, 158, 188, 50))
            painter.drawRoundedRect(QtCore.QRectF(start_x, bar_rect.top() - 6, max(8, end_x - start_x), bar_rect.height() + 12), 8, 8)


class RecordingSaveDialog(QtWidgets.QDialog):
    def __init__(self, parent, suggested_name: str):
        super().__init__(parent)
        self.setWindowTitle("Salvar log CSV")
        self.resize(520, 260)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(suggested_name)
        self.description_edit = QtWidgets.QPlainTextEdit()
        self.description_edit.setPlaceholderText("Descrição do teste, placa, cenário, observações...")
        form.addRow("Nome:", self.name_edit)
        form.addRow("Descrição:", self.description_edit)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox()
        self.save_btn = buttons.addButton("Salvar", QtWidgets.QDialogButtonBox.AcceptRole)
        self.delete_btn = buttons.addButton("Excluir", QtWidgets.QDialogButtonBox.DestructiveRole)
        self.cancel_btn = buttons.addButton("Cancelar", QtWidgets.QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)
        self.save_btn.clicked.connect(lambda: self.done(1))
        self.delete_btn.clicked.connect(lambda: self.done(2))
        self.cancel_btn.clicked.connect(lambda: self.done(0))

    def values(self):
        return self.name_edit.text().strip(), self.description_edit.toPlainText().strip()


class CsvLogViewerDialog(QtWidgets.QDialog):
    def __init__(self, parent, csv_path: Path):
        super().__init__(parent)
        self.csv_path = Path(csv_path)
        self.meta_path = self.csv_path.with_suffix(".meta.json")
        self.records = []
        self.meta = {}
        self.series = {}
        self.visible_records = []
        self.window_start = 0
        self.series_colors = {}
        self.setWindowTitle(f"Leitor de Log CSV - {self.csv_path.name}")
        fit_dialog_to_screen(self, 1500, 920, min_width=1180, min_height=680)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("Log CSV")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #17324d;")
        self.clock_label = QtWidgets.QLabel("--:--:--")
        self.clock_label.setStyleSheet("font-size: 22px; font-weight: 800; color: #219ebc;")
        self.date_label = QtWidgets.QLabel("--/--/----")
        self.date_label.setStyleSheet("font-size: 12px; color: #5b7288;")
        self.window_label = QtWidgets.QLabel("Janela: --")
        self.window_label.setStyleSheet("font-size: 12px; color: #5b7288;")
        top.addWidget(self.title_label)
        top.addStretch(1)
        top.addWidget(self.window_label)
        top.addSpacing(14)
        top.addWidget(self.clock_label)
        top.addSpacing(8)
        top.addWidget(self.date_label)
        layout.addLayout(top)

        controls = QtWidgets.QHBoxLayout()
        self.plot_type_combo = QtWidgets.QComboBox()
        self.plot_type_combo.addItems(["line", "step", "scatter", "bar"])
        self.prev_btn = QtWidgets.QPushButton("<< Voltar")
        self.next_btn = QtWidgets.QPushButton("Avançar >>")
        controls.addWidget(QtWidgets.QLabel("Plot:"))
        controls.addWidget(self.plot_type_combo)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.next_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, 1)

        self.plot = SerialPlotWidget()
        splitter.addWidget(self.plot)

        right = QtWidgets.QTabWidget()
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addTab(self.table, "Tabela")
        self.errors = QtWidgets.QPlainTextEdit()
        self.errors.setReadOnly(True)
        right.addTab(self.errors, "Erros")

        view_tab = QtWidgets.QWidget()
        view_layout = QtWidgets.QVBoxLayout(view_tab)
        limit_row = QtWidgets.QHBoxLayout()
        self.point_limit_spin = QtWidgets.QSpinBox()
        self.point_limit_spin.setRange(10, 5000)
        self.point_limit_spin.setSingleStep(10)
        self.point_limit_spin.setValue(120)
        self.visible_vars_spin = QtWidgets.QSpinBox()
        self.visible_vars_spin.setRange(1, 12)
        self.visible_vars_spin.setValue(4)
        self.auto_vars_btn = QtWidgets.QPushButton("Aplicar variáveis")
        self.normalize_check = QtWidgets.QCheckBox("Equalizar séries")
        self.split_check = QtWidgets.QCheckBox("Dividir variáveis")
        self.stack_step_spin = QtWidgets.QDoubleSpinBox()
        self.stack_step_spin.setRange(0.1, 10.0)
        self.stack_step_spin.setSingleStep(0.1)
        self.stack_step_spin.setValue(1.5)
        self.stack_step_spin.setEnabled(False)
        limit_row.addWidget(QtWidgets.QLabel("Pontos na janela:"))
        limit_row.addWidget(self.point_limit_spin)
        limit_row.addWidget(QtWidgets.QLabel("Variáveis exibidas:"))
        limit_row.addWidget(self.visible_vars_spin)
        limit_row.addWidget(self.auto_vars_btn)
        limit_row.addWidget(self.normalize_check)
        limit_row.addWidget(self.split_check)
        limit_row.addWidget(QtWidgets.QLabel("Passo:"))
        limit_row.addWidget(self.stack_step_spin)
        limit_row.addStretch(1)
        view_layout.addLayout(limit_row)
        self.series_list = QtWidgets.QListWidget()
        self.series_list.itemChanged.connect(self._refresh_plot)
        view_layout.addWidget(self.series_list, 1)
        color_row = QtWidgets.QHBoxLayout()
        self.color_btn = QtWidgets.QPushButton("Mudar cor")
        self.export_excel_btn = QtWidgets.QPushButton("Exportar Excel")
        color_row.addWidget(self.color_btn)
        color_row.addWidget(self.export_excel_btn)
        color_row.addStretch(1)
        view_layout.addLayout(color_row)
        right.addTab(view_tab, "Exibir")
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 3)

        nav = QtWidgets.QHBoxLayout()
        self.offset_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.offset_slider.setRange(0, 0)
        nav.addWidget(QtWidgets.QLabel("Timeline"))
        nav.addWidget(self.offset_slider, 1)
        layout.addLayout(nav)

        self.plot_type_combo.currentTextChanged.connect(self._refresh_plot)
        self.point_limit_spin.valueChanged.connect(self._rebuild_visible_window)
        self.prev_btn.clicked.connect(self._step_back)
        self.next_btn.clicked.connect(self._step_forward)
        self.offset_slider.valueChanged.connect(self._slider_changed)
        self.auto_vars_btn.clicked.connect(self._apply_variable_limit)
        self.visible_vars_spin.valueChanged.connect(self._apply_variable_limit)
        self.color_btn.clicked.connect(self._change_series_color)
        self.export_excel_btn.clicked.connect(self._export_excel)
        self.normalize_check.toggled.connect(self._refresh_plot)
        self.split_check.toggled.connect(self._toggle_split_mode)
        self.stack_step_spin.valueChanged.connect(self._refresh_plot)

    def _load(self):
        try:
            with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                self.records = list(reader)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro", f"Falha ao abrir log:\n{exc}")
            self.reject()
            return
        if self.meta_path.exists():
            try:
                self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                self.meta = {}
        self.series = extract_numeric_series(self.records)
        default_colors = ["#43aa8b", "#ffb703", "#8ecae6", "#fb8500", "#ff6b6b", "#b388eb", "#00b4d8", "#90be6d"]
        for name in self.series.keys():
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            self.series_list.addItem(item)
            self.series_colors[name] = default_colors[len(self.series_colors) % len(default_colors)]
        started = self.meta.get("started_at") or (self.records[0].get("timestamp") if self.records else "")
        dt_obj = None
        try:
            dt_obj = datetime.fromisoformat(str(started))
        except Exception:
            dt_obj = None
        self.title_label.setText(self.csv_path.stem.replace("_", " "))
        if dt_obj:
            self.clock_label.setText(dt_obj.strftime("%H:%M:%S"))
            self.date_label.setText(dt_obj.strftime("%d/%m/%Y"))
        else:
            self.clock_label.setText("--:--:--")
            self.date_label.setText(self.csv_path.name)
        self._check_default_series()
        self._rebuild_visible_window()

    def _check_default_series(self):
        self.series_list.blockSignals(True)
        for index in range(self.series_list.count()):
            item = self.series_list.item(index)
            item.setCheckState(QtCore.Qt.Checked if index < 4 else QtCore.Qt.Unchecked)
        self.series_list.blockSignals(False)

    def _apply_variable_limit(self):
        limit = self.visible_vars_spin.value()
        checked = 0
        self.series_list.blockSignals(True)
        for index in range(self.series_list.count()):
            item = self.series_list.item(index)
            if checked < limit:
                item.setCheckState(QtCore.Qt.Checked)
                checked += 1
            else:
                item.setCheckState(QtCore.Qt.Unchecked)
        self.series_list.blockSignals(False)
        self._refresh_plot()

    def _change_series_color(self):
        item = self.series_list.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(self, "Exibir", "Selecione uma variável na aba Exibir.")
            return
        current = QtGui.QColor(self.series_colors.get(item.text(), "#43aa8b"))
        color = QtWidgets.QColorDialog.getColor(current, self, f"Cor de {item.text()}")
        if color.isValid():
            self.series_colors[item.text()] = color.name()
            self._refresh_plot()

    def _export_excel(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Exportar Excel", str(self.csv_path.with_suffix(".xls")), "Excel (*.xls)")
        if not path:
            return
        headers = list(self.records[0].keys()) if self.records else []
        rows = self.visible_records
        html = [
            "<html><head><meta charset='utf-8'></head><body><table border='1'><tr>",
            *[f"<th>{header}</th>" for header in headers],
            "</tr>",
        ]
        for row in rows:
            html.append("<tr>")
            html.extend(f"<td>{str(row.get(header, ''))}</td>" for header in headers)
            html.append("</tr>")
        html.append("</table></body></html>")
        Path(path).write_text("".join(html), encoding="utf-8")

    def _window_end(self):
        return min(len(self.records), self.window_start + self.point_limit_spin.value())

    def _toggle_split_mode(self, checked: bool):
        self.stack_step_spin.setEnabled(bool(checked))
        self._refresh_plot()

    def _slider_changed(self, value: int):
        self.window_start = max(0, int(value))
        self._rebuild_visible_window(refresh_slider=False)

    def _step_back(self):
        step = max(1, self.point_limit_spin.value() // 2)
        self.window_start = max(0, self.window_start - step)
        self._rebuild_visible_window()

    def _step_forward(self):
        step = max(1, self.point_limit_spin.value() // 2)
        max_start = max(0, len(self.records) - self.point_limit_spin.value())
        self.window_start = min(max_start, self.window_start + step)
        self._rebuild_visible_window()

    def _rebuild_visible_window(self, refresh_slider: bool = True):
        if not self.records:
            self.visible_records = []
            return
        max_start = max(0, len(self.records) - self.point_limit_spin.value())
        self.window_start = min(max(self.window_start, 0), max_start)
        end_index = self._window_end()
        self.visible_records = self.records[self.window_start:end_index]
        if refresh_slider:
            self.offset_slider.blockSignals(True)
            self.offset_slider.setRange(0, max_start)
            self.offset_slider.setValue(self.window_start)
            self.offset_slider.blockSignals(False)
        first_elapsed = safe_float(self.visible_records[0].get("elapsed_ms")) or 0
        last_elapsed = safe_float(self.visible_records[-1].get("elapsed_ms")) or 0
        self.window_label.setText(
            f"Janela {self.window_start + 1}-{end_index} de {len(self.records)} | "
            f"{format_duration_ms(first_elapsed)} -> {format_duration_ms(last_elapsed)}"
        )
        self._refresh_table()
        self._refresh_errors()
        self._refresh_plot()

    def _refresh_table(self):
        headers = list(self.records[0].keys()) if self.records else []
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.visible_records))
        for row_index, row in enumerate(self.visible_records):
            for col_index, header in enumerate(headers):
                self.table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(str(row.get(header, ""))))
        self.table.resizeColumnsToContents()

    def _refresh_errors(self):
        errors = [row for row in self.visible_records if str(row.get("error_flag", "")).lower() in {"1", "true", "yes"}]
        self.errors.setPlainText(
            "\n\n".join(
                f"[{row.get('timestamp', '-')}] {row.get('error_message', '').strip() or row.get('raw', '').strip()}"
                for row in errors
            ) or "Nenhum erro na janela atual."
        )

    def _refresh_plot(self):
        selected = []
        for index in range(self.series_list.count()):
            item = self.series_list.item(index)
            if item.checkState() == QtCore.Qt.Checked:
                selected.append(item.text())
        series = extract_numeric_series(self.visible_records)
        if self.normalize_check.isChecked() or self.split_check.isChecked():
            transformed = {}
            step = self.stack_step_spin.value()
            for idx, name in enumerate(selected):
                points = series.get(name, [])
                if not points:
                    continue
                ys = [p[1] for p in points]
                min_y, max_y = min(ys), max(ys)
                span = max(max_y - min_y, 1e-9)
                offset = idx * step if self.split_check.isChecked() else 0.0
                transformed[name] = [(x, ((y - min_y) / span) + offset) for x, y in points]
            for name, points in series.items():
                if name not in transformed:
                    transformed[name] = points
            series = transformed
        self.plot.set_data(series, selected_series=selected, plot_type=self.plot_type_combo.currentText(), color_map=self.series_colors)


class CsvLogBrowserDialog(QtWidgets.QDialog):
    def __init__(self, parent, logs_dir: Path):
        super().__init__(parent)
        self.logs_dir = Path(logs_dir)
        self.setWindowTitle("Ler Log CSV")
        fit_dialog_to_screen(self, 1380, 860, min_width=1040, min_height=620)
        self._build_ui()
        self._load_logs()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(str(self.logs_dir))
        title.setStyleSheet("font-size: 12px; color: #6c8399;")
        layout.addWidget(title)
        actions = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Atualizar lista")
        self.open_btn = QtWidgets.QPushButton("Abrir log")
        actions.addWidget(self.refresh_btn)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.list_table = QtWidgets.QTableWidget(0, 3)
        self.list_table.setHorizontalHeaderLabels(["Arquivo", "Data", "Tamanho"])
        self.list_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.list_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.list_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.list_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.list_table.itemDoubleClicked.connect(lambda *_: self._open_selected())
        layout.addWidget(self.list_table, 1)

        self.refresh_btn.clicked.connect(self._load_logs)
        self.open_btn.clicked.connect(self._open_selected)

    def _load_logs(self):
        files = sorted(self.logs_dir.glob("*.csv"), key=lambda item: item.stat().st_mtime, reverse=True) if self.logs_dir.exists() else []
        self.list_table.setRowCount(len(files))
        for row_index, csv_path in enumerate(files):
            name_item = QtWidgets.QTableWidgetItem(csv_path.name)
            name_item.setData(QtCore.Qt.UserRole, str(csv_path))
            date_item = QtWidgets.QTableWidgetItem(datetime.fromtimestamp(csv_path.stat().st_mtime).strftime("%d/%m/%Y %H:%M"))
            size_item = QtWidgets.QTableWidgetItem(f"{csv_path.stat().st_size} B")
            self.list_table.setItem(row_index, 0, name_item)
            self.list_table.setItem(row_index, 1, date_item)
            self.list_table.setItem(row_index, 2, size_item)
        if files:
            self.list_table.selectRow(0)

    def _open_selected(self):
        row = self.list_table.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "Ler Log CSV", "Selecione um log da lista.")
            return
        item = self.list_table.item(row, 0)
        path = Path(item.data(QtCore.Qt.UserRole))
        parent = self.parentWidget()
        self.accept()
        QtCore.QTimer.singleShot(0, lambda: CsvLogViewerDialog(parent, path).exec_())
