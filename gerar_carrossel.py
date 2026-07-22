"""
Carrossel LinkedIn — "A cacada ao 44,5"
Gera um PDF de 3 paginas (formato aceito pelo LinkedIn como documento/carrossel)
+ PNGs individuais para preview.

Slides 1 e 2 usam numeros ja apurados (constantes abaixo).
Slide 3 consulta a base real. Sem conexao, cai em dados sinteticos so para layout.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv

load_dotenv()
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
DB_URL = os.environ.get("DATABASE_URL")   # vem do .env, nunca hardcoded
W, H, DPI = 1080, 1350, 200
FIGSIZE = (W / DPI, H / DPI)

BG      = "#12161C"
FG      = "#F2F5F8"
MUTED   = "#8B98A8"
ACCENT  = "#4C8DFF"
WARN    = "#FF6B4A"
OK      = "#3DD68C"
GRID    = "#232A34"

TOTAL_REGISTROS = "10,6 milhões"
NUM_ERRADO      = "44,5"
IBGE_REF        = 11.6
NULOS           = 0
ANTES_1950      = 142
PCT_ANTES_1950  = "0,0013%"
FUTURAS         = 0
MEDIA           = 9.5
MEDIANA         = 3.2
P90             = 37.1


def novo_slide():
    fig = plt.figure(figsize=FIGSIZE, dpi=DPI, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return fig, ax


def rodape(ax, n):
    ax.text(0.08, 0.055, "Eduardo Pereira de Amorim", color=MUTED,
            fontsize=8.5, va="center")
    ax.text(0.92, 0.055, f"{n}/3", color=MUTED, fontsize=8.5,
            va="center", ha="right")
    ax.plot([0.08, 0.92], [0.093, 0.093], color=GRID, lw=1)


# ---------------------------------------------------------------- SLIDE 1
def slide1():
    fig, ax = novo_slide()
    ax.text(0.08, 0.88, "THE NUMBER I PUBLISHED", color=WARN,
            fontsize=11, weight="bold")
    ax.text(0.08, 0.845, "O número que eu publiquei", color=MUTED, fontsize=10.5)

    ax.text(0.08, 0.66, NUM_ERRADO, color=FG, fontsize=118, weight="bold", va="center")
    ax.text(0.08, 0.545, "anos  ·  years", color=MUTED, fontsize=17, va="center")
    ax.plot([0.085, 0.800], [0.665, 0.665], color=WARN, lw=5)

    ax.text(0.08, 0.44,
            "“Empresas baixadas duraram em média\n44,5 anos ativas”",
            color=FG, fontsize=15.5, va="top", linespacing=1.55, style="italic")

    ax.plot([0.08, 0.12], [0.335, 0.335], color=WARN, lw=3)
    ax.text(0.08, 0.275, "Estava errado.", color=WARN, fontsize=25,
            weight="bold", va="center")
    ax.text(0.08, 0.215, "It was wrong. Here's how I found out.",
            color=MUTED, fontsize=13.5, va="center")
    rodape(ax, 1)
    return fig


# ---------------------------------------------------------------- SLIDE 2
def slide2():
    fig, ax = novo_slide()
    ax.text(0.08, 0.90, "FIRST HYPOTHESIS: SENTINEL DATES", color=ACCENT,
            fontsize=11, weight="bold")
    ax.text(0.08, 0.865, "Primeira hipótese: datas sentinela", color=MUTED, fontsize=10.5)

    ax.text(0.08, 0.795, f"Diagnóstico em {TOTAL_REGISTROS} de registros",
            color=FG, fontsize=14, weight="bold", va="top")

    linhas = [
        (f"{NULOS}",              "datas nulas",             "null dates"),
        (f"{ANTES_1950}",         f"antes de 1950  ({PCT_ANTES_1950})", "rows before 1950"),
        (f"{FUTURAS}",            "datas futuras",           "future dates"),
        ("1901–2026",             "intervalo completo",      "full range"),
    ]
    y = 0.665
    for valor, pt, en in linhas:
        fs = 24 if len(valor) > 4 else 31
        ax.text(0.08, y, valor, color=OK, fontsize=fs, weight="bold", va="center")
        ax.text(0.55, y + 0.017, pt, color=FG, fontsize=12.5, va="center")
        ax.text(0.55, y - 0.021, en, color=MUTED, fontsize=10, va="center")
        ax.plot([0.08, 0.92], [y - 0.062, y - 0.062], color=GRID, lw=1)
        y -= 0.115

    ax.text(0.08, 0.185, "A base estava limpa.", color=FG,
            fontsize=19, weight="bold", va="center")
    ax.text(0.08, 0.135, "O problema era pior  ·  The problem was worse",
            color=WARN, fontsize=11.5, va="center")
    rodape(ax, 2)
    return fig


# ---------------------------------------------------------------- SLIDE 3
def carregar_idades():
    if DB_URL:
        import pandas as pd
        from sqlalchemy import create_engine
        q = """
            SELECT EXTRACT(EPOCH FROM (now() - data_abertura)) / 31557600.0 AS anos
            FROM empresas_gold
            WHERE data_abertura BETWEEN '1900-01-01' AND now()
        """
        return pd.read_sql(q, create_engine(DB_URL))["anos"].values
    rng = np.random.default_rng(7)
    base = rng.lognormal(np.log(MEDIANA), 1.62, 900_000)
    return base[base < 130]


def slide3():
    idades = carregar_idades()
    fig, ax = novo_slide()

    ax.text(0.08, 0.925, "THE REAL DISTRIBUTION", color=ACCENT,
            fontsize=11, weight="bold")
    ax.text(0.08, 0.892, "A distribuição real", color=MUTED, fontsize=10.5)
    ax.text(0.08, 0.845, "A média esconde a distribuição",
            color=FG, fontsize=16, weight="bold", va="top")

    g = fig.add_axes([0.11, 0.335, 0.81, 0.44]); g.set_facecolor(BG)
    g.hist(idades, bins=110, range=(0, 60), color=ACCENT, edgecolor="none", alpha=0.92)
    g.axvline(MEDIANA, color=OK,   lw=2.6)
    g.axvline(MEDIA,   color=WARN, lw=2.6, ls="--")
    g.text(MEDIANA - 1.2, g.get_ylim()[1] * 0.97, f"Mediana\n{MEDIANA:.1f} anos".replace(".", ","),
           color=OK, fontsize=11.5, weight="bold", va="top", ha="right")
    g.text(MEDIA + 1.4, g.get_ylim()[1] * 0.97, f"Média\n{MEDIA:.1f} anos".replace(".", ","),
           color=WARN, fontsize=11.5, weight="bold", va="top")
    g.set_xlabel("Idade da empresa (anos)  ·  Company age (years)",
                 color=MUTED, fontsize=10, labelpad=8)
    g.set_yticks([])
    g.tick_params(colors=MUTED, labelsize=10)
    for s in ("top", "right", "left"):
        g.spines[s].set_visible(False)
    g.spines["bottom"].set_color(GRID)

    ax.text(0.08, 0.245,
            "Metade das empresas brasileiras tem menos de 3 anos.\n"
            "Uma cauda de 10% com 37+ anos triplica a média.",
            color=FG, fontsize=11.5, va="top", linespacing=1.7)

    ax.text(0.08, 0.145, "0% de datas inválidas ≠ 0% de conclusões inválidas",
            color=ACCENT, fontsize=11.5, weight="bold", va="center")
    ax.text(0.08, 0.108, "Data quality validates the data — not the question.",
            color=MUTED, fontsize=10.5, va="center")
    rodape(ax, 3)
    return fig


if __name__ == "__main__":
    figs = [slide1(), slide2(), slide3()]
    with PdfPages("carrossel_44_5.pdf") as pdf:
        for f in figs:
            pdf.savefig(f, facecolor=BG)
    for i, f in enumerate(figs, 1):
        f.savefig(f"slide{i}.png", facecolor=BG, dpi=DPI)
    print("ok: carrossel_44_5.pdf + slide1..3.png")
