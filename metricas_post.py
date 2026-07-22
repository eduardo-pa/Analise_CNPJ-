"""
Recalcula as métricas divulgadas, a partir da empresas_gold reconstruída.

Substitui os números apurados sobre a Gold antiga, que continha apenas 16% da
base por um erro de shard no ETL. Rodar depois de qualquer recarga:

    python metricas_post.py
"""

import os

from dotenv import load_dotenv
import psycopg2

load_dotenv()

# Códigos de situação cadastral da Receita Federal
SITUACAO = {1: "Nula", 2: "Ativa", 3: "Suspensa", 4: "Inapta", 8: "Baixada"}

CONSULTAS = [
    ("Universo", """
        SELECT count(*) AS empresas,
               min(data_abertura) AS mais_antiga,
               max(data_abertura) AS mais_recente
        FROM empresas_gold
    """),

    # Só empresas ATIVAS. Medir "idade" de empresa baixada não faz sentido —
    # o relógio dela parou na baixa. É também o recorte que o IBGE publica
    # (11,6 anos), o que torna a comparação legítima.
    ("Idade das empresas ATIVAS (anos)", """
        WITH idades AS (
            SELECT (CURRENT_DATE - data_abertura) / 365.25 AS anos
            FROM empresas_gold
            WHERE situacao_cadastral = 2
        )
        SELECT round(avg(anos)::numeric, 1)                                    AS media,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS mediana,
               round(percentile_cont(0.9) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS p90,
               round(max(anos)::numeric, 1)                                    AS maxima
        FROM idades
    """),

    ("Idade de TODAS as empresas, ativas ou não (anos)", """
        WITH idades AS (
            SELECT (CURRENT_DATE - data_abertura) / 365.25 AS anos
            FROM empresas_gold
        )
        SELECT round(avg(anos)::numeric, 1)                                    AS media,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS mediana,
               round(percentile_cont(0.9) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS p90,
               round(max(anos)::numeric, 1)                                    AS maxima
        FROM idades
    """),

    ("Capital social zero", """
        SELECT count(*) FILTER (WHERE capital_social = 0)                AS empresas,
               round(100.0 * count(*) FILTER (WHERE capital_social = 0)
                     / count(*), 1)                                      AS pct
        FROM empresas_gold
    """),

    ("Varejo — CNAE 47", """
        SELECT count(*) AS empresas,
               round(100.0 * count(*) / (SELECT count(*) FROM empresas_gold), 1) AS pct
        FROM empresas_gold
        WHERE left(cnae_fiscal::text, 2) = '47'
    """),

    ("Situação cadastral", """
        SELECT situacao_cadastral,
               count(*) AS empresas,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM empresas_gold
        GROUP BY 1 ORDER BY 2 DESC
    """),

    # Só possível agora: data_situacao entrou na Gold nesta reconstrução.
    # date - date devolve INTEGER (dias), não interval — por isso a divisão
    # direta por 365.25 em vez de EXTRACT(EPOCH ...).
    ("Sobrevivencia das empresas baixadas (anos ativos)", """
        WITH duracao AS (
            SELECT (data_situacao - data_abertura) / 365.25 AS anos
            FROM empresas_gold
            WHERE situacao_cadastral = 8
              AND data_situacao IS NOT NULL
              AND data_situacao >= data_abertura
        )
        SELECT count(*)                                                        AS baixadas,
               round(avg(anos)::numeric, 1)                                    AS media,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS mediana,
               round(percentile_cont(0.9) WITHIN GROUP (ORDER BY anos)::numeric, 1) AS p90
        FROM duracao
    """),

    ("Baixadas descartadas do calculo (data ausente ou anterior a abertura)", """
        SELECT count(*) FILTER (WHERE data_situacao IS NULL)             AS sem_data,
               count(*) FILTER (WHERE data_situacao < data_abertura)     AS data_invertida,
               count(*)                                                  AS total_baixadas
        FROM empresas_gold
        WHERE situacao_cadastral = 8
    """),

    ("São Paulo — aberturas por ano (2015+)", """
        SELECT EXTRACT(YEAR FROM data_abertura)::int AS ano, count(*) AS empresas
        FROM empresas_gold
        WHERE uf = 'SP' AND data_abertura >= DATE '2015-01-01'
        GROUP BY 1 ORDER BY 1
    """),

    ("Distribuição por década", """
        SELECT (EXTRACT(YEAR FROM data_abertura)::int / 10) * 10 AS decada,
               count(*) AS empresas,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM empresas_gold
        GROUP BY 1 ORDER BY 1
    """),
]


def main():
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            for titulo, sql in CONSULTAS:
                print(f"\n{'=' * 70}\n{titulo}\n{'=' * 70}")
                cur.execute(sql)
                colunas = [d[0] for d in cur.description]
                linhas = cur.fetchall()

                print(" | ".join(f"{c:>18}" for c in colunas))
                for linha in linhas:
                    celulas = []
                    for valor in linha:
                        if isinstance(valor, int) and not isinstance(valor, bool):
                            celulas.append(f"{valor:>18,}")
                        else:
                            celulas.append(f"{str(valor):>18}")
                    print(" | ".join(celulas))

                if titulo == "Situação cadastral":
                    print("\n  Legenda:", ", ".join(
                        f"{k}={v}" for k, v in SITUACAO.items()))


if __name__ == "__main__":
    main()
