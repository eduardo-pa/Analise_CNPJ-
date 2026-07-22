-- Sobrevivência empresarial por setor econômico.
--
-- Mede o tempo que empresas BAIXADAS permaneceram ativas: da abertura até a
-- data da baixa. Só é possível desde que situacao_cadastral e data_situacao
-- passaram a ser carregadas na camada Gold.
--
-- A versão anterior calculava AVG(hoje - data_abertura) sobre TODAS as
-- empresas, incluindo as já fechadas. Isso não é sobrevivência: uma empresa
-- aberta em 1990 e baixada em 1995 entrava na média como "36 anos" em vez de 5.
--
-- Pré-agregar em MV também resolve os ~9 s de varredura completa por acesso.
--
--     python aplicar_indices.py mv_sobrevivencia_setor.sql

DROP MATERIALIZED VIEW IF EXISTS mv_sobrevivencia_setor;

CREATE MATERIALIZED VIEW mv_sobrevivencia_setor AS
WITH duracao AS (
    SELECT
        e.cnae_fiscal,
        (e.data_situacao - e.data_abertura) / 365.25 AS anos
    FROM empresas_gold e
    WHERE e.situacao_cadastral = 8              -- 8 = baixada
      AND e.data_situacao IS NOT NULL
      AND e.data_situacao >= e.data_abertura    -- descarta datas invertidas
)
SELECT
    COALESCE(c.descricao, 'CNAE ' || d.cnae_fiscal::text)                    AS setor,
    COUNT(*)                                                                 AS total,
    ROUND(AVG(d.anos)::numeric, 2)                                           AS media,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY d.anos)::numeric, 2)  AS mediana,
    ROUND(PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY d.anos)::numeric, 2)  AS p05,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY d.anos)::numeric, 2)  AS q1,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY d.anos)::numeric, 2)  AS q3,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY d.anos)::numeric, 2)  AS p95
FROM duracao d
LEFT JOIN cnaes_referencia c ON d.cnae_fiscal = c.codigo
GROUP BY 1
HAVING COUNT(*) >= 1000    -- setores com amostra pequena distorcem percentis
ORDER BY mediana DESC;

-- Índice único: exigido por REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_sobrevivencia_setor
    ON mv_sobrevivencia_setor (setor);
