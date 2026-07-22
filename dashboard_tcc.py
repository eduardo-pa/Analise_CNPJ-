import os
import io
import urllib.request
from datetime import date
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import plotly.express as px
import plotly.graph_objects as go
import json
import bcrypt
from dotenv import load_dotenv
from comparador_regional import render_comparador

# --- CONFIGURAÇÃO DE CONEXÃO ---
# Streamlit Cloud injeta st.secrets; local usa DATABASE_URL do .env.
load_dotenv()


def _url_do_banco():
    """st.secrets tem precedência (deploy); .env é o caminho local."""
    try:
        url = st.secrets.get("db_url")
        if url:
            return url
    except Exception:
        # Sem arquivo de secrets — normal em execução local.
        pass

    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    raise RuntimeError(
        "Credencial ausente. Local: preencha DATABASE_URL no .env. "
        "Deploy: defina db_url em .streamlit/secrets.toml"
    )


engine = create_engine(
    _url_do_banco(),
    pool_pre_ping=True,
    connect_args={"client_encoding": "utf8"},
)

# --- FUNÇÕES DE SEGURANÇA E BANCO ---
def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL
            );
        """))
        conn.commit()
    # Migração idempotente: ignora erro se a coluna já existir
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE usuarios "
                "ADD COLUMN filtros_favoritos TEXT NOT NULL DEFAULT '[]'"
            ))
            conn.commit()
    except Exception:
        pass

def gerar_hash_senha(senha):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha.encode('utf-8'), salt).decode('utf-8')

def verificar_login(email, password):
    try:
        with engine.connect() as conn:
            query = text("SELECT * FROM usuarios WHERE email = :email")
            df = pd.read_sql(query, conn, params={"email": email})
            if not df.empty:
                hash_banco = df.iloc[0]['senha']
                if bcrypt.checkpw(password.encode('utf-8'), hash_banco.encode('utf-8')):
                    return df
        return pd.DataFrame()
    except: return pd.DataFrame()

def cadastrar_usuario(nome, email, password):
    try:
        hash_seguro = gerar_hash_senha(password)
        with engine.connect() as conn:
            query = text("INSERT INTO usuarios (nome, email, senha) VALUES (:nome, :email, :senha)")
            conn.execute(query, {"nome": nome, "email": email, "senha": hash_seguro})
            conn.commit()
        return True
    except: return False

def excluir_conta_db(email):
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM usuarios WHERE email = :email"), {"email": email})
            conn.commit()
        return True
    except: return False

def carregar_favoritos(email: str) -> list:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT filtros_favoritos FROM usuarios WHERE email = :e"),
                {"e": email},
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else []
    except:
        return []

def salvar_favoritos(email: str, favoritos: list) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE usuarios SET filtros_favoritos = :f WHERE email = :e"),
                {"f": json.dumps(favoritos, ensure_ascii=False), "e": email},
            )
            conn.commit()
        return True
    except:
        return False

# --- CACHE DE FILTROS (ESSENCIAL PARA 10M DE LINHAS) ---
@st.cache_data(ttl=3600)
def carregar_opcoes_filtros():
    """Busca as opções de filtros direto das tabelas de referência para ser rápido"""
    try:
        with engine.connect() as conn:
            cnaes = pd.read_sql("SELECT DISTINCT descricao FROM cnaes_referencia ORDER BY 1", conn)
            cidades = pd.read_sql("SELECT DISTINCT descricao FROM municipios_referencia ORDER BY 1", conn)
            return cnaes['descricao'].tolist(), cidades['descricao'].tolist()
    except:
        return [], []

@st.cache_data(ttl=3600)
def carregar_sobrevivencia():
    """Tempo que empresas BAIXADAS permaneceram ativas, por setor.

    Lê da MV mv_sobrevivencia_setor (ver mv_sobrevivencia_setor.sql), que mede
    data_situacao - data_abertura apenas para empresas com situação 'baixada'.

    A versão anterior fazia AVG(hoje - data_abertura) sobre todas as empresas,
    incluindo as já fechadas — o que media idade desde a fundação, não
    sobrevivência. Uma empresa aberta em 1990 e baixada em 1995 contava como
    36 anos em vez de 5.
    """
    sql = text("""
        SELECT setor, total, media, mediana, p05, q1, q3, p95
        FROM mv_sobrevivencia_setor
        ORDER BY mediana DESC
        LIMIT 20
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)

@st.cache_data(ttl=3600)
def carregar_crescimento_uf():
    sql = text("""
        SELECT ano, cod_municipio, nome_municipio, total
        FROM mv_crescimento_municipio
        ORDER BY nome_municipio, ano
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)

@st.cache_data(ttl=3600)
def carregar_data_quality():
    # Uma única passagem na tabela: LEFT JOIN detecta CNAEs não mapeados
    # sem trazer registros individuais para o Python.
    sql = text("""
        SELECT
            COUNT(*)                                                               AS total,
            COUNT(*) FILTER (WHERE e.capital_social IS NULL
                                   OR e.capital_social <= 0)                      AS capital_invalido,
            COUNT(*) FILTER (WHERE e.data_abertura IS NULL
                                   OR e.data_abertura < '1800-01-01'
                                   OR e.data_abertura > CURRENT_DATE)             AS data_invalida,
            COUNT(*) FILTER (WHERE c.codigo IS NULL)                              AS cnae_nao_mapeado
        FROM empresas_gold e
        LEFT JOIN cnaes_referencia c ON e.cnae_fiscal = c.codigo
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn).iloc[0]  # retorna Series com 1 linha

@st.cache_data(ttl=86400)  # GeoJSON do IBGE muda raramente — cache de 24 h
def carregar_geojson_estados():
    url = (
        "https://raw.githubusercontent.com/codeforamerica/"
        "click_that_hood/master/public/data/brazil-states.geojson"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())

@st.cache_data(ttl=3600)
def carregar_contagem_uf():
    with engine.connect() as conn:
        return pd.read_sql(text("""
            SELECT uf, total
            FROM mv_densidade_uf
            ORDER BY total DESC
        """), conn)

@st.cache_data(ttl=3600)
def carregar_bolhas_ano_uf():
    sql = text("""
        SELECT ano, cod_municipio, nome_municipio, total_empresas, capital_medio
        FROM mv_bolhas_ano_municipio
        WHERE ano IN (
            SELECT ano FROM mv_bolhas_ano_municipio
            GROUP BY ano
            ORDER BY SUM(total_empresas) DESC
            LIMIT 10
        )
        ORDER BY ano, nome_municipio
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


@st.cache_data(ttl=3600)
def carregar_treemap_setores():
    sql = text("""
        SELECT divisao_cnae, total_empresas, capital_total
        FROM mv_treemap_setores
        ORDER BY total_empresas DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Inteligência CNPJ Gold",
    page_icon="🏢",
    layout="wide",
)
init_db()

if 'logado' not in st.session_state:
    st.session_state['logado'] = False

# --- INTERFACE DE ACESSO ---
if not st.session_state['logado']:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 Sistema de Inteligência CNPJ")
        t_log, t_cad = st.tabs(["Login", "Criar Conta"])
        
        with t_log:
            e_l = st.text_input("E-mail", key="l_email")
            p_l = st.text_input("Senha", type="password", key="l_pass")
            if st.button("Entrar"):
                u = verificar_login(e_l, p_l)
                if not u.empty:
                    st.session_state['logado'] = True
                    st.session_state['user_nome'] = u.iloc[0]['nome']
                    st.session_state['user_email'] = u.iloc[0]['email']
                    st.rerun()
                else: st.error("Acesso Negado.")
        
        with t_cad:
            n_n = st.text_input("Nome")
            n_e = st.text_input("E-mail")
            n_p = st.text_input("Senha (min 8 carac.)", type="password")
            if st.button("Cadastrar"):
                if len(n_p) >= 8 and cadastrar_usuario(n_n, n_e, n_p):
                    st.success("Cadastrado! Faça o login.")
                else: st.error("Erro no cadastro ou senha curta.")

# --- DASHBOARD LOGADO ---
else:
    # --- SIDEBAR ---
    with st.sidebar:
        st.title(f"👋 Olá, {st.session_state['user_nome']}")
        if st.button("Sair do Sistema", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        
        st.markdown("---")
        st.subheader("🎯 Filtros Rápidos")

        # Carregando filtros via Cache
        lista_cnaes, lista_cidades = carregar_opcoes_filtros()

        # --- Favoritos ---
        favoritos = carregar_favoritos(st.session_state["user_email"])
        if favoritos:
            st.caption("⭐ Favoritos")
            for i, fav in enumerate(favoritos):
                col_btn, col_del = st.columns([4, 1])
                with col_btn:
                    if st.button(
                        fav["nome"], key=f"fav_apply_{i}", use_container_width=True
                    ):
                        st.session_state["setor_sel"] = fav["setor"]
                        st.session_state["cidade_sel"] = fav["cidade"]
                        st.rerun()
                with col_del:
                    if st.button(
                        "✕", key=f"fav_del_{i}", help=f"Remover '{fav['nome']}'"
                    ):
                        favoritos.pop(i)
                        salvar_favoritos(st.session_state["user_email"], favoritos)
                        st.rerun()

        setor_sel = st.selectbox(
            "Setor (CNAE)", ["Todos"] + lista_cnaes, key="setor_sel"
        )
        cidade_sel = st.selectbox(
            "Cidade", ["Todas"] + lista_cidades, key="cidade_sel"
        )

        with st.expander("💾 Salvar como favorito"):
            nome_fav = st.text_input(
                "Nome do favorito",
                key="nome_fav",
                placeholder="ex.: Tech São Paulo",
            )
            if st.button("Salvar", key="btn_salvar_fav", use_container_width=True):
                if nome_fav.strip():
                    favoritos.append(
                        {"nome": nome_fav.strip(), "setor": setor_sel, "cidade": cidade_sel}
                    )
                    if salvar_favoritos(st.session_state["user_email"], favoritos):
                        st.session_state["nome_fav"] = ""
                        st.success("✅ Favorito salvo!")
                        st.rerun()
                    else:
                        st.error("Erro ao salvar o favorito.")
                else:
                    st.warning("Digite um nome para o favorito.")

        st.markdown("---")
        with st.expander("⚙️ Gerenciar Conta"):
            confirma = st.checkbox("Confirmar exclusão")
            if st.button("EXCLUIR MINHA CONTA", type="primary"):
                if confirma and excluir_conta_db(st.session_state['user_email']):
                    st.session_state.clear()
                    st.rerun()

    # --- CORPO DO DASHBOARD ---
    st.markdown("""
<style>
[data-testid="stMetric"] {
    background: linear-gradient(135deg,
        rgba(28, 131, 225, 0.09),
        rgba(28, 131, 225, 0.02));
    border: 1px solid rgba(28, 131, 225, 0.28);
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricLabel"] > div {
    font-size: 0.78rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.75;
}
[data-testid="stMetricValue"] > div {
    font-size: 1.7rem;
    font-weight: 700;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] hr {
    border-color: rgba(255, 255, 255, 0.1);
}
</style>
""", unsafe_allow_html=True)
    st.title("📊 Análise Estratégica")

    try:
        with engine.connect() as conn:
            # SQL OTIMIZADO: Usamos joins e filtros diretos
            sql = """
                SELECT e.razao_social, e.capital_social, e.data_abertura, 
                       c.descricao as setor, m.descricao as cidade
                FROM empresas_gold e
                LEFT JOIN cnaes_referencia c ON e.cnae_fiscal = c.codigo
                LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
                WHERE e.capital_social > 0
            """
            
            params = {}
            if setor_sel != "Todos":
                sql += " AND c.descricao = :setor"
                params["setor"] = setor_sel
            if cidade_sel != "Todas":
                sql += " AND m.descricao = :cidade"
                params["cidade"] = cidade_sel

            # Limitamos em 30k para garantir que o Streamlit não trave os 16GB de RAM
            sql += " LIMIT 30000"

            df = pd.read_sql(text(sql), conn, params=params)

        if not df.empty:
            df['data_abertura'] = pd.to_datetime(df['data_abertura'])
            df['ano'] = df['data_abertura'].dt.year

            # KPIs Superiores
            with st.container(border=True):
                k1, k2, k3 = st.columns(3)
                k1.metric("🏭 Empresas Identificadas", f"{len(df):,}")
                k2.metric("💰 Capital Total", f"R$ {df['capital_social'].sum():,.2f}")
                k3.metric("📐 Média de Capital", f"R$ {df['capital_social'].mean():,.2f}")

            st.divider()

            # GRÁFICOS
            col_esq, col_dir = st.columns([2, 1])
            with col_esq:
                st.subheader("📈 Evolução de Abertura por Ano")
                df_evol = df.groupby('ano').size().reset_index(name='qtd')
                fig_l = px.line(df_evol, x='ano', y='qtd', markers=True, template="plotly_dark")
                st.plotly_chart(fig_l, use_container_width=True)
            
            with col_dir:
                st.subheader("🏙️ Capital por Cidade (Top 10)")
                # Mostra as top cidades no gráfico para não virar bagunça
                df_top_cidades = df.groupby('cidade')['capital_social'].sum().nlargest(10).reset_index()
                fig_p = px.pie(df_top_cidades, names='cidade', values='capital_social', hole=0.4)
                st.plotly_chart(fig_p, use_container_width=True)

            st.divider()

            # TABELA RANKING
            st.subheader("🏆 Maiores Empresas do Recorte")
            n_rank = st.select_slider(
                "Registros exibidos",
                options=[10, 25, 50, 100],
                value=25,
            )

            sql_rank = """
                SELECT e.razao_social, e.capital_social, e.data_abertura,
                       c.descricao AS setor, m.descricao AS cidade
                FROM empresas_gold e
                LEFT JOIN cnaes_referencia      c ON e.cnae_fiscal   = c.codigo
                LEFT JOIN municipios_referencia m ON e.cod_municipio = m.codigo
                WHERE e.capital_social > 0
            """
            params_rank: dict = {}
            if setor_sel != "Todos":
                sql_rank += " AND c.descricao = :setor"
                params_rank["setor"] = setor_sel
            if cidade_sel != "Todas":
                sql_rank += " AND m.descricao = :cidade"
                params_rank["cidade"] = cidade_sel
            sql_rank += " ORDER BY e.capital_social DESC LIMIT :n"
            params_rank["n"] = n_rank

            with engine.connect() as conn_rank:
                df_rank = pd.read_sql(text(sql_rank), conn_rank, params=params_rank)

            st.dataframe(
                df_rank,
                use_container_width=True, hide_index=True,
                column_config={
                    "capital_social": st.column_config.NumberColumn("Capital (R$)", format="R$ %.2f"),
                    "data_abertura": st.column_config.DateColumn("Abertura")
                }
            )

            today = date.today().strftime("%Y%m%d")
            col_csv, col_xlsx = st.columns(2)

            with col_csv:
                st.download_button(
                    label="⬇️ Exportar CSV",
                    data=df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                    file_name=f"empresas_{today}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            with col_xlsx:
                _buf = io.BytesIO()
                df.to_excel(_buf, index=False, engine="openpyxl")
                st.download_button(
                    label="⬇️ Exportar Excel",
                    data=_buf.getvalue(),
                    file_name=f"empresas_{today}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        else:
            st.warning("⚠️ Nenhum dado encontrado. Tente ajustar os filtros.")

    except Exception as e:
        st.error(f"❌ Erro de Processamento: {e}")

    # --- DATA QUALITY ---
    with st.expander("🔍 Qualidade dos Dados — empresas_gold"):
        try:
            dq = carregar_data_quality()
            total = int(dq["total"])

            def _badge(pct: float) -> str:
                if pct < 1:
                    return "🟢 OK"
                if pct < 5:
                    return "🟡 Atenção"
                return "🔴 Crítico"

            col_dq1, col_dq2, col_dq3 = st.columns(3)
            items = [
                (col_dq1, "capital_invalido", "💰 Capital nulo/zero"),
                (col_dq2, "data_invalida",    "📅 Data de abertura inválida"),
                (col_dq3, "cnae_nao_mapeado", "🏭 CNAE não mapeado"),
            ]
            for col, key, label in items:
                n = int(dq[key])
                pct = n / total * 100 if total else 0.0
                col.metric(label, f"{pct:.2f}%", delta=f"{n:,} registros", delta_color="off")
                col.caption(_badge(pct))

            st.caption(
                f"Universo: {total:,} registros em empresas_gold · "
                "Thresholds: 🟢 < 1 %  ·  🟡 1–5 %  ·  🔴 > 5 %"
            )
        except Exception as e_dq:
            st.error(f"❌ Erro ao carregar métricas de qualidade: {e_dq}")

    st.divider()

    # --- TREEMAP DE SETORES ECONÔMICOS ---
    st.title("🌳 Distribuição por Setor Econômico")

    try:
        df_tree = carregar_treemap_setores()

        if not df_tree.empty:
            fig_tree = px.treemap(
                df_tree,
                path=[px.Constant("Brasil"), "divisao_cnae"],
                values="total_empresas",
                color="capital_total",
                color_continuous_scale="Blues",
                hover_data={"capital_total": ":,.0f", "total_empresas": ":,"},
                labels={
                    "total_empresas": "Empresas",
                    "capital_total": "Capital Total (R$)",
                    "divisao_cnae": "Divisão CNAE",
                },
                title="Empresas por Divisão de CNAE",
            )
            fig_tree.update_traces(
                texttemplate="<b>%{label}</b><br>%{value:,} empresas",
                hovertemplate=(
                    "<b>%{label}</b><br>"
                    "Empresas: %{value:,}<br>"
                    "Capital Total: R$ %{customdata[0]:,.0f}<extra></extra>"
                ),
            )
            fig_tree.update_layout(margin=dict(t=50, l=10, r=10, b=10))
            st.plotly_chart(fig_tree, use_container_width=True)
        else:
            st.info("Nenhum setor encontrado para o treemap.")
    except Exception as e_tree:
        st.error(f"❌ Erro ao carregar treemap de setores: {e_tree}")

    st.divider()

    # --- ANÁLISE DE SOBREVIVÊNCIA EMPRESARIAL ---
    st.title("📉 Sobrevivência Empresarial")

    try:
        df_sobrev = carregar_sobrevivencia()

        if not df_sobrev.empty:
            total_baixadas = int(df_sobrev["total"].sum())
            media_geral = (
                (df_sobrev["media"] * df_sobrev["total"]).sum()
                / df_sobrev["total"].sum()
            )
            mediana_geral = (
                (df_sobrev["mediana"] * df_sobrev["total"]).sum()
                / df_sobrev["total"].sum()
            )

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("🏢 Empresas baixadas analisadas", f"{total_baixadas:,}".replace(",", "."))
            col_b.metric("⏱️ Média de sobrevivência", f"{media_geral:.1f} anos")
            col_c.metric("📊 Mediana de sobrevivência", f"{mediana_geral:.1f} anos")

            st.caption(
                "A média é puxada por uma cauda de empresas longevas; a mediana "
                "descreve melhor o caso típico. Por isso as duas aparecem juntas."
            )

            st.subheader("📦 Distribuição do Tempo de Atividade por Setor (Top 20)")

            # Box plot montado a partir dos percentis pré-calculados na MV —
            # não há dados brutos trafegando do banco para o navegador.
            fig_box = go.Figure()
            for _, linha in df_sobrev.iterrows():
                fig_box.add_trace(go.Box(
                    name=str(linha["setor"])[:40],
                    q1=[float(linha["q1"])],
                    median=[float(linha["mediana"])],
                    q3=[float(linha["q3"])],
                    lowerfence=[float(linha["p05"])],
                    upperfence=[float(linha["p95"])],
                    mean=[float(linha["media"])],
                    boxmean=True,
                    marker_color="indianred",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{linha['setor']}</b><br>"
                        f"Empresas baixadas: {int(linha['total']):,}<br>".replace(",", ".") +
                        f"Mediana: {linha['mediana']} anos<br>"
                        f"Média: {linha['media']} anos<extra></extra>"
                    ),
                ))

            fig_box.update_layout(
                template="plotly_dark",
                yaxis_title="Anos de atividade até a baixa",
                xaxis_tickangle=-40,
                height=560,
            )
            st.plotly_chart(fig_box, use_container_width=True)
            st.caption(
                "Tempo entre abertura e baixa, por setor econômico (CNAE). "
                "Apenas empresas com situação cadastral 'baixada' e ao menos "
                "1.000 ocorrências no setor."
            )

        else:
            st.warning(
                "⚠️ Sem dados de sobrevivência. Crie a view materializada com: "
                "`python aplicar_indices.py mv_sobrevivencia_setor.sql`"
            )

    except Exception as e:
        st.error(f"❌ Erro na análise de sobrevivência: {e}")

    st.divider()

    # --- RANKING DE MUNICÍPIOS POR DENSIDADE ---
    st.title("📍 Densidade de Empresas por Município")

    try:
        df_ranking = pd.read_sql(
            text("""
                SELECT nome_municipio, SUM(total) AS total_empresas
                FROM mv_crescimento_municipio
                GROUP BY nome_municipio
                ORDER BY total_empresas DESC
                LIMIT 30
            """),
            engine.connect(),
        )

        if not df_ranking.empty:
            fig_rank = px.bar(
                df_ranking.sort_values("total_empresas"),
                x="total_empresas",
                y="nome_municipio",
                orientation="h",
                template="plotly_dark",
                labels={"total_empresas": "Empresas Abertas (1990–hoje)", "nome_municipio": "Município"},
                color="total_empresas",
                color_continuous_scale="Blues",
            )
            fig_rank.update_layout(
                height=700,
                showlegend=False,
                coloraxis_showscale=False,
                yaxis=dict(tickfont=dict(size=11)),
            )
            st.plotly_chart(fig_rank, use_container_width=True)
            st.caption(
                f"Top 30 municípios por volume de empresas abertas desde 1990. "
                f"Total na base: {df_ranking['total_empresas'].sum():,} empresas."
            )
        else:
            st.warning("⚠️ Sem dados de município disponíveis.")

    except Exception as e:
        st.error(f"❌ Erro ao carregar ranking de municípios: {e}")

    st.divider()

    # --- CRESCIMENTO POR MUNICÍPIO ---
    st.title("🗺️ Crescimento por Município")

    try:
        df_uf = carregar_crescimento_uf()

        if not df_uf.empty:
            top5_nomes = (
                df_uf.groupby("nome_municipio")["total"]
                .sum()
                .nlargest(5)
                .index.tolist()
            )
            todos_nomes_mun = sorted(df_uf["nome_municipio"].unique().tolist())

            mun_sel = st.multiselect(
                "Selecione os Municípios",
                options=todos_nomes_mun,
                default=top5_nomes,
            )

            if mun_sel:
                df_plot = df_uf[df_uf["nome_municipio"].isin(mun_sel)]
                fig_uf = px.line(
                    df_plot,
                    x="ano",
                    y="total",
                    color="nome_municipio",
                    markers=True,
                    template="plotly_dark",
                    labels={"ano": "Ano", "total": "Empresas Abertas", "nome_municipio": "Município"},
                )
                fig_uf.update_layout(height=450)
                st.plotly_chart(fig_uf, use_container_width=True)
            else:
                st.info("Selecione ao menos um município para exibir o gráfico.")

        else:
            st.warning("⚠️ Sem dados de crescimento por município.")

    except Exception as e:
        st.error(f"❌ Erro no gráfico por município: {e}")

    st.divider()

    # --- GRÁFICO DE BOLHAS: ANO × CAPITAL × MUNICÍPIO ---
    st.title("🫧 Capital Social por Ano de Abertura e Município")

    try:
        df_bolhas = carregar_bolhas_ano_uf()

        if not df_bolhas.empty:
            todos_nomes_b = sorted(df_bolhas["nome_municipio"].unique().tolist())
            nomes_bolha = st.multiselect(
                "Filtrar Municípios no gráfico de bolhas",
                options=todos_nomes_b,
                default=todos_nomes_b[:20] if len(todos_nomes_b) > 20 else todos_nomes_b,
                key="mun_bolha",
            )

            df_b = df_bolhas[df_bolhas["nome_municipio"].isin(nomes_bolha)] if nomes_bolha else df_bolhas

            fig_bolha = px.scatter(
                df_b,
                x="ano",
                y="capital_medio",
                size="total_empresas",
                color="nome_municipio",
                hover_name="nome_municipio",
                hover_data={
                    "ano": True,
                    "total_empresas": ":,",
                    "capital_medio": ":,.0f",
                    "nome_municipio": False,
                },
                labels={
                    "ano": "Ano de Abertura",
                    "capital_medio": "Capital Social Médio (R$)",
                    "total_empresas": "Qtd. Empresas",
                    "nome_municipio": "Município",
                },
                size_max=60,
                template="plotly_dark",
                title="Top 10 anos com mais aberturas — tamanho = quantidade de empresas",
            )
            fig_bolha.update_layout(
                height=520,
                xaxis=dict(tickmode="linear", dtick=1),
                yaxis_tickprefix="R$ ",
                yaxis_tickformat=",.0f",
            )
            st.plotly_chart(fig_bolha, use_container_width=True)
        else:
            st.info("Sem dados suficientes para o gráfico de bolhas.")

    except Exception as e_b:
        st.error(f"❌ Erro no gráfico de bolhas: {e_b}")

    st.divider()
    render_comparador()

    # --- RODAPÉ ---
    st.markdown("---")
    st.markdown(
        """
        <div style="
            text-align: center;
            color: #6b7280;
            font-size: 0.78rem;
            padding: 1rem 0 0.25rem;
            line-height: 1.8;
        ">
            📂 Fonte: <b>Receita Federal do Brasil</b>
            &nbsp;·&nbsp; Base CNPJ pública (dados abertos)
            &nbsp;·&nbsp; Competência: <b>Fevereiro/2026</b>
            &nbsp;·&nbsp; Última carga no banco: <b>08/05/2026</b>
        </div>
        """,
        unsafe_allow_html=True,
    )