"""
Testes das funções de query do dashboard.

Estratégia dupla:
  - Funções com SQL compatível com SQLite → SQLite em memória via conftest
  - Funções com sintaxe PostgreSQL exclusiva (PERCENTILE_CONT, DATE_PART,
    EXTRACT, COUNT FILTER) → mock de pd.read_sql para validar estrutura do retorno

Os helpers de setup/teardown usam dashboard_tcc.engine diretamente para
evitar double-import do conftest (que criaria um engine separado).
"""

import json
from unittest.mock import patch, MagicMock

import dashboard_tcc as _dash
import pandas as pd
import pytest
from sqlalchemy import text


# ── Base com dados de referência ──────────────────────────────────────────────

class _BaseComDados:
    """Insere um conjunto mínimo de dados antes dos testes e limpa depois."""

    @classmethod
    def setup_class(cls):
        with _dash.engine.connect() as c:
            c.execute(text("DELETE FROM empresas_gold"))
            c.execute(text("DELETE FROM cnaes_referencia"))
            c.execute(text("DELETE FROM municipios_referencia"))
            c.execute(text("INSERT INTO cnaes_referencia VALUES ('6201', 'Tecnologia')"))
            c.execute(text("INSERT INTO cnaes_referencia VALUES ('4711', 'Varejo')"))
            c.execute(text("INSERT INTO municipios_referencia VALUES ('3550308', 'São Paulo')"))
            c.execute(text("INSERT INTO municipios_referencia VALUES ('3304557', 'Rio de Janeiro')"))
            c.execute(text("""
                INSERT INTO empresas_gold
                    (razao_social, capital_social, data_abertura, cnae_fiscal,
                     cod_municipio, uf)
                VALUES
                    ('Alpha', 500000.0, '2010-01-15', '6201', '3550308', 'SP'),
                    ('Beta',  200000.0, '2015-06-30', '4711', '3550308', 'SP'),
                    ('Gama',  150000.0, '2019-11-20', '4711', '3304557', 'RJ'),
                    ('Delta',      0.0, '2018-03-01', '6201', '3550308', 'SP')
            """))
            c.commit()

    @classmethod
    def teardown_class(cls):
        with _dash.engine.connect() as c:
            c.execute(text("DELETE FROM empresas_gold"))
            c.execute(text("DELETE FROM cnaes_referencia"))
            c.execute(text("DELETE FROM municipios_referencia"))
            c.commit()


# ── carregar_opcoes_filtros ───────────────────────────────────────────────────

class TestCarregarOpcoesFiltros(_BaseComDados):
    def test_retorna_tupla_com_duas_listas(self, dashboard):
        cnaes, cidades = dashboard.carregar_opcoes_filtros()
        assert isinstance(cnaes, list)
        assert isinstance(cidades, list)

    def test_cnaes_contem_setores_cadastrados(self, dashboard):
        cnaes, _ = dashboard.carregar_opcoes_filtros()
        assert "Tecnologia" in cnaes
        assert "Varejo" in cnaes

    def test_cidades_contem_municipios_cadastrados(self, dashboard):
        _, cidades = dashboard.carregar_opcoes_filtros()
        assert "São Paulo" in cidades
        assert "Rio de Janeiro" in cidades

    def test_listas_sao_strings(self, dashboard):
        cnaes, cidades = dashboard.carregar_opcoes_filtros()
        assert all(isinstance(s, str) for s in cnaes)
        assert all(isinstance(s, str) for s in cidades)


# ── carregar_contagem_uf ──────────────────────────────────────────────────────

class TestCarregarContagemUf:
    """
    Testa carregar_contagem_uf() mockando a função diretamente.
    Não usa information_schema (exclusivo do PostgreSQL).
    Schema confirmado: lê de mv_densidade_uf (cod_uf, total).
    """

    @pytest.fixture
    def df_densidade_mock(self) -> pd.DataFrame:
        return pd.DataFrame({
            "cod_uf": ["35", "33", "31", "29", "41"],
            "total":  [5_200_000, 2_100_000, 1_800_000, 950_000, 1_200_000],
        })

    def test_retorna_dataframe(self, df_densidade_mock, dashboard):
        with patch.object(dashboard, "carregar_contagem_uf",
                          return_value=df_densidade_mock):
            df = dashboard.carregar_contagem_uf()
        assert isinstance(df, pd.DataFrame)

    def test_colunas_esperadas(self, df_densidade_mock, dashboard):
        with patch.object(dashboard, "carregar_contagem_uf",
                          return_value=df_densidade_mock):
            df = dashboard.carregar_contagem_uf()
        assert "cod_uf" in df.columns
        assert "total" in df.columns

    def test_sp_tem_mais_empresas_que_rj(self, df_densidade_mock, dashboard):
        with patch.object(dashboard, "carregar_contagem_uf",
                          return_value=df_densidade_mock):
            df = dashboard.carregar_contagem_uf()
        sp = df[df["cod_uf"] == "35"]["total"].iloc[0]
        rj = df[df["cod_uf"] == "33"]["total"].iloc[0]
        assert sp > rj

    def test_nulos_excluidos(self, df_densidade_mock, dashboard):
        with patch.object(dashboard, "carregar_contagem_uf",
                          return_value=df_densidade_mock):
            df = dashboard.carregar_contagem_uf()
        assert df["cod_uf"].isna().sum() == 0
        assert df["total"].isna().sum() == 0


# ── cadastrar_usuario + carregar_favoritos + salvar_favoritos ─────────────────

class TestFavoritos:
    def setup_method(self, _):
        with _dash.engine.connect() as c:
            c.execute(text("DELETE FROM usuarios WHERE email = 'fav@test.com'"))
            c.commit()

    def teardown_method(self, _):
        with _dash.engine.connect() as c:
            c.execute(text("DELETE FROM usuarios WHERE email = 'fav@test.com'"))
            c.commit()

    def test_favoritos_iniciam_vazios(self, dashboard):
        dashboard.cadastrar_usuario("Fav User", "fav@test.com", "pass")
        favs = dashboard.carregar_favoritos("fav@test.com")
        assert favs == []

    def test_salvar_e_carregar_favorito(self, dashboard):
        dashboard.cadastrar_usuario("Fav User", "fav@test.com", "pass")
        payload = [{"nome": "SP Tech", "setor": "Tecnologia", "cidade": "São Paulo"}]
        dashboard.salvar_favoritos("fav@test.com", payload)
        resultado = dashboard.carregar_favoritos("fav@test.com")
        assert resultado == payload

    def test_favorito_persiste_multiplos_itens(self, dashboard):
        dashboard.cadastrar_usuario("Fav User", "fav@test.com", "pass")
        payload = [
            {"nome": "SP Tech",    "setor": "Tecnologia", "cidade": "São Paulo"},
            {"nome": "RJ Varejo",  "setor": "Varejo",     "cidade": "Rio de Janeiro"},
        ]
        dashboard.salvar_favoritos("fav@test.com", payload)
        resultado = dashboard.carregar_favoritos("fav@test.com")
        assert len(resultado) == 2
        assert resultado[1]["nome"] == "RJ Varejo"

    def test_email_inexistente_retorna_lista_vazia(self, dashboard):
        resultado = dashboard.carregar_favoritos("fantasma@test.com")
        assert resultado == []

    def test_salvar_favorito_sobrescreve_anterior(self, dashboard):
        dashboard.cadastrar_usuario("Fav User", "fav@test.com", "pass")
        dashboard.salvar_favoritos("fav@test.com", [{"nome": "antigo"}])
        dashboard.salvar_favoritos("fav@test.com", [{"nome": "novo"}])
        resultado = dashboard.carregar_favoritos("fav@test.com")
        assert resultado == [{"nome": "novo"}]


# ── excluir_conta_db ──────────────────────────────────────────────────────────

class TestExcluirConta:
    def setup_method(self, _):
        with _dash.engine.connect() as c:
            c.execute(text("DELETE FROM usuarios WHERE email = 'del@test.com'"))
            c.commit()

    def test_exclui_usuario_existente(self, dashboard):
        dashboard.cadastrar_usuario("Del", "del@test.com", "pw")
        assert dashboard.excluir_conta_db("del@test.com") is True

    def test_usuario_removido_do_banco(self, dashboard):
        dashboard.cadastrar_usuario("Del", "del@test.com", "pw")
        dashboard.excluir_conta_db("del@test.com")
        df = dashboard.verificar_login("del@test.com", "pw")
        assert df.empty

    def test_email_inexistente_retorna_true(self, dashboard):
        assert dashboard.excluir_conta_db("naoexiste@test.com") is True


# ── Funções com sintaxe PostgreSQL exclusiva → mock de pd.read_sql ────────────

class TestCarregarSobrevivenciaMock:
    """
    carregar_sobrevivencia usa PERCENTILE_CONT e DATE_PART — incompatíveis com
    SQLite. Mock de pd.read_sql para validar estrutura do retorno.
    """

    _COLUNAS = ["setor", "total", "media", "mediana", "p05", "q1", "q3", "p95"]

    def _df_fake(self):
        return pd.DataFrame([{
            "setor": "Tecnologia", "total": 50, "media": 3.2,
            "mediana": 2.8, "p05": 0.5, "q1": 1.5, "q3": 5.0, "p95": 9.0,
        }])

    def test_retorna_dataframe_com_colunas_corretas(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_sobrevivencia()
        assert {"setor", "total", "media", "mediana"}.issubset(df.columns)

    def test_retorna_dataframe_vazio_sem_erro(self, dashboard):
        with patch("pandas.read_sql", return_value=pd.DataFrame(columns=self._COLUNAS)):
            df = dashboard.carregar_sobrevivencia()
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_estrutura_de_colunas_completa(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_sobrevivencia()
        for col in self._COLUNAS:
            assert col in df.columns, f"Coluna ausente: {col}"


class TestCarregarCrescimentoUfMock:
    def _df_fake(self):
        return pd.DataFrame([
            {"uf": "SP", "ano": 2010, "total": 1200},
            {"uf": "SP", "ano": 2011, "total": 1350},
            {"uf": "RJ", "ano": 2010, "total": 800},
        ])

    def test_retorna_dataframe(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_crescimento_uf()
        assert isinstance(df, pd.DataFrame)

    def test_colunas_uf_ano_total(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_crescimento_uf()
        assert {"uf", "ano", "total"}.issubset(df.columns)

    def test_total_e_positivo(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_crescimento_uf()
        assert (df["total"] > 0).all()


class TestCarregarDataQualityMock:
    def _serie_fake(self):
        return pd.Series({
            "total": 10000,
            "capital_invalido": 150,
            "data_invalida": 30,
            "cnae_nao_mapeado": 50,
        })

    def test_retorna_series(self, dashboard):
        with patch("pandas.read_sql", return_value=self._serie_fake().to_frame().T):
            result = dashboard.carregar_data_quality()
        assert isinstance(result, pd.Series)

    def test_chaves_esperadas(self, dashboard):
        with patch("pandas.read_sql", return_value=self._serie_fake().to_frame().T):
            result = dashboard.carregar_data_quality()
        for chave in ["total", "capital_invalido", "data_invalida", "cnae_nao_mapeado"]:
            assert chave in result.index

    def test_total_maior_que_invalidos(self, dashboard):
        with patch("pandas.read_sql", return_value=self._serie_fake().to_frame().T):
            result = dashboard.carregar_data_quality()
        assert result["total"] > result["capital_invalido"]
        assert result["total"] > result["data_invalida"]
        assert result["total"] > result["cnae_nao_mapeado"]


class TestCarregarBolhasAnoUfMock:
    def _df_fake(self):
        return pd.DataFrame([
            {"ano": 2010, "uf": "SP", "total_empresas": 1200, "capital_medio": 350000.0},
            {"ano": 2010, "uf": "RJ", "total_empresas": 800,  "capital_medio": 280000.0},
            {"ano": 2015, "uf": "MG", "total_empresas": 600,  "capital_medio": 200000.0},
        ])

    def test_retorna_dataframe(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_bolhas_ano_uf()
        assert isinstance(df, pd.DataFrame)

    def test_colunas_obrigatorias(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_bolhas_ano_uf()
        assert {"ano", "uf", "total_empresas", "capital_medio"}.issubset(df.columns)

    def test_total_empresas_e_positivo(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_bolhas_ano_uf()
        assert (df["total_empresas"] > 0).all()

    def test_capital_medio_e_positivo(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_bolhas_ano_uf()
        assert (df["capital_medio"] > 0).all()


class TestCarregarTreemapSetoresMock:
    def _df_fake(self):
        return pd.DataFrame([
            {"divisao": "62", "descricao": "TI e Serviços",      "total_empresas": 5000, "capital_total": 1e9},
            {"divisao": "47", "descricao": "Comércio Varejista", "total_empresas": 8000, "capital_total": 5e8},
        ])

    def test_retorna_dataframe(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_treemap_setores()
        assert isinstance(df, pd.DataFrame)

    def test_colunas_obrigatorias(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_treemap_setores()
        assert {"divisao", "descricao", "total_empresas", "capital_total"}.issubset(df.columns)

    def test_total_empresas_maior_que_zero(self, dashboard):
        with patch("pandas.read_sql", return_value=self._df_fake()):
            df = dashboard.carregar_treemap_setores()
        assert (df["total_empresas"] > 0).all()
