#!/usr/bin/env python3
"""
Coleta diária dos indicadores CEPEA/ESALQ: Boi Gordo, Bezerro, Soja e Milho.

O que faz:
  - Baixa a tabela pública de cada indicador em cepea.org.br
  - Extrai as cotações diárias mostradas (normalmente as últimas ~15)
  - Acrescenta ao histórico em CSV (um arquivo por commodity), sem duplicar datas
  - Gera um arquivo consolidado 'historico_consolidado.csv' com as 4 séries lado a lado

Como usar:
  python3 coleta_cepea.py

Para automatizar (Linux/Mac, roda todo dia às 19h, depois do fechamento do indicador):
  crontab -e
  0 19 * * 1-5 /usr/bin/python3 /caminho/completo/coleta_cepea.py >> /caminho/completo/coleta.log 2>&1

Para automatizar sem precisar de computador ligado, use GitHub Actions
(arquivo .github/workflows/coleta.yml agendado com cron) — posso gerar esse
workflow também, é a opção mais simples de "set and forget".
"""

import csv
import io
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_cepea")
os.makedirs(OUT_DIR, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PainelAgroBot/1.0)"}

# key -> (nome, url, indice_da_tabela, coluna_de_valor, unidade)
FONTES = {
    "boi_gordo": (
        "Boi Gordo (SP)",
        "https://cepea.org.br/br/indicador/boi-gordo.aspx",
        0,
        "Valor R$*",
        "R$/@",
    ),
    "bezerro": (
        "Bezerro (MS)",
        "https://cepea.org.br/br/indicador/bezerro.aspx",
        0,
        "Valor R$*",
        "R$/cabeça",
    ),
    "soja": (
        "Soja (Paranaguá)",
        "https://cepea.org.br/br/indicador/soja.aspx",
        0,
        "Valor R$*",
        "R$/saca 60kg",
    ),
    "milho": (
        "Milho (ESALQ/B3)",
        "https://cepea.org.br/br/indicador/milho.aspx",
        0,
        "Valor R$*",
        "R$/saca 60kg",
    ),
}


def baixar_tabela(url: str, indice: int) -> pd.DataFrame:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    tabelas = pd.read_html(io.StringIO(resp.text), decimal=",", thousands=".")
    return tabelas[indice]


def normalizar(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
    df = df.rename(columns={df.columns[0]: "data", col_valor: "valor"})
    df = df[["data", "valor"]].copy()
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    df = df.dropna(subset=["data"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df.sort_values("data")


def atualizar_csv(chave: str, df_novo: pd.DataFrame) -> pd.DataFrame:
    caminho = os.path.join(OUT_DIR, f"{chave}.csv")
    if os.path.exists(caminho):
        df_antigo = pd.read_csv(caminho, parse_dates=["data"])
        combinado = pd.concat([df_antigo, df_novo], ignore_index=True)
        combinado = combinado.drop_duplicates(subset="data").sort_values("data")
    else:
        combinado = df_novo
    combinado.to_csv(caminho, index=False, date_format="%Y-%m-%d")
    return combinado


def gerar_consolidado():
    series = {}
    for chave, (nome, _, _, _, _) in FONTES.items():
        caminho = os.path.join(OUT_DIR, f"{chave}.csv")
        if os.path.exists(caminho):
            df = pd.read_csv(caminho, parse_dates=["data"]).set_index("data")
            series[nome] = df["valor"]
    if not series:
        return
    consolidado = pd.DataFrame(series).sort_index()
    consolidado.to_csv(os.path.join(OUT_DIR, "historico_consolidado.csv"))


def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Iniciando coleta CEPEA...")
    resumo = []
    for chave, (nome, url, indice, col_valor, unidade) in FONTES.items():
        try:
            bruto = baixar_tabela(url, indice)
            df = normalizar(bruto, col_valor)
            combinado = atualizar_csv(chave, df)
            ultimo = combinado.iloc[-1]
            resumo.append(
                f"  {nome:<20} {ultimo['data'].strftime('%d/%m/%Y')}  {ultimo['valor']:.2f} {unidade}  "
                f"({len(combinado)} registros no histórico)"
            )
            print(f"  OK  {nome}: {len(df)} linhas capturadas nesta rodada")
        except Exception as e:
            print(f"  ERRO ao coletar {nome}: {e}", file=sys.stderr)
        time.sleep(1)  # educado com o servidor do CEPEA

    gerar_consolidado()

    print("\nResumo (última cotação de cada indicador):")
    for linha in resumo:
        print(linha)
    print(f"\nArquivos salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
