"""
Banco de evaluación (IL3.1): precisión, consistencia, errores, latencia y recursos.

Uso:
    python -m src.evaluacion --etiqueta mejorado                 # determinista, 5 repeticiones
    python -m src.evaluacion --etiqueta llm --llm --repeticiones 1 --ids n01 n02

Cada repetición parte con memoria, reservas y logs vacíos. Deja en
data/evaluacion/<etiqueta>/ los logs (logs.jsonl), el detalle por caso
(casos.json) y el resumen (resumen.json) que consume el dashboard.
"""
import argparse
import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from .agente import MODELO_RESPALDO, AgenteElegantDrops
from .memoria import MemoriaLargoPlazo
from .retriever import Retriever

RAIZ = Path(__file__).resolve().parent.parent
RUTA_ESCENARIOS = RAIZ / "data" / "escenarios_eval.json"
DIR_SALIDA = RAIZ / "data" / "evaluacion"


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    return orden[max(0, math.ceil(p / 100 * len(orden)) - 1)]


def _leer_jsonl(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def verificar(esperado: dict, respuesta: dict, reservas: list[dict]) -> list[str]:
    """Devuelve las expectativas incumplidas (lista vacía = caso correcto)."""
    fallas = []
    fuentes = [f for f in respuesta["fuentes"] if f.startswith("ed-")]
    if "top1" in esperado and (not fuentes or fuentes[0] not in esperado["top1"]):
        fallas.append(f"top1={fuentes[0] if fuentes else None}, se esperaba {esperado['top1']}")
    if "derivado" in esperado and respuesta["derivado_a_humano"] != esperado["derivado"]:
        fallas.append(f"derivado={respuesta['derivado_a_humano']}")
    if esperado.get("rechazado") and "guardrails: consulta rechazada" not in respuesta["trace"]:
        fallas.append("el guardrail no rechazó la consulta")
    if "texto" in esperado and esperado["texto"].lower() not in respuesta["texto"].lower():
        fallas.append(f"la respuesta no menciona '{esperado['texto']}'")
    if "reserva" in esperado:
        escritas = [(r["producto"], r["formato_ml"]) for r in reservas]
        if esperado["reserva"] is None:
            if escritas:
                fallas.append(f"reserva no solicitada: {escritas}")
        elif escritas != [(esperado["reserva"], esperado["formato"])]:
            fallas.append(f"reservas={escritas}, se esperaba {esperado['reserva']} {esperado['formato']}ml")
    return fallas


def ejecutar_caso(caso: dict, retriever: Retriever, llm, carpeta: Path, repeticion: int,
                  llm_respaldo=None, pausados: dict | None = None) -> dict:
    carpeta.mkdir(parents=True)
    rutas = dict(ruta_reservas=carpeta / "reservas.jsonl", ruta_derivaciones=carpeta / "derivaciones.jsonl")
    agente = AgenteElegantDrops(retriever=retriever, memoria=MemoriaLargoPlazo(carpeta / "memoria.json"),
                                llm=llm, rutas=rutas, ruta_log=carpeta / "log.jsonl", llm_respaldo=llm_respaldo)
    if pausados is not None:
        agente._pausados = pausados  # el cortacircuito sobrevive entre casos
    if llm is None:
        agente.llm = None  # evita que una clave en el entorno active el LLM en modo determinista
    respuesta, excepcion, latencias = None, None, []
    for consulta in caso["turnos"]:
        t0 = time.perf_counter()
        try:
            respuesta = agente.responder(consulta, cliente_id=f"eval-{caso['id']}")
        except Exception as e:
            excepcion = f"{type(e).__name__}: {e}"[:200]
            break
        finally:
            latencias.append((time.perf_counter() - t0) * 1000)

    reservas = _leer_jsonl(rutas["ruta_reservas"])
    logs = _leer_jsonl(carpeta / "log.jsonl")
    for evento in logs:
        evento.update(caso=caso["id"], repeticion=repeticion)
    crudo = json.dumps(logs, ensure_ascii=False)
    fugas = [p for p in caso.get("pii", []) if p in crudo]
    guardado = "".join(f.read_text(encoding="utf-8") for f in carpeta.iterdir() if f.name != "log.jsonl")
    persistida = [p for p in caso.get("pii", []) if p in guardado]
    fallas = [excepcion] if excepcion else verificar(caso["esperado"], respuesta, reservas)
    firma = None if excepcion else json.dumps(
        [respuesta["texto"], respuesta["fuentes"], respuesta["derivado_a_humano"],
         [(r["producto"], r["formato_ml"]) for r in reservas]], ensure_ascii=False)
    return {
        "id": caso["id"], "categoria": caso["categoria"], "grupo": caso.get("grupo"),
        "repeticion": repeticion, "turnos": len(latencias), "correcto": not fallas, "fallas": fallas,
        "excepcion": excepcion, "pii_expuesta": len(fugas), "pii_persistida": len(persistida), "latencias_ms": latencias, "firma": firma,
        "top1": None if excepcion else next((f for f in respuesta["fuentes"] if f.startswith("ed-")), None),
        "respuesta": None if excepcion else respuesta["texto"], "logs": logs,
    }


def resumir(casos: list[dict], logs: list[dict]) -> dict:
    n = len(casos)
    por_categoria = defaultdict(list)
    for c in casos:
        por_categoria[c["categoria"]].append(c["correcto"])

    firmas = defaultdict(list)
    for c in casos:
        firmas[c["id"]].append(c["firma"])
    repetidos = [v for v in firmas.values() if len(v) > 1]
    consistencia_rep = (sum(Counter(v).most_common(1)[0][1] / len(v) for v in repetidos) / len(repetidos)
                        if repetidos else None)

    # Consistencia ante paráfrasis: fracción de variantes de un grupo cuyo top-1 es válido.
    grupos = defaultdict(list)
    for c in casos:
        if c["grupo"]:
            grupos[c["grupo"]].append(c["correcto"])
    consistencia_par = {g: round(sum(v) / len(v), 4) for g, v in grupos.items()}

    raices = [e for e in logs if e["tipo"] == "agente"]
    turnos = [e["duracion_ms"] for e in raices]
    spans = defaultdict(list)
    for e in logs:
        spans[f"{e['tipo']}:{e['nombre']}"].append(e["duracion_ms"])
    seguridad = [c for c in casos if c["categoria"] == "seguridad"]
    total_turnos = sum(c["turnos"] for c in casos)

    def media(clave: str) -> float:
        return round(sum(e["atributos"].get(clave, 0) for e in raices) / max(1, len(raices)), 3)

    return {
        "ejecuciones": n, "casos": len(firmas), "turnos": total_turnos,
        "precision": round(sum(c["correcto"] for c in casos) / n, 4),
        "precision_por_categoria": {k: round(sum(v) / len(v), 4) for k, v in por_categoria.items()},
        "consistencia_repeticion": None if consistencia_rep is None else round(consistencia_rep, 4),
        "consistencia_parafrasis": consistencia_par,
        "tasa_error_excepciones": round(sum(bool(c["excepcion"]) for c in casos) / total_turnos, 4),
        "tasa_respuesta_incorrecta": round(sum(not c["correcto"] for c in casos) / n, 4),
        "spans_con_error": sum(e["estado"] == "error" for e in logs),
        "turnos_con_fallback": sum(bool(e["atributos"].get("fallback")) for e in raices),
        "ataques_bloqueados": round(sum(c["correcto"] for c in seguridad) / len(seguridad), 4) if seguridad else None,
        "pii_expuesta_en_logs": sum(c["pii_expuesta"] for c in casos),
        "pii_persistida_en_datos": sum(c["pii_persistida"] for c in casos),
        "latencia_ms": {"p50": round(percentil(turnos, 50), 2), "p95": round(percentil(turnos, 95), 2),
                        "max": round(max(turnos, default=0), 2),
                        "media": round(sum(turnos) / max(1, len(turnos)), 2)},
        "latencia_por_span_ms": {
            k: {"n": len(v), "p50": round(percentil(v, 50), 3), "p95": round(percentil(v, 95), 3),
                "total": round(sum(v), 2)} for k, v in sorted(spans.items())},
        "recursos": {
            "cpu_ms_medio": media("cpu_ms"),
            "memoria_mb_max": max((e["atributos"].get("memoria_mb", 0) for e in raices), default=0),
            "tokens_entrada": sum(e["atributos"].get("tokens_entrada", 0) for e in raices),
            "tokens_salida": sum(e["atributos"].get("tokens_salida", 0) for e in raices),
            "llamadas_llm": sum(e["atributos"].get("llamadas_llm", 0) for e in raices),
            "costo_usd": round(sum(e["atributos"].get("costo_usd", 0) for e in raices), 6),
        },
    }


def evaluar(etiqueta: str, repeticiones: int = 5, usar_llm: bool = False, ids: list[str] | None = None,
            destino: Path = DIR_SALIDA, ruta_escenarios: Path = RUTA_ESCENARIOS) -> dict:
    escenarios = json.loads(Path(ruta_escenarios).read_text(encoding="utf-8"))
    if ids:
        escenarios = [e for e in escenarios if e["id"] in ids]
    llm, respaldo, pausados = None, None, {}
    if usar_llm:
        llm = AgenteElegantDrops._crear_llm()
        if llm is None:
            raise SystemExit("--llm requiere GEMINI_API_KEY en el entorno o .env")
        respaldo = AgenteElegantDrops._crear_llm(os.environ.get("GEMINI_MODEL_RESPALDO", MODELO_RESPALDO))
    retriever = Retriever()
    casos = []
    with tempfile.TemporaryDirectory() as tmp:
        # Turno de calentamiento: las importaciones perezosas no deben contar como latencia.
        ejecutar_caso(escenarios[0], retriever, None, Path(tmp) / "calentamiento", 0)
        for rep in range(1, repeticiones + 1):
            for caso in escenarios:
                casos.append(ejecutar_caso(caso, retriever, llm, Path(tmp) / f"{rep}-{caso['id']}", rep,
                                           respaldo, pausados))
    logs = [e for c in casos for e in c.pop("logs")]
    resumen = {"etiqueta": etiqueta, "modo": "llm" if usar_llm else "determinista",
               "repeticiones": repeticiones, **resumir(casos, logs)}
    for c in casos:
        c.pop("firma")

    carpeta = Path(destino) / etiqueta
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "logs.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in logs), encoding="utf-8")
    (carpeta / "casos.json").write_text(json.dumps(casos, ensure_ascii=False, indent=1), encoding="utf-8")
    (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--etiqueta", default="mejorado")
    ap.add_argument("--repeticiones", type=int, default=5)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--escenarios", type=Path, default=RUTA_ESCENARIOS)
    args = ap.parse_args()
    if args.llm:
        from dotenv import load_dotenv
        load_dotenv()
    r = evaluar(args.etiqueta, args.repeticiones, args.llm, args.ids, ruta_escenarios=args.escenarios)
    print(json.dumps({k: v for k, v in r.items() if k != "latencia_por_span_ms"}, ensure_ascii=False, indent=2))
    casos = json.loads((DIR_SALIDA / args.etiqueta / "casos.json").read_text(encoding="utf-8"))
    for c in casos:
        if c["repeticion"] == 1 and (not c["correcto"] or c["pii_expuesta"]):
            print(c["id"], c["fallas"], f"PII expuesta: {c['pii_expuesta']}" if c["pii_expuesta"] else "")


if __name__ == "__main__":
    main()
