# Serial CSV e Plot

## Visão geral

A aba `Serial` agora trabalha em 3 modos:

- `Terminal`: monitor serial tradicional.
- `Plot`: plota séries numéricas recebidas pela serial.
- `CSV Log`: mostra linhas CSV em tabela, com erros destacados.

O menu `Ferramentas > Ler Log CSV` abre logs salvos da pasta `logs` do projeto.

## Formato esperado

O modo CSV funciona melhor quando o firmware envia:

```text
tempo_ms,fps,temp,pressao
0,29.8,24.1,101.3
33,30.1,24.2,101.4
66,29.9,24.2,101.2
```

Também funciona sem cabeçalho:

```text
0,29.8,24.1
33,30.1,24.2
66,29.9,24.2
```

Nesse caso o V CLI cria colunas automáticas como `value_1`, `value_2`, `value_3`.

## Gráficos ao vivo

- O plot usa as colunas numéricas detectadas no CSV.
- O eixo X usa o tempo decorrido da captura em milissegundos.
- O seletor lateral permite ligar e desligar séries.
- Tipos de plot disponíveis:
  - `line`
  - `step`
  - `scatter`
  - `bar`

## Gravação de log

1. Abra a aba `Serial`.
2. Clique em `Gravar CSV`.
3. Faça a captura normalmente.
4. Clique em `Parar gravação`.
5. Escolha:
   - `Salvar`: define nome e descrição.
   - `Excluir`: apaga a captura temporária.

Os arquivos ficam em:

```text
<projeto>/logs/
```

Ao salvar, o sistema gera:

- `nome_do_log.csv`: dados da captura
- `nome_do_log.meta.json`: descrição e metadados

## Registro de erro

Linhas contendo termos como `error`, `erro`, `exception`, `fail`, `panic` ou `traceback` são marcadas como erro.

- No modo ao vivo elas aparecem na área de erros.
- No log salvo elas ficam com `error_flag = true`.
- No leitor de logs elas aparecem separadas na aba `Erros`.

## Dicas para funcionar melhor

- Use sempre a mesma quantidade de colunas por linha CSV.
- Se possível, envie um cabeçalho na primeira linha.
- Evite misturar textos livres no meio do stream CSV.
- Se quiser registrar eventos textuais, prefixe mensagens de erro claramente para facilitar a detecção.
