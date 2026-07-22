"""
Testes de segurança: gerar_hash_senha e verificar_login.

Usa o engine SQLite em memória configurado pelo conftest — sem PostgreSQL.
Os helpers de setup/teardown acessam o engine via dashboard_tcc.engine para
garantir que é o mesmo engine que as funções testadas utilizam.
"""

import bcrypt
import pytest
from sqlalchemy import text

import dashboard_tcc as _dash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _criar_usuario(nome: str, email: str, senha: str) -> None:
    h = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    with _dash.engine.connect() as c:
        c.execute(
            text("INSERT INTO usuarios (nome, email, senha) VALUES (:n, :e, :s)"),
            {"n": nome, "e": email, "s": h},
        )
        c.commit()


def _remover_usuario(email: str) -> None:
    with _dash.engine.connect() as c:
        c.execute(text("DELETE FROM usuarios WHERE email = :e"), {"e": email})
        c.commit()


# ── gerar_hash_senha ──────────────────────────────────────────────────────────

class TestGerarHashSenha:
    def test_retorna_string(self, dashboard):
        resultado = dashboard.gerar_hash_senha("minha_senha")
        assert isinstance(resultado, str)

    def test_hash_nao_e_texto_puro(self, dashboard):
        resultado = dashboard.gerar_hash_senha("minha_senha")
        assert resultado != "minha_senha"

    def test_hash_valido_verificavel_com_bcrypt(self, dashboard):
        senha = "test@123"
        h = dashboard.gerar_hash_senha(senha)
        assert bcrypt.checkpw(senha.encode(), h.encode())

    def test_mesma_senha_gera_hashes_diferentes(self, dashboard):
        """bcrypt usa salt aleatório — dois hashes da mesma senha devem diferir."""
        h1 = dashboard.gerar_hash_senha("senha")
        h2 = dashboard.gerar_hash_senha("senha")
        assert h1 != h2

    def test_hash_comeca_com_prefixo_bcrypt(self, dashboard):
        h = dashboard.gerar_hash_senha("qualquer")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_senha_vazia_nao_levanta_excecao(self, dashboard):
        h = dashboard.gerar_hash_senha("")
        assert bcrypt.checkpw(b"", h.encode())

    def test_senha_unicode_e_hasheada_corretamente(self, dashboard):
        senha = "日本語パスワード"
        h = dashboard.gerar_hash_senha(senha)
        assert bcrypt.checkpw(senha.encode("utf-8"), h.encode())


# ── verificar_login ───────────────────────────────────────────────────────────

class TestVerificarLogin:
    def setup_method(self, _):
        _remover_usuario("maria@test.com")
        _criar_usuario("Maria", "maria@test.com", "senha_certa")

    def teardown_method(self, _):
        _remover_usuario("maria@test.com")

    def test_credenciais_corretas_retorna_dataframe_nao_vazio(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "senha_certa")
        assert not df.empty

    def test_nome_retornado_e_correto(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "senha_certa")
        assert df.iloc[0]["nome"] == "Maria"

    def test_email_retornado_e_correto(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "senha_certa")
        assert df.iloc[0]["email"] == "maria@test.com"

    def test_senha_errada_retorna_dataframe_vazio(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "senha_errada")
        assert df.empty

    def test_email_inexistente_retorna_dataframe_vazio(self, dashboard):
        df = dashboard.verificar_login("ninguem@test.com", "senha_certa")
        assert df.empty

    def test_email_case_sensitive(self, dashboard):
        """Email em caixa diferente não deve autenticar."""
        df = dashboard.verificar_login("MARIA@TEST.COM", "senha_certa")
        assert df.empty

    def test_senha_em_branco_nao_autentica(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "")
        assert df.empty

    def test_retorna_dataframe_com_coluna_nome(self, dashboard):
        df = dashboard.verificar_login("maria@test.com", "senha_certa")
        assert "nome" in df.columns

    def test_senha_nao_exposta_como_texto_puro(self, dashboard):
        """Se a coluna senha vazar no DataFrame, deve ser hash, nunca texto puro."""
        df = dashboard.verificar_login("maria@test.com", "senha_certa")
        if "senha" in df.columns:
            assert df.iloc[0]["senha"] != "senha_certa"
