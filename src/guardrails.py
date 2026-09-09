"""
Guardrails de entrada (IL1.3 — control de contexto y condiciones de precisión).

Valida la consulta del cliente antes de que entre al pipeline RAG:
  - Rechaza cadenas vacías o excesivamente largas (protección de costos/abuso).
  - Detecta intentos de instrucción al sistema ("ignora tus instrucciones",
    "actúa como", etc.) para mitigar prompt injection básica desde el canal
    de chat, dado que la entrada es texto libre de un tercero no confiable.
  - No procesa datos sensibles evidentes (ej. números de tarjeta) — si se
    detectan, la consulta se corta y se deriva a un humano por WhatsApp.
"""
import re

PATRONES_INYECCION = [
    r"ignora(?:r)?\s+(tus|las)\s+instrucciones",
    r"eres\s+ahora",
    r"actúa\s+como\s+(?!.*asesor)",
    r"system\s*prompt",
]

PATRON_TARJETA = r"\b(?:\d[ -]*?){13,16}\b"

MAX_LARGO_CONSULTA = 500


class ConsultaRechazada(Exception):
    pass


def validar_consulta(texto: str) -> str:
    texto = (texto or "").strip()
    if not texto:
        raise ConsultaRechazada("La consulta está vacía.")
    if len(texto) > MAX_LARGO_CONSULTA:
        raise ConsultaRechazada("La consulta excede el largo permitido.")
    for patron in PATRONES_INYECCION:
        if re.search(patron, texto, flags=re.IGNORECASE):
            raise ConsultaRechazada(
                "Se detectó un posible intento de manipulación de instrucciones."
            )
    if re.search(PATRON_TARJETA, texto):
        raise ConsultaRechazada(
            "La consulta contiene un posible número de tarjeta; se deriva a un asesor humano."
        )
    return texto
