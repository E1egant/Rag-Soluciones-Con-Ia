"""
Análisis de logs y trazas (IL3.2): cuellos de botella, errores, bucles y anomalías.

Uso:
    python -m src.analisis data/evaluacion/mejorado/logs.jsonl
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

UMBRAL_BUCLE = 3        # misma herramienta con igual entrada dentro de una traza
FACTOR_ATIPICO = 5      # veces la mediana de latencia de los turnos
MINIMO_ATIPICO_MS = 100  # evita alertar por ruido en turnos de pocos milisegundos


def leer_logs(ruta: Path) -> list[dict]:
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    eventos = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            eventos.append(json.loads(linea))
        except json.JSONDecodeError:
            continue  # una línea truncada no debe impedir analizar el resto
    return eventos


def tiempo_propio(eventos: list[dict]) -> dict[str, float]:
    """Milisegundos exclusivos por span (sin sus hijos): es lo que señala el cuello de botella."""
    hijos = defaultdict(float)
    for e in eventos:
        if e["parent_span_id"]:
            hijos[e["parent_span_id"]] += e["duracion_ms"]
    total = defaultdict(float)
    for e in eventos:
        total[f"{e['tipo']}:{e['nombre']}"] += max(0.0, e["duracion_ms"] - hijos[e["span_id"]])
    return dict(sorted(total.items(), key=lambda x: x[1], reverse=True))


def analizar(eventos: list[dict]) -> dict:
    propio = tiempo_propio(eventos)
    suma = sum(propio.values()) or 1
    errores = Counter(
        (f"{e['tipo']}:{e['nombre']}", (e["error"] or "").split(".")[0][:90])
        for e in eventos if e["estado"] == "error" and e["tipo"] != "evento"
    )
    llamadas = Counter(
        (e["trace_id"], e.get("repeticion"), e["nombre"],
         json.dumps(e["atributos"].get("entrada"), sort_keys=True, ensure_ascii=False))
        for e in eventos if e["tipo"] == "herramienta"
    )
    turnos = [e for e in eventos if e["tipo"] == "agente"]
    mediana = statistics.median([e["duracion_ms"] for e in turnos]) if turnos else 0
    limite = max(MINIMO_ATIPICO_MS, mediana * FACTOR_ATIPICO)
    resultados = Counter(
        e["atributos"].get("resultado", "").split(":")[0]
        for e in eventos if e["tipo"] == "herramienta"
    )
    return {
        "turnos": len(turnos),
        "cuellos_de_botella": [
            {"span": k, "ms_propios": round(v, 2), "porcentaje": round(100 * v / suma, 1)}
            for k, v in list(propio.items())[:5]],
        "errores": [{"span": s, "detalle": d, "veces": n} for (s, d), n in errores.most_common()],
        "bucles": [{"trace_id": t, "herramienta": h, "veces": n}
                   for (t, _, h, _), n in llamadas.items() if n >= UMBRAL_BUCLE],
        "limite_latencia_atipica_ms": round(limite, 1),
        "turnos_atipicos": [
            {"trace_id": e["trace_id"], "duracion_ms": round(e["duracion_ms"], 1),
             "consulta": e["atributos"].get("consulta", "")}
            for e in turnos if e["duracion_ms"] > limite],
        "senales_de_negocio": {
            "sin_resultados": resultados.get("SIN_RESULTADOS", 0),
            "reservas_rechazadas": resultados.get("RECHAZADA", 0) + resultados.get("NO_AUTORIZADA", 0),
            "derivados": sum(bool(e["atributos"].get("derivado")) for e in turnos),
            "rechazados_por_guardrail": sum(bool(e["atributos"].get("rechazado")) for e in turnos),
            "fallbacks": sum(bool(e["atributos"].get("fallback")) for e in turnos),
        },
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("uso: python -m src.analisis <ruta_logs.jsonl>")
    print(json.dumps(analizar(leer_logs(Path(sys.argv[1]))), ensure_ascii=False, indent=2))
