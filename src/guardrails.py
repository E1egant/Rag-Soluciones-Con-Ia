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

# Palabras que no aportan información de negocio (gusto, presupuesto, género,
# ocasión) aunque no sean stopwords gramaticales — sirven para distinguir
# "perfume" o "algo bueno" (vagas) de "perfume dulce para el día" (específica).
STOPWORDS_GRAMATICALES = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "una", "para", "con", "no", "su", "al", "lo", "como", "mas", "o",
    "pero", "sus", "le", "ya", "este", "mi", "sin", "mis", "esta",
}
PALABRAS_GENERICAS = {
    "perfume", "perfumes", "decant", "decants", "algo", "alguno", "alguna",
    "cosa", "producto", "bueno", "buena", "buenos", "buenas", "hola",
    "ayuda", "ayudame", "quiero", "busco", "necesito", "porfa", "porfavor",
    "gracias", "info", "informacion",
}
MIN_PALABRAS_INFORMATIVAS = 2


class ConsultaRechazada(Exception):
    pass


def es_consulta_vaga(texto: str) -> bool:
    """Detecta consultas demasiado genéricas para recomendar con seguridad.

    Sin este filtro, una consulta como "perfume" puede rozar el umbral de
    similitud del retriever por coincidencia parcial con algún documento y
    el LLM termina recomendando un producto puntual sin base real (riesgo
    de respuesta no deseada por falta de info, no por fallo del modelo).
    """
    palabras = re.findall(r"[a-záéíóúñ0-9]+", texto.lower())
    informativas = [
        p for p in palabras
        if p not in STOPWORDS_GRAMATICALES and p not in PALABRAS_GENERICAS
    ]
    return len(informativas) < MIN_PALABRAS_INFORMATIVAS


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
