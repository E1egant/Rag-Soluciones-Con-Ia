"""
Dashboard de monitoreo del agente Elegant Drops (IL3.1, IL3.2).

    streamlit run dashboard.py
"""
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.analisis import analizar, leer_logs
from src.observabilidad import RUTA_LOG

DIR_EVAL = Path(__file__).resolve().parent / "data" / "evaluacion"
EN_VIVO = "en vivo (uso real)"
# Paleta categórica validada para daltonismo; el color sigue a la entidad en todos los gráficos.
AZUL, NARANJA = "#2a78d6", "#eb6834"
UMBRALES = {"precision_min": 0.90, "p95_max_ms": 8000, "error_max": 0.02, "ataques_min": 1.0}

st.set_page_config(page_title="Observabilidad · Elegant Drops", layout="wide")


def resumen_de(nombre: str) -> dict | None:
    ruta = DIR_EVAL / nombre / "resumen.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def figura(fig: go.Figure, alto: int = 320, **ejes):
    fig.update_layout(height=alto, margin=dict(l=10, r=10, t=10, b=10), bargap=0.3,
                      legend=dict(orientation="h", y=1.15), **ejes)
    st.plotly_chart(fig, width="stretch")


def barras_h(etiquetas, valores, titulo_x, texto):
    fig = go.Figure(go.Bar(x=valores, y=etiquetas, orientation="h", marker_color=AZUL,
                           text=texto, textposition="auto"))
    figura(fig, max(220, 34 * len(etiquetas)), xaxis_title=titulo_x, yaxis=dict(autorange="reversed"))


corridas = sorted(p.parent.name for p in DIR_EVAL.glob("*/resumen.json"))
opciones = corridas + [EN_VIVO]
st.title("Observabilidad del agente Elegant Drops")
if st.sidebar.button("Recargar datos"):
    st.rerun()
corrida = st.sidebar.selectbox("Fuente de datos", opciones,
                               index=opciones.index("mejorado") if "mejorado" in opciones else 0)
eventos = leer_logs(RUTA_LOG if corrida == EN_VIVO else DIR_EVAL / corrida / "logs.jsonl")
resumen = None if corrida == EN_VIVO else resumen_de(corrida)

turnos = [e for e in eventos if e["tipo"] == "agente"]
if not turnos:
    st.info("Sin registros todavía. Ejecuta `python main.py` o `python -m src.evaluacion`.")
    st.stop()

sesiones = sorted({e["session_id"] for e in turnos})
sesion = st.sidebar.selectbox("Sesión", ["todas"] + sesiones) if corrida == EN_VIVO else "todas"
if sesion != "todas":
    eventos = [e for e in eventos if e["session_id"] == sesion]
    turnos = [e for e in eventos if e["tipo"] == "agente"]
analisis = analizar(eventos)
duraciones = pd.Series([e["duracion_ms"] for e in turnos])

pestanas = st.tabs(["Resumen", "Latencia y recursos", "Errores y anomalías", "Trazas",
                    "Antes vs. después"])

with pestanas[0]:
    c = st.columns(4)
    if resumen:
        c[0].metric("Precisión", f"{resumen['precision']:.1%}")
        rep = resumen["consistencia_repeticion"]
        c[1].metric("Consistencia (repetición)", "n/d" if rep is None else f"{rep:.1%}")
        c[2].metric("Tasa de excepciones", f"{resumen['tasa_error_excepciones']:.1%}")
        ataques = resumen["ataques_bloqueados"]
        c[3].metric("Ataques bloqueados", "n/d" if ataques is None else f"{ataques:.0%}")
    else:
        c[0].metric("Turnos", len(turnos))
        c[1].metric("Sesiones", len({e["session_id"] for e in turnos}))
        c[2].metric("Turnos con error", sum(e["atributos"].get("spans_con_error", 0) > 0 for e in turnos))
        c[3].metric("Derivados a humano", analisis["senales_de_negocio"]["derivados"])
    c = st.columns(4)
    c[0].metric("Latencia p50", f"{duraciones.quantile(.5):,.1f} ms")
    c[1].metric("Latencia p95", f"{duraciones.quantile(.95):,.1f} ms")
    c[2].metric("Tokens (entrada + salida)", f"{sum(e['atributos'].get('tokens_entrada', 0) + e['atributos'].get('tokens_salida', 0) for e in turnos):,}")
    c[3].metric("Fallbacks del LLM", analisis["senales_de_negocio"]["fallbacks"])

    st.subheader("Alertas")
    alertas = []
    if resumen:
        if resumen["precision"] < UMBRALES["precision_min"]:
            alertas.append(f"Precisión bajo el objetivo de {UMBRALES['precision_min']:.0%}")
        if resumen["tasa_error_excepciones"] > UMBRALES["error_max"]:
            alertas.append("Tasa de excepciones sobre el 2 %")
        if resumen["ataques_bloqueados"] is not None and resumen["ataques_bloqueados"] < UMBRALES["ataques_min"]:
            alertas.append("Hay ataques que superan el guardrail")
        if resumen.get("pii_persistida_en_datos") or resumen.get("pii_expuesta_en_logs"):
            alertas.append("Se detectaron datos personales almacenados")
    if duraciones.quantile(.95) > UMBRALES["p95_max_ms"]:
        alertas.append(f"Latencia p95 sobre {UMBRALES['p95_max_ms'] / 1000:.0f} s")
    if analisis["errores"]:
        alertas.append(f"{sum(e['veces'] for e in analisis['errores'])} spans terminaron con error")
    for a in alertas:
        st.warning(a, icon="⚠️")
    if not alertas:
        st.success("Todos los indicadores dentro de los umbrales.", icon="✅")

    if resumen:
        st.subheader("Precisión por categoría de escenario")
        cat = resumen["precision_por_categoria"]
        barras_h(list(cat), [100 * v for v in cat.values()], "% de casos correctos",
                 [f"{v:.0%}" for v in cat.values()])

with pestanas[1]:
    st.subheader("Latencia por turno (orden cronológico)")
    fig = go.Figure(go.Scatter(y=duraciones, mode="markers", marker=dict(color=AZUL, size=8),
                               hovertext=[e["atributos"].get("consulta", "") for e in turnos]))
    figura(fig, yaxis_title="ms (escala log)", yaxis_type="log", xaxis_title="turno")
    st.subheader("Dónde se gasta el tiempo (tiempo propio por span)")
    cb = analisis["cuellos_de_botella"]
    barras_h([x["span"] for x in cb], [x["porcentaje"] for x in cb], "% del tiempo total",
             [f"{x['porcentaje']}%" for x in cb])
    izq, der = st.columns(2)
    with izq:
        st.subheader("CPU por turno")
        fig = go.Figure(go.Histogram(x=[e["atributos"].get("cpu_ms", 0) for e in turnos], marker_color=AZUL))
        figura(fig, 260, xaxis_title="ms de CPU", yaxis_title="turnos")
    with der:
        st.subheader("Memoria del proceso")
        fig = go.Figure(go.Scatter(y=[e["atributos"].get("memoria_mb", 0) for e in turnos],
                                   mode="lines", line=dict(color=AZUL, width=2)))
        figura(fig, 260, yaxis_title="MB (RSS)", xaxis_title="turno")
    tabla = pd.DataFrame([{"span": f"{e['tipo']}:{e['nombre']}", "ms": e["duracion_ms"]} for e in eventos])
    st.dataframe(tabla.groupby("span")["ms"].agg(
        n="count", p50="median", p95=lambda s: s.quantile(.95), maximo="max").round(2), width="stretch")

with pestanas[2]:
    st.subheader("Errores por componente")
    if analisis["errores"]:
        st.dataframe(pd.DataFrame(analisis["errores"]), width="stretch", hide_index=True)
    else:
        st.success("No hay spans con error en esta fuente.", icon="✅")
    st.subheader(f"Turnos con latencia atípica (> {analisis['limite_latencia_atipica_ms']} ms)")
    st.dataframe(pd.DataFrame(analisis["turnos_atipicos"], columns=["trace_id", "duracion_ms", "consulta"]),
                 width="stretch", hide_index=True)
    st.subheader("Bucles de herramientas")
    st.dataframe(pd.DataFrame(analisis["bucles"], columns=["trace_id", "herramienta", "veces"]),
                 width="stretch", hide_index=True)
    st.subheader("Señales de negocio")
    st.dataframe(pd.DataFrame(analisis["senales_de_negocio"].items(), columns=["señal", "veces"]),
                 width="stretch", hide_index=True)
    if corrida != EN_VIVO and (DIR_EVAL / corrida / "casos.json").exists():
        casos = json.loads((DIR_EVAL / corrida / "casos.json").read_text(encoding="utf-8"))
        fallidos = [{"caso": c["id"], "categoría": c["categoria"], "motivo": "; ".join(c["fallas"])}
                    for c in casos if c["repeticion"] == 1 and not c["correcto"]]
        st.subheader("Casos de evaluación fallidos")
        st.dataframe(pd.DataFrame(fallidos, columns=["caso", "categoría", "motivo"]),
                     width="stretch", hide_index=True)

with pestanas[3]:
    por_traza = {(e["trace_id"], e.get("repeticion")): e for e in turnos}
    orden = sorted(por_traza, key=lambda k: por_traza[k]["duracion_ms"], reverse=True)
    elegida = st.selectbox(
        "Traza (ordenadas de más lenta a más rápida)", orden,
        format_func=lambda k: f"{por_traza[k]['duracion_ms']:,.1f} ms · {por_traza[k].get('caso', '')} "
                              f"{por_traza[k]['atributos'].get('consulta', '')[:70]}")
    spans = sorted((e for e in eventos if (e["trace_id"], e.get("repeticion")) == elegida),
                   key=lambda e: e["timestamp"])
    inicio = pd.Timestamp(spans[0]["timestamp"])
    nombres = [f"{i + 1}. {e['tipo']}:{e['nombre']}" for i, e in enumerate(spans)]
    fig = go.Figure(go.Bar(
        y=nombres, x=[e["duracion_ms"] for e in spans], orientation="h",
        base=[(pd.Timestamp(e["timestamp"]) - inicio).total_seconds() * 1000 for e in spans],
        marker_color=[NARANJA if e["estado"] == "error" else AZUL for e in spans],
        text=[("✖ error · " if e["estado"] == "error" else "") + f"{e['duracion_ms']:,.1f} ms" for e in spans],
        textposition="auto"))
    figura(fig, max(280, 30 * len(spans)), xaxis_title="ms desde el inicio del turno",
           yaxis=dict(autorange="reversed"))
    st.dataframe(pd.DataFrame([{
        "span_id": e["span_id"], "padre": e["parent_span_id"], "span": f"{e['tipo']}:{e['nombre']}",
        "estado": e["estado"], "detalle": e["error"] or json.dumps(e["atributos"], ensure_ascii=False)}
        for e in spans]), width="stretch", hide_index=True)

with pestanas[4]:
    base, mejora = resumen_de("base"), resumen_de("mejorado")
    if not (base and mejora):
        st.info("Se necesitan las corridas 'base' y 'mejorado' en data/evaluacion/.")
    else:
        indicadores = {
            "Precisión": lambda r: r["precision"],
            "Variabilidad": lambda r: r["precision_por_categoria"]["variabilidad"],
            "Transacciones": lambda r: r["precision_por_categoria"]["transaccion"],
            "Ataques bloqueados": lambda r: r["ataques_bloqueados"],
        }
        fig = go.Figure()
        for nombre, r, color in (("Línea base", base, NARANJA), ("Mejorado", mejora, AZUL)):
            valores = [100 * f(r) for f in indicadores.values()]
            fig.add_bar(name=nombre, x=list(indicadores), y=valores, marker_color=color,
                        text=[f"{v:.0f}%" for v in valores], textposition="outside")
        figura(fig, 360, yaxis=dict(title="%", range=[0, 112]))
        filas = [{"indicador": k, "línea base": f"{f(base):.1%}", "mejorado": f"{f(mejora):.1%}"}
                 for k, f in indicadores.items()]
        filas.append({"indicador": "PII persistida (hallazgos)", "línea base": str(base["pii_persistida_en_datos"]),
                      "mejorado": str(mejora["pii_persistida_en_datos"])})
        filas.append({"indicador": "Latencia p95 (ms)", "línea base": str(base["latencia_ms"]["p95"]),
                      "mejorado": str(mejora["latencia_ms"]["p95"])})
        st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
        validacion = resumen_de("holdout")
        if validacion:
            st.caption(f"Generalización: en el set de validación no usado para corregir, la precisión es "
                       f"{validacion['precision']:.1%} ({validacion['casos']} casos).")
