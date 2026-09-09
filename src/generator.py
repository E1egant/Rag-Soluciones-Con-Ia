"""
Módulo de generación (llamada al LLM).

Soporta dos proveedores de LLM, seleccionados automáticamente según qué
variable de entorno esté configurada (ver .env.example):

  1. GEMINI_API_KEY  -> Google Gemini (gemini-2.5-flash). Recomendado para
     este proyecto: tiene una capa gratuita permanente sin tarjeta de
     crédito, suficiente para desarrollar y defender el encargo
     (ver docs/informe.docx, sección 5.1, para la justificación completa).
  2. ANTHROPIC_API_KEY -> Claude, vía la API de Anthropic (de pago).

Si no hay ninguna clave configurada, el sistema opera en "modo demo":
ejecuta igualmente el pipeline completo de recuperación y ensamblado de
contexto, pero devuelve el prompt final en vez de una respuesta generada.
Esto permite instalar y probar el repositorio (incluidos los tests) sin
credenciales, y no debe usarse como entrega final — para la entrega y la
defensa se debe configurar GEMINI_API_KEY.

El punto de entrada público (`generar_respuesta`) es el único lugar que
conoce el proveedor concreto, de modo que cambiarlo no afecta al resto del
pipeline (bajo acoplamiento, ver justificación de arquitectura en el informe).
"""
import os

MODELO_ANTHROPIC_POR_DEFECTO = "claude-sonnet-4-6"
MODELO_GEMINI_POR_DEFECTO = "gemini-2.5-flash"


def _generar_con_gemini(system_prompt: str, user_prompt: str, modelo: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    respuesta = client.models.generate_content(
        model=modelo,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
        ),
    )
    return respuesta.text


def _generar_con_anthropic(system_prompt: str, user_prompt: str, modelo: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    mensaje = client.messages.create(
        model=modelo,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in mensaje.content if block.type == "text")


def generar_respuesta(system_prompt: str, user_prompt: str, modelo: str | None = None,
                       max_tokens: int = 400) -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return _generar_con_gemini(
            system_prompt, user_prompt, modelo or MODELO_GEMINI_POR_DEFECTO, max_tokens
        )

    if os.environ.get("ANTHROPIC_API_KEY"):
        return _generar_con_anthropic(
            system_prompt, user_prompt, modelo or MODELO_ANTHROPIC_POR_DEFECTO, max_tokens
        )

    # Modo demo/offline: permite ejecutar tests y demos sin credenciales.
    return (
        "[MODO DEMO — falta configurar GEMINI_API_KEY o ANTHROPIC_API_KEY en .env]\n"
        "Esta es la respuesta que generaría el LLM a partir del prompt:\n"
        f"{user_prompt[:400]}..."
    )
