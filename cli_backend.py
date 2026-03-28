"""
Camada de Backend - CLI arduino-cli Wrapper

Interface com arduino-cli para operacoes:
- Manipulacao de arquivos e projetos
- Compilacao e upload para placas
- Gerenciamento de bibliotecas
- Controle de configuracoes

Responsabilidades:
✓ Executar comandos arduino-cli via subprocess
✓ Parsear outputs JSON (placas, bibliotecas, tools)
✓ Gerenciar projeto.fuzil (configuracoes em JSON)
✓ Logging de operacoes atraves de callback
✓ Tratamento de erros e timeouts

NOTAS TECNICAS:
- arduino-cli.exe deve estar no diretorio raiz
- cli.yaml eh auto-gerenciado
- Outputs sao em JSON para maquina parsing
- Todas operacoes passam por logging
"""

import os
import json
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional, Dict, Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CLIBackend:
    def __init__(self, base_dir: str, config_callback: Optional[Callable] = None):
        """
        Args:
            base_dir: Diretório base do projeto
            config_callback: Função para log/output (recebe string)
        """
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.cli_path = self.base_dir / "arduino-cli.exe"
        self.config_file = self.base_dir / "cli.yaml"
        self.config_callback = config_callback or (lambda x: None)
        self._process_lock = threading.Lock()
        self._current_process: Optional[subprocess.Popen] = None
        self._abort_requested = False
        
        # Criar diretórios necessários
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        
        # Inicializar CLI se necessário
        self._init_cli()

    def _run_subprocess(self, cmd: list, timeout: int = 60):
        """Wrapper para subprocess.run sem abrir janela no Windows."""
        kwargs = {"capture_output": True, "text": True, "timeout": timeout}
        if CREATE_NO_WINDOW:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    def _popen_subprocess(self, cmd: list, **kwargs):
        """Wrapper para subprocess.Popen sem abrir janela no Windows."""
        if CREATE_NO_WINDOW:
            kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
        return subprocess.Popen(cmd, **kwargs)
    
    def _init_cli(self):
        """Inicializa configuração do CLI"""
        # Sempre recria para garantir consistência
        if self.config_file.exists():
            try:
                self.config_file.unlink()
                self.log("Configuração anterior removida")
            except Exception as e:
                self.log(f"Aviso ao remover config: {e}")
        
        self.log("Inicializando arduino-cli...")
        self.run_cli_sync(["config", "init", "--config-file", str(self.config_file)])

    def _parse_cli_json(self, output: str) -> Optional[Any]:
        """Tenta extrair o primeiro JSON válido ignorando texto extra"""
        if not output:
            return None
        decoder = json.JSONDecoder()
        text = output.strip()
        idx = 0
        while idx < len(text):
            if text[idx] in ('{', '['):
                try:
                    data, _ = decoder.raw_decode(text[idx:])
                    return data
                except json.JSONDecodeError:
                    idx += 1
            else:
                idx += 1
        return None

    def _build_board_option_args(self, config: Optional[Dict[str, Any]]) -> list:
        """Converte configuracoes salvas em --board-options para o arduino-cli"""
        if not config:
            return []

        options = []
        variant = config.get("variant")
        if variant:
            options.append(f"variant={variant}")

        tools = config.get("tools")
        if isinstance(tools, dict):
            for option, value in tools.items():
                if option and option != "variant" and value:
                    options.append(f"{option}={value}")

        if not options:
            return []
        return ["--board-options", ",".join(options)]

    def abort_current_action(self) -> bool:
        """Aborta a acao atual (compile/upload/export), se estiver em execucao."""
        with self._process_lock:
            self._abort_requested = True
            proc = self._current_process
        if not proc:
            return False
        try:
            proc.terminate()
            return True
        except Exception:
            return False

    def _run_action_command(self, cmd: list, timeout: int = 120) -> tuple:
        """Executa comando longo com suporte a cancelamento."""
        proc = None
        try:
            with self._process_lock:
                self._abort_requested = False
            proc = self._popen_subprocess(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with self._process_lock:
                self._current_process = proc

            start = time.time()
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if timeout and (time.time() - start) > timeout:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            proc.kill()
                        return ("", False, f"Operacao expirou (timeout {timeout}s)")
                    continue

            output = (stdout or "") + (stderr or "")
            with self._process_lock:
                aborted = self._abort_requested

            if aborted:
                return (output, False, "Operacao abortada pelo usuario")
            if proc.returncode != 0:
                error_msg = (stderr or "Comando falhou")
                return (output, False, error_msg[:500])
            return (output, True, "")
        except Exception as e:
            return ("", False, str(e))
        finally:
            with self._process_lock:
                if self._current_process is proc:
                    self._current_process = None

    def log(self, text: str):
        """Registra log via callback - tolerante a falhas"""
        try:
            if self.config_callback:
                self.config_callback(str(text))
        except Exception:
            pass  # Ignora erros de logging, nunca deve quebrar execução
    
    def run_cli_sync(self, args: list) -> str:
        """Executa CLI de forma síncrona e retorna output"""
        if not self.cli_path.exists():
            self.log(f"Erro: arduino-cli não encontrado em {self.cli_path}")
            return ""
        
        # Validar argumentos - garantir que nao ha None
        if not args or any(arg is None for arg in args):
            self.log("[ERRO] Argumentos inválidos para CLI")
            return ""
        
        cmd = [str(self.cli_path), "--config-file", str(self.config_file)] + [str(arg) for arg in args]
        try:
            result = self._run_subprocess(cmd, timeout=60)
            
            # Log do comando
            cmd_short = ' '.join(str(a) for a in args[:2])
            self.log(f"[COMANDO] {cmd_short}")
            
            if result.returncode != 0:
                # Erro do CLI
                if result.stderr:
                    self.log(f"[ERRO CLI] {result.stderr[:200]}")
                if result.stdout:
                    self.log(f"[SAIDA] {result.stdout[:200]}")
                return ""
            
            # Sucesso
            if result.stdout:
                return result.stdout
            return ""
            
        except subprocess.TimeoutExpired:
            self.log("[ERRO] Comando expirou (timeout 60s)")
            return ""
        except Exception as e:
            self.log(f"[ERRO] Exceção ao executar CLI: {e}")
            return ""
    
    def run_cli_async(self, args: list):
        """Executa CLI de forma assíncrona em thread separada"""
        def task():
            self.run_cli_sync(args)
        
        threading.Thread(target=task, daemon=True).start()
    
    # ==================== PROJETOS ====================
    
    def list_projects(self) -> list:
        """Lista todos os projetos"""
        if not self.projects_dir.exists():
            return []
        return sorted([d.name for d in self.projects_dir.iterdir() if d.is_dir()])
    
    def load_project(self, project_path: str) -> Optional[Dict[str, Any]]:
        """
        Carrega configurações de um projeto
        Se não existe project.fuzil, cria automático
        Com tratamento robusto de encoding
        """
        project_path = Path(project_path)
        if not project_path.exists():
            self.log(f"Erro: Projeto não existe em {project_path}")
            return None
        
        fuse_file = project_path / "project.fuzil"
        
        # Se não existe, criar padrão
        if not fuse_file.exists():
            self.log(f"Arquivo de definições não encontrado. Criando padrão...")
            self._create_default_fuzil(project_path)
        
        # Carregar configuração com tratamento de encoding
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
            try:
                with open(fuse_file, 'r', encoding=encoding) as f:
                    config = json.load(f)
                
                if encoding != 'utf-8':
                    self.log(f"[INFO] Arquivo estava em {encoding}, convertendo para UTF-8...")
                    with open(fuse_file, 'w', encoding='utf-8') as f:
                        json.dump(config, f, indent=4, ensure_ascii=False)
                
                self.log(f"Projeto carregado: {project_path.name}")
                return config
                
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            except Exception as e:
                self.log(f"Erro ao carregar com {encoding}: {e}")
                continue
        
        # Se nenhum encoding funcionou, criar nova config
        self.log(f"Erro: Não consegui ler project.fuzil, criando novo...")
        self._create_default_fuzil(project_path)
        
        # Tentar carregar novamente
        try:
            with open(fuse_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except Exception as e:
            self.log(f"Erro crítico ao carregar project.fuzil: {e}")
            return None
    
    def _create_default_fuzil(self, project_path: Path):
        """Cria arquivo project.fuzil padrao"""
        default_config = {
            "fqbn": "arduino:avr:uno",
            "name": project_path.name,
            "created": True,
            "custom_libs": [],
            "properties": {
                "author": "",
                "version": "1.0.0",
                "contributors": "",
                "description": ""
            }
        }
        
        fuse_file = project_path / "project.fuzil"
        try:
            with open(fuse_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4, ensure_ascii=False)
            self.log(f"Arquivo project.fuzil criado em {fuse_file}")
        except Exception as e:
            self.log(f"Erro ao criar project.fuzil: {e}")
    def _get_ino_template(self, project_name: str, template_key: str = "clean") -> str:
        """Retorna conteudo do .ino conforme preset selecionado."""
        templates = {
            "clean": """void setup() {
}

void loop() {
}
""",
            "serial": f"""void setup() {{
  Serial.begin(115200);
  while (!Serial) {{
    ; // Aguarda Serial em placas que exigem
  }}
  Serial.println("Projeto {project_name} iniciado");
}}

void loop() {{
  Serial.println("Rodando...");
  delay(1000);
}}
""",
            "blink_delay": """const int LED_PIN = LED_BUILTIN;

void setup() {
  pinMode(LED_PIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_PIN, HIGH);
  delay(500);
  digitalWrite(LED_PIN, LOW);
  delay(500);
}
""",
            "blink_non_blocking": """const int LED_PIN = LED_BUILTIN;
const unsigned long BLINK_INTERVAL_MS = 500;
unsigned long last_blink_ms = 0;
bool led_state = false;

void setup() {
  pinMode(LED_PIN, OUTPUT);
}

void loop() {
  unsigned long now = millis();
  if (now - last_blink_ms >= BLINK_INTERVAL_MS) {
    last_blink_ms = now;
    led_state = !led_state;
    digitalWrite(LED_PIN, led_state ? HIGH : LOW);
  }

  // Outras tarefas podem rodar aqui sem travar o loop
}
""",
        }
        return templates.get(template_key, templates["clean"])

    def create_project(self, project_path: str, project_name: Optional[str] = None, template_key: str = "clean") -> bool:
        """
        Cria novo projeto
        """
        project_path = Path(project_path)
        project_path.mkdir(parents=True, exist_ok=True)

        if not project_name:
            project_name = project_path.name

        # Criar arquivo .ino
        ino_file = project_path / f"{project_name}.ino"
        if not ino_file.exists():
            try:
                ino_template = self._get_ino_template(project_name, template_key)
                with open(ino_file, 'w', encoding='utf-8') as f:
                    f.write(ino_template)
                self.log(f"Arquivo {project_name}.ino criado com template '{template_key}'")
            except Exception as e:
                self.log(f"Erro ao criar arquivo .ino: {e}")
                return False

        # Criar project.fuzil
        self._create_default_fuzil(project_path)

        self.log(f"Projeto '{project_name}' criado com sucesso")
        return True
    
    # ==================== COMPILAÇÃO E UPLOAD ====================
    
    def compile(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Compila projeto e retorna (output, success, error_message)"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli não encontrado")
        
        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN inválido")
        
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "compile", "--fqbn", str(fqbn)]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        try:
            result = self._run_subprocess(cmd, timeout=120)
            
            output = result.stdout + result.stderr
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "Compilação falhou"
                return (output, False, error_msg[:500])
            
            return (output, True, "")
            
        except subprocess.TimeoutExpired:
            return ("", False, "Compilação expirou (timeout 120s)")
        except Exception as e:
            return ("", False, str(e))

    def export_binary(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Exporta binário compilado usando --export-binaries"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli não encontrado")

        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN inválido")

        output_dir = Path(project_path) / "build"
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self.cli_path),
            "--config-file",
            str(self.config_file),
            "compile",
            "--fqbn",
            str(fqbn),
            "--export-binaries",
            "--output-dir",
            str(output_dir),
        ]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))

        try:
            result = self._run_subprocess(cmd, timeout=120)

            output = result.stdout + result.stderr

            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "Exportação falhou"
                return (output, False, error_msg[:500])

            return (output, True, "")

        except subprocess.TimeoutExpired:
            return ("", False, "Exportação expirou (timeout 120s)")
        except Exception as e:
            return ("", False, str(e))

    def upload(self, project_path: str, fqbn: str, port: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Faz upload para placa e retorna (output, success, error_message)"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli não encontrado")
        
        if not project_path or not fqbn or not port:
            return ("", False, "Caminho, FQBN ou porta inválidos")
        
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "upload", "-p", str(port), "--fqbn", str(fqbn)]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        try:
            result = self._run_subprocess(cmd, timeout=120)
            
            output = result.stdout + result.stderr
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "Upload falhou"
                return (output, False, error_msg[:500])
            
            return (output, True, "")
            
        except subprocess.TimeoutExpired:
            return ("", False, "Upload expirou (timeout 120s)")
        except Exception as e:
            return ("", False, str(e))
    
    def compile_action(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Versao abortavel de compilacao."""
        if not self.cli_path.exists():
            return ("", False, "arduino-cli nao encontrado")
        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN invalido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "compile", "--fqbn", str(fqbn)]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        return self._run_action_command(cmd, timeout=180)

    def export_binary_action(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Versao abortavel de exportacao de binario."""
        if not self.cli_path.exists():
            return ("", False, "arduino-cli nao encontrado")
        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN invalido")
        output_dir = Path(project_path) / "build"
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self.cli_path),
            "--config-file",
            str(self.config_file),
            "compile",
            "--fqbn",
            str(fqbn),
            "--export-binaries",
            "--output-dir",
            str(output_dir),
        ]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        return self._run_action_command(cmd, timeout=180)

    def upload_action(self, project_path: str, fqbn: str, port: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Versao abortavel de upload."""
        if not self.cli_path.exists():
            return ("", False, "arduino-cli nao encontrado")
        if not project_path or not fqbn or not port:
            return ("", False, "Caminho, FQBN ou porta invalidos")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "upload", "-p", str(port), "--fqbn", str(fqbn)]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        return self._run_action_command(cmd, timeout=180)

    # ==================== PLACAS ====================
    
    def list_boards(self) -> list:
        """Lista todas as placas disponíveis"""
        output = self.run_cli_sync(["board", "listall", "--format", "json"])
        
        if not output or not output.strip():
            self.log("[AVISO] Nenhuma placa disponível. Verifique a configuração.")
            return []
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                self.log("[ERRO] JSON inválido ao parsear placas")
                return []
            boards = data.get("boards", [])
            if boards:
                self.log(f"Carregadas {len(boards)} placas")
            else:
                self.log("[AVISO] Nenhuma placa encontrada")
            return boards
        except json.JSONDecodeError as e:
            self.log(f"[ERRO] JSON inválido ao parsear placas: {str(e)[:100]}")
            return []
        except Exception as e:
            self.log(f"[ERRO] Erro ao listar placas: {e}")
            return []
    
    def get_board_variants(self, fqbn: str) -> list:
        """Extrai variantes disponíveis para uma placa específica"""
        # Comando correto: board details -b <FQBN> --format json
        output = self.run_cli_sync(["board", "details", "-b", fqbn, "--full", "--format", "json"])
        
        if not output or not output.strip():
            return []
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                return []
            
            # Tenta extrair variantes de várias estruturas possíveis
            variants = []
            
            # Estrutura: "config_options" -> "options" -> "variant"
            if "config_options" in data:
                config_opts = data.get("config_options", {})
                if "options" in config_opts:
                    for opt in config_opts["options"]:
                        if opt.get("option") == "variant":
                            variant_values = opt.get("values", [])
                            for val in variant_values:
                                variants.append({
                                    "id": val.get("value", ""),
                                    "name": val.get("label", "")
                                })
            
            # Fallback: procura em properties
            if not variants and "properties" in data:
                props = data.get("properties", {})
                if "build.variant" in props:
                    # Se houver, retorna como opção única
                    variants.append({
                        "id": props.get("build.variant", ""),
                        "name": props.get("build.variant", "")
                    })
            
            return variants
            
        except Exception as e:
            self.log(f"[ERRO] Ao extrair variantes: {e}")
            return []
    
    def get_upload_modes(self, fqbn: str) -> list:
        """Extrai modos de upload disponíveis para uma placa"""
        # Comando correto: board details -b <FQBN> --format json
        output = self.run_cli_sync(["board", "details", "-b", fqbn, "--full", "--format", "json"])
        
        if not output or not output.strip():
            # Fallback: Serial é sempre suportado
            return [{"id": "serial", "name": "Serial"}]
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                return [{"id": "serial", "name": "Serial"}]
            modes = []
            
            # Procura em upload_protocol ou similar
            if "upload_protocol" in data:
                protocol = data.get("upload_protocol", "")
                if protocol:
                    modes.append({"id": protocol, "name": protocol.capitalize()})
            
            # Procura em programmers para DFU, SWD, etc
            if "programmers" in data:
                programmers = data.get("programmers", [])
                for prog in programmers:
                    if isinstance(prog, dict):
                        prog_id = prog.get("id", "")
                        prog_name = prog.get("name", prog_id)
                        if prog_id:
                            modes.append({"id": prog_id, "name": prog_name})
            
            # Sempre add Serial se não estiver lá
            if not any(m["id"] == "serial" for m in modes):
                modes.insert(0, {"id": "serial", "name": "Serial"})
            
            return modes if modes else [{"id": "serial", "name": "Serial"}]
            
        except Exception as e:
            self.log(f"[ERRO] Ao extrair upload modes: {e}")
            return [{"id": "serial", "name": "Serial"}]
    
    def get_platform_tools(self, fqbn: str) -> list:
        """Extrai ferramentas/configurações disponíveis para uma placa (como Arduino IDE)"""
        output = self.run_cli_sync(["board", "details", "-b", fqbn, "--full", "--format", "json"])
        
        if not output or not output.strip():
            return []
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                return []
            tools = []
            
            # Procura em config_options para ferramentas customizadas (Arduino IDE format)
            if "config_options" in data:
                config_opts = data.get("config_options", {})
                
                # config_opts pode ser dict ou list dependendo da versão
                options_list = []
                if isinstance(config_opts, dict):
                    options_list = config_opts.get("options", [])
                elif isinstance(config_opts, list):
                    options_list = config_opts
                
                for opt in options_list:
                    opt_id = opt.get("option", "")
                    opt_label = opt.get("option_label", opt_id)
                    if opt_id == "variant":
                        continue
                    values_list = opt.get("values", [])
                    
                    # Converter valores para formato padrão
                    tool_values = []
                    selected_value = None
                    
                    for val in values_list:
                        if isinstance(val, dict):
                            val_id = val.get("value", "")
                            val_label = val.get("value_label", val_id)
                            is_selected = val.get("selected", False)
                            
                            if val_id:
                                tool_values.append({
                                    "id": val_id,
                                    "name": val_label
                                })
                                if is_selected:
                                    selected_value = val_id
                    
                    # Se houver valores, adicionar ao resultado
                    if tool_values:
                        tool = {
                            "id": opt_id,
                            "name": opt_label,
                            "values": tool_values,
                            "selected": selected_value
                        }
                        tools.append(tool)
                        self.log(f"[TOOL] {opt_label}: {len(tool_values)} opcoes")
            
            return tools
            
        except Exception as e:
            self.log(f"[ERRO] Ao extrair ferramentas: {str(e)[:100]}")
            return []
    
    def add_board_json(self, url: str):
        """Adiciona URL de JSON de placas"""
        if not url:
            return
        self.run_cli_async(["config", "add", "board_manager.additional_urls", url])
        self.run_cli_async(["core", "update-index"])

    def add_board_json_sync(self, url: str, progress_callback: Optional[Callable[[str], None]] = None) -> tuple:
        """Adiciona URL de JSON de placas de forma sincronizada com progresso."""
        if not url:
            return ("", False, "URL invalida")

        steps = [
            ("Adicionando URL de placas...", ["config", "add", "board_manager.additional_urls", url]),
            ("Atualizando indice de placas...", ["core", "update-index"]),
        ]

        output_total = ""

        for label, args in steps:
            if progress_callback:
                progress_callback(label)
            cmd = [str(self.cli_path), "--config-file", str(self.config_file)] + args
            out, success, err = self._run_action_command(cmd, timeout=120)
            output_total += out
            if not success:
                return (output_total, False, f"{label} - {err}")

        self.log(f"URL de placas registrado: {url}")
        return (output_total, True, "")

    def add_board_zip_sync(self, zip_path: str, progress_callback: Optional[Callable[[str], None]] = None) -> tuple:
        """Adiciona suporte a placas a partir de ZIP com package*_index.json."""
        if not zip_path:
            return ("", False, "Caminho do ZIP invalido")

        zip_file = Path(zip_path)
        if not zip_file.exists():
            return ("", False, f"ZIP nao encontrado: {zip_file}")

        if zip_file.suffix.lower() != ".zip":
            return ("", False, "Arquivo selecionado nao e um ZIP")

        extracted_root = self.base_dir / "board_indexes"
        extracted_root.mkdir(parents=True, exist_ok=True)
        extracted_dir = extracted_root / f"{zip_file.stem}_{int(time.time())}"

        try:
            if progress_callback:
                progress_callback("Extraindo ZIP de placas...")
            with zipfile.ZipFile(zip_file, "r") as zf:
                zf.extractall(extracted_dir)
        except Exception as e:
            return ("", False, f"Falha ao extrair ZIP: {e}")

        index_candidates = list(extracted_dir.rglob("package*_index.json"))
        if not index_candidates:
            return ("", False, "Nao foi encontrado arquivo package*_index.json dentro do ZIP")

        index_file = index_candidates[0].resolve()
        index_uri = index_file.as_uri()
        output_total = ""

        steps = [
            ("Registrando indice local de placas...", ["config", "add", "board_manager.additional_urls", index_uri]),
            ("Atualizando indice de placas...", ["core", "update-index"]),
        ]

        for label, args in steps:
            if progress_callback:
                progress_callback(label)
            cmd = [str(self.cli_path), "--config-file", str(self.config_file)] + args
            out, success, err = self._run_action_command(cmd, timeout=120)
            output_total += out

            # Se URL ja existe no config, tratamos como sucesso para seguir fluxo
            already_exists = "already exists" in (out or "").lower() or "ja existe" in (out or "").lower()
            if not success and not already_exists:
                return (output_total, False, f"{label} - {err}")

        self.log(f"Indice local de placas registrado: {index_file}")
        return (output_total, True, "")
    
    # ==================== BIBLIOTECAS ====================
    
    def list_libraries(self) -> list:
        """Lista bibliotecas instaladas - com múltiplas tentativas de parsing"""
        output = self.run_cli_sync(["lib", "list", "--format", "json"])
        
        if not output or not output.strip():
            self.log("[INFO] Nenhuma biblioteca instalada")
            return []
        
        data = self._parse_cli_json(output)
        if data is None:
            self.log("[ERRO] JSON inválido ao listar bibliotecas")
            return []
        
        try:
            
            # Tenta diversas estruturas possíveis
            libs = []
            if isinstance(data, dict):
                # Estrutura: {"installed_libraries": [...]}
                if "installed_libraries" in data:
                    libs = data.get("installed_libraries", [])
                # Estrutura: {"libraries": [...]}
                elif "libraries" in data:
                    libs = data.get("libraries", [])
                # Estrutura: {library_name: {...}, ...}
                else:
                    for name, info in data.items():
                        if isinstance(info, dict):
                            info['name'] = info.get('name', name)
                            libs.append(info)
            elif isinstance(data, list):
                # Já é lista
                libs = data
            
            # Garantir que cada lib tem campos obrigatórios
            for lib in libs:
                # Tentar extrair nome de múltiplos campos
                if 'name' not in lib or not lib['name'] or lib['name'] == '':
                    lib['name'] = (lib.get('title') or lib.get('library') or 
                                  lib.get('Title') or lib.get('Library') or 'Desconhecida')
                
                # Version - múltiplas tentativas
                if 'version' not in lib or not lib['version']:
                    lib['version'] = (lib.get('release') or lib.get('installed') or 
                                     lib.get('latest') or 'N/A')
                
                # Description/Sentence
                if 'sentence' not in lib or not lib['sentence']:
                    lib['sentence'] = (lib.get('sentence') or lib.get('description') or 
                                      lib.get('Description') or lib.get('Sentence') or '')
            
            if libs:
                self.log(f"[OK] Carregadas {len(libs)} bibliotecas")
            else:
                self.log("[INFO] Nenhuma biblioteca encontrada")
            
            return libs
            
        except json.JSONDecodeError as e:
            self.log(f"[ERRO] JSON inválido: {str(e)[:80]}")
            return []
        except Exception as e:
            self.log(f"[ERRO] Ao listar bibliotecas: {str(e)[:80]}")
            return []
    
    def list_libraries_fixed(self) -> list:
        """Parsing robusto para a aba Bibliotecas."""
        output = self.run_cli_sync(["lib", "list", "--format", "json"])
        if not output or not output.strip():
            self.log("[INFO] Nenhuma biblioteca instalada")
            return []

        data = self._parse_cli_json(output)
        if data is None:
            self.log("[ERRO] JSON invalido ao listar bibliotecas")
            return []

        raw_libs = []
        if isinstance(data, dict):
            if "installed_libraries" in data:
                raw_libs = data.get("installed_libraries", [])
            elif "libraries" in data:
                raw_libs = data.get("libraries", [])
            else:
                raw_libs = [v for v in data.values() if isinstance(v, dict)]
        elif isinstance(data, list):
            raw_libs = data

        libs = []
        for item in raw_libs:
            if not isinstance(item, dict):
                continue
            lib_obj = item.get("library") if isinstance(item.get("library"), dict) else item
            rel_obj = item.get("release") if isinstance(item.get("release"), dict) else {}
            inst_obj = item.get("installed") if isinstance(item.get("installed"), dict) else {}
            path_value = (
                item.get("path")
                or item.get("location")
                or item.get("install_dir")
                or lib_obj.get("path")
                or lib_obj.get("location")
                or inst_obj.get("path")
                or ""
            )
            name = (
                lib_obj.get("name")
                or lib_obj.get("title")
                or item.get("name")
                or item.get("title")
                or "Desconhecida"
            )
            version = (
                inst_obj.get("version")
                or rel_obj.get("version")
                or lib_obj.get("version")
                or item.get("version")
                or "N/A"
            )
            sentence = (
                lib_obj.get("sentence")
                or lib_obj.get("paragraph")
                or lib_obj.get("description")
                or item.get("sentence")
                or item.get("description")
                or ""
            )
            libs.append({"name": name, "version": version, "sentence": sentence, "path": path_value})

        unique = {}
        for lib in libs:
            key = f"{lib.get('name','').strip().lower()}::{lib.get('version','').strip().lower()}"
            if key not in unique:
                unique[key] = lib
        normalized = list(unique.values())

        if normalized:
            self.log(f"[OK] Carregadas {len(normalized)} bibliotecas")
        else:
            self.log("[INFO] Nenhuma biblioteca encontrada")
        return normalized

    def install_library_zip(self, zip_path: str):
        """Instala biblioteca de arquivo ZIP"""
        if not os.path.exists(zip_path):
            self.log(f"Erro: Arquivo não encontrado: {zip_path}")
            return
        self.run_cli_async(["lib", "install", str(zip_path)])
    
    def install_library(self, library_name: str):
        """Instala biblioteca pelo nome"""
        if not library_name:
            return
        self.run_cli_async(["lib", "install", library_name])

    def uninstall_library(self, library_name: str) -> tuple:
        """Remove biblioteca pelo nome e retorna (output, success, error_message)."""
        if not library_name:
            return ("", False, "Nome de biblioteca invalido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "lib", "uninstall", str(library_name)]
        return self._run_action_command(cmd, timeout=120)

    def find_library_path(self, library_name: str) -> Optional[Path]:
        """Tenta localizar o caminho de uma biblioteca instalada."""
        if not library_name:
            return None
        libs = self.list_libraries_fixed()
        for lib in libs:
            if lib.get("name", "").strip().lower() == library_name.strip().lower():
                raw_path = (lib.get("path") or "").strip()
                if raw_path:
                    p = Path(raw_path)
                    if p.exists():
                        return p

        candidates = [
            self.base_dir / "libraries" / library_name,
            Path.home() / "Documents" / "Arduino" / "libraries" / library_name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
    
    # ==================== CÓDIGO ====================
    
    def open_code_editor(self, project_path: str, editor: str = "code") -> bool:
        """Abre editor de código (VS Code por padrão)"""
        project_path = Path(project_path)
        
        if not project_path.exists():
            self.log(f"Erro: Projeto não existe em {project_path}")
            return False
        
        try:
            self._popen_subprocess([editor, str(project_path)])
            self.log(f"Editor '{editor}' aberto para {project_path.name}")
            return True
        except FileNotFoundError:
            self.log(f"Erro: Editor '{editor}' não encontrado no PATH")
            return False
        except Exception as e:
            self.log(f"Erro ao abrir editor: {e}")
            return False

