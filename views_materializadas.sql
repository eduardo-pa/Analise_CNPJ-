-- Gerado por etl_cnpj.py a partir do catálogo do PostgreSQL.
-- Recriado automaticamente após a reconstrução de empresas_gold.

CREATE MATERIALIZED VIEW mv_bolhas_ano_municipio AS
 SELECT EXTRACT(year FROM e.data_abertura)::integer AS ano,
    e.cod_municipio,
    COALESCE(m.descricao, e.cod_municipio::text) AS nome_municipio,
    count(*) AS total_empresas,
    avg(e.capital_social) AS capital_medio
   FROM empresas_gold e
     LEFT JOIN municipios_referencia m ON m.codigo = e.cod_municipio
  WHERE e.data_abertura IS NOT NULL AND e.cod_municipio IS NOT NULL AND e.capital_social > 0::double precision
  GROUP BY (EXTRACT(year FROM e.data_abertura)::integer), e.cod_municipio, m.descricao;

CREATE MATERIALIZED VIEW mv_comparador_uf_kpis AS
 SELECT "left"(cod_municipio::text, 2) AS cod_uf,
    count(*) AS total_empresas,
    round(avg(capital_social) FILTER (WHERE capital_social > 0::double precision)::numeric, 2) AS capital_medio
   FROM empresas_gold
  GROUP BY ("left"(cod_municipio::text, 2));

CREATE MATERIALIZED VIEW mv_crescimento_municipio AS
 SELECT EXTRACT(year FROM e.data_abertura)::integer AS ano,
    e.cod_municipio,
    COALESCE(m.descricao, e.cod_municipio::text) AS nome_municipio,
    count(*) AS total
   FROM empresas_gold e
     LEFT JOIN municipios_referencia m ON m.codigo = e.cod_municipio
  WHERE e.data_abertura IS NOT NULL AND e.cod_municipio IS NOT NULL AND e.data_abertura >= '1990-01-01 00:00:00'::timestamp without time zone
  GROUP BY (EXTRACT(year FROM e.data_abertura)::integer), e.cod_municipio, m.descricao;

CREATE MATERIALIZED VIEW mv_treemap_setores AS
 SELECT "left"(cnae_fiscal::text, 2) AS divisao_cnae,
    count(*) AS total_empresas,
    sum(capital_social) AS capital_total
   FROM empresas_gold
  WHERE capital_social >= 0::double precision AND cnae_fiscal IS NOT NULL
  GROUP BY ("left"(cnae_fiscal::text, 2));

CREATE UNIQUE INDEX mv_treemap_setores_divisao_cnae_idx ON public.mv_treemap_setores USING btree (divisao_cnae);
CREATE INDEX mv_bolhas_ano_municipio_ano_idx ON public.mv_bolhas_ano_municipio USING btree (ano);
CREATE INDEX mv_crescimento_municipio_ano_idx ON public.mv_crescimento_municipio USING btree (ano);
CREATE INDEX mv_crescimento_municipio_cod_municipio_idx ON public.mv_crescimento_municipio USING btree (cod_municipio);
CREATE UNIQUE INDEX uidx_mv_comparador_uf_kpis_cod_uf ON public.mv_comparador_uf_kpis USING btree (cod_uf);
