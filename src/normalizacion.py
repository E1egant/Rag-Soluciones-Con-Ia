"""
Normalización de consultas: tildes, errores de tipeo, sinónimos, inglés,
género y referencias a productos. Surge de las fallas de la línea base
(data/evaluacion/base).
"""
import re
import unicodedata
from difflib import get_close_matches

# Raíz de la palabra del cliente -> términos del catálogo.
ALIAS = {
    r"dulce|sweet": "dulce vainilla praliné",
    r"a?vainill\w*|vanilla": "vainilla",
    r"fresc\w*|fresquit\w*|fresh": "bergamota marinas",
    r"marin[oa]s?": "marinas",
    r"amaderad\w*|woody": "cedro pachuli",
    r"floral\w*|flores": "jazmín azahar",
    r"cafe|coffee": "café",
}
GENEROS = {"femenino": r"mujer|femenin\w+|dama|ella", "masculino": r"hombre|masculin\w+|varon|caballero"}
# Palabras válidas a las que se puede corregir un error de tipeo.
VOCABULARIO = ["dulce", "vainilla", "vainillado", "fresco", "amaderado", "floral", "marino", "bergamota",
               "pachuli", "jazmin", "azahar", "praline", "lavanda", "canela", "salvia", "romero",
               "femenino", "masculino"]
PALABRAS_NOMBRE_IGNORADAS = {"di", "in", "la", "le", "de", "el"}
PATRON_REFERENCIA = r"\b(\w*(reserv|apart|compr)\w*lo|lo quiero|ese|este|esa|esta|el primero)\b"


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", sin_tildes(texto))


def corregir(consulta: str) -> list[str]:
    """Correcciones de tipeo para palabras que se parecen mucho al vocabulario."""
    extra = []
    for palabra in _palabras(consulta):
        if len(palabra) >= 5 and palabra not in VOCABULARIO:
            cercana = get_close_matches(palabra, VOCABULARIO, n=1, cutoff=0.8)
            extra += cercana
    return extra


def expandir(consulta: str) -> str:
    """Agrega a la consulta los términos de catálogo equivalentes, sin borrar lo que dijo el cliente."""
    # "5ml" calza con la lista de formatos de cualquier ficha y generaba candidatos falsos.
    base = re.sub(r"\b\d+\s*ml\b", " ", consulta, flags=re.IGNORECASE)
    correcciones = corregir(base)
    palabras = _palabras(base) + correcciones
    extra = [terminos for patron, terminos in ALIAS.items()
             if any(re.fullmatch(patron, p) for p in palabras)]
    return " ".join([base, *correcciones, *extra])


def detectar_genero(consulta: str) -> str | None:
    palabras = _palabras(consulta) + corregir(consulta)
    hallados = [g for g, patron in GENEROS.items() if any(re.fullmatch(patron, p) for p in palabras)]
    return hallados[0] if len(hallados) == 1 else None


def productos_mencionados(consulta: str, productos: dict[str, dict]) -> list[str]:
    """Ids cuyo nombre aparece en la consulta (basta con dos palabras distintivas del nombre)."""
    dichas = set(_palabras(consulta))
    hallados = []
    for id_producto, p in productos.items():
        nombre = [w for w in _palabras(p["nombre"]) if w not in PALABRAS_NOMBRE_IGNORADAS]
        if nombre and len(dichas.intersection(nombre)) >= min(2, len(nombre)):
            hallados.append(id_producto)
    return hallados


def es_referencia(consulta: str) -> bool:
    """El cliente alude a algo ya conversado ("resérvalo", "ese")."""
    return bool(re.search(PATRON_REFERENCIA, sin_tildes(consulta)))
