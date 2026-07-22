#!/usr/bin/env python3
"""
benchmark_queries.py — Benchmarks das queries principais do dashboard_tcc.py

Executa cada query N vezes no PostgreSQL (padrão: 5), mede o tempo médio com
time.perf_counter e analisa o plano via EXPLAIN ANALYZE para detectar Seq Scans
em tabelas grandes e sugerir índices.

Uso:
    python benchmark_queries.py
    python benchmark_queries.py --runs 10
    python benchmark_queries.py --query Q5
    python benchmark_queries.py --db-url "postgresql://user:pass@host:5432/db"

Observação: EXPLAIN ANALYZE executa a query mais uma vez após as N rodadas de
timing — o tempo total de execução do script é (N + 1) × tempo_da_query.
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError, ProgrammingError
except ImportError:
    sys.exit("❌  SQLAlchemy não encontrado. Execute: pip install sqlalchemy psycopg2-binary")


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO DE CONEXÃO
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_db_url(override: str | None) -> str:
    """Resolução em três níveis: argumento CLI → secrets.toml → database.py."""
    if override:
        return override

    secrets = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if secrets.exists():
        try:
            try:
                import tomllib                   # Python 3.11+
            except ImportError:
                import tomli as tomllib          # type: ignore[no-redef]
            with secrets.open("rb") as fh:
                return tomllib.load(fh)["db_url"]
        except Exception:
            pass

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from database import SQLALCHEMY_DATABASE_URL  # type: ignore[import]
        return SQLALCHEMY_DATABASE_URL
    except ImportError:
        pass

    return "postgresql://postgres:postgres@localhost:5432/tcc_cnpj"


# ═══════════════════════════════════════════════════════════════════════════════
# DEFINIÇÃO DAS QUERIES
# ═══════════════════════════════════════════════════════════════════════════════

QUERIES: list[dict] = [
    {
        "id": "Q1",
        "name": "Filtros — CNAEs disponíveis",
        "sql": "SELECT DISTINCT descricao FROM cnaes_referencia ORDER BY 1",
    },
    {
        "id": "Q2",
        "name": "Filtros — Municípios disponíveis",
        "sql": "SELECT DISTINCT descricao FROM municipios_referencia ORDER BY 1",
    },
    {
        "id": "Q3",
        "name": "Análise Estratégica (sem filtros — pior caso)",
        "sql": """
            SELECT e.razao_social, e.capital_social, e.data_abertura,
                   c.descricao AS setor, m.descricao AS cidade
            FROM empresas_gold e
            LEFT JOIN cnaes_referencia     c ON e.cnae_fiscal   = c.codigo
            LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
            WHERE e.capital_social > 0
            LIMIT 30000
        """,
    },
    {
        "id": "Q4",
        "name": "Ranking por Capital Social (top 25)",
        "sql": """
            SELECT e.razao_social, e.capital_social, c.descricao AS setor
            FROM empresas_gold e
            LEFT JOIN cnaes_referencia c ON e.cnae_fiscal = c.codigo
            WHERE e.capital_social > 0
            ORDER BY e.capital_social DESC
            LIMIT 25
        """,
    },
    {
        "id": "Q5",
        "name": "Sobrevivência Empresarial — mv_sobrevivencia_setor",
        "sql": """
            SELECT setor, total, media, mediana, p05, q1, q3, p95
            FROM mv_sobrevivencia_setor
            ORDER BY mediana DESC
            LIMIT 20
        """,
    },
    {
        "id": "Q6",
        "name": "Crescimento por Município — mv_crescimento_municipio",
        "sql": """
            SELECT ano, cod_municipio, total
            FROM mv_crescimento_municipio
            ORDER BY cod_municipio, ano
        """,
    },
    {
        "id": "Q7",
        "name": "Data Quality (passagem única na tabela)",
        "sql": """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE e.capital_social IS NULL
                                       OR e.capital_social <= 0)      AS capital_invalido,
                COUNT(*) FILTER (WHERE e.data_abertura IS NULL
                                       OR e.data_abertura < '1800-01-01'
                                       OR e.data_abertura > CURRENT_DATE) AS data_invalida,
                COUNT(*) FILTER (WHERE c.codigo IS NULL)               AS cnae_nao_mapeado
            FROM empresas_gold e
            LEFT JOIN cnaes_referencia c ON e.cnae_fiscal = c.codigo
        """,
    },
    {
        "id": "Q8",
        "name": "Contagem por Município (mapa coroplético)",
        "sql": """
            SELECT cod_municipio, COUNT(*) AS total
            FROM empresas_gold
            WHERE cod_municipio IS NOT NULL
            GROUP BY cod_municipio
            ORDER BY total DESC
        """,
    },
    {
        "id": "Q9",
        "name": "Bolhas: Ano × Capital × Município — mv_bolhas_ano_municipio (top 10 anos)",
        "sql": """
            SELECT ano, cod_municipio, total_empresas, capital_medio
            FROM mv_bolhas_ano_municipio
            WHERE ano IN (
                SELECT ano FROM mv_bolhas_ano_municipio
                GROUP BY ano
                ORDER BY SUM(total_empresas) DESC
                LIMIT 10
            )
            ORDER BY ano, cod_municipio
        """,
    },
    {
        "id": "Q10",
        "name": "Treemap de Setores Econômicos — mv_treemap_setores",
        "sql": """
            SELECT divisao_cnae, total_empresas, capital_total
            FROM mv_treemap_setores
            ORDER BY total_empresas DESC
        """,
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# SUGESTÕES DE ÍNDICE
# ═══════════════════════════════════════════════════════════════════════════════

# Tabelas onde Seq Scan é problemático (10–50 M linhas)
LARGE_TABLES: set[str] = {"empresas_gold"}

# coluna → (nome_do_índice, DDL, justificativa)
INDEX_SUGGESTIONS: dict[str, tuple[str, str, str]] = {
    "capital_social": (
        "idx_eg_capital_positivo",
        (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_capital_positivo\n"
            "    ON empresas_gold (capital_social, cnae_fiscal, cod_municipio)\n"
            "    WHERE capital_social > 0;"
        ),
        "Índice parcial covering: filtro + JOINs sem heap fetch adicional",
    ),
    "cnae_fiscal": (
        "idx_eg_cnae_fiscal",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_cnae_fiscal\n    ON empresas_gold (cnae_fiscal);",
        "B-tree para JOIN com cnaes_referencia (~1 300 valores distintos)",
    ),
    "cod_municipio": (
        "idx_eg_cod_municipio",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_cod_municipio\n    ON empresas_gold (cod_municipio);",
        "B-tree para JOIN com municipios_referencia (~5 570 municípios)",
    ),
    "situacao_cadastral": (
        "idx_eg_sobrevivencia",
        (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_sobrevivencia\n"
            "    ON empresas_gold (cnae_fiscal, data_abertura, data_situacao)\n"
            "    WHERE situacao_cadastral IN ('08', 'BAIXADA');"
        ),
        "Índice parcial para BAIXADAS (≈10–15% das linhas) — covering para Q5",
    ),
    "data_abertura": (
        "idx_eg_data_abertura",
        (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_data_abertura\n"
            "    ON empresas_gold USING brin (data_abertura)\n"
            "    WITH (pages_per_range = 128);"
        ),
        "BRIN: footprint mínimo para datas inseridas em ordem cronológica",
    ),
    "data_situacao": (
        "idx_eg_data_situacao",
        (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_data_situacao\n"
            "    ON empresas_gold USING brin (data_situacao)\n"
            "    WITH (pages_per_range = 128);"
        ),
        "BRIN para data_situacao — mesma justificativa de data_abertura",
    ),
    "uf": (
        "idx_eg_uf_abertura",
        (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eg_uf_abertura\n"
            "    ON empresas_gold (uf, data_abertura)\n"
            "    WHERE uf IS NOT NULL AND data_abertura IS NOT NULL;"
        ),
        "Composto parcial para GROUP BY uf + eliminação de NULLs (Q6, Q8, Q9)",
    ),
}

KNOWN_COLUMNS: list[str] = list(INDEX_SUGGESTIONS.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# TIMING E EXPLAIN
# ═══════════════════════════════════════════════════════════════════════════════

def time_query(conn, sql: str, runs: int) -> list[float]:
    """Executa a query `runs` vezes; retorna tempos em ms (inclui fetch completo)."""
    stmt = text(sql)
    times: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        result = conn.execute(stmt)
        result.fetchall()            # garante que todos os dados são transferidos
        times.append((time.perf_counter() - t0) * 1000)
    return times


def get_explain_plan(conn, sql: str) -> list[str] | None:
    """Roda EXPLAIN (ANALYZE, BUFFERS) e retorna as linhas do plano."""
    try:
        rows = conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {sql.strip()}"))
        return [row[0] for row in rows]
    except Exception:
        return None


def parse_plan(plan_lines: list[str]) -> dict:
    """
    Extrai de EXPLAIN ANALYZE:
      - Nós Seq Scan (com tabela e filtros)
      - Nós Index Scan (nomes dos índices usados)
      - Planning e Execution time
    """
    seq_scans:   list[dict] = []
    index_scans: list[str]  = []
    planning_ms  = 0.0
    execution_ms = 0.0

    for i, line in enumerate(plan_lines):
        # ── Seq Scan ────────────────────────────────────────────────────
        m = re.search(r"(?:Parallel )?Seq Scan on (\w+)", line, re.IGNORECASE)
        if m:
            table = m.group(1)
            filters: list[str] = []
            # Procura linhas de Filter/Index Cond logo abaixo deste nó
            for j in range(i + 1, min(i + 8, len(plan_lines))):
                next_line = plan_lines[j]
                fm = re.search(r"Filter:\s+(.+)", next_line)
                if fm:
                    filters.append(fm.group(1).strip())
                # Interrompe ao encontrar um novo nó do plano
                if re.search(r"->\s+\w", next_line):
                    break
            seq_scans.append({"table": table, "filters": filters})

        # ── Index Scan / Index Only Scan ─────────────────────────────────
        m = re.search(
            r"Index (?:Only )?Scan(?:\s+Backward)? using (\w+)", line, re.IGNORECASE
        )
        if m:
            index_scans.append(m.group(1))

        # ── Bitmap Index Scan ────────────────────────────────────────────
        m = re.search(r"Bitmap Index Scan on (\w+)", line, re.IGNORECASE)
        if m:
            index_scans.append(m.group(1))

        # ── Tempos do planner ────────────────────────────────────────────
        m = re.search(r"Planning Time:\s+([\d.]+)\s+ms", line)
        if m:
            planning_ms = float(m.group(1))

        m = re.search(r"Execution Time:\s+([\d.]+)\s+ms", line)
        if m:
            execution_ms = float(m.group(1))

    large_seq = [s for s in seq_scans if s["table"] in LARGE_TABLES]

    return {
        "seq_scans":         seq_scans,
        "large_seq_scans":   large_seq,
        "index_scans":       index_scans,
        "has_large_seq_scan": bool(large_seq),
        "planning_ms":       planning_ms,
        "execution_ms":      execution_ms,
    }


def unindexed_columns(large_seq_scans: list[dict]) -> list[str]:
    """Retorna colunas sem índice encontradas nos filtros de Seq Scans grandes."""
    all_text = " ".join(
        f for scan in large_seq_scans for f in scan["filters"]
    ).lower()
    return [col for col in KNOWN_COLUMNS if col in all_text]


def check_index_exists(conn, index_name: str) -> bool:
    """Verifica em pg_indexes se o índice já existe no banco."""
    try:
        row = conn.execute(
            text("SELECT COUNT(*) FROM pg_indexes WHERE indexname = :n"),
            {"n": index_name},
        ).fetchone()
        return bool(row and row[0] > 0)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATAÇÃO DO RELATÓRIO
# ═══════════════════════════════════════════════════════════════════════════════

W = 74


def _sep(char: str = "─") -> str:
    return char * W


def _header(*lines: str) -> str:
    sep = "═" * W
    body = "\n".join(f"  {ln}" for ln in lines)
    return f"{sep}\n{body}\n{sep}"


def _trunc(s: str, n: int = 65) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def print_query_report(q: dict, times: list[float], plan_info: dict | None, conn) -> dict:
    avg_ms = sum(times) / len(times)
    min_ms = min(times)
    max_ms = max(times)

    print()
    print(_sep())
    print(f"  {q['id']} · {q['name']}")
    print(_sep())
    print(f"  Tempo médio  : {avg_ms:>9.2f} ms   "
          f"[min {min_ms:.2f}  /  max {max_ms:.2f}  /  σ {_stddev(times):.2f}]")

    meta = {"id": q["id"], "name": q["name"], "avg_ms": avg_ms}

    if plan_info is None:
        print("  EXPLAIN      : ❌  Não disponível")
        return meta

    # ── Plano ────────────────────────────────────────────────────────────────
    if plan_info["index_scans"]:
        shown = plan_info["index_scans"][:4]
        extra = len(plan_info["index_scans"]) - len(shown)
        suffix = f"  +{extra} mais" if extra else ""
        print(f"  Plano        : 🔍 Index Scan  →  {', '.join(shown)}{suffix}")

    for scan in plan_info["seq_scans"]:
        symbol = "⚠️ " if scan["table"] in LARGE_TABLES else "ℹ️ "
        print(f"  Plano        : {symbol}Seq Scan on {scan['table']}")
        for f in scan["filters"]:
            print(f"                 Filter: {_trunc(f)}")

    if not plan_info["seq_scans"] and not plan_info["index_scans"]:
        print("  Plano        : ℹ️  Sem nós de scan (hash agg / CTE materializada)")

    print(
        f"  EXPLAIN      : planning {plan_info['planning_ms']:.2f} ms  ·  "
        f"execution {plan_info['execution_ms']:.2f} ms"
    )

    # ── Status e sugestões ───────────────────────────────────────────────────
    if plan_info["has_large_seq_scan"]:
        meta["seq_scan_large"] = True
        cols = unindexed_columns(plan_info["large_seq_scans"])
        print("  Status       : ⚠️  Seq Scan em tabela grande detectado")

        if cols:
            print(f"  Cols filtradas: {', '.join(cols)}")
            seen_idx: set[str] = set()
            for col in cols:
                idx_name, ddl, hint = INDEX_SUGGESTIONS[col]
                if idx_name in seen_idx:
                    continue
                seen_idx.add(idx_name)
                exists = check_index_exists(conn, idx_name)
                if exists:
                    print(f"\n  ℹ️  '{idx_name}' já existe — execute ANALYZE para")
                    print(    "      atualizar as estatísticas e forçar uso do índice:")
                    print(    "      ANALYZE empresas_gold;")
                else:
                    print(f"\n  💡 Sugestão para '{col}'  →  {idx_name}")
                    print(f"     {hint}")
                    print("     DDL:")
                    for ln in ddl.splitlines():
                        print(f"       {ln}")
        else:
            print("  Colunas filtradas não mapeadas — inspecione o EXPLAIN manualmente.")
    else:
        print("  Status       : ✅ Sem Seq Scan em tabela grande")

    return meta


def print_summary(results: list[dict]):
    ok   = [r for r in results if not r.get("error") and not r.get("seq_scan_large")]
    warn = [r for r in results if not r.get("error") and r.get("seq_scan_large")]
    err  = [r for r in results if r.get("error")]

    print()
    print(_header("RESUMO"))
    print(f"  ✅ Com índice (sem seq scan) : {len(ok):>2}")
    print(f"  ⚠️  Seq Scan em tabela grande : {len(warn):>2}")
    print(f"  ❌ Falhou / ignorada         : {len(err):>2}")

    timed = [r for r in results if "avg_ms" in r]
    if timed:
        slowest = max(timed, key=lambda r: r["avg_ms"])
        fastest = min(timed, key=lambda r: r["avg_ms"])
        print()
        print(f"  Mais lenta  : {slowest['id']} · {slowest['name']}")
        print(f"                {slowest['avg_ms']:.2f} ms em média")
        print(f"  Mais rápida : {fastest['id']} · {fastest['name']}")
        print(f"                {fastest['avg_ms']:.2f} ms em média")

    if warn or err:
        print()
        print("  Para aplicar todos os índices recomendados:")
        print("    python aplicar_indices.py")

    print(_sep("═"))


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark das queries do dashboard_tcc.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python benchmark_queries.py\n"
            "  python benchmark_queries.py --runs 10 --query Q5\n"
            "  python benchmark_queries.py --db-url 'postgresql://u:p@host/db'"
        ),
    )
    parser.add_argument(
        "--runs", type=int, default=5, metavar="N",
        help="Número de execuções por query para calcular a média (padrão: 5)",
    )
    parser.add_argument(
        "--db-url", dest="db_url", default=None,
        help="URL de conexão PostgreSQL (sobrepõe .streamlit/secrets.toml)",
    )
    parser.add_argument(
        "--query", default=None, metavar="ID",
        help="Roda apenas a query com este ID, ex: --query Q5",
    )
    args = parser.parse_args()

    db_url = _resolve_db_url(args.db_url)
    display_url = re.sub(r":([^:@/]+)@", ":***@", db_url)

    print(_header(
        "BENCHMARK QUERIES — dashboard_tcc.py",
        f"Banco   : {display_url}",
        f"Rodadas : {args.runs} por query  (+ 1 EXPLAIN ANALYZE cada)",
        f"Data    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ))

    # ── Testa a conexão ──────────────────────────────────────────────────────
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except OperationalError as exc:
        print(f"\n❌  Impossível conectar ao PostgreSQL.")
        print(f"   URL  : {display_url}")
        cause = getattr(exc, "orig", exc)
        print(f"   Erro : {str(cause).splitlines()[0]}")
        print("\n  Verifique:")
        print("  • PostgreSQL está rodando?")
        print("  • Credenciais em .streamlit/secrets.toml estão corretas?")
        print("  • O banco 'tcc_cnpj' existe?")
        sys.exit(1)

    # ── Seleciona queries ────────────────────────────────────────────────────
    queries_to_run = QUERIES
    if args.query:
        queries_to_run = [q for q in QUERIES if q["id"].upper() == args.query.upper()]
        if not queries_to_run:
            ids = ", ".join(q["id"] for q in QUERIES)
            print(f"\n❌  Query '{args.query}' não encontrada. IDs disponíveis: {ids}")
            sys.exit(1)

    summary: list[dict] = []

    with engine.connect() as conn:
        for q in queries_to_run:
            try:
                # ── Timing ──────────────────────────────────────────────────
                times = time_query(conn, q["sql"], args.runs)

                # ── EXPLAIN ANALYZE ──────────────────────────────────────────
                plan_lines = get_explain_plan(conn, q["sql"])
                plan_info  = parse_plan(plan_lines) if plan_lines else None

                meta = print_query_report(q, times, plan_info, conn)
                summary.append(meta)

            except (ProgrammingError, Exception) as exc:
                conn.rollback()  # restaura a conexão para o estado limpo
                print()
                print(_sep())
                print(f"  {q['id']} · {q['name']}")
                print(_sep())
                msg = str(exc).split("\n")[0][:100]
                print(f"  ❌  Query falhou: {msg}")
                missing = re.search(r'column "(\w+)" does not exist', str(exc))
                if missing:
                    col = missing.group(1)
                    print(f"\n  ⚠️  A coluna '{col}' não existe em empresas_gold.")
                    print("      Adicione ao ETL (tratar_dados_final.py) antes de usar.")
                summary.append({"id": q["id"], "name": q["name"], "error": True})

    print_summary(summary)


if __name__ == "__main__":
    main()
