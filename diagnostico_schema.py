from sqlalchemy import text

from database import engine   # credencial resolvida em um lugar so

with engine.connect() as conn:

    print("=== COLUNAS municipios_referencia ===")
    r = conn.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'municipios_referencia' ORDER BY ordinal_position"
    ))
    for row in r:
        print(row)

    print("\n=== AMOSTRA municipios_referencia (3 linhas) ===")
    r = conn.execute(text("SELECT * FROM municipios_referencia LIMIT 3"))
    for row in r:
        print(row)

    print("\n=== AMOSTRA cod_municipio em empresas_gold (5 valores) ===")
    r = conn.execute(text("SELECT DISTINCT cod_municipio FROM empresas_gold LIMIT 5"))
    for row in r:
        print(row)

    print("\n=== AMOSTRA _kpis_por_uf resultado real ===")
    r = conn.execute(text("""
        SELECT
            LEFT(cod_municipio::text, 2) AS cod_uf,
            COUNT(*) AS total_empresas,
            ROUND(AVG(capital_social) FILTER (WHERE capital_social > 0)::numeric, 2) AS capital_medio
        FROM empresas_gold
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 5
    """))
    for row in r:
        print(row)
