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

from . import observabilidad as obs
from .context_builder import rerank
from .memoria import MemoriaLargoPlazo
from .normalizacion import detectar_genero, expandir, productos_mencionados
from .retriever import Retriever
from .seguridad import redactar

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUTA_RESERVAS = DATA_DIR / "reservas.jsonl"
RUTA_DERIVACIONES = DATA_DIR / "derivaciones.jsonl"


def _append_jsonl(ruta: Path, entrada: dict) -> None:
    entrada = {"timestamp": datetime.now(timezone.utc).isoformat(), **entrada}
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")


def _productos(retriever: Retriever) -> dict[str, dict]:
    return {d.id: d.metadata for d in retriever.documentos if d.fuente == "interna"}


def crear_herramientas(cliente_id: str, retriever: Retriever, memoria: MemoriaLargoPlazo,
                       ruta_reservas: Path = RUTA_RESERVAS,
                       ruta_derivaciones: Path = RUTA_DERIVACIONES,
                       autorizados: set[str] | None = None) -> list:
    """autorizados: ids que el cliente identificó en este turno (None = sin restricción)."""
    productos = _productos(retriever)
    reservadas: set[tuple[str, int]] = set()

    @tool
    def buscar_catalogo(consulta: str, max_precio_10ml: int = 0, incluir_agotados: bool = False) -> str:
        """Busca decants por notas olfativas, marca o estilo en el catálogo interno y fichas externas.
        max_precio_10ml: tope en CLP para el formato de 10ml (0 = sin tope).
        incluir_agotados=True cuando el cliente pide un producto específico, para informar su estado real.
        Devuelve id, precio, stock y notas; úsalo antes de recomendar o reservar."""
        # Si nombran un producto hay que informar su estado real aunque esté agotado: el LLM
        # llegó a decir que un producto agotado no existía en el catálogo.
        nombrados = productos_mencionados(consulta, productos)
        with obs.span("retriever", "tfidf.buscar") as sp:
            resultados = rerank(retriever.buscar(expandir(consulta), k=6, solo_disponibles=not incluir_agotados))
            genero = detectar_genero(consulta)
            if genero:
                resultados = [r for r in resultados
                              if r.documento.metadata.get("genero", genero) in (genero, "unisex")]
            if sp:
                sp.atributos.update(documentos=len(resultados),
                                    score_max=round(max((r.score for r in resultados), default=0), 4))
        textos = {d.id: d.texto for d in retriever.documentos}
        lineas = [f"[{i}] {textos[i]}" for i in nombrados]
        for r in resultados:
            d = r.documento
            if d.id in nombrados:
                continue
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
        if autorizados is not None and id_producto not in autorizados:
            return (f"NO_AUTORIZADA: el cliente no ha pedido ni confirmado {p['nombre']}; "
                    "pregúntale antes de reservarlo")
        if not p["stock"]:
            return f"RECHAZADA: {p['nombre']} está agotado; busca una alternativa"
        if formato_ml not in p["formatos_ml"]:
            return f"RECHAZADA: formato {formato_ml}ml no existe; disponibles {p['formatos_ml']}"
        # Si el LLM falla tras reservar, el ejecutor de respaldo reutiliza estas herramientas.
        if (id_producto, formato_ml) not in reservadas:
            _append_jsonl(ruta_reservas, {"cliente_id": cliente_id, "producto": id_producto,
                                          "formato_ml": formato_ml})
            reservadas.add((id_producto, formato_ml))
        return f"RESERVA_OK: {p['nombre']} {formato_ml}ml para {cliente_id}"

    @tool
    def guardar_preferencia(hecho: str) -> str:
        """Guarda en memoria de largo plazo un dato duradero del cliente (gusto, presupuesto, alergia, género)."""
        memoria.guardar(cliente_id, redactar(hecho))
        return "PREFERENCIA_GUARDADA"

    @tool
    def recordar_preferencias(consulta: str) -> str:
        """Recupera de la memoria de largo plazo las preferencias del cliente más relevantes a la consulta."""
        hechos = memoria.recuperar(cliente_id, consulta)
        return "\n".join(f"- {h}" for h in hechos) or "SIN_PREFERENCIAS"

    @tool
    def derivar_a_humano(motivo: str) -> str:
        """Deriva el caso a un asesor humano (reclamos, pagos, falta de información en el catálogo)."""
        _append_jsonl(ruta_derivaciones, {"cliente_id": cliente_id, "motivo": redactar(motivo)})
        return "DERIVADO"

    return [buscar_catalogo, consultar_stock, comparar_productos, reservar_decant,
            guardar_preferencia, recordar_preferencias, derivar_a_humano]
