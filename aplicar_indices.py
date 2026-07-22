"""
Aplica os índices de init_indexes.sql sobre a camada Gold já existente.

Equivale a `psql -f init_indexes.sql`, sem depender do psql estar no PATH.

CREATE INDEX CONCURRENTLY não pode rodar dentro de bloco de transação, então
cada comando é executado em autocommit. Índices já existentes são ignorados
(IF NOT EXISTS), o que torna o script seguro para rodar mais de uma vez.

    python aplicar_indices.py
"""

import re
import sys
import time
from pathlib import Path

import psycopg2

from database import SQLALCHEMY_DATABASE_URL

# Aceita outro arquivo .sql como argumento:
#     python aplicar_indices.py mv_sobrevivencia_setor.sql
PADRAO = "init_indexes.sql"
ARQUIVO = Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else PADRAO)


def separar_comandos(sql: str) -> list[str]:
    """Remove comentários de linha e devolve os comandos individuais."""
    sem_comentarios = re.sub(r"--[^\n]*", "", sql)
    return [c.strip() for c in sem_comentarios.split(";") if c.strip()]


def rotulo(comando: str) -> str:
    """Extrai o nome do índice para exibição, ou resume o comando."""
    m = re.search(r"INDEX\s+(?:CONCURRENTLY\s+)?(?:IF NOT EXISTS\s+)?(\w+)",
                  comando, re.IGNORECASE)
    if m:
        return m.group(1)
    return comando.split()[0].upper()


def main():
    if not ARQUIVO.exists():
        raise SystemExit(f"Arquivo não encontrado: {ARQUIVO}")

    comandos = separar_comandos(ARQUIVO.read_text(encoding="utf-8"))
    print(f"{len(comandos)} comandos em {ARQUIVO.name}\n")

    conn = psycopg2.connect(SQLALCHEMY_DATABASE_URL)
    conn.autocommit = True          # obrigatório para CONCURRENTLY
    total = time.time()

    try:
        with conn.cursor() as cur:
            for i, comando in enumerate(comandos, 1):
                nome = rotulo(comando)
                print(f"[{i}/{len(comandos)}] {nome} ...", end=" ", flush=True)
                inicio = time.time()
                try:
                    cur.execute(comando)
                    print(f"OK ({time.time() - inicio:.1f}s)")
                except psycopg2.Error as e:
                    print(f"FALHOU\n    {str(e).strip()}")
    finally:
        conn.close()

    print(f"\nConcluído em {(time.time() - total) / 60:.1f} min")
    print("Rode 'python benchmark_queries.py' para medir o efeito.")


if __name__ == "__main__":
    main()
