"""
Script único para criar a MV do Comparador Regional.
Lê as credenciais do mesmo secrets.toml usado pelo dashboard.
Rodar: python criar_mv_comparador.py
"""
from sqlalchemy import text

from database import engine   # credencial resolvida em um lugar so


sql = text("""
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_comparador_uf_kpis AS
SELECT
    LEFT(cod_municipio::text, 2) AS cod_uf,
    COUNT(*) AS total_empresas,
    ROUND(AVG(capital_social) FILTER (WHERE capital_social > 0)::numeric, 2) AS capital_medio
FROM empresas_gold
GROUP BY 1;
""")

idx = text("""
CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_comparador_uf_kpis_cod_uf
    ON mv_comparador_uf_kpis (cod_uf);
""")

with engine.connect() as conn:
    conn.execute(sql)
    conn.execute(idx)
    conn.commit()
    print("✅ mv_comparador_uf_kpis criada com sucesso!")
