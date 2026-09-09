"""
Re-ranking y ensamblado de contexto.

El re-ranker aplica reglas de negocio simples por sobre el score de similitud
(ej. priorizar fuente interna sobre externa, ya que precio/stock son
autoritativos solo en el catálogo propio). El ensamblador construye el bloque
de contexto que se inyecta al prompt, controlando un límite de caracteres para
evitar exceder la ventana de contexto del modelo (IL1.3: control de contexto).
"""
from .retriever import ResultadoRecuperacion

PESO_FUENTE_INTERNA = 1.15  # prioriza precio/stock reales sobre reseñas externas
LIMITE_CARACTERES_CONTEXTO = 2500


def rerank(resultados: list[ResultadoRecuperacion]) -> list[ResultadoRecuperacion]:
    ajustados = []
    for r in resultados:
        factor = PESO_FUENTE_INTERNA if r.documento.fuente == "interna" else 1.0
        ajustados.append(ResultadoRecuperacion(documento=r.documento, score=r.score * factor))
    ajustados.sort(key=lambda r: r.score, reverse=True)
    return ajustados


def ensamblar_contexto(resultados: list[ResultadoRecuperacion]) -> str:
    bloques = []
    total = 0
    for r in resultados:
        etiqueta = "[CATÁLOGO INTERNO]" if r.documento.fuente == "interna" else "[FUENTE EXTERNA]"
        bloque = f"{etiqueta} {r.documento.texto}"
        if total + len(bloque) > LIMITE_CARACTERES_CONTEXTO:
            break
        bloques.append(bloque)
        total += len(bloque)
    return "\n".join(bloques)
