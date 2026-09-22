# Visualizador de EPUBs — Curso Online de Filosofia

Leitor local de EPUBs que abre no navegador. Não precisa instalar nada além do
[Python 3](https://www.python.org/downloads/) (usa somente a biblioteca padrão).

A biblioteca padrão são os EPUBs da pasta [`../epubs`](../epubs).

> Baixe o repositório (botão **Code → Download ZIP**) ou clone-o: o leitor e os
> EPUBs vêm juntos.

## Como usar

**Windows:** dê um duplo clique em `Visualizador.bat`.

**macOS / Linux:**

    python3 visualizador.py

O navegador abre automaticamente em `http://127.0.0.1:8017/`.

## Recursos

- Prateleira de capas estilo Kindle, com busca e ordem por aula.
- Leitura com sumário navegável, temas claro/sépia/escuro, tamanho da fonte,
  memória de posição e navegação pelas setas do teclado.
- Botões **Abrir EPUB** e arrastar-e-soltar para importar outros arquivos
  (copia para a pasta da biblioteca).
- Tudo roda localmente (`127.0.0.1`); nada sai para a internet.

## Opções

    python visualizador.py --dir "C:\caminho\epubs"   # outra biblioteca
    python visualizador.py --port 8080                # outra porta
    python visualizador.py --no-browser               # só o servidor

## Estrutura

    epubs/         # EPUBs (COF001–COF011 e futuros)
    visualizador/  # este leitor (visualizador.py + visualizador.html)
