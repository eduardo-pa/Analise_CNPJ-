-- =============================================================================
-- ARQUIVO: mv_comparador_uf_kpis.sql
-- Opcional — cria MV para acelerar o painel de KPIs do Comparador Regional
-- Executar UMA VEZ no psql: \i mv_comparador_uf_kpis.sql
-- =============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_comparador_uf_kpis AS
SELECT
    LEFT(cod_municipio::text, 2)                               AS cod_uf,
    COUNT(*)                                                   AS total_empresas,
    ROUND(
        AVG(capital_social) FILTER (WHERE capital_social > 0)::numeric,
        2
    )                                                          AS capital_medio,
    ROUND(
        COUNT(*) FILTER (WHERE situacao_cadastral = '2')
        * 100.0 / NULLIF(COUNT(*), 0),
        1
    )                                                          AS pct_ativas
FROM empresas_gold
GROUP BY 1;

-- Índice para busca direta por UF
CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_comparador_uf_kpis_cod_uf
    ON mv_comparador_uf_kpis (cod_uf);

-- =============================================================================
-- INTEGRAÇÃO EM dashboard_tcc.py
-- =============================================================================
-- Adicionar o import no topo do arquivo:
--
--   from comparador_regional import render_comparador
--
-- Adicionar "Comparador Regional" à lista de páginas da sidebar:
--
--   PAGINAS = {
--       "🏠 Início":            render_inicio,
--       "🗺️ Comparador Regional": render_comparador,   # ← ADD
--       "📊 Dashboard":         render_dashboard,
--       "👤 Perfil":            render_perfil,
--   }
--
-- O roteador existente já cuida do resto (padrão if/elif por página selecionada).
--
-- =============================================================================
-- ATUALIZAÇÃO EM refresh_views.py
-- =============================================================================
-- Adicionar a nova MV à lista de refresh:
--
--   VIEWS = [
--       "mv_crescimento_municipio",
--       "mv_bolhas_ano_municipio",
--       "mv_treemap_setores",
--       "mv_densidade_uf",
--       "mv_comparador_uf_kpis",   # ← ADD (se criada)
--   ]
--
-- O loop existente já executa REFRESH MATERIALIZED VIEW CONCURRENTLY para cada item.
-- =============================================================================
