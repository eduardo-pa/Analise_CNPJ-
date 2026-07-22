"""
Testes para dashboard_tcc.py.
Usa SQLite em memória — não requer PostgreSQL local.

Ordem de inicialização (crítica):
  1. Cria o engine SQLite e o schema antes de qualquer import do dashboard
  2. Monta o mock do Streamlit no sys.modules
  3. Substitui sqlalchemy.create_engine pelo lambda que devolve o engine de teste
  4. Importa dashboard_tcc  →  engine = create_engine(...) recebe nosso engine
  5. Restaura create_engine original
"""

import os
import sys

# Garante que o diretório raiz do projeto está no PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

import bcrypt
import pandas as pd
import pytest
import sqlalchemy as _sa
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

# ── 1. Engine SQLite compartilhado por todos os testes ───────────────────────
# StaticPool garante que todas as conexões reusam o mesmo banco em memória.
_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

with _ENGINE.connect() as _c:
    # Tabela de usuários com sintaxe SQLite (sem SERIAL do PostgreSQL).
    # Precisa existir ANTES de dashboard_tcc ser importado, para que o
    # init_db() do módulo (CREATE TABLE IF NOT EXISTS) seja silencioso.
    _c.execute(text("""
        CREATE TABLE usuarios (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            nome  TEXT    NOT NULL,
            email TEXT    UNIQUE NOT NULL,
            senha TEXT    NOT NULL
        )
    """))
    _c.execute(text("""
        CREATE TABLE empresas_gold (
            razao_social   TEXT,
            capital_social REAL,
            data_abertura  TEXT,
            cnae_fiscal    TEXT,
            cod_municipio  TEXT
        )
    """))
    _c.execute(text("CREATE TABLE cnaes_referencia    (codigo TEXT, descricao TEXT)"))
    _c.execute(text("CREATE TABLE municipios_referencia (codigo TEXT, descricao TEXT)"))
    _c.commit()

# ── 2. Mock do Streamlit ─────────────────────────────────────────────────────
_mock_st = MagicMock()
_mock_st.secrets = {"db_url": "sqlite:///:memory:"}
_mock_st.session_state = {"logado": False}
_mock_st.cache_data = lambda **kw: (lambda f: f)
# Botões retornam False para que nenhum handler execute durante o import
_mock_st.button.return_value = False
_mock_st.checkbox.return_value = False
# columns/tabs precisam desempacotar o número certo de elementos
_mock_st.columns.side_effect = lambda spec: [
    MagicMock() for _ in (range(spec) if isinstance(spec, int) else spec)
]
_mock_st.tabs.side_effect = lambda names: [MagicMock() for _ in names]
sys.modules["streamlit"] = _mock_st

# ── 3. Injeta o engine de teste antes do import do dashboard ─────────────────
_orig_create_engine = _sa.create_engine
_sa.create_engine = lambda *a, **kw: _ENGINE

import dashboard_tcc  # noqa: E402 — import tardio intencional

_sa.create_engine = _orig_create_engine  # restaura o original

from dashboard_tcc import (  # noqa: E402
    cadastrar_usuario,
    excluir_conta_db,
    verificar_login,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _insert_user(nome: str, email: str, pwd: str) -> None:
    h = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
    with dashboard_tcc.engine.connect() as c:
        c.execute(
            text("INSERT INTO usuarios (nome, email, senha) VALUES (:n, :e, :s)"),
            {"n": nome, "e": email, "s": h},
        )
        c.commit()


def _delete_user(email: str) -> None:
    with dashboard_tcc.engine.connect() as c:
        c.execute(text("DELETE FROM usuarios WHERE email = :e"), {"e": email})
        c.commit()


# ─── verificar_login ──────────────────────────────────────────────────────────

class TestVerificarLogin:
    def setup_method(self):
        _insert_user("Ana", "ana@test.com", "senha123")

    def teardown_method(self):
        _delete_user("ana@test.com")

    def test_credenciais_corretas_retorna_usuario(self):
        df = verificar_login("ana@test.com", "senha123")
        assert not df.empty
        assert df.iloc[0]["nome"] == "Ana"

    def test_senha_errada_retorna_dataframe_vazio(self):
        df = verificar_login("ana@test.com", "errada")
        assert df.empty

    def test_email_inexistente_retorna_dataframe_vazio(self):
        df = verificar_login("ghost@test.com", "senha123")
        assert df.empty


# ─── cadastrar_usuario ────────────────────────────────────────────────────────

class TestCadastrarUsuario:
    def teardown_method(self):
        _delete_user("novo@test.com")

    def test_cadastro_bem_sucedido_retorna_true(self):
        assert cadastrar_usuario("Novo", "novo@test.com", "abc456") is True

    def test_senha_armazenada_como_bcrypt(self):
        cadastrar_usuario("Novo", "novo@test.com", "abc456")
        with dashboard_tcc.engine.connect() as c:
            row = c.execute(
                text("SELECT senha FROM usuarios WHERE email = :e"),
                {"e": "novo@test.com"},
            ).fetchone()
        assert row is not None
        assert row[0] != "abc456", "Senha não pode ser texto puro"
        assert bcrypt.checkpw(b"abc456", row[0].encode())

    def test_email_duplicado_retorna_false(self):
        cadastrar_usuario("Novo", "novo@test.com", "abc456")
        assert cadastrar_usuario("Cópia", "novo@test.com", "xyz789") is False


# ─── excluir_conta_db ─────────────────────────────────────────────────────────

class TestExcluirContaDb:
    def setup_method(self):
        _delete_user("excluir@test.com")  # slate limpo antes de cada teste
        _insert_user("Excluível", "excluir@test.com", "pass1")

    def teardown_method(self):
        _delete_user("excluir@test.com")

    def test_exclui_usuario_existente_retorna_true(self):
        assert excluir_conta_db("excluir@test.com") is True

    def test_usuario_removido_do_banco(self):
        excluir_conta_db("excluir@test.com")
        df = verificar_login("excluir@test.com", "pass1")
        assert df.empty

    def test_email_inexistente_retorna_true(self):
        # DELETE sem linhas afetadas não levanta exceção → deve retornar True
        assert excluir_conta_db("fantasma@test.com") is True


# ─── Query principal do dashboard ─────────────────────────────────────────────

class TestQueryPrincipal:
    """
    Replica a query inline do dashboard para validar filtros e SQL injection.
    A agregação fica no banco; o Pandas recebe o resultado já filtrado.
    """

    @classmethod
    def setup_class(cls):
        with _ENGINE.connect() as c:
            c.execute(text("INSERT INTO cnaes_referencia    VALUES ('6201', 'Tecnologia')"))
            c.execute(text("INSERT INTO cnaes_referencia    VALUES ('4711', 'Varejo')"))
            c.execute(text("INSERT INTO municipios_referencia VALUES ('3550308', 'São Paulo')"))
            c.execute(text("INSERT INTO municipios_referencia VALUES ('3304557', 'Rio de Janeiro')"))
            c.execute(text("""
                INSERT INTO empresas_gold VALUES
                    ('Alpha', 500000.0, '2010-01-15', '6201', '3550308'),
                    ('Beta',  200000.0, '2015-06-30', '4711', '3550308'),
                    ('Gama',  150000.0, '2019-11-20', '4711', '3304557'),
                    ('Delta',      0.0, '2018-03-01', '6201', '3550308')
            """))
            c.commit()

    @classmethod
    def teardown_class(cls):
        with _ENGINE.connect() as c:
            c.execute(text("DELETE FROM empresas_gold"))
            c.execute(text("DELETE FROM cnaes_referencia"))
            c.execute(text("DELETE FROM municipios_referencia"))
            c.commit()

    def _run(self, setor_sel: str = "Todos", cidade_sel: str = "Todas") -> pd.DataFrame:
        sql = """
            SELECT e.razao_social, e.capital_social, e.data_abertura,
                   c.descricao AS setor, m.descricao AS cidade
            FROM empresas_gold e
            LEFT JOIN cnaes_referencia     c ON e.cnae_fiscal   = c.codigo
            LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
            WHERE e.capital_social > 0
        """
        params: dict = {}
        if setor_sel != "Todos":
            sql += " AND c.descricao = :setor"
            params["setor"] = setor_sel
        if cidade_sel != "Todas":
            sql += " AND m.descricao = :cidade"
            params["cidade"] = cidade_sel
        sql += " LIMIT 30000"
        with _ENGINE.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def test_exclui_empresas_com_capital_zero(self):
        df = self._run()
        assert len(df) == 3                        # Delta (capital=0) excluída
        assert (df["capital_social"] > 0).all()

    def test_colunas_retornadas(self):
        df = self._run()
        assert set(df.columns) == {
            "razao_social", "capital_social", "data_abertura", "setor", "cidade"
        }

    def test_filtro_setor(self):
        df = self._run(setor_sel="Tecnologia")
        assert len(df) == 1
        assert df.iloc[0]["razao_social"] == "Alpha"

    def test_filtro_cidade(self):
        df = self._run(cidade_sel="Rio de Janeiro")
        assert len(df) == 1
        assert df.iloc[0]["razao_social"] == "Gama"

    def test_filtro_setor_e_cidade_combinados(self):
        df = self._run(setor_sel="Varejo", cidade_sel="São Paulo")
        assert len(df) == 1
        assert df.iloc[0]["razao_social"] == "Beta"

    def test_filtro_sem_resultado_retorna_vazio(self):
        df = self._run(setor_sel="Tecnologia", cidade_sel="Rio de Janeiro")
        assert df.empty

    def test_sql_injection_nao_vaza_dados(self):
        # Payload clássico: sem parametrização retornaria todas as linhas.
        # Com :setor parametrizado, busca a string literal → resultado vazio.
        df = self._run(setor_sel="' OR '1'='1")
        assert df.empty
