"""
ETL CNPJ — Receita Federal
Substitui tratar_dados_final.py e tratar_dados.py.

Correções em relação à versão anterior:
  1. Lê os 10 shards de cada arquivo (antes: só o shard 0).
  2. Não assume que Empresas{N} corresponde a Estabelecimentos{N} — os arquivos
     são fatiados por critérios diferentes (Empresas por faixa de CNPJ,
     Estabelecimentos embaralhado). O join só é correto sobre a base completa.
  3. Chave normalizada com zfill(8), nunca lstrip('0').
  4. Uma linha por EMPRESA (filtra matriz), não por estabelecimento.
  5. Carrega situacao_cadastral e data_situacao — permite análise de sobrevivência.
  6. Portões de qualidade que abortam a carga em vez de gravar dado silenciosamente errado.
  7. Nenhuma data simulada. Nenhuma credencial em código.

Estratégia: Query-First. O join roda no Postgres, não em pandas — a base completa
não cabe em 16 GB de RAM. Os CSVs entram por COPY nas tabelas bronze.

Uso:
    set DATABASE_URL=postgresql://postgres:SENHA@localhost:5432/tcc_cnpj
    set CNPJ_DIR=C:\\Users\\eduar\\OneDrive\\Desktop\\tccCNPJ\\2026-02\\2026-02
    python etl_cnpj.py
"""

import os
import io
import sys
import glob
import time
import zipfile

import psycopg2
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Configuração — nada hardcoded
# ----------------------------------------------------------------------------
load_dotenv()

try:
    DATABASE_URL = os.environ["DATABASE_URL"]
    CNPJ_DIR = os.environ["CNPJ_DIR"]
except KeyError as e:
    sys.exit(f"Variável de ambiente obrigatória ausente: {e}. Veja .env.example")

# Tablespace opcional para as tabelas bronze (~75 GB). Se o disco do Postgres
# não comportar, aponte CNPJ_TABLESPACE_DIR para um volume com espaço sobrando.
# A camada Gold (~9 GB) permanece no tablespace padrão.
TABLESPACE_DIR = os.environ.get("CNPJ_TABLESPACE_DIR")
TABLESPACE = "ts_cnpj_bronze" if TABLESPACE_DIR else None
CLAUSULA_TS = f" TABLESPACE {TABLESPACE}" if TABLESPACE else ""

# CNPJ_SKIP_BRONZE=1 reaproveita as bronze já carregadas. Útil ao iterar sobre
# a lógica da Gold sem repetir os ~30 min de COPY.
PULAR_BRONZE = os.environ.get("CNPJ_SKIP_BRONZE", "").strip() in ("1", "true", "True")

# Layout oficial da Receita Federal (posicional, sem cabeçalho)
DDL_EMPRESAS = """
CREATE UNLOGGED TABLE bronze_empresas (
    cnpj_basico                 text,
    razao_social                text,
    natureza_juridica           text,
    qualificacao_responsavel    text,
    capital_social              text,
    porte_empresa               text,
    ente_federativo_responsavel text
){ts};
"""

DDL_ESTABELECIMENTOS = """
CREATE UNLOGGED TABLE bronze_estabelecimentos (
    cnpj_basico                text, cnpj_ordem            text,
    cnpj_dv                    text, matriz_filial         text,
    nome_fantasia              text, situacao_cadastral    text,
    data_situacao              text, motivo_situacao       text,
    nome_cidade_exterior       text, pais                  text,
    data_inicio_atividade      text, cnae_fiscal           text,
    cnae_secundaria            text, tipo_logradouro       text,
    logradouro                 text, numero                text,
    complemento                text, bairro                text,
    cep                        text, uf                    text,
    municipio                  text, ddd_1                 text,
    telefone_1                 text, ddd_2                 text,
    telefone_2                 text, ddd_fax               text,
    fax                        text, correio_eletronico    text,
    situacao_especial          text, data_situacao_especial text
){ts};
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def preparar_tablespace():
    """Cria o tablespace das bronze se CNPJ_TABLESPACE_DIR estiver definido.

    CREATE TABLESPACE não pode rodar dentro de bloco de transação, então usa
    uma conexão própria em autocommit, separada da transação principal.
    """
    if not TABLESPACE:
        log("Tablespace: padrão (bronze e gold no mesmo volume)")
        return

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_tablespace WHERE spcname = %s", (TABLESPACE,))
            if cur.fetchone():
                log(f"Tablespace {TABLESPACE} já existe")
                return

            caminho = TABLESPACE_DIR.replace("\\", "/")
            log(f"Criando tablespace {TABLESPACE} em {caminho}")
            try:
                cur.execute(f"CREATE TABLESPACE {TABLESPACE} LOCATION '{caminho}'")
            except psycopg2.Error as e:
                sys.exit(
                    f"Falha ao criar o tablespace: {e}\n"
                    f"Verifique se a pasta {TABLESPACE_DIR} existe, está VAZIA, e se a "
                    f"conta que roda o serviço do PostgreSQL tem Controle Total sobre ela."
                )
    finally:
        conn.close()


class SemBytesNulos(io.RawIOBase):
    """Remove bytes NUL (0x00) do stream durante o COPY.

    Os arquivos da Receita contêm NUL esparsos em campos de texto. O PostgreSQL
    rejeita 0x00 em colunas text sob qualquer encoding, então o COPY aborta.
    Filtrar aqui evita descompactar 30 GB em disco só para limpar.
    """

    TAMANHO_PADRAO = 1 << 20  # 1 MB

    def __init__(self, fh):
        self._fh = fh
        self.removidos = 0

    def readable(self):
        return True

    def read(self, size=-1):
        alvo = size if size and size > 0 else self.TAMANHO_PADRAO
        while True:
            bloco = self._fh.read(alvo)
            if not bloco:
                return b""              # EOF real
            if b"\x00" in bloco:
                self.removidos += bloco.count(b"\x00")
                bloco = bloco.replace(b"\x00", b"")
                if not bloco:
                    continue            # bloco era só NUL: lê o próximo
            return bloco


def copiar_shards(cur, padrao, tabela):
    """Streama cada ZIP direto para o Postgres via COPY. Sem pandas, sem RAM."""
    arquivos = sorted(glob.glob(os.path.join(CNPJ_DIR, padrao)))
    if not arquivos:
        sys.exit(f"Nenhum arquivo encontrado para o padrão {padrao} em {CNPJ_DIR}")

    log(f"{tabela}: {len(arquivos)} shards encontrados")
    total_nulos = 0
    for caminho in arquivos:
        nome = os.path.basename(caminho)
        with zipfile.ZipFile(caminho) as z:
            interno = z.namelist()[0]
            with z.open(interno) as bruto:
                fh = SemBytesNulos(bruto)
                cur.copy_expert(
                    f"COPY {tabela} FROM STDIN WITH "
                    "(FORMAT csv, DELIMITER ';', QUOTE '\"', ENCODING 'LATIN1')",
                    fh,
                )
                total_nulos += fh.removidos
        cur.execute(f"SELECT count(*) FROM {tabela}")
        log(f"  {nome} carregado — acumulado: {cur.fetchone()[0]:,}")

    if total_nulos:
        log(f"  {total_nulos:,} bytes NUL removidos na origem ({tabela})")


ARQUIVO_MVS = "views_materializadas.sql"


def bronze_pronta(cur):
    """Diz se as bronze já existem e estão populadas."""
    cur.execute("SELECT to_regclass('bronze_empresas'), "
                "to_regclass('bronze_estabelecimentos')")
    if any(t is None for t in cur.fetchone()):
        return False
    cur.execute("SELECT (SELECT count(*) FROM bronze_empresas) > 0 "
                "AND (SELECT count(*) FROM bronze_estabelecimentos) > 0")
    return cur.fetchone()[0]


def capturar_dependentes(cur):
    """Salva definição e índices das MVs que dependem de empresas_gold.

    As MVs precisam cair junto com a tabela (DROP CASCADE) e ser recriadas
    depois. Ler do catálogo garante que nada se perca, mesmo para views cuja
    definição não está versionada no repositório.
    """
    cur.execute("SELECT to_regclass('empresas_gold')")
    if cur.fetchone()[0] is None:
        return None

    cur.execute("""
        SELECT DISTINCT m.oid::regclass::text, pg_get_viewdef(m.oid, true)
        FROM pg_depend d
        JOIN pg_rewrite r ON r.oid = d.objid
        JOIN pg_class   m ON m.oid = r.ev_class
        WHERE d.refobjid = 'empresas_gold'::regclass
          AND m.relkind  = 'm'
          AND m.oid     <> 'empresas_gold'::regclass
        ORDER BY 1
    """)
    mvs = cur.fetchall()
    if not mvs:
        return None

    nomes = [n for n, _ in mvs]
    cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = ANY(%s)", (nomes,))
    indices = [r[0] for r in cur.fetchall()]

    log(f"Views materializadas dependentes: {', '.join(nomes)}")

    # Versiona as definições — três delas não tinham script no repositório.
    with open(ARQUIVO_MVS, "w", encoding="utf-8") as f:
        f.write("-- Gerado por etl_cnpj.py a partir do catálogo do PostgreSQL.\n")
        f.write("-- Recriado automaticamente após a reconstrução de empresas_gold.\n\n")
        for nome, definicao in mvs:
            f.write(f"CREATE MATERIALIZED VIEW {nome} AS\n{definicao}\n\n")
        for idx in indices:
            f.write(f"{idx};\n")
    log(f"Definições salvas em {ARQUIVO_MVS}")

    return mvs, indices


def recriar_dependentes(cur, capturado):
    if not capturado:
        return
    mvs, indices = capturado
    log("Recriando views materializadas")
    for nome, definicao in mvs:
        cur.execute(f"CREATE MATERIALIZED VIEW {nome} AS {definicao}")
        log(f"  {nome}")
    for idx in indices:
        cur.execute(idx)
    log(f"  {len(indices)} índices restaurados")


SQL_GOLD = """
DROP TABLE IF EXISTS empresas_gold CASCADE;

CREATE TABLE empresas_gold AS
SELECT
    e.cnpj_basico,
    e.razao_social,
    NULLIF(e.natureza_juridica, '')::bigint                       AS natureza_juridica,
    COALESCE(replace(e.capital_social, ',', '.')::numeric, 0)     AS capital_social,
    to_date(NULLIF(st.data_inicio_atividade, '0'), 'YYYYMMDD')    AS data_abertura,
    NULLIF(st.cnae_fiscal, '')::bigint                            AS cnae_fiscal,
    NULLIF(st.municipio, '')::bigint                              AS cod_municipio,
    st.uf,
    NULLIF(st.situacao_cadastral, '')::int                        AS situacao_cadastral,
    to_date(NULLIF(st.data_situacao, '0'), 'YYYYMMDD')            AS data_situacao
FROM (
    SELECT lpad(cnpj_basico, 8, '0') AS cnpj_basico,
           razao_social, natureza_juridica, capital_social
    FROM bronze_empresas
) e
JOIN (
    -- matriz_filial = '1' garante uma linha por EMPRESA, não por estabelecimento
    SELECT lpad(cnpj_basico, 8, '0') AS cnpj_basico,
           data_inicio_atividade, cnae_fiscal, municipio, uf,
           situacao_cadastral, data_situacao
    FROM bronze_estabelecimentos
    WHERE matriz_filial = '1'
      -- Quarentena: a origem tem um punhado de datas impossíveis (ano 0,
      -- datas futuras). São descartadas aqui e contabilizadas no log, em vez
      -- de contaminarem médias e séries temporais na camada final.
      AND data_inicio_atividade ~ '^[0-9]{8}$'
      AND to_date(data_inicio_atividade, 'YYYYMMDD')
          BETWEEN DATE '1900-01-01' AND CURRENT_DATE
) st USING (cnpj_basico);

ALTER TABLE empresas_gold ADD PRIMARY KEY (cnpj_basico);
"""

# Portões de qualidade. Cada um retorna (rótulo, valor, condição_ok).
CHECKS = [
    ("Linhas na Gold",
     "SELECT count(*) FROM empresas_gold",
     lambda v, ctx: v > 40_000_000),

    ("Retenção Gold/Empresas",
     "SELECT round(100.0 * (SELECT count(*) FROM empresas_gold) "
     "/ NULLIF((SELECT count(*) FROM bronze_empresas), 0), 2)",
     lambda v, ctx: v is not None and v > 90),

    ("CNPJs duplicados",
     "SELECT count(*) - count(DISTINCT cnpj_basico) FROM empresas_gold",
     lambda v, ctx: v == 0),

    ("Datas de abertura nulas",
     "SELECT count(*) FROM empresas_gold WHERE data_abertura IS NULL",
     lambda v, ctx: v == 0),

    ("Datas fora do intervalo plausível",
     "SELECT count(*) FROM empresas_gold "
     "WHERE data_abertura < DATE '1900-01-01' OR data_abertura > now()",
     lambda v, ctx: v == 0),

    # Detecta o bug de shard: nenhuma década deve concentrar mais de 60% da base
    ("Maior concentração por década",
     """SELECT round(100.0 * max(c) / sum(c), 1) FROM (
            SELECT count(*) AS c FROM empresas_gold
            GROUP BY (EXTRACT(YEAR FROM data_abertura)::int / 10)
        ) t""",
     lambda v, ctx: v is not None and v < 60),
]


def rodar_checks(cur):
    log("Portões de qualidade")
    falhas = []
    for rotulo, sql, ok in CHECKS:
        cur.execute(sql)
        valor = cur.fetchone()[0]
        passou = ok(valor, None)
        marca = "OK  " if passou else "FALHA"
        log(f"  [{marca}] {rotulo}: {valor:,}" if isinstance(valor, int)
            else f"  [{marca}] {rotulo}: {valor}")
        if not passou:
            falhas.append(rotulo)
    if falhas:
        raise RuntimeError(
            "Carga abortada — portões reprovados: " + ", ".join(falhas)
        )
    log("Todos os portões passaram")


def main():
    inicio = time.time()

    # Fora da transação principal: CREATE TABLESPACE exige autocommit.
    preparar_tablespace()

    with psycopg2.connect(DATABASE_URL) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            # O join de ~66 M x ~55 M linhas derrama dezenas de GB em arquivos
            # temporários. Por padrão eles vão para o tablespace default; se
            # houver disco alternativo, mande os temporários para lá também,
            # senão o volume do Postgres enche no meio da execução.
            if TABLESPACE:
                cur.execute(f"SET temp_tablespaces = '{TABLESPACE}'")
                log(f"Arquivos temporários direcionados para {TABLESPACE}")

            # Mais memória para hash join e criação de índices nesta sessão.
            cur.execute("SET work_mem = '512MB'")
            cur.execute("SET maintenance_work_mem = '2GB'")
            cur.execute("SET synchronous_commit = off")

            if PULAR_BRONZE and bronze_pronta(cur):
                log("Bronze já carregada — pulando (CNPJ_SKIP_BRONZE=1)")
            else:
                log("Recriando tabelas bronze")
                cur.execute("DROP TABLE IF EXISTS bronze_empresas, "
                            "bronze_estabelecimentos")
                cur.execute(DDL_EMPRESAS.format(ts=CLAUSULA_TS))
                cur.execute(DDL_ESTABELECIMENTOS.format(ts=CLAUSULA_TS))

                copiar_shards(cur, "Empresas*.zip", "bronze_empresas")
                copiar_shards(cur, "Estabelecimentos*.zip",
                              "bronze_estabelecimentos")

        # Persiste as bronze antes de validar. Sem isso, uma reprovação de
        # portão faria rollback de ~30 min de carga junto com a Gold.
        conn.commit()
        log("Bronze consolidada (commit)")

        with conn.cursor() as cur:
            cur.execute("SET work_mem = '512MB'")
            cur.execute("SET maintenance_work_mem = '2GB'")
            if TABLESPACE:
                cur.execute(f"SET temp_tablespaces = '{TABLESPACE}'")

            # Quantifica o que a quarentena vai descartar, antes de descartar.
            cur.execute("""
                SELECT count(*) FROM bronze_estabelecimentos
                WHERE matriz_filial = '1'
                  AND NOT (data_inicio_atividade ~ '^[0-9]{8}$'
                           AND to_date(data_inicio_atividade, 'YYYYMMDD')
                               BETWEEN DATE '1900-01-01' AND CURRENT_DATE)
            """)
            quarentena = cur.fetchone()[0]
            if quarentena:
                log(f"Quarentena: {quarentena:,} matrizes com data de abertura "
                    f"inválida serão excluídas da Gold")

            # As MVs dependem da Gold: captura antes do DROP CASCADE.
            dependentes = capturar_dependentes(cur)

            log("Construindo empresas_gold (join no Postgres)")
            cur.execute(SQL_GOLD)

            rodar_checks(cur)

            log("Criando índices")
            cur.execute("CREATE INDEX idx_eg_cnae ON empresas_gold (cnae_fiscal)")
            cur.execute("CREATE INDEX idx_eg_municipio ON empresas_gold (cod_municipio)")
            cur.execute("CREATE INDEX idx_eg_uf ON empresas_gold (uf)")
            cur.execute("CREATE INDEX idx_eg_data_abertura ON empresas_gold "
                        "USING brin (data_abertura) WITH (pages_per_range = 128)")
            # Índice parcial covering: só ~73% das linhas têm capital > 0, e o
            # ranking do dashboard só olha essas. Sem ele, o Q4 do benchmark
            # faz Seq Scan em 66,7 M de linhas (~12 s).
            cur.execute("""
                CREATE INDEX idx_eg_capital_positivo ON empresas_gold
                    (capital_social DESC, cnae_fiscal, cod_municipio)
                    WHERE capital_social > 0
            """)
            cur.execute("ANALYZE empresas_gold")

            recriar_dependentes(cur, dependentes)

        conn.commit()

    log(f"Concluído em {(time.time() - inicio) / 60:.1f} min")


if __name__ == "__main__":
    main()
