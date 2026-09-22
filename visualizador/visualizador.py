#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
visualizador.py — Visualizador local de EPUBs no navegador, sem dependências.

Uso:
    python visualizador.py                  # abre o navegador automaticamente
    python visualizador.py --port 8080      # porta fixa
    python visualizador.py --dir "C:\\caminho\\epubs"
    python visualizador.py --no-browser     # apenas inicia o servidor

A biblioteca padrão é a pasta "epubs" ao lado deste script (ou a pasta "epubs"
um nível acima, no caso do repositório).
Qualquer arquivo .epub local pode ser carregado pelo botão "Abrir EPUB".
"""

import argparse
import json
import mimetypes
import posixpath
import re
import sys
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import xml.etree.ElementTree as ET

PASTA = Path(__file__).resolve().parent
PAGINA = PASTA / "visualizador.html"
IDIOMA = "pt-BR"


def _biblioteca_padrao():
    """Pasta epubs ao lado do script ou um nível acima (layout do repositório)."""
    for candidato in (PASTA / "epubs", PASTA.parent / "epubs"):
        if candidato.is_dir():
            return candidato
    return PASTA / "epubs"

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("application/xhtml+xml", ".xhtml")
mimetypes.add_type("image/svg+xml", ".svg")


# ---------------------------------------------------------------------------
# Leitura de EPUB
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


class Epub:
    """Leitor mínimo de EPUB2/EPUB3 baseado em zipfile + ElementTree."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.zip = zipfile.ZipFile(caminho)
        self._abrir()

    def fechar(self):
        try:
            self.zip.close()
        except Exception:
            pass

    # -- infraestrutura -----------------------------------------------------

    def _xml(self, nome):
        return ET.fromstring(self.zip.read(nome))

    def _resolver(self, base, href):
        href = unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(base, href))

    def ler_texto(self, nome):
        return self.zip.read(nome).decode("utf-8", errors="replace")

    # -- parsing ------------------------------------------------------------

    def _abrir(self):
        container = self._xml("META-INF/container.xml")
        raiz = next(el for el in container.iter() if _local(el.tag) == "rootfile")
        self.opf = raiz.get("full-path")
        self.opf_dir = posixpath.dirname(self.opf)

        opf = self._xml(self.opf)
        self.meta = {}
        self.manifest = {}
        self.spine = []
        self.ncx = None

        for el in opf.iter():
            nome = _local(el.tag)
            if nome == "metadata":
                self._ler_metadados(el)
            elif nome == "item":
                self.manifest[el.get("id")] = {
                    "href": el.get("href", ""),
                    "type": el.get("media-type", ""),
                    "props": el.get("properties", "") or "",
                }
            elif nome == "spine":
                self.ncx = el.get("toc")
            elif nome == "itemref":
                item = self.manifest.get(el.get("idref"))
                if item:
                    self.spine.append(self._resolver(self.opf_dir, item["href"]))

        self.css = [
            self._resolver(self.opf_dir, it["href"])
            for it in self.manifest.values()
            if it["type"] == "text/css" and not it["href"].startswith("http")
        ]
        self.capa = self._achar_capa()
        self.toc = self._ler_toc()

    def _ler_metadados(self, md):
        for el in md:
            nome = _local(el.tag)
            texto = (el.text or "").strip()
            if nome == "title" and "title" not in self.meta:
                self.meta["title"] = texto
            elif nome == "creator":
                self.meta.setdefault("author", texto)
            elif nome == "date":
                self.meta.setdefault("date", texto)
            elif nome == "language":
                self.meta.setdefault("language", texto)
            elif nome == "meta":
                prop = el.get("property") or el.get("name") or ""
                valor = el.get("content") or texto
                if prop == "calibre:series":
                    self.meta.setdefault("series", valor)
                elif prop == "calibre:series_index":
                    self.meta.setdefault("series_index", valor)
                elif prop == "belongs-to-collection":
                    self.meta.setdefault("series", valor)

    def _achar_capa(self):
        for it in self.manifest.values():
            if "cover-image" in it["props"].split():
                return self._resolver(self.opf_dir, it["href"])
        for item_id, it in self.manifest.items():
            if item_id == "cover" and it["type"].startswith("image/"):
                return self._resolver(self.opf_dir, it["href"])
        return None

    def _ler_toc(self):
        # EPUB3: documento de navegação
        nav = next((it for it in self.manifest.values() if "nav" in it["props"].split()), None)
        if nav:
            caminho = self._resolver(self.opf_dir, nav["href"])
            try:
                entradas = self._toc_nav(caminho)
                if entradas:
                    return self._mapear(entradas, caminho)
            except ET.ParseError:
                pass

        # EPUB2: toc.ncx
        if self.ncx and self.ncx in self.manifest:
            caminho = self._resolver(self.opf_dir, self.manifest[self.ncx]["href"])
            try:
                return self._mapear(self._toc_ncx(caminho), caminho)
            except ET.ParseError:
                pass
        return []

    def _toc_nav(self, caminho):
        raiz = self._xml(caminho)
        alvo = None
        for el in raiz.iter():
            if _local(el.tag) != "nav":
                continue
            tipo = el.get("{http://www.idpf.org/2007/ops}type") or el.get("type") or ""
            if "toc" in tipo:
                alvo = el
                break
        if alvo is None:
            return []
        entradas = []

        def coletar(lista, nivel):
            for li in lista:
                if _local(li.tag) != "li":
                    continue
                link = next((c for c in li if _local(c.tag) == "a"), None)
                if link is not None:
                    rotulo = " ".join("".join(link.itertext()).split())
                    entradas.append((rotulo, link.get("href", ""), nivel))
                for sub in li:
                    if _local(sub.tag) in ("ol", "ul"):
                        coletar(sub, nivel + 1)

        for lista in alvo:
            if _local(lista.tag) in ("ol", "ul"):
                coletar(lista, 0)
        return entradas

    def _toc_ncx(self, caminho):
        raiz = self._xml(caminho)
        entradas = []

        def coletar(pontos, nivel):
            for ponto in pontos:
                if _local(ponto.tag) != "navPoint":
                    continue
                rotulo = ""
                src = ""
                for filho in ponto:
                    if _local(filho.tag) == "navLabel":
                        rotulo = " ".join("".join(filho.itertext()).split())
                    elif _local(filho.tag) == "content":
                        src = filho.get("src", "")
                if src:
                    entradas.append((rotulo, src, nivel))
                coletar([f for f in ponto if _local(f.tag) == "navPoint"], nivel + 1)

        for mapa in raiz.iter():
            if _local(mapa.tag) == "navMap":
                coletar(list(mapa), 0)
                break
        return entradas

    def _mapear(self, entradas, toc_path):
        indice = {href: i for i, href in enumerate(self.spine)}
        base = posixpath.dirname(toc_path)
        saida = []
        for rotulo, href, nivel in entradas:
            alvo = self._resolver(base, href)
            saida.append({
                "label": rotulo,
                "index": indice.get(alvo),
                "depth": nivel,
            })
        return saida

    # -- utilidades ----------------------------------------------------------

    def resumo(self, ident):
        return {
            "id": ident,
            "title": self.meta.get("title") or self.caminho.stem,
            "author": self.meta.get("author", ""),
            "date": self.meta.get("date", ""),
            "series": self.meta.get("series", ""),
            "series_index": self.meta.get("series_index", ""),
            "chapters": len(self.spine),
            "cover": self.capa,
            "size": self.caminho.stat().st_size,
        }

    def detalhe(self, ident):
        r = self.resumo(ident)
        r.update({
            "spine": self.spine,
            "toc": self.toc,
            "css": self.css,
        })
        return r

    def capitulo(self, indice):
        return self.ler_texto(self.spine[indice])

    def recurso(self, caminho):
        caminho = posixpath.normpath(unquote(caminho)).lstrip("/")
        if caminho.startswith("..") or caminho not in self.zip.namelist():
            return None, None
        tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
        return self.zip.read(caminho), tipo


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------

class Aplicacao:
    def __init__(self, pasta):
        self.pasta = Path(pasta)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.cache_resumos = {}

    def livros(self):
        saida = []
        for caminho in sorted(self.pasta.glob("*.epub")):
            chave = (str(caminho), caminho.stat().st_mtime_ns, caminho.stat().st_size)
            resumo = self.cache_resumos.get(chave)
            if resumo is None:
                try:
                    epub = Epub(caminho)
                    resumo = epub.resumo(caminho.name)
                    epub.fechar()
                except Exception:
                    continue
                self.cache_resumos[chave] = resumo
            saida.append(resumo)
        self.cache_resumos = {k: v for k, v in self.cache_resumos.items() if k in
                              {(str(p), p.stat().st_mtime_ns, p.stat().st_size)
                               for p in self.pasta.glob("*.epub")}}
        saida.sort(key=_ordem_prateleira)
        return saida

    def caminho_de(self, ident):
        if "/" in ident or "\\" in ident or not ident.lower().endswith(".epub"):
            return None
        caminho = self.pasta / ident
        return caminho if caminho.is_file() else None

    def salvar_upload(self, nome, dados):
        nome = posixpath.basename(nome.replace("\\", "/"))
        nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome).strip() or "arquivo.epub"
        if not nome.lower().endswith(".epub"):
            nome += ".epub"
        destino = self.pasta / nome
        if destino.exists() and destino.stat().st_size == len(dados):
            return nome
        contador = 1
        while destino.exists():
            destino = self.pasta / f"{Path(nome).stem} ({contador}).epub"
            contador += 1
        destino.write_bytes(dados)
        return destino.name


def _chave_natural(texto):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", texto or "")]


def _ordem_prateleira(livro):
    indice = str(livro.get("series_index") or "").strip()
    if indice.isdigit():
        return (0, int(indice), _chave_natural(livro.get("title", "")))
    return (1, 0, _chave_natural(livro.get("title", "")))


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "COFVis/1.0"

    def log_message(self, formato, *args):  # silencia o log padrão
        pass

    # -- respostas ----------------------------------------------------------

    def _json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _erro(self, status, mensagem):
        self._json({"erro": mensagem}, status)

    def _bytes(self, corpo, tipo):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    # -- rotas ---------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        rota = unquote(url.path)
        partes = [p for p in rota.split("/") if p]

        try:
            if rota == "/" or rota == "/index.html":
                if not PAGINA.exists():
                    return self._erro(500, "visualizador.html não encontrado")
                self._bytes(PAGINA.read_bytes(), "text/html; charset=utf-8")

            elif rota == "/api/books":
                self._json(self.app.livros())

            elif len(partes) == 3 and partes[:2] == ["api", "book"]:
                ident = partes[2]
                caminho = self.app.caminho_de(ident)
                if not caminho:
                    return self._erro(404, "livro não encontrado")
                epub = Epub(caminho)
                try:
                    self._json(epub.detalhe(ident))
                finally:
                    epub.fechar()

            elif len(partes) == 5 and partes[:2] == ["api", "book"] and partes[3] == "chapter":
                ident, numero = partes[2], partes[4]
                caminho = self.app.caminho_de(ident)
                if not caminho:
                    return self._erro(404, "livro não encontrado")
                epub = Epub(caminho)
                try:
                    if not numero.isdigit() or not (0 <= int(numero) < len(epub.spine)):
                        return self._erro(404, "capítulo inválido")
                    corpo = epub.capitulo(int(numero)).encode("utf-8")
                    self._bytes(corpo, "application/xhtml+xml; charset=utf-8")
                finally:
                    epub.fechar()

            elif len(partes) >= 5 and partes[:2] == ["api", "book"] and partes[3] == "res":
                ident = partes[2]
                caminho = self.app.caminho_de(ident)
                if not caminho:
                    return self._erro(404, "livro não encontrado")
                relativo = "/".join(partes[4:])
                epub = Epub(caminho)
                try:
                    corpo, tipo = epub.recurso(relativo)
                    if corpo is None:
                        return self._erro(404, "recurso não encontrado")
                    self._bytes(corpo, tipo)
                finally:
                    epub.fechar()

            else:
                self._erro(404, "rota desconhecida")

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._erro(500, str(e))

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/upload":
            return self._erro(404, "rota desconhecida")
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho <= 0 or tamanho > 300 * 1024 * 1024:
                return self._erro(400, "tamanho inválido")
            dados = self.rfile.read(tamanho)
            if not dados[:2] == b"PK":
                return self._erro(400, "o arquivo não parece ser um EPUB")
            nome = parse_qs(url.query).get("name", ["arquivo.epub"])[0]
            salvo = self.app.salvar_upload(nome, dados)
            try:
                ep = Epub(self.app.caminho_de(salvo))
                ep.fechar()
            except Exception:
                try:
                    (self.app.pasta / salvo).unlink()
                except OSError:
                    pass
                return self._erro(400, "arquivo EPUB inválido ou corrompido")
            self._json({"ok": True, "id": salvo})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._erro(500, str(e))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Visualizador local de EPUBs no navegador.")
    ap.add_argument("--dir", type=Path, default=_biblioteca_padrao(),
                    help="pasta com os arquivos .epub (padrão: .\\epubs)")
    ap.add_argument("--port", type=int, default=8017, help="porta do servidor")
    ap.add_argument("--no-browser", action="store_true", help="não abre o navegador")
    args = ap.parse_args()

    handler = type("HandlerLocal", (Handler,), {"app": Aplicacao(args.dir)})

    porta = args.port
    servidor = None
    for tentativa in range(20):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), handler)
            break
        except OSError:
            porta += 1
    if servidor is None:
        sys.exit("Não foi possível abrir uma porta para o servidor.")

    endereco = f"http://127.0.0.1:{porta}/"
    print("=" * 56)
    print("  Visualizador de EPUBs — Curso Online de Filosofia")
    print("=" * 56)
    print(f"  Biblioteca : {args.dir}")
    print(f"  Endereço   : {endereco}")
    print("  Para encerrar, feche esta janela ou pressione Ctrl+C.")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(endereco)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
