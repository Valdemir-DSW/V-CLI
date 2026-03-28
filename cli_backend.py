"""
Camada de Backend - CLI arduino-cli Wrapper

Interface com arduino-cli para operacoes:
- Manipulacao de arquivos e projetos
- Compilacao e upload para placas
- Gerenciamento de bibliotecas
- Controle de configuracoes

Responsabilidades:
Ã¢Å“â€œ Executar comandos arduino-cli via subprocess
Ã¢Å“â€œ Parsear outputs JSON (placas, bibliotecas, tools)
Ã¢Å“â€œ Gerenciar projeto.fuzil (configuracoes em JSON)
Ã¢Å“â€œ Logging de operacoes atraves de callback
Ã¢Å“â€œ Tratamento de erros e timeouts

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
            base_dir: DiretÃƒÂ³rio base do projeto
            config_callback: FunÃƒÂ§ÃƒÂ£o para log/output (recebe string)
        """
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.cli_path = self.base_dir / "arduino-cli.exe"
        appdata_local = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.arduino15_dir = appdata_local / "Arduino15"
        self.vcli_data_dir = self.arduino15_dir / "V-CLI"
        self.vcli_data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.vcli_data_dir / "cli.yaml"
        self._fallback_config_file = self.base_dir / "cli.yaml"
        self._ensure_writable_config_path()
        self.config_callback = config_callback or (lambda x: None)
        self._process_lock = threading.Lock()
        self._current_process: Optional[subprocess.Popen] = None
        self._abort_requested = False
        
        # Criar diretÃƒÂ³rios necessÃƒÂ¡rios
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        
        # Inicializar CLI se necessÃƒÂ¡rio
        self._init_cli()

    def _ensure_writable_config_path(self):
        """Garante que o caminho de config escolhido seja gravÃƒÂ¡vel."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "a", encoding="utf-8"):
                pass
        except Exception:
            self.config_file = self._fallback_config_file
            try:
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self.log(f"[AVISO] Sem permissÃƒÂ£o no AppData. Usando config local: {self.config_file}")

    def _run_subprocess(self, cmd: list, timeout: int = 60):
        """Wrapper para subprocess.run sem abrir janela no Windows."""
        kwargs = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout,
        }
        if CREATE_NO_WINDOW:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    def _popen_subprocess(self, cmd: list, **kwargs):
        """Wrapper para subprocess.Popen sem abrir janela no Windows."""
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
        if CREATE_NO_WINDOW:
            kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
        return subprocess.Popen(cmd, **kwargs)
    
    def _init_cli(self):
        """Inicializa configuraÃƒÂ§ÃƒÂ£o do CLI"""
        if not self.config_file.exists():
            self.log("Inicializando arduino-cli...")
            self.run_cli_sync(["config", "init", "--config-file", str(self.config_file)])
            if not self.config_file.exists():
                try:
                    self._run_subprocess(
                        [str(self.cli_path), "config", "init", "--dest-file", str(self.config_file)],
                        timeout=60,
                    )
                except Exception:
                    pass
        # Evita travar a UI na inicializaÃƒÂ§ÃƒÂ£o: faz syncs pesados em thread.
        threading.Thread(target=self._background_cli_warmup, daemon=True).start()

    def _background_cli_warmup(self):
        try:
            self._ensure_default_indexes()
            self._ensure_indexes_downloaded()
        except Exception as exc:
            self.log(f"[AVISO] Warmup do CLI falhou: {exc}")

    def _ensure_default_indexes(self):
        """Garante URLs padrÃƒÂ£o ÃƒÂºteis no Board Manager."""
        default_urls = [
            "https://espressif.github.io/arduino-esp32/package_esp32_index.json",
            "https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json",
            "https://arduino.esp8266.com/stable/package_esp8266com_index.json",
            "https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json",
            "https://www.pjrc.com/teensy/package_teensy_index.json",
        ]
        current_urls = self.get_additional_board_urls()
        for url in default_urls:
            if url in current_urls:
                continue
            out, success, err = self.add_board_json_sync(url)
            if success:
                self.log(f"[OK] URL padrÃƒÂ£o registrada: {url}")
            else:
                self.log(f"[AVISO] Falha ao registrar URL padrÃƒÂ£o '{url}': {err or out[:150]}")

    def _ensure_indexes_downloaded(self):
        """Garante ÃƒÂ­ndices locais para listagens e versÃƒÂµes."""
        try:
            if not (self.arduino15_dir / "library_index.json").exists():
                self.run_cli_sync(["lib", "update-index"])
            if not any(self.arduino15_dir.glob("package*_index.json")):
                self.run_cli_sync(["core", "update-index"])
        except Exception:
            pass

    def _parse_cli_json(self, output: str) -> Optional[Any]:
        """Tenta extrair o primeiro JSON vÃƒÂ¡lido ignorando texto extra"""
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

    def _load_json_file(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                return None
            except Exception:
                continue
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
            pass  # Ignora erros de logging, nunca deve quebrar execuÃƒÂ§ÃƒÂ£o
    
    def run_cli_sync(self, args: list) -> str:
        """Executa CLI de forma sÃƒÂ­ncrona e retorna output"""
        if not self.cli_path.exists():
            self.log(f"Erro: arduino-cli nÃƒÂ£o encontrado em {self.cli_path}")
            return ""
        
        # Validar argumentos - garantir que nao ha None
        if not args or any(arg is None for arg in args):
            self.log("[ERRO] Argumentos invÃƒÂ¡lidos para CLI")
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
            self.log(f"[ERRO] ExceÃƒÂ§ÃƒÂ£o ao executar CLI: {e}")
            return ""
    
    def run_cli_async(self, args: list):
        """Executa CLI de forma assÃƒÂ­ncrona em thread separada"""
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
        Carrega configuraÃƒÂ§ÃƒÂµes de um projeto
        Se nÃƒÂ£o existe project.fuzil, cria automÃƒÂ¡tico
        Com tratamento robusto de encoding
        """
        project_path = Path(project_path)
        if not project_path.exists():
            self.log(f"Erro: Projeto nÃƒÂ£o existe em {project_path}")
            return None
        
        fuse_file = project_path / "project.fuzil"
        
        # Se nÃƒÂ£o existe, criar padrÃƒÂ£o
        if not fuse_file.exists():
            self.log(f"Arquivo de definiÃƒÂ§ÃƒÂµes nÃƒÂ£o encontrado. Criando padrÃƒÂ£o...")
            self._create_default_fuzil(project_path)
        
        # Carregar configuraÃƒÂ§ÃƒÂ£o com tratamento de encoding
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
        self.log(f"Erro: NÃƒÂ£o consegui ler project.fuzil, criando novo...")
        self._create_default_fuzil(project_path)
        
        # Tentar carregar novamente
        try:
            with open(fuse_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except Exception as e:
            self.log(f"Erro crÃƒÂ­tico ao carregar project.fuzil: {e}")
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
    
    # ==================== COMPILAÃƒâ€¡ÃƒÆ’O E UPLOAD ====================
    
    def compile(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Compila projeto e retorna (output, success, error_message)"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli nÃƒÂ£o encontrado")
        
        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN invÃƒÂ¡lido")
        
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "compile", "--fqbn", str(fqbn)]
        cmd += self._build_board_option_args(config)
        cmd.append(str(project_path))
        try:
            result = self._run_subprocess(cmd, timeout=120)
            
            output = result.stdout + result.stderr
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "CompilaÃƒÂ§ÃƒÂ£o falhou"
                return (output, False, error_msg[:500])
            
            return (output, True, "")
            
        except subprocess.TimeoutExpired:
            return ("", False, "CompilaÃƒÂ§ÃƒÂ£o expirou (timeout 120s)")
        except Exception as e:
            return ("", False, str(e))

    def export_binary(self, project_path: str, fqbn: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Exporta binÃƒÂ¡rio compilado usando --export-binaries"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli nÃƒÂ£o encontrado")

        if not project_path or not fqbn:
            return ("", False, "Caminho ou FQBN invÃƒÂ¡lido")

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
                error_msg = result.stderr if result.stderr else "ExportaÃƒÂ§ÃƒÂ£o falhou"
                return (output, False, error_msg[:500])

            return (output, True, "")

        except subprocess.TimeoutExpired:
            return ("", False, "ExportaÃƒÂ§ÃƒÂ£o expirou (timeout 120s)")
        except Exception as e:
            return ("", False, str(e))

    def upload(self, project_path: str, fqbn: str, port: str, config: Optional[Dict[str, Any]] = None) -> tuple:
        """Faz upload para placa e retorna (output, success, error_message)"""
        if not self.cli_path.exists():
            return ("", False, f"arduino-cli nÃƒÂ£o encontrado")
        
        if not project_path or not fqbn or not port:
            return ("", False, "Caminho, FQBN ou porta invÃƒÂ¡lidos")
        
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
        """Lista todas as placas disponÃƒÂ­veis"""
        output = self.run_cli_sync(["board", "listall", "--format", "json"])
        
        if not output or not output.strip():
            self.log("[AVISO] Nenhuma placa disponÃƒÂ­vel. Verifique a configuraÃƒÂ§ÃƒÂ£o.")
            return []
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                self.log("[ERRO] JSON invÃƒÂ¡lido ao parsear placas")
                return []
            boards = data.get("boards", [])
            if boards:
                self.log(f"Carregadas {len(boards)} placas")
            else:
                self.log("[AVISO] Nenhuma placa encontrada")
            return boards
        except json.JSONDecodeError as e:
            self.log(f"[ERRO] JSON invÃƒÂ¡lido ao parsear placas: {str(e)[:100]}")
            return []
        except Exception as e:
            self.log(f"[ERRO] Erro ao listar placas: {e}")
            return []
    
    def get_board_variants(self, fqbn: str) -> list:
        """Extrai variantes disponÃƒÂ­veis para uma placa especÃƒÂ­fica"""
        # Comando correto: board details -b <FQBN> --format json
        output = self.run_cli_sync(["board", "details", "-b", fqbn, "--full", "--format", "json"])
        
        if not output or not output.strip():
            return []
        
        try:
            data = self._parse_cli_json(output)
            if not isinstance(data, dict):
                return []
            
            # Tenta extrair variantes de vÃƒÂ¡rias estruturas possÃƒÂ­veis
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
                    # Se houver, retorna como opÃƒÂ§ÃƒÂ£o ÃƒÂºnica
                    variants.append({
                        "id": props.get("build.variant", ""),
                        "name": props.get("build.variant", "")
                    })
            
            return variants
            
        except Exception as e:
            self.log(f"[ERRO] Ao extrair variantes: {e}")
            return []
    
    def get_upload_modes(self, fqbn: str) -> list:
        """Extrai modos de upload disponÃƒÂ­veis para uma placa"""
        # Comando correto: board details -b <FQBN> --format json
        output = self.run_cli_sync(["board", "details", "-b", fqbn, "--full", "--format", "json"])
        
        if not output or not output.strip():
            # Fallback: Serial ÃƒÂ© sempre suportado
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
            
            # Sempre add Serial se nÃƒÂ£o estiver lÃƒÂ¡
            if not any(m["id"] == "serial" for m in modes):
                modes.insert(0, {"id": "serial", "name": "Serial"})
            
            return modes if modes else [{"id": "serial", "name": "Serial"}]
            
        except Exception as e:
            self.log(f"[ERRO] Ao extrair upload modes: {e}")
            return [{"id": "serial", "name": "Serial"}]
    
    def get_platform_tools(self, fqbn: str) -> list:
        """Extrai ferramentas/configuraÃƒÂ§ÃƒÂµes disponÃƒÂ­veis para uma placa (como Arduino IDE)"""
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
                
                # config_opts pode ser dict ou list dependendo da versÃƒÂ£o
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
                    
                    # Converter valores para formato padrÃƒÂ£o
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

        extracted_root = self.vcli_data_dir / "board_indexes"
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

    def get_additional_board_urls(self) -> list:
        """Lista URLs configuradas em board_manager.additional_urls."""
        output = self.run_cli_sync(["config", "dump", "--format", "json"])
        data = self._parse_cli_json(output)
        if not isinstance(data, dict):
            return []
        manager = data.get("board_manager", {})
        urls = manager.get("additional_urls", [])
        if isinstance(urls, list):
            return [str(u).strip() for u in urls if str(u).strip()]
        if isinstance(urls, str) and urls.strip():
            return [urls.strip()]
        return []

    def remove_board_json_sync(self, url: str) -> tuple:
        """Remove URL de JSON adicional de placas."""
        if not url:
            return ("", False, "URL invalida")
        cmd = [
            str(self.cli_path),
            "--config-file",
            str(self.config_file),
            "config",
            "delete",
            "board_manager.additional_urls",
            url,
        ]
        out, ok, err = self._run_action_command(cmd, timeout=60)
        if not ok:
            return (out, False, err)
        out2 = self.run_cli_sync(["core", "update-index"])
        return (out + out2, True, "")

    @staticmethod
    def _normalize_version(version: str) -> list:
        clean = str(version or "").strip()
        if not clean:
            return [0]
        parts = []
        for token in clean.replace("-", ".").split("."):
            num = "".join(ch for ch in token if ch.isdigit())
            parts.append(int(num) if num else 0)
        return parts or [0]

    @classmethod
    def _is_newer_version(cls, candidate: str, current: str) -> bool:
        return cls._normalize_version(candidate) > cls._normalize_version(current)

    def _iter_package_index_files(self) -> list:
        files = set()
        files.add(self.arduino15_dir / "package_index.json")
        for p in self.arduino15_dir.glob("package*_index.json"):
            files.add(p)
        board_idx_root = self.vcli_data_dir / "board_indexes"
        if board_idx_root.exists():
            for p in board_idx_root.rglob("package*_index.json"):
                files.add(p)
        return [p for p in files if p.exists()]

    def _core_catalog(self) -> Dict[str, dict]:
        catalog: Dict[str, dict] = {}
        for idx_file in self._iter_package_index_files():
            data = self._load_json_file(idx_file)
            if not isinstance(data, dict):
                continue
            for pkg in data.get("packages", []):
                if not isinstance(pkg, dict):
                    continue
                pkg_name = str(pkg.get("name") or "").strip()
                pkg_url = str(pkg.get("websiteURL") or "").strip()
                for platform in pkg.get("platforms", []):
                    if not isinstance(platform, dict):
                        continue
                    arch = str(platform.get("architecture") or "").strip()
                    if not pkg_name or not arch:
                        continue
                    core_id = f"{pkg_name}:{arch}"
                    version = str(platform.get("version") or "").strip()
                    entry = catalog.setdefault(
                        core_id,
                        {
                            "id": core_id,
                            "name": str(platform.get("name") or core_id),
                            "url": str(platform.get("url") or pkg_url),
                            "versions": [],
                            "latest_version": "",
                        },
                    )
                    if version and version not in entry["versions"]:
                        entry["versions"].append(version)
        for entry in catalog.values():
            entry["versions"].sort(key=lambda v: self._normalize_version(v), reverse=True)
            if entry["versions"]:
                entry["latest_version"] = entry["versions"][0]
        return catalog

    def search_cores(self, term: str = "") -> list:
        catalog = list(self._core_catalog().values())
        installed_map = {}
        for core in self.list_installed_cores():
            installed_obj = core.get("installed") if isinstance(core.get("installed"), dict) else {}
            core_id = str(core.get("id") or installed_obj.get("id") or "").strip()
            version = str(core.get("installed_version") or core.get("version") or installed_obj.get("version") or "").strip()
            if core_id:
                installed_map[core_id] = version

        normalized_term = term.strip().lower()
        results = []
        for core in catalog:
            hay = f"{core.get('name','')} {core.get('id','')}".lower()
            if normalized_term and normalized_term not in hay:
                continue
            item = dict(core)
            item["installed_version"] = installed_map.get(item.get("id", ""), "")
            results.append(item)
        results.sort(key=lambda x: x.get("name", "").lower())
        return results

    def list_installed_cores(self) -> list:
        output = self.run_cli_sync(["core", "list", "--format", "json"])
        data = self._parse_cli_json(output)
        if isinstance(data, dict):
            if isinstance(data.get("installed_platforms"), list):
                return data.get("installed_platforms", [])
            if isinstance(data.get("platforms"), list):
                return data.get("platforms", [])
        if isinstance(data, list):
            return data
        return []

    def list_core_updates(self) -> list:
        installed = self.list_installed_cores()
        catalog = self._core_catalog()
        updates = []
        for core in installed:
            installed_obj = core.get("installed") if isinstance(core.get("installed"), dict) else {}
            core_id = str(core.get("id") or installed_obj.get("id") or "").strip()
            current_version = str(
                core.get("installed_version")
                or core.get("version")
                or installed_obj.get("version")
                or ""
            ).strip()
            latest = catalog.get(core_id, {})
            latest_version = str(latest.get("latest_version") or "").strip()
            if core_id and current_version and latest_version and self._is_newer_version(latest_version, current_version):
                updates.append(
                    {
                        "id": core_id,
                        "name": latest.get("name") or core.get("name") or core_id,
                        "installed_version": current_version,
                        "latest_version": latest_version,
                        "url": latest.get("url", ""),
                        "versions": latest.get("versions", []),
                    }
                )
        return updates

    def _is_version_not_found_error(self, message: str) -> bool:
        msg = str(message or "").lower()
        patterns = (
            "version not found",
            "versao nao encontrada",
            "nao encontrada",
        )
        return "version" in msg and any(p in msg for p in patterns)

    def _is_os_tool_unavailable_error(self, message: str) -> bool:
        msg = str(message or "").lower()
        patterns = (
            "no available version for your operating system",
            "no version available for your operating system",
            "no versions available for the current os",
            "no version available for the current os",
            "nao ha versao disponivel para o sistema operacional",
            "sem versao disponivel para o sistema operacional",
            "error downloading tool",
        )
        return any(p in msg for p in patterns)

    def install_core_sync(self, core_id: str, version: str = "") -> tuple:
        if not core_id:
            return ("", False, "ID da plataforma invalido")
        clean_version = (version or "").strip()
        target = f"{core_id}@{clean_version}" if clean_version else core_id.strip()
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "core", "install", target]
        out, ok, err = self._run_action_command(cmd, timeout=600)
        if ok:
            return (out, ok, err)
        msg = f"{err}\n{out}".lower()
        if self._is_version_not_found_error(msg):
            self.run_cli_sync(["core", "update-index"])
            return self._run_action_command(cmd, timeout=600)
        if self._is_os_tool_unavailable_error(msg):
            self.run_cli_sync(["core", "update-index"])
            output_total = out or ""
            attempted = {clean_version} if clean_version else set()
            for candidate in self.get_core_versions(core_id):
                if not candidate or candidate in attempted:
                    continue
                attempted.add(candidate)
                cmd_try = [
                    str(self.cli_path),
                    "--config-file",
                    str(self.config_file),
                    "core",
                    "install",
                    f"{core_id}@{candidate}",
                ]
                out_try, ok_try, err_try = self._run_action_command(cmd_try, timeout=600)
                output_total += "\n" + (out_try or "")
                if ok_try:
                    return (output_total, True, "")
                low_try = f"{err_try}\n{out_try}".lower()
                if not self._is_os_tool_unavailable_error(low_try):
                    return (output_total, False, err_try or err)
            cmd_latest = [str(self.cli_path), "--config-file", str(self.config_file), "core", "install", core_id.strip()]
            out2, ok2, err2 = self._run_action_command(cmd_latest, timeout=600)
            output_total += "\n" + (out2 or "")
            if ok2:
                return (output_total, True, "")
            hint = (
                "Pacote sem ferramenta compativel com o sistema operacional atual. "
                "Tente instalar outra versao da plataforma no gerenciador "
                "(mais antiga ou estavel) ou contate packages@arduino.cc."
            )
            return (output_total, False, err2 or err or hint)
        return (out, ok, err)
    def uninstall_core_sync(self, core_id: str) -> tuple:
        if not core_id:
            return ("", False, "ID da plataforma invalido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "core", "uninstall", core_id.strip()]
        return self._run_action_command(cmd, timeout=600)

    def upgrade_core_sync(self, core_id: str) -> tuple:
        if not core_id:
            return ("", False, "ID da plataforma invalido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "core", "upgrade", core_id.strip()]
        return self._run_action_command(cmd, timeout=600)

    def get_core_versions(self, core_id: str) -> list:
        core_id = str(core_id or "").strip()
        if not core_id:
            return []
        catalog = self._core_catalog()
        if core_id in catalog:
            return list(catalog[core_id].get("versions", []))
        return []
    
    # ==================== BIBLIOTECAS ====================
    
    def list_libraries(self) -> list:
        """Lista bibliotecas instaladas - com mÃƒÂºltiplas tentativas de parsing"""
        output = self.run_cli_sync(["lib", "list", "--format", "json"])
        
        if not output or not output.strip():
            self.log("[INFO] Nenhuma biblioteca instalada")
            return []
        
        data = self._parse_cli_json(output)
        if data is None:
            self.log("[ERRO] JSON invÃƒÂ¡lido ao listar bibliotecas")
            return []
        
        try:
            
            # Tenta diversas estruturas possÃƒÂ­veis
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
                # JÃƒÂ¡ ÃƒÂ© lista
                libs = data
            
            # Garantir que cada lib tem campos obrigatÃƒÂ³rios
            for lib in libs:
                # Tentar extrair nome de mÃƒÂºltiplos campos
                if 'name' not in lib or not lib['name'] or lib['name'] == '':
                    lib['name'] = (lib.get('title') or lib.get('library') or 
                                  lib.get('Title') or lib.get('Library') or 'Desconhecida')
                
                # Version - mÃƒÂºltiplas tentativas
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
            self.log(f"[ERRO] JSON invÃƒÂ¡lido: {str(e)[:80]}")
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

    def install_library_zip_sync(self, zip_path: str) -> tuple:
        """Instala biblioteca de arquivo ZIP com retorno detalhado."""
        if not zip_path:
            return ("", False, "Caminho do ZIP invÃƒÂ¡lido")
        if not os.path.exists(zip_path):
            return ("", False, f"Arquivo nÃƒÂ£o encontrado: {zip_path}")
        if Path(zip_path).suffix.lower() != ".zip":
            return ("", False, "Arquivo selecionado nÃƒÂ£o ÃƒÂ© um ZIP")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "lib", "install", str(zip_path)]
        return self._run_action_command(cmd, timeout=300)

    def install_library_sync(self, library_name: str, version: str = "") -> tuple:
        """Instala biblioteca pelo nome, opcionalmente em versÃƒÂ£o especÃƒÂ­fica."""
        if not library_name:
            return ("", False, "Nome da biblioteca invÃƒÂ¡lido")
        if str(library_name).lower().endswith(".zip"):
            return self.install_library_zip_sync(library_name)
        target = library_name.strip()
        normalized_version = (version or "").strip().lower()
        if normalized_version in {"latest", "mais recente", "newest"}:
            normalized_version = ""
        if normalized_version:
            target = f"{target}@{normalized_version}"
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "lib", "install", target]
        out, ok, err = self._run_action_command(cmd, timeout=300)
        if ok:
            return (out, ok, err)
        msg = f"{err}\n{out}".lower()
        if "version" in msg and ("not found" in msg or "nÃƒÂ£o encontrada" in msg or "nao encontrada" in msg):
            self.run_cli_sync(["lib", "update-index"])
            return self._run_action_command(cmd, timeout=300)
        return (out, ok, err)

    def install_library_zip(self, zip_path: str):
        """Compat: mantÃƒÂ©m API antiga assÃƒÂ­ncrona."""
        def task():
            out, ok, err = self.install_library_zip_sync(zip_path)
            if not ok:
                self.log(f"[ERRO] InstalaÃƒÂ§ÃƒÂ£o ZIP falhou: {err or out[:180]}")
        threading.Thread(target=task, daemon=True).start()

    def install_library(self, library_name: str):
        """Compat: mantÃƒÂ©m API antiga assÃƒÂ­ncrona."""
        def task():
            out, ok, err = self.install_library_sync(library_name)
            if not ok:
                self.log(f"[ERRO] InstalaÃƒÂ§ÃƒÂ£o de biblioteca falhou: {err or out[:180]}")
        threading.Thread(target=task, daemon=True).start()

    def uninstall_library(self, library_name: str) -> tuple:
        """Remove biblioteca pelo nome e retorna (output, success, error_message)."""
        if not library_name:
            return ("", False, "Nome de biblioteca invalido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "lib", "uninstall", str(library_name)]
        return self._run_action_command(cmd, timeout=120)

    def _library_catalog(self) -> Dict[str, dict]:
        index_file = self.arduino15_dir / "library_index.json"
        data = self._load_json_file(index_file)
        catalog: Dict[str, dict] = {}
        if not isinstance(data, dict):
            return catalog
        for lib in data.get("libraries", []):
            if not isinstance(lib, dict):
                continue
            name = str(lib.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            entry = catalog.setdefault(
                key,
                {
                    "name": name,
                    "sentence": str(lib.get("sentence") or ""),
                    "paragraph": str(lib.get("paragraph") or ""),
                    "url": str(lib.get("website") or ""),
                    "versions": [],
                    "latest_version": "",
                },
            )
            ver = str(lib.get("version") or "").strip()
            if ver and ver not in entry["versions"]:
                entry["versions"].append(ver)
        for entry in catalog.values():
            entry["versions"].sort(key=lambda v: self._normalize_version(v), reverse=True)
            entry["latest_version"] = entry["versions"][0] if entry["versions"] else ""
        return catalog

    def search_libraries(self, term: str = "", limit: int = 0) -> list:
        catalog = self._library_catalog()
        normalized_term = term.strip().lower()
        all_items = sorted(catalog.values(), key=lambda x: x.get("name", "").lower())
        if normalized_term:
            all_items = [
                x
                for x in all_items
                if normalized_term in f"{x.get('name','')} {x.get('sentence','')} {x.get('paragraph','')}".lower()
            ]
        elif limit <= 0:
            limit = 20

        if limit > 0:
            all_items = all_items[:limit]
        return all_items

    def get_library_versions(self, library_name: str) -> list:
        name = str(library_name or "").strip().lower()
        if not name:
            return []
        catalog = self._library_catalog()
        entry = catalog.get(name)
        if not entry:
            return []
        return list(entry.get("versions", []))

    def list_library_updates(self) -> list:
        installed = self.list_libraries_fixed()
        catalog = self._library_catalog()
        updates = []
        for lib in installed:
            name = str(lib.get("name") or "").strip()
            current = str(lib.get("version") or "").strip()
            if not name:
                continue
            meta = catalog.get(name.lower())
            if not meta:
                continue
            latest = str(meta.get("latest_version") or "").strip()
            if current and latest and self._is_newer_version(latest, current):
                updates.append(
                    {
                        "name": name,
                        "version": current,
                        "latest_version": latest,
                        "sentence": meta.get("sentence", ""),
                        "url": meta.get("url", ""),
                        "versions": meta.get("versions", []),
                    }
                )
        return updates

    def upgrade_library_sync(self, library_name: str) -> tuple:
        if not library_name:
            return ("", False, "Nome da biblioteca invÃƒÂ¡lido")
        cmd = [str(self.cli_path), "--config-file", str(self.config_file), "lib", "upgrade", library_name.strip()]
        return self._run_action_command(cmd, timeout=300)

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
            self.vcli_data_dir / "libraries" / library_name,
            self.arduino15_dir / "libraries" / library_name,
            Path.home() / "Documents" / "Arduino" / "libraries" / library_name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
    
    # ==================== CÃƒâ€œDIGO ====================
    
    def open_code_editor(self, project_path: str, editor: str = "code") -> bool:
        """Abre editor de cÃƒÂ³digo (VS Code por padrÃƒÂ£o)"""
        project_path = Path(project_path)
        
        if not project_path.exists():
            self.log(f"Erro: Projeto nÃƒÂ£o existe em {project_path}")
            return False
        
        try:
            self._popen_subprocess([editor, str(project_path)])
            self.log(f"Editor '{editor}' aberto para {project_path.name}")
            return True
        except FileNotFoundError:
            self.log(f"Erro: Editor '{editor}' nÃƒÂ£o encontrado no PATH")
            return False
        except Exception as e:
            self.log(f"Erro ao abrir editor: {e}")
            return False



