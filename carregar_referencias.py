"""
Carrega as tabelas de referência da Receita Federal (CNAEs, Municípios,
Naturezas Jurídicas).

Credenciais e caminho dos dados vêm do ambiente — veja .env.example.

    python carregar_referencias.py
"""

import os
import sys
import zipfile

import pandas as pd
from dotenv import load_dotenv

from database import engine

load_dotenv()

REFERENCIAS = {
    "Cnaes": "cnaes_referencia",
    "Municipios": "municipios_referencia",
    "Naturezas": "naturezas_referencia",
}


def carregar_referencias():
    caminho_pasta = os.environ.get("CNPJ_DIR")
    if not caminho_pasta:
        sys.exit("CNPJ_DIR não definida. Veja .env.example")

    for arquivo, tabela in REFERENCIAS.items():
        zip_path = os.path.join(caminho_pasta, f"{arquivo}.zip")
        if not os.path.exists(zip_path):
            print(f"[AVISO] {arquivo}.zip não encontrado em {caminho_pasta}")
            continue

        print(f"[..] Carregando {arquivo}...")
        with zipfile.ZipFile(zip_path) as z:
            with z.open(z.namelist()[0]) as f:
                # Arquivos de referência são pequenos: cabem em memória.
                df = pd.read_csv(f, sep=";", encoding="latin-1",
                                 header=None, dtype=str)
                df.columns = ["codigo", "descricao"]
                df["codigo"] = pd.to_numeric(df["codigo"], errors="coerce")
                df["descricao"] = df["descricao"].astype(str).str.strip()

                df.to_sql(tabela, engine, if_exists="replace", index=False)
                print(f"[OK] {tabela}: {len(df):,} registros")


if __name__ == "__main__":
    carregar_referencias()
