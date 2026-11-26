"""
Respuestas a una oferta pendiente ("sí", "el segundo", "5ml", "no gracias").

Solo se interpreta como respuesta un mensaje formado íntegramente por palabras de
confirmación, selección o formato: ante cualquier otra palabra se trata como una
consulta nueva, para no reservar por una frase ambigua.
"""
import re

from .normalizacion import PALABRAS_NOMBRE_IGNORADAS, productos_mencionados, sin_tildes

AFIRMACIONES = {"si", "dale", "ok", "okay", "ya", "claro", "confirmo", "confirmado", "perfecto", "bueno",
                "hazlo", "obvio", "reservalo", "reservamelo", "resevalo", "apartalo", "apartamelo"}
RELLENO = {"el", "la", "lo", "ese", "esa", "este", "esta", "quiero", "quisiera", "en", "de", "del", "por",
           "favor", "porfa", "porfavor", "gracias", "reserva", "reservar", "reservame", "uno", "una",
           "un", "formato", "ml", "me", "con", "entonces", "mejor"}
ORDINALES = {"primero": 0, "primer": 0, "primera": 0, "segundo": 1, "segunda": 1}
PATRON_NEGATIVA = r"(no|nop|nada|mejor no|todavia no|ahora no)( (gracias|por ahora|todavia|aun))*"


def _palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", re.sub(r"(\d)\s*ml\b", r"\1 ml", sin_tildes(texto)))


def resolver(consulta: str, pendiente: list[str], productos: dict[str, dict]) -> tuple[str | None, list[str]]:
    """Devuelve ("rechazar", []), ("confirmar", ids posibles) o (None, [])."""
    palabras = _palabras(consulta)
    if not pendiente or not palabras:
        return None, []
    if re.fullmatch(PATRON_NEGATIVA, " ".join(palabras)):
        return "rechazar", []

    nombrados = [i for i in productos_mencionados(consulta, productos) if i in pendiente]
    del_nombre = {w for i in nombrados for w in _palabras(productos[i]["nombre"])} | PALABRAS_NOMBRE_IGNORADAS
    ordinales = [ORDINALES[p] for p in palabras if p in ORDINALES]
    hay_formato = bool(re.search(r"\b\d+\s*ml\b", sin_tildes(consulta)))
    hay_afirmacion = any(p in AFIRMACIONES for p in palabras)
    resto = [p for p in palabras
             if p not in AFIRMACIONES | RELLENO | del_nombre and p not in ORDINALES
             and not (hay_formato and p.isdigit())]
    if resto or not (nombrados or ordinales or hay_formato or hay_afirmacion):
        return None, []
    if nombrados:
        return "confirmar", nombrados
    if ordinales:
        indice = ordinales[0]
        return ("confirmar", [pendiente[indice]]) if indice < len(pendiente) else (None, [])
    return "confirmar", list(pendiente)


CORTESIA = r"((muchas )?gracias|chao|adios|hasta luego|nos vemos|hola|buenas|buenos dias|genial|listo)"


def es_cortesia(consulta: str) -> bool:
    """Saludos y despedidas: buscarlos en el catálogo terminaba derivando a un humano."""
    return bool(re.fullmatch(rf"{CORTESIA}( {CORTESIA})*", " ".join(_palabras(consulta))))
