import re
import shutil
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets


def fit_dialog_to_screen(dialog, preferred_width: int, preferred_height: int, min_width: int = 980, min_height: int = 640):
    screen = QtWidgets.QApplication.primaryScreen()
    available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1366, 768)
    width = min(preferred_width, max(min_width, available.width() - 36))
    height = min(preferred_height, max(min_height, available.height() - 36))
    dialog.resize(width, height)


class SimpleCodeHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        def make_format(color: str, bold: bool = False):
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(color))
            if bold:
                fmt.setFontWeight(QtGui.QFont.Bold)
            return fmt

        keyword_format = make_format("#7c4dff", True)
        keywords = [
            "class", "def", "return", "if", "else", "elif", "for", "while", "try", "except",
            "finally", "import", "from", "as", "with", "pass", "break", "continue", "True", "False", "None",
            "void", "int", "float", "double", "char", "bool", "struct", "const", "static", "public", "private",
            "include", "define", "switch", "case", "default", "new", "delete", "namespace", "using",
        ]
        for word in keywords:
            self.rules.append((re.compile(rf"\b{re.escape(word)}\b"), keyword_format))

        self.rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), make_format("#0f9d58")))
        self.rules.append((re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"), make_format("#0f9d58")))
        self.rules.append((re.compile(r"#.*$"), make_format("#7f8c8d")))
        self.rules.append((re.compile(r"//.*$"), make_format("#7f8c8d")))
        self.rules.append((re.compile(r"\b\d+(?:\.\d+)?\b"), make_format("#d35400")))
        self.rules.append((re.compile(r"\b[A-Za-z_]\w*(?=\s*\()"), make_format("#1565c0", True)))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span()
                self.setFormat(start, end - start, fmt)


class GutterArea(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)

    def mousePressEvent(self, event):
        self.editor.handle_gutter_click(event.pos())


class FoldableCodeEditor(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gutter = GutterArea(self)
        self.fold_regions = {}
        self.folded_starts = set()
        self.block_indent_cache = {}
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_gutter_width(0)
        self._highlight_current_line()

    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 34 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter_area(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def _highlight_current_line(self):
        extra = []
        if not self.isReadOnly():
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(QtGui.QColor("#eef6ff"))
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra.append(selection)
        self.setExtraSelections(extra)

    def rebuild_fold_regions(self):
        self.fold_regions = {}
        self.block_indent_cache = {}
        lines = self.toPlainText().splitlines()
        stack = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" \t"))
            self.block_indent_cache[idx] = indent
            while stack and indent <= stack[-1][1] and stripped:
                start_idx, _ = stack.pop()
                if idx - 1 > start_idx:
                    self.fold_regions[start_idx] = idx - 1
            if stripped.endswith("{") or re.match(r"^\s*(class|def)\s+\w+", line) or re.match(r"^\s*(if|else|elif|for|while|try|except|finally)\b.*:\s*$", line):
                stack.append((idx, indent))
        last_line = len(lines) - 1
        while stack:
            start_idx, _ = stack.pop()
            if last_line > start_idx:
                self.fold_regions[start_idx] = last_line
        self._apply_fold_state()
        self.gutter.update()

    def _apply_fold_state(self):
        doc = self.document()
        for start, end in self.fold_regions.items():
            hide = start in self.folded_starts
            for line_no in range(start + 1, end + 1):
                block = doc.findBlockByLineNumber(line_no)
                if block.isValid():
                    block.setVisible(not hide)
                    block.setLineCount(1 if not hide else 0)
        doc.markContentsDirty(0, doc.characterCount())
        self.viewport().update()

    def toggle_fold(self, start_line: int):
        if start_line not in self.fold_regions:
            return
        if start_line in self.folded_starts:
            self.folded_starts.remove(start_line)
        else:
            self.folded_starts.add(start_line)
        self._apply_fold_state()
        self.gutter.update()

    def paint_gutter(self, event):
        painter = QtGui.QPainter(self.gutter)
        painter.fillRect(event.rect(), QtGui.QColor("#f2f5f8"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        current_line = self.textCursor().blockNumber()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_no = block_number + 1
                if block_number == current_line:
                    painter.fillRect(0, top, self.gutter.width(), self.fontMetrics().height() + 2, QtGui.QColor("#dceeff"))
                painter.setPen(QtGui.QColor("#708090"))
                painter.drawText(0, top, self.gutter.width() - 16, self.fontMetrics().height(), QtCore.Qt.AlignRight, str(line_no))
                if block_number in self.fold_regions:
                    rect = QtCore.QRect(self.gutter.width() - 14, top + 2, 10, 10)
                    painter.setPen(QtGui.QColor("#4a6572"))
                    painter.setBrush(QtGui.QColor("#ffffff"))
                    painter.drawRect(rect)
                    painter.drawLine(rect.left() + 2, rect.center().y(), rect.right() - 2, rect.center().y())
                    if block_number not in self.folded_starts:
                        painter.drawLine(rect.center().x(), rect.top() + 2, rect.center().x(), rect.bottom() - 2)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def handle_gutter_click(self, pos):
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid():
            if block.isVisible() and top <= pos.y() <= bottom:
                if block_number in self.fold_regions and pos.x() >= self.gutter.width() - 18:
                    self.toggle_fold(block_number)
                else:
                    cursor = QtGui.QTextCursor(block)
                    self.setTextCursor(cursor)
                return
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1


class CodeEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent, project_path: Path):
        super().__init__(parent)
        self.project_path = Path(project_path)
        self.current_file = None
        self.original_texts = {}
        self.modified_files = set()
        self.setWindowTitle(f"Code Editor - {self.project_path.name}")
        fit_dialog_to_screen(self, 1560, 940, min_width=1080, min_height=700)
        self._build_ui()
        self._load_tree()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        self.file_model = QtWidgets.QFileSystemModel(self)
        self.file_model.setRootPath(str(self.project_path))
        self.file_model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.NoDotAndDotDot | QtCore.QDir.Files)
        self.file_tree = QtWidgets.QTreeView()
        self.file_tree.setModel(self.file_model)
        self.file_tree.setRootIndex(self.file_model.index(str(self.project_path)))
        for column in [1, 2, 3]:
            self.file_tree.hideColumn(column)
        self.file_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.file_tree.doubleClicked.connect(self._open_from_index)
        self.file_tree.customContextMenuRequested.connect(self._open_tree_context_menu)
        left_layout.addWidget(QtWidgets.QLabel("Arquivos"))
        left_layout.addWidget(self.file_tree, 1)
        splitter.addWidget(left)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        top = QtWidgets.QHBoxLayout()
        self.file_label = QtWidgets.QLabel("Nenhum arquivo aberto")
        self.file_label.setStyleSheet("font-weight: 700;")
        self.reload_btn = QtWidgets.QPushButton("Recarregar")
        self.save_btn = QtWidgets.QPushButton("Salvar")
        self.cancel_btn = QtWidgets.QPushButton("Cancelar")
        top.addWidget(self.file_label, 1)
        top.addWidget(self.reload_btn)
        top.addWidget(self.save_btn)
        top.addWidget(self.cancel_btn)
        center_layout.addLayout(top)

        self.editor = FoldableCodeEditor()
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.editor.setTabStopDistance(32)
        font = QtGui.QFont("Consolas")
        font.setPointSize(11)
        self.editor.setFont(font)
        self.highlighter = SimpleCodeHighlighter(self.editor.document())
        center_layout.addWidget(self.editor, 1)
        splitter.addWidget(center)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.modified_list = QtWidgets.QListWidget()
        self.modified_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.revert_btn = QtWidgets.QPushButton("Reverter arquivo")
        self.open_modified_btn = QtWidgets.QPushButton("Abrir arquivo")
        right_layout.addWidget(QtWidgets.QLabel("Arquivos editados"))
        right_layout.addWidget(self.modified_list, 1)
        right_layout.addWidget(self.open_modified_btn)
        right_layout.addWidget(self.revert_btn)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        splitter.setStretchFactor(2, 2)

        self.reload_btn.clicked.connect(self.reload_current_file)
        self.save_btn.clicked.connect(self.save_current_file)
        self.cancel_btn.clicked.connect(self.reject)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        self.revert_btn.clicked.connect(self.revert_selected_file)
        self.open_modified_btn.clicked.connect(self.open_selected_modified)

    def _load_tree(self):
        self.file_tree.expandToDepth(1)

    def _open_from_index(self, index):
        path = Path(self.file_model.filePath(index))
        if path.is_dir():
            return
        self.open_file(path)

    def _selected_tree_path(self) -> Path:
        index = self.file_tree.currentIndex()
        if index.isValid():
            return Path(self.file_model.filePath(index))
        return self.project_path

    def _open_tree_context_menu(self, pos):
        index = self.file_tree.indexAt(pos)
        target = Path(self.file_model.filePath(index)) if index.isValid() else self.project_path
        base_dir = target if target.is_dir() else target.parent
        menu = QtWidgets.QMenu(self)
        new_file_action = menu.addAction("Novo arquivo")
        new_folder_action = menu.addAction("Nova pasta")
        delete_action = menu.addAction("Excluir")
        if target == self.project_path:
            delete_action.setEnabled(False)
        action = menu.exec_(self.file_tree.viewport().mapToGlobal(pos))
        if action == new_file_action:
            self._create_file(base_dir)
        elif action == new_folder_action:
            self._create_folder(base_dir)
        elif action == delete_action:
            self._delete_path(target)

    def _create_file(self, base_dir: Path):
        name, ok = QtWidgets.QInputDialog.getText(self, "Novo arquivo", "Nome do arquivo:")
        if not ok or not str(name).strip():
            return
        safe_name = str(name).strip().replace("\\", "/").split("/")[-1]
        target = base_dir / safe_name
        if target.exists():
            QtWidgets.QMessageBox.warning(self, "Code Editor", f"O arquivo '{safe_name}' já existe.")
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
            self.file_tree.setCurrentIndex(self.file_model.index(str(target)))
            self.open_file(target)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao criar arquivo:\n{exc}")

    def _create_folder(self, base_dir: Path):
        name, ok = QtWidgets.QInputDialog.getText(self, "Nova pasta", "Nome da pasta:")
        if not ok or not str(name).strip():
            return
        safe_name = str(name).strip().replace("\\", "/").split("/")[-1]
        target = base_dir / safe_name
        if target.exists():
            QtWidgets.QMessageBox.warning(self, "Code Editor", f"A pasta '{safe_name}' já existe.")
            return
        try:
            target.mkdir(parents=True, exist_ok=False)
            self.file_tree.expand(self.file_model.index(str(base_dir)))
            self.file_tree.setCurrentIndex(self.file_model.index(str(target)))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao criar pasta:\n{exc}")

    def _delete_path(self, target: Path):
        if target == self.project_path:
            return
        kind = "pasta" if target.is_dir() else "arquivo"
        answer = QtWidgets.QMessageBox.question(
            self,
            "Excluir",
            f"Deseja excluir {kind} '{target.name}'?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if self.current_file and self.current_file == target:
                self.current_file = None
                self.editor.blockSignals(True)
                self.editor.setPlainText("")
                self.editor.blockSignals(False)
                self.file_label.setText("Nenhum arquivo aberto")
            self.modified_files = {path for path in self.modified_files if path != target and target not in path.parents}
            self.original_texts = {path: text for path, text in self.original_texts.items() if path != target and target not in path.parents}
            self._refresh_modified_list()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao excluir {kind}:\n{exc}")

    def open_file(self, path: Path):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao abrir arquivo:\n{exc}")
            return
        self.current_file = Path(path)
        self.original_texts.setdefault(self.current_file, text)
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self.editor.rebuild_fold_regions()
        self.file_label.setText(str(self.current_file.relative_to(self.project_path)))

    def reload_current_file(self):
        if self.current_file:
            self.open_file(self.current_file)

    def save_current_file(self):
        if not self.current_file:
            return
        try:
            self.current_file.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.original_texts[self.current_file] = self.editor.toPlainText()
            self._sync_modified_state(self.current_file, self.editor.toPlainText())
            QtWidgets.QMessageBox.information(self, "Code Editor", "Arquivo salvo.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao salvar:\n{exc}")

    def _on_editor_text_changed(self):
        self.editor.rebuild_fold_regions()
        if self.current_file:
            self._sync_modified_state(self.current_file, self.editor.toPlainText())

    def _sync_modified_state(self, path: Path, text: str):
        original = self.original_texts.get(path, "")
        if text != original:
            self.modified_files.add(path)
        elif path in self.modified_files:
            self.modified_files.remove(path)
        self._refresh_modified_list()

    def _refresh_modified_list(self):
        current = str(self.current_file) if self.current_file else ""
        self.modified_list.clear()
        for path in sorted(self.modified_files):
            item = QtWidgets.QListWidgetItem(str(path.relative_to(self.project_path)))
            item.setData(QtCore.Qt.UserRole, str(path))
            self.modified_list.addItem(item)
            if str(path) == current:
                self.modified_list.setCurrentItem(item)

    def open_selected_modified(self):
        item = self.modified_list.currentItem()
        if not item:
            return
        self.open_file(Path(item.data(QtCore.Qt.UserRole)))

    def revert_selected_file(self):
        target = self.current_file
        item = self.modified_list.currentItem()
        if item:
            target = Path(item.data(QtCore.Qt.UserRole))
        if not target:
            return
        original = self.original_texts.get(target)
        if original is None:
            return
        if target == self.current_file:
            self.editor.blockSignals(True)
            self.editor.setPlainText(original)
            self.editor.blockSignals(False)
            self.editor.rebuild_fold_regions()
        self._sync_modified_state(target, original)
