from sqlalchemy import text

from database import engine   # credencial resolvida em um lugar so

with engine.connect() as conn:
    print("=== mv_densidade_uf ===")
    try:
        r = conn.execute(text("SELECT * FROM mv_densidade_uf LIMIT 5"))
        for row in r: print(row)
    except Exception as e:
        print(f"ERRO: {e}")

    print("\n=== mv_crescimento_municipio colunas ===")
    r = conn.execute(text(
        "SELECT attname FROM pg_attribute "
        "WHERE attrelid = 'mv_crescimento_municipio'::regclass "
        "  AND attnum > 0 AND NOT attisdropped ORDER BY attnum"
    ))
    for row in r: print(row)
