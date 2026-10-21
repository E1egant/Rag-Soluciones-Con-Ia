"""
Herramientas del agente (IE1).

Consulta:      buscar_catalogo, consultar_stock, recordar_preferencias
Razonamiento:  comparar_productos
Escritura:     reservar_decant, guardar_preferencia, derivar_a_humano

Se construyen con `crear_herramientas` para atar `cliente_id`, el retriever y
la memoria sin exponerlos al LLM como parámetros.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

from .context_builder import rerank
from .memoria import MemoriaLargoPlazo
from .retriever import Retriever

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUTA_RESERVAS = DATA_DIR / "reservas.jsonl"
RUTA_DERIVACIONES = DATA_DIR / "derivaciones.jsonl"


def _append_jsonl(ruta: Path, entrada: dict) -> None:
    entrada = {"timestamp": datetime.now(timezone.utc).isoformat(), **entrada}
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


SINONIMOS = {
    "dulce": "vainilla praliné", "vainillado": "vainilla", "fresco": "bergamota marinas",
    "amaderado": "cedro pachuli", "floral": "jazmín azahar", "cafe": "café",
}


def _expandir(consulta: str) -> str:
    extra = [v for k, v in SINONIMOS.items() if k in consulta.lower()]
    return " ".join([consulta, *extra])


def _productos(retriever: Retriever) -> dict[str, dict]:
    return {d.id: d.metadata for d in retriever.documentos if d.fuente == "interna"}


def crear_herramientas(cliente_id: str, retriever: Retriever, memoria: MemoriaLargoPlazo,
                       ruta_reservas: Path = RUTA_RESERVAS,
                       ruta_derivaciones: Path = RUTA_DERIVACIONES) -> list:
    productos = _productos(retriever)

    @tool
    def buscar_catalogo(consulta: str, max_precio_10ml: int = 0, incluir_agotados: bool = False) -> str:
        """Busca decants por notas olfativas, marca o estilo en el catálogo interno y fichas externas.
        max_precio_10ml: tope en CLP para el formato de 10ml (0 = sin tope).
        incluir_agotados=True cuando el cliente pide un producto específico, para informar su estado real.
        Devuelve id, precio, stock y notas; úsalo antes de recomendar o reservar."""
        resultados = rerank(retriever.buscar(_expandir(consulta), k=6, solo_disponibles=not incluir_agotados))
        lineas = []
        for r in resultados:
            d = r.documento
            if d.fuente == "interna":
                if max_precio_10ml and d.metadata["precio_10ml_clp"] > max_precio_10ml:
                    continue
                lineas.append(f"[{d.id}] {d.texto}")
            else:
                lineas.append(f"[{d.id}] (externa) {d.texto}")
        return "\n".join(lineas) or "SIN_RESULTADOS"

    @tool
    def consultar_stock(id_producto: str) -> str:
        """Devuelve stock, formatos (ml) y precio de 10ml de un producto por su id (ej. ed-001)."""
        p = productos.get(id_producto)
        if not p:
            return f"ERROR: producto {id_producto} no existe"
        estado = "DISPONIBLE" if p["stock"] else "AGOTADO"
        return (f"{p['nombre']} ({p['marca']}): {estado}; formatos {p['formatos_ml']} ml; "
                f"10ml ${p['precio_10ml_clp']} CLP")

    @tool
    def comparar_productos(ids: list[str]) -> str:
        """Compara 2 o más productos por id: notas, precio, disponibilidad y la opción más económica disponible."""
        filas, disponibles = [], []
        for i in ids:
            p = productos.get(i)
            if not p:
                filas.append(f"{i}: no existe")
                continue
            notas = ", ".join(p["notas_salida"] + p["notas_corazon"] + p["notas_fondo"])
            filas.append(f"{p['nombre']} ({p['genero']}): ${p['precio_10ml_clp']} 10ml, "
                         f"{'disponible' if p['stock'] else 'agotado'}, notas: {notas}")
            if p["stock"]:
                disponibles.append(p)
        if disponibles:
            barato = min(disponibles, key=lambda p: p["precio_10ml_clp"])
            filas.append(f"Más económico disponible: {barato['nombre']}")
        return "\n".join(filas)

    @tool
    def reservar_decant(id_producto: str, formato_ml: int) -> str:
        """Registra una reserva de un decant para el cliente. Valida stock y formato antes de escribir."""
        p = productos.get(id_producto)
        if not p:
            return f"ERROR: producto {id_producto} no existe"
        if not p["stock"]:
            return f"RECHAZADA: {p['nombre']} está agotado; busca una alternativa"
        if formato_ml not in p["formatos_ml"]:
            return f"RECHAZADA: formato {formato_ml}ml no existe; disponibles {p['formatos_ml']}"
        _append_jsonl(ruta_reservas, {"cliente_id": cliente_id, "producto": id_producto,
                                      "formato_ml": formato_ml})
        return f"RESERVA_OK: {p['nombre']} {formato_ml}ml para {cliente_id}"

    @tool
    def guardar_preferencia(hecho: str) -> str:
        """Guarda en memoria de largo plazo un dato duradero del cliente (gusto, presupuesto, alergia, género)."""
        memoria.guardar(cliente_id, hecho)
        return "PREFERENCIA_GUARDADA"

    @tool
    def recordar_preferencias(consulta: str) -> str:
        """Recupera de la memoria de largo plazo las preferencias del cliente más relevantes a la consulta."""
        hechos = memoria.recuperar(cliente_id, consulta)
        return "\n".join(f"- {h}" for h in hechos) or "SIN_PREFERENCIAS"

    @tool
    def derivar_a_humano(motivo: str) -> str:
        """Deriva el caso a un asesor humano (reclamos, pagos, falta de información en el catálogo)."""
        _append_jsonl(ruta_derivaciones, {"cliente_id": cliente_id, "motivo": motivo})
        return "DERIVADO"

    return [buscar_catalogo, consultar_stock, comparar_productos, reservar_decant,
            guardar_preferencia, recordar_preferencias, derivar_a_humano]
