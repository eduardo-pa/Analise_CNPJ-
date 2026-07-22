"""
Ponto único de resolução da credencial do banco.

Ordem de precedência:
  1. Variável de ambiente DATABASE_URL  (desenvolvimento local, via .env)
  2. .streamlit/secrets.toml            (deploy no Streamlit Cloud)

Antes existiam duas fontes independentes — .env e secrets.toml — e trocar a
senha em uma delas deixava metade do projeto sem conectar. Agora há uma só
cadeia, e o secrets.toml é apenas o fallback de produção.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

_SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


def resolver_url() -> str:
    """Devolve a URL de conexão, ou levanta erro explicando como configurar."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    if _SECRETS.exists():
        try:
            import tomllib                     # Python 3.11+
        except ModuleNotFoundError:            # pragma: no cover
            import tomli as tomllib            # type: ignore[no-redef]
        with _SECRETS.open("rb") as fh:
            url = tomllib.load(fh).get("db_url")
        if url:
            return url

    raise RuntimeError(
        "Credencial do banco não encontrada.\n"
        "  Local : copie .env.example para .env e preencha DATABASE_URL\n"
        "  Deploy: defina db_url em .streamlit/secrets.toml"
    )


SQLALCHEMY_DATABASE_URL = resolver_url()

# client_encoding explícito: sem ele, mensagens de erro do PostgreSQL em
# português (locale pt_BR no Windows) chegam em latin-1 e o psycopg2 estoura
# com UnicodeDecodeError, escondendo o erro real.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"client_encoding": "utf8"},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Abre a sessão e garante o fechamento depois do uso."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
