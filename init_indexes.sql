-- Índices da camada Gold.
--
-- O etl_cnpj.py já cria todos ao final da carga. Este arquivo existe para
-- aplicá-los sobre uma Gold já existente, sem refazer o ETL:
--
--     psql -U postgres -d tcc_cnpj -f init_indexes.sql
--
-- CONCURRENTLY não bloqueia leituras, mas não pode rodar dentro de transação.

-- Filtros por setor e por município (dashboard e mapa coroplético)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_cnae
    ON empresas_gold (cnae_fiscal);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_municipio
    ON empresas_gold (cod_municipio);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_uf
    ON empresas_gold (uf);

-- BRIN: as linhas foram inseridas em ordem aproximada de data_abertura, então
-- o BRIN cobre a coluna com uma fração do tamanho de um B-tree.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_data_abertura
    ON empresas_gold USING brin (data_abertura)
    WITH (pages_per_range = 128);

-- Índice parcial covering para o ranking por capital social.
-- Só ~73% das empresas têm capital > 0, e o ranking olha apenas essas.
-- Sem ele, a query faz Seq Scan em 66,7 M de linhas (~12 s por execução).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_capital_positivo
    ON empresas_gold (capital_social DESC, cnae_fiscal, cod_municipio)
    WHERE capital_social > 0;

ANALYZE empresas_gold;
