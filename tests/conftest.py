"""
Fixtures compartilhadas entre todos os módulos de teste.

IMPORTANTE sobre o engine:
  conftest.py é carregado ANTES dos módulos de teste.
  Ele importa dashboard_tcc com create_engine mockado → dashboard_tcc.engine
  aponta para o SQLite em memória.
  Nos helpers dos testes, acesse o engine via `dashboard_tcc.engine` (não via
  `from tests.conftest import _ENGINE`) para evitar uma segunda importação do
  conftest que criaria um engine separado.
"""

import os
import sys

import pytest

# Testes nao devem depender de .env nem de secrets.toml locais.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

_mock_st = MagicMock()
_mock_st.secrets = {"db_url": "sqlite:///:memory:"}
_mock_st.session_state = {"logado": False}
_mock_st.cache_data = lambda **kw: (lambda f: f)
_mock_st.button.return_value = False
_mock_st.checkbox.return_value = False
_mock_st.columns.side_effect = lambda spec: [
    MagicMock() for _ in (range(spec) if isinstance(spec, int) else spec)
]
_mock_st.tabs.side_effect = lambda names: [MagicMock() for _ in names]
sys.modules["streamlit"] = _mock_st

import sqlalchemy as _sa
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

with _ENGINE.connect() as _c:
    _c.execute(text("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            nome  TEXT    NOT NULL,
            email TEXT    UNIQUE NOT NULL,
            senha TEXT    NOT NULL,
            filtros_favoritos TEXT NOT NULL DEFAULT '[]'
        )
    """))
    _c.execute(text("""
        CREATE TABLE IF NOT EXISTS empresas_gold (
            razao_social       TEXT,
            capital_social     REAL,
            data_abertura      TEXT,
            cnae_fiscal        TEXT,
            cod_municipio      TEXT,
            uf                 TEXT,
            situacao_cadastral TEXT,
            data_situacao      TEXT
        )
    """))
    _c.execute(text("CREATE TABLE IF NOT EXISTS cnaes_referencia     (codigo TEXT, descricao TEXT)"))
    _c.execute(text("CREATE TABLE IF NOT EXISTS municipios_referencia (codigo TEXT, descricao TEXT)"))
    _c.commit()

_orig_create_engine = _sa.create_engine
_sa.create_engine = lambda *a, **kw: _ENGINE

import dashboard_tcc  # noqa: E402

_sa.create_engine = _orig_create_engine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def engine():
    """Engine de teste — o mesmo objeto que dashboard_tcc.engine usa."""
    return dashboard_tcc.engine


@pytest.fixture(scope="session")
def dashboard():
    """Módulo dashboard_tcc com engine de teste já injetado."""
    return dashboard_tcc


@pytest.fixture
def limpar_usuarios(engine):
    """Limpa a tabela de usuários antes e depois de cada teste que pede."""
    with engine.connect() as c:
        c.execute(text("DELETE FROM usuarios"))
        c.commit()
    yield
    with engine.connect() as c:
        c.execute(text("DELETE FROM usuarios"))
        c.commit()
