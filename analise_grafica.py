"""
Gráficos de capital social por natureza jurídica.

Lê de empresas_gold — a camada final validada pelo etl_cnpj.py.
(Antes lia de empresas_amostra, tabela descontinuada cujas datas eram geradas
artificialmente e cujo universo cobria apenas parte da base.)

    python analise_grafica.py
"""

import matplotlib
matplotlib.use("Agg")          # backend sem janela: funciona em servidor/CI

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from sqlalchemy import text

from database import engine

CONSULTA = text("""
    SELECT n.descricao AS natureza,
           e.capital_social
    FROM empresas_gold e
    JOIN naturezas_referencia n
      ON e.natureza_juridica = n.codigo
    WHERE e.capital_social > 0
""")


def gerar_analises_completas():
    print("[..] Consultando empresas_gold...")
    with engine.connect() as conn:
        df = pd.read_sql(CONSULTA, conn)

    if df.empty:
        print("[ERRO] Nenhum registro retornado. A Gold foi construída?")
        return

    print(f"[OK] {len(df):,} empresas com capital social positivo")

    # --- Gráfico 1: média por natureza jurídica -----------------------------
    print("[..] grafico_media.png")
    plt.figure(figsize=(12, 7))
    top_10 = (df.groupby("natureza")["capital_social"]
                .mean().sort_values(ascending=False).head(10))
    sns.barplot(x=top_10.values, y=top_10.index,
                palette="viridis", hue=top_10.index, legend=False)
    plt.title("Top 10: Investimento Médio por Natureza Jurídica", fontsize=14)
    plt.xlabel("Média de Capital Social (R$)")
    plt.tight_layout()
    plt.savefig("grafico_media.png", dpi=150)
    plt.close()

    # --- Gráfico 2: contagem por faixa de capital ---------------------------
    print("[..] grafico_distribuicao.png")
    faixas = [0, 10_000, 100_000, 1_000_000, float("inf")]
    rotulos = ["Até 10k", "10k - 100k", "100k - 1M", "Acima de 1M"]
    df["faixa_capital"] = pd.cut(df["capital_social"], bins=faixas, labels=rotulos)

    plt.figure(figsize=(10, 6))
    sns.countplot(data=df, x="faixa_capital",
                  palette="Blues_d", hue="faixa_capital", legend=False)
    plt.title("Quantidade de Empresas por Faixa de Capital Social", fontsize=14)
    plt.ylabel("Número de Empresas")
    plt.xlabel("Faixa de Investimento")
    plt.tight_layout()
    plt.savefig("grafico_distribuicao.png", dpi=150)
    plt.close()

    print("[OK] Gráficos gerados")


if __name__ == "__main__":
    gerar_analises_completas()
