import re
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

        keyword_format = QtGui.QTextCharFormat()
        keyword_format.setForeground(QtGui.QColor("#7c4dff"))
        keyword_format.setFontWeight(QtGui.QFont.Bold)
        keywords = [
            "class", "def", "return", "if", "else", "elif", "for", "while", "try", "except",
            "finally", "import", "from", "as", "with", "pass", "break", "continue", "True", "False", "None",
            "void", "int", "float", "double", "char", "bool", "struct", "const", "static", "public", "private",
            "include", "define", "switch", "case", "default", "new", "delete", "namespace", "using",
        ]
        for word in keywords:
            self.rules.append((re.compile(rf"\b{re.escape(word)}\b"), keyword_format))

        string_format = QtGui.QTextCharFormat()
        string_format.setForeground(QtGui.QColor("#0f9d58"))
        self.rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), string_format))
        self.rules.append((re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'"), string_format))

        comment_format = QtGui.QTextCharFormat()
        comment_format.setForeground(QtGui.QColor("#7f8c8d"))
        self.rules.append((re.compile(r"#.*$"), comment_format))
        self.rules.append((re.compile(r"//.*$"), comment_format))

        number_format = QtGui.QTextCharFormat()
        number_format.setForeground(QtGui.QColor("#d35400"))
        self.rules.append((re.compile(r"\b\d+(?:\.\d+)?\b"), number_format))

        func_format = QtGui.QTextCharFormat()
        func_format.setForeground(QtGui.QColor("#1565c0"))
        func_format.setFontWeight(QtGui.QFont.Bold)
        self.rules.append((re.compile(r"\b[A-Za-z_]\w*(?=\s*\()"), func_format))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span()
                self.setFormat(start, end - start, fmt)


class CodeEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent, project_path: Path):
        super().__init__(parent)
        self.project_path = Path(project_path)
        self.current_file = None
        self.full_text_cache = ""
        self.focus_range = None
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
        self.file_tree.doubleClicked.connect(self._open_from_index)
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

        self.editor = QtWidgets.QPlainTextEdit()
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
        self.symbols = QtWidgets.QListWidget()
        self.focus_symbol_btn = QtWidgets.QPushButton("Focar símbolo")
        self.full_view_btn = QtWidgets.QPushButton("Visão completa")
        right_layout.addWidget(QtWidgets.QLabel("Funções e variáveis"))
        right_layout.addWidget(self.symbols, 1)
        right_layout.addWidget(self.focus_symbol_btn)
        right_layout.addWidget(self.full_view_btn)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 2)

        self.reload_btn.clicked.connect(self.reload_current_file)
        self.save_btn.clicked.connect(self.save_current_file)
        self.cancel_btn.clicked.connect(self.reject)
        self.symbols.itemDoubleClicked.connect(lambda *_: self.goto_symbol())
        self.focus_symbol_btn.clicked.connect(self.focus_symbol)
        self.full_view_btn.clicked.connect(self.restore_full_view)

    def _load_tree(self):
        self.file_tree.expandToDepth(1)

    def _open_from_index(self, index):
        path = Path(self.file_model.filePath(index))
        if path.is_dir():
            return
        self.open_file(path)

    def open_file(self, path: Path):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao abrir arquivo:\n{exc}")
            return
        self.current_file = Path(path)
        self.full_text_cache = text
        self.focus_range = None
        self.editor.setPlainText(text)
        self.file_label.setText(str(self.current_file.relative_to(self.project_path)))
        self._rebuild_symbols()

    def reload_current_file(self):
        if self.current_file:
            self.open_file(self.current_file)

    def save_current_file(self):
        if not self.current_file:
            return
        try:
            self.current_file.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.full_text_cache = self.editor.toPlainText()
            QtWidgets.QMessageBox.information(self, "Code Editor", "Arquivo salvo.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Code Editor", f"Falha ao salvar:\n{exc}")

    def _rebuild_symbols(self):
        self.symbols.clear()
        lines = self.editor.toPlainText().splitlines()
        patterns = [
            re.compile(r"^\s*def\s+([A-Za-z_]\w*)"),
            re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
            re.compile(r"^\s*(?:void|int|float|double|bool|char|String|long|short)\s+([A-Za-z_]\w*)\s*\("),
            re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*"),
        ]
        for idx, line in enumerate(lines):
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    item = QtWidgets.QListWidgetItem(f"{match.group(1)}  ·  L{idx + 1}")
                    item.setData(QtCore.Qt.UserRole, idx)
                    self.symbols.addItem(item)
                    break

    def goto_symbol(self):
        item = self.symbols.currentItem()
        if not item:
            return
        line_no = int(item.data(QtCore.Qt.UserRole))
        block = self.editor.document().findBlockByLineNumber(line_no)
        cursor = QtGui.QTextCursor(block)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def _symbol_bounds(self, start_line: int):
        lines = self.editor.toPlainText().splitlines()
        start = max(0, start_line)
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            line = lines[idx]
            if re.match(r"^\s*(def|class)\s+[A-Za-z_]\w*", line) or re.match(r"^\s*(?:void|int|float|double|bool|char|String|long|short)\s+[A-Za-z_]\w*\s*\(", line):
                end = idx
                break
        return start, end

    def focus_symbol(self):
        item = self.symbols.currentItem()
        if not item:
            return
        if not self.full_text_cache:
            self.full_text_cache = self.editor.toPlainText()
        start, end = self._symbol_bounds(int(item.data(QtCore.Qt.UserRole)))
        lines = self.full_text_cache.splitlines()
        self.focus_range = (start, end)
        self.editor.setPlainText("\n".join(lines[start:end]))

    def restore_full_view(self):
        if self.full_text_cache:
            self.editor.setPlainText(self.full_text_cache)
            self.focus_range = None
            self._rebuild_symbols()
