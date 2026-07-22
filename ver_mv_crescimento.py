from sqlalchemy import text

from database import engine   # credencial resolvida em um lugar so

with engine.connect() as conn:
    # Colunas da MV
    r = conn.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'mv_crescimento_municipio' ORDER BY ordinal_position"
    ))
    print("=== COLUNAS mv_crescimento_municipio ===")
    for row in r:
        print(row)

    # Amostra de dados
    r2 = conn.execute(text(
        "SELECT * FROM mv_crescimento_municipio LIMIT 5"
    ))
    print("\n=== AMOSTRA (5 linhas) ===")
    for row in r2:
        print(row)
