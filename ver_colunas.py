from sqlalchemy import text

from database import engine   # credencial resolvida em um lugar so

with engine.connect() as conn:
    r = conn.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'empresas_gold' ORDER BY ordinal_position"
    ))
    for row in r:
        print(row)
