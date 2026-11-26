"""
Protección de datos en la telemetría (IL3.3).

Todo lo que se escribe en logs pasa por aquí: la PII se enmascara y el
identificador del cliente se reemplaza por un seudónimo.
"""
import hashlib
import hmac
import os
import re

# El orden importa: tarjeta antes que teléfono para no fragmentar los dígitos.
PATRONES_PII = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # Grupos fijos: un patrón de "13 a 19 dígitos" libre fusionaba teléfono y RUT contiguos.
    ("TARJETA", re.compile(r"\b(?:(?:\d{4}[ -]?){3}\d{1,7}|\d{4}[ -]?\d{6}[ -]?\d{5}|\d{13,19})\b")),
    ("RUT", re.compile(r"\b\d{1,2}(?:\.?\d{3}){2}\s?-\s?[\dkK]\b")),
    ("TELEFONO", re.compile(r"(?<!\w)(?:\+?56[ -]?)?9[ -]?\d{4}[ -]?\d{4}(?!\d)")),
]


def redactar(texto: str) -> str:
    for etiqueta, patron in PATRONES_PII:
        texto = patron.sub(f"[{etiqueta}]", texto)
    return texto


def seudonimizar(cliente_id: str) -> str:
    # Sin LOG_SALT el seudónimo es adivinable por diccionario; en producción es obligatorio.
    sal = os.environ.get("LOG_SALT", "elegantdrops-dev").encode()
    return "cli_" + hmac.new(sal, cliente_id.encode(), hashlib.sha256).hexdigest()[:12]
