"""
Módulo de ingesta.

Convierte las dos fuentes de datos del caso Elegant Drops en documentos
homogéneos para el índice de recuperación:

  - Fuente interna: catálogo de productos (data/catalogo.json), que incluye
    precio, stock y formatos disponibles.
  - Fuente externa: notas olfativas y descripciones curadas (data/notas_externas.json),
    que simulan reseñas/fichas técnicas públicas de perfumería.

Cada documento resultante mantiene su procedencia (`fuente`) para trazabilidad,
tal como exige IL1.4 del encargo.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Documento:
    id: str
    texto: str
    fuente: str  # "interna" | "externa"
    metadata: dict = field(default_factory=dict)


def _texto_desde_producto(p: dict) -> str:
    notas = ", ".join(p["notas_salida"] + p["notas_corazon"] + p["notas_fondo"])
    disponibilidad = "disponible" if p["stock"] else "agotado"
    return (
        f"{p['nombre']} de {p['marca']} ({p['concentracion']}, {p['genero']}). "
        f"Notas olfativas: {notas}. "
        f"Formatos en decant: {', '.join(str(m) + 'ml' for m in p['formatos_ml'])}. "
        f"Precio referencia 10ml: ${p['precio_10ml_clp']} CLP. "
        f"Estado de stock: {disponibilidad}."
    )


def cargar_catalogo_interno() -> list[Documento]:
    with open(DATA_DIR / "catalogo.json", encoding="utf-8") as f:
        productos = json.load(f)
    return [
        Documento(
            id=p["id"],
            texto=_texto_desde_producto(p),
            fuente="interna",
            metadata=p,
        )
        for p in productos
    ]


def cargar_fuente_externa() -> list[Documento]:
    path = DATA_DIR / "notas_externas.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        notas = json.load(f)
    return [
        Documento(
            id=n["id"],
            texto=n["texto"],
            fuente="externa",
            metadata={"referencia": n.get("referencia", "")},
        )
        for n in notas
    ]


def cargar_documentos() -> list[Documento]:
    """Combina fuentes internas y externas en un único corpus para indexar."""
    return cargar_catalogo_interno() + cargar_fuente_externa()
