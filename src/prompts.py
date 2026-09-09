"""
Formulación de prompts (IL1.1 / IE2).

Cada plantilla documenta su justificación de diseño en el docstring, tal
como exige el informe (apartado "Formulación de prompts"). Las decisiones
se resumen también en el informe técnico (docs/informe.docx), sección 2.

Convenciones aplicadas a todos los prompts del sistema:
  1. Rol explícito al inicio (system prompt) para fijar tono de marca y
     evitar que el modelo "alucine" fuera del dominio de perfumería.
  2. Instrucción explícita de NO inventar precios/stock que no estén en el
     contexto recuperado — mitiga alucinaciones, requisito central de RAG.
  3. Formato de salida acotado (WhatsApp-friendly: sin markdown pesado,
     con emojis moderados) porque el canal real de atención es WhatsApp.
  4. Instrucción de transparencia: si no hay contexto suficiente, el agente
     debe decirlo y ofrecer derivar a un humano, en vez de responder vacío.
"""

SYSTEM_PROMPT = """\
Eres el asistente virtual de Elegant Drops, una tienda chilena de decants y \
perfumes de nicho. Tu tono es cercano, entendido en perfumería y orientado a \
ayudar al cliente a encontrar el decant correcto según su gusto y presupuesto.

Reglas estrictas:
- Usa ÚNICAMENTE la información del CONTEXTO entregado para precios, stock, \
  formatos y notas olfativas. Nunca inventes un dato que no esté en el contexto.
- Si el contexto no alcanza para responder con seguridad, dilo explícitamente \
  y ofrece derivar la consulta a un asesor humano por WhatsApp.
- Si un producto aparece "agotado" en el contexto, acláralo y sugiere una \
  alternativa disponible con notas olfativas similares.
- Responde en español de Chile, en 3 a 6 líneas, formato apto para WhatsApp \
  (sin tablas, listas simples con guiones si son necesarias).
"""

# Justificación (IL1.1): el prompt de recomendación separa "gusto declarado"
# de "restricciones duras" (presupuesto, género, disponibilidad) porque el
# retriever necesita la consulta reescrita para maximizar recall, mientras
# que el LLM necesita las restricciones explícitas para filtrar candidatos
# en la etapa de generación (defensa en profundidad ante fallos del retriever).
PROMPT_RECOMENDACION = """\
CONTEXTO RECUPERADO:
{contexto}

CONSULTA DEL CLIENTE:
{consulta}

RESTRICCIONES DECLARADAS POR EL CLIENTE (si existen, respétalas estrictamente):
{restricciones}

Tarea: recomienda 1 a 3 opciones del contexto que mejor calcen con la consulta \
y las restricciones. Para cada una, menciona: nombre, marca, 1-2 notas \
olfativas clave, formato/precio disponible. Cierra con una pregunta de \
seguimiento breve para avanzar la venta (ej. tamaño deseado).
"""

# Justificación (IL1.1): prompt de reescritura de consulta (query rewriting).
# Se usa antes del retriever para expandir sinónimos de perfumería
# (ej. "dulce" -> "vainilla, praliné, gourmand") y así mejorar el recall del
# TF-IDF, que es sensible a vocabulario exacto.
PROMPT_REESCRITURA = """\
Reescribe la siguiente consulta de un cliente de perfumería como una lista \
de 3 a 6 palabras clave de búsqueda (notas olfativas, marcas, tipo de \
ocasión), sin explicaciones adicionales, separadas por comas.

Para género, usa EXACTAMENTE una de estas palabras si aplica: masculino, \
femenino, unisex (nunca "hombre" o "mujer").

Consulta original: {consulta}
"""

# Justificación (IL1.1): prompt de consulta vaga. Se activa ANTES del
# retriever (ver guardrails.es_consulta_vaga) cuando la consulta no trae
# suficiente información de negocio (gusto, presupuesto, género, ocasión).
# Evita que el pipeline fuerce una recomendación puntual sobre una coincidencia
# débil del TF-IDF solo porque el score superó el umbral por casualidad —
# es preferible pedir un dato más que arriesgar una respuesta no deseada.
PROMPT_CONSULTA_VAGA = """\
El cliente escribió: "{consulta}"

Es un saludo o una consulta demasiado general para recomendar un decant \
específico todavía. Redacta una respuesta breve y cercana que:
1) salude o reconozca la consulta,
2) pida 1-2 datos concretos para poder recomendar bien: notas olfativas o \
   gusto (dulce, fresco, amaderado, etc.), presupuesto aproximado, género \
   (masculino/femenino/unisex) u ocasión de uso.
No recomiendes ningún producto todavía ni menciones precios.
"""

# Justificación (IL1.1): prompt de manejo de baja confianza. Se activa cuando
# el retriever no supera un umbral de score (ver orchestrator.py), evitando
# que el LLM "rellene" con conocimiento genérico no verificado del catálogo.
PROMPT_SIN_CONTEXTO_SUFICIENTE = """\
El cliente preguntó: "{consulta}"

No se encontró información suficiente en el catálogo ni en las fuentes \
externas para responder con precisión. Redacta una respuesta breve que:
1) reconozca la consulta,
2) indique honestamente que necesitas confirmar con el equipo,
3) ofrezca continuar por WhatsApp con un asesor.
No inventes productos ni precios.
"""
