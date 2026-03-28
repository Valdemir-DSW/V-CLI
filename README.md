# V CLI - Arduino Development Interface

**A modern, lightweight IDE for Arduino development with arduino-cli**

[🇧🇷 Português](#português) | [🇬🇧 English](#english)

---

## English

### Overview

V CLI is a professional Tkinter-based interface for `arduino-cli`, designed to streamline Arduino project development. It provides an intuitive workspace with project management, real-time serial monitoring, and comprehensive board/library support.

### Key Features

✨ **Project Management**
- Quick project creation and access
- Automatic history tracking (latest 20 projects)
- Persistent configuration storage
- One-click folder navigation

⚙️ **Board Configuration**
- Support for 500+ Arduino boards (Arduino, STM32, ESP32, etc.)
- Dynamic variant and tool selection
- JSON-based configuration file
- Auto-detect board parameters

📡 **Serial Monitor**
- Real-time data visualization
- Synchronized send/receive
- Timestamp logging
- TX log export
- Auto-scan available ports

📚 **Library Management**
- Browse installed libraries
- Install from ZIP files
- Search and discovery
- Version tracking

🔨 **Compilation & Upload**
- One-click compilation with status feedback
- Automatic binary export
- Direct upload via USB/Serial
- Detailed error logging

### Installation

1. **Requirements**
   - Python 3.7+
   - arduino-cli executable (included)
   - Windows/macOS/Linux

2. **Setup**
   ```bash
   python start.py
   ```

3. **First Run**
   - Select "New" to create a project
   - Choose a folder
   - Select a board
   - Start coding!

### Project Structure

```
V cli/
├── main.py                 # Tkinter UI (5 tabs)
├── cli_backend.py          # arduino-cli wrapper
├── cli.yaml               # CLI configuration
├── arduino-cli.exe        # arduino-cli executable
├── locales/               # Translations
│   ├── en.json
│   └── pt.json
├── projects/              # Your projects directory
└── README.md
```

### Configuration

Each project contains a `project.fuse` file:

```json
{
  "fqbn": "arduino:avr:uno",
  "name": "MyProject",
  "variant": "default",
  "port": "COM3",
  "baudrate": "115200",
  "tools": {
    "pnum": "uno",
    "upload_method": "serial"
  }
}
```

### Interface

#### 5-Tab Layout
1. **Code** - Project settings, compilation, upload
2. **Boards** - Board selection and discovery
3. **Libraries** - Installed libraries and management
4. **Serial** - Monitor and terminal

#### Features at a Glance
- **Project Actions**
  - `...` button: Open folder in Windows Explorer
  - VS Code: Launch VS Code editor
  - Compile: Build project (light green)
  - Upload: Deploy to board (dark green)

- **Configuration**
  - Board selection with dynamic variants
  - Serial port and baud rate
  - Tool-specific options per board
  - Auto-save configurations

#### Console
- Real-time output from all operations
- Command logging
- Error highlighting
- Scrollable with full history

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Double-click | Open project from history |
| Right-click | Remove project from history |
| Ctrl+K | Clear serial console |

### Troubleshooting

**"arduino-cli not found"**
- Ensure `arduino-cli.exe` is in the root directory
- Run `check_env.py` to validate setup

**Port not detected**
- Check USB cable connection
- Restart the application
- Install CH340 drivers for certain boards

**Compilation fails**
- Verify board selection matches your hardware
- Check file encoding (UTF-8 recommended)
- Review error log in console

### Contributing

Report issues and suggest features via GitHub issues.

### License

See LICENSE.txt file in the project root.

---

## Português

### Visão Geral

V CLI é uma interface moderna baseada em Tkinter para `arduino-cli`, desenhada para otimizar o desenvolvimento de projetos Arduino. Oferece um ambiente de trabalho intuitivo com gerenciamento de projetos, monitoramento serial em tempo real e suporte abrangente para placas e bibliotecas.

### Características Principais

✨ **Gerenciamento de Projetos**
- Criação rápida e acesso a projetos
- Rastreamento automático de histórico (últimos 20 projetos)
- Armazenamento persistente de configurações
- Navegação com um clique para a pasta

⚙️ **Configuração de Placas**
- Suporte para 500+ placas Arduino (Arduino, STM32, ESP32, etc.)
- Seleção dinâmica de variantes e ferramentas
- Arquivo de configuração baseado em JSON
- Detecção automática de parâmetros

📡 **Monitor Serial**
- Visualização de dados em tempo real
- Envio/recebimento sincronizado
- Registro com timestamp
- Exportação de log TX
- Varredura automática de portas disponíveis

📚 **Gerenciamento de Bibliotecas**
- Navegação de bibliotecas instaladas
- Instalação a partir de arquivos ZIP
- Busca e descoberta
- Rastreamento de versão

🔨 **Compilação e Upload**
- Compilação com um clique e feedback de status
- Exportação automática de binário
- Upload direto via USB/Serial
- Registro detalhado de erros

### Instalação

1. **Requisitos**
   - Python 3.7+
   - Executável arduino-cli (incluído)
   - Windows/macOS/Linux

2. **Configuração**
   ```bash
   python start.py
   ```

3. **Primeira Execução**
   - Selecione "Novo" para criar um projeto
   - Escolha uma pasta
   - Selecione uma placa
   - Comece a programar!

### Estrutura do Projeto

```
V cli/
├── main.py                 # UI com Tkinter (5 abas)
├── cli_backend.py          # Arduino-cli wrapper
├── cli.yaml               # Configuração CLI
├── arduino-cli.exe        # Executável arduino-cli
├── locales/               # Traduções
│   ├── en.json
│   └── pt.json
├── projects/              # Diretório de projetos
└── README.md
```

### Configuração

Cada projeto contém um arquivo `project.fuse`:

```json
{
  "fqbn": "arduino:avr:uno",
  "name": "MeuProjeto",
  "variant": "padrão",
  "port": "COM3",
  "baudrate": "115200",
  "tools": {
    "pnum": "uno",
    "upload_method": "serial"
  }
}
```

### Interface

#### 5 Abas
1. **Código** - Configurações do projeto, compilação, upload
2. **Placas** - Seleção e descoberta de placas
3. **Bibliotecas** - Bibliotecas instaladas e gerenciamento
4. **Serial** - Monitor e terminal

#### Recursos Principais
- **Ações do Projeto**
  - Botão `...`: Abrir pasta no Explorer do Windows
  - VS Code: Lançar editor VS Code
  - Compilar: Construir projeto (verde claro)
  - Upload: Implantar na placa (verde escuro)

- **Configuração**
  - Seleção de placa com variantes dinâmicas
  - Porta serial e taxa de baud
  - Opções específicas de ferramentas por placa
  - Auto-save das configurações

#### Console
- Saída em tempo real de todas as operações
- Registro de comandos
- Destaque de erros
- Rolável com histórico completo

### Atalhos do Teclado

| Atalho | Ação |
|--------|------|
| Duplo-clique | Abrir projeto do histórico |
| Clique direito | Remover projeto do histórico |
| Ctrl+K | Limpar console serial |

### Solução de Problemas

**"arduino-cli não encontrado"**
- Certifique-se de que `arduino-cli.exe` está no diretório raiz
- Execute `check_env.py` para validar a configuração

**Porta não detectada**
- Verifique a conexão do cabo USB
- Reinicie a aplicação
- Instale drivers CH340 para certas placas

**Compilação falha**
- Verifique se a seleção de placa corresponde ao seu hardware
- Verificar codificação de arquivo (UTF-8 recomendado)
- Revise o log de erros no console

### Contribuindo

Relate problemas e sugira recursos via GitHub issues.

### Licença

Veja o arquivo LICENSE.txt na raiz do projeto.
| `invalid memory address or nil pointer dereference` | `python reset_cli.py` |
| Bibliotecas em branco | Clique **Atualizar** |
| Serial não funciona | `pip install pyserial` |
| arduino-cli não encontrado | Coloque .exe na pasta |

## 📦 Dependências

- Python 3.7+
- tkinter (incluído)
- pyserial (para monitor serial)
- arduino-cli v0.20.0+

```bash
# Monitor serial (opcional)
pip install pyserial
```

## 🔧 Recuperação

Se houver erros:
```bash
python reset_cli.py
```

Isto:
- Remove cli.yaml corrompido
- Recria com padrão
- Testa funcionamento
- Opcionalmente limpa dados locais

## 📖 Arquivos Importantes

- `cli_backend.py` - Operações CLI
- `main.py` - Interface completa
- `cli.yaml` - Configuração (auto-gerado)
- `.recent_projects.json` - Histórico (auto-gerado)

## 🎯 Melhorias Futuras

- [ ] Atalhos de teclado
- [ ] Tema escuro/claro
- [ ] Ícones customizados
- [ ] Detecção automática de placa
- [ ] Editor com syntax highlighting
- [ ] Exemplos de projetos

## ⚙️ Configuração Manual

Edite `cli.yaml` para adicionar placas customizadas:

```yaml
board_manager:
    additional_urls:
        - https://github.com/stm32duino/...
        - https://raw.githubusercontent.com/espressif/...
```

## 🔄 Fluxo Histórico

1. Novo/Abrir → projeto adicionado ao histórico
2. Histórico salvo em `.recent_projects.json`
3. Próxima vez → duplo-clique para abrir
4. Right-click para remover
5. Máximo 20 projetos no histórico

## 🎓 Exemplo: Novo Projeto

```
1. Clique [Novo]
2. Selecione C:\meus_projetos\MeuArduino
3. Arquivo MeuArduino.ino é criado
4. Arquivo project.fuse é criado
5. Projeto aparece no histórico
6. Duplo-clique → Abre no histórico
7. Aba Placas → Selecione placa
8. Aba Config → Configure (opcional)
9. Aba Código → Compilar e Upload
10. Aba Serial → Monitore serial (opcional)
```

## 📝 Notas

- Console mostra logs em tempo real
- Scroll automático no console
- Todas as configurações são salvas em project.fuse
- Histórico persiste entre execuções
- Monitor serial funciona em background

## 🤝 Estrutura Interna

```
VCliApp (UI)
├── CLIBackend (lógica)
│   ├── run_cli_sync()
│   ├── run_cli_async()
│   ├── list_boards()
│   ├── list_libraries()
│   └── ...
├── _create_code_tab()
├── _create_boards_tab()
├── _create_libs_tab()
├── _create_settings_tab()
├── _create_serial_tab()
└── Métodos de gerenciamento
```

## 📄 Licença

Conforme LICENSE.txt
