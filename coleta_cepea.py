#!/usr/bin/env python3
"""
Coleta diária dos indicadores CEPEA/ESALQ: Boi Gordo, Bezerro, Soja e Milho.

O que faz:
  - Baixa a tabela pública de cada indicador em cepea.org.br
  - Extrai as cotações diárias mostradas (normalmente as últimas ~15)
  - Acrescenta ao histórico DIÁRIO em CSV (um arquivo por commodity), sem duplicar datas
  - Extrai também o histórico MENSAL (médias dos últimos 24 meses) embutido no
    código dos gráficos de cada página — dado oficial do CEPEA, com datas exatas
    de mês/ano, sem precisar "adivinhar" nada
  - Gera dois arquivos consolidados:
      historico_consolidado.csv        (diário, as 4 séries lado a lado)
      historico_mensal_consolidado.csv (mensal, últimos ~24 meses, as 4 séries lado a lado)

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
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_cepea")
os.makedirs(OUT_DIR, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PainelAgroBot/1.0)"}

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

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


def baixar_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def baixar_tabela(html: str, indice: int) -> pd.DataFrame:
    tabelas = pd.read_html(io.StringIO(html), decimal=",", thousands=".")
    return tabelas[indice]


def normalizar(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
    df = df.rename(columns={df.columns[0]: "data", col_valor: "valor"})
    df = df[["data", "valor"]].copy()
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    df = df.dropna(subset=["data"])
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df.sort_values("data")


def extrair_serie_mensal(html: str) -> pd.DataFrame:
    """
    Extrai o histórico mensal (médias dos últimos ~24 meses) que fica embutido
    no JavaScript do gráfico popup da página do indicador principal (o primeiro
    bloco de callback = indicador oficial; blocos seguintes são séries secundárias
    como 'Média a Prazo' ou 'Peso Médio', que ignoramos).
    """
    blocos = re.findall(
        r"callbacks:\s*\{.*?open:\s*function\(\)\s*\{(.*?)\}\s*\},", html, re.DOTALL
    )
    if not blocos:
        return pd.DataFrame(columns=["data", "valor"])
    bloco = blocos[0]

    m_valores = re.search(r"valor_array\[1\]\s*=\s*\[(.*?)\];", bloco)
    m_labels = re.search(r"canvas_data\[1\]\s*=\s*\[(.*?)\];", bloco)
    if not m_valores or not m_labels:
        return pd.DataFrame(columns=["data", "valor"])

    valores = [float(v) for v in m_valores.group(1).split(",")]
    labels = [l.strip().strip("'") for l in m_labels.group(1).split(",")]

    datas, vals = [], []
    for label, valor in zip(labels, valores):
        try:
            nome_mes, ano = label.split("/")
            mes = MESES_PT[nome_mes.strip().lower()]
            ano_completo = 2000 + int(ano)
            datas.append(pd.Timestamp(year=ano_completo, month=mes, day=1))
            vals.append(valor)
        except (KeyError, ValueError):
            continue

    return pd.DataFrame({"data": datas, "valor": vals}).sort_values("data")


def atualizar_csv(chave: str, df_novo: pd.DataFrame, sufixo: str = "") -> pd.DataFrame:
    caminho = os.path.join(OUT_DIR, f"{chave}{sufixo}.csv")
    if os.path.exists(caminho):
        df_antigo = pd.read_csv(caminho, parse_dates=["data"])
        combinado = pd.concat([df_antigo, df_novo], ignore_index=True)
        combinado = combinado.drop_duplicates(subset="data", keep="last").sort_values("data")
    else:
        combinado = df_novo
    combinado.to_csv(caminho, index=False, date_format="%Y-%m-%d")
    return combinado


def gerar_consolidado(sufixo: str, nome_saida: str):
    series = {}
    for chave, (nome, _, _, _, _) in FONTES.items():
        caminho = os.path.join(OUT_DIR, f"{chave}{sufixo}.csv")
        if os.path.exists(caminho):
            df = pd.read_csv(caminho, parse_dates=["data"]).set_index("data")
            series[nome] = df["valor"]
    if not series:
        return
    consolidado = pd.DataFrame(series).sort_index()
    consolidado.to_csv(os.path.join(OUT_DIR, nome_saida), date_format="%Y-%m-%d")


def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Iniciando coleta CEPEA...")
    resumo = []
    for chave, (nome, url, indice, col_valor, unidade) in FONTES.items():
        try:
            html = baixar_html(url)

            # Diário (tabela pública, ~15 últimas cotações, datas exatas)
            bruto = baixar_tabela(html, indice)
            df_diario = normalizar(bruto, col_valor)
            combinado_diario = atualizar_csv(chave, df_diario)

            # Mensal (24 meses embutidos no gráfico, datas exatas por mês/ano)
            df_mensal = extrair_serie_mensal(html)
            if not df_mensal.empty:
                combinado_mensal = atualizar_csv(chave, df_mensal, sufixo="_mensal")
            else:
                combinado_mensal = pd.DataFrame()

            ultimo = combinado_diario.iloc[-1]
            resumo.append(
                f"  {nome:<20} {ultimo['data'].strftime('%d/%m/%Y')}  {ultimo['valor']:.2f} {unidade}  "
                f"({len(combinado_diario)} registros diários, {len(combinado_mensal)} meses)"
            )
            print(
                f"  OK  {nome}: {len(df_diario)} linhas diárias, "
                f"{len(df_mensal)} meses capturados nesta rodada"
            )
        except Exception as e:
            print(f"  ERRO ao coletar {nome}: {e}", file=sys.stderr)
        time.sleep(1)  # educado com o servidor do CEPEA

    gerar_consolidado("", "historico_consolidado.csv")
    gerar_consolidado("_mensal", "historico_mensal_consolidado.csv")

    print("\nResumo (última cotação de cada indicador):")
    for linha in resumo:
        print(linha)
    print(f"\nArquivos salvos em: {OUT_DIR}")


if __name__ == "__main__":
    main()
