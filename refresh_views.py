"""
Atualiza as Materialized Views do banco tcc_cnpj.
Execute diariamente via agendador (Task Scheduler / cron).

Uso:
    python refresh_views.py
"""

import sys

from sqlalchemy import text

from database import engine

VIEWS = [
    "mv_crescimento_municipio",
    "mv_bolhas_ano_municipio",
    "mv_treemap_setores",
    "mv_comparador_uf_kpis",
    "mv_sobrevivencia_setor",
]


def refresh_views() -> bool:
    ok = True
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for view in VIEWS:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
                print(f"[OK] {view}")
            except Exception as exc:
                print(f"[ERRO] {view}: {exc}")
                ok = False
    return ok


if __name__ == "__main__":
    success = refresh_views()
    sys.exit(0 if success else 1)
