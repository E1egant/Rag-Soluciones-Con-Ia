# Rag-Soluciones-Con-Ia

### Asistente RAG — Elegant Drops

Prototipo de agente conversacional con recuperación aumentada (RAG) para
**Elegant Drops** (tienda chilena de decants de perfumes, elegantdrops.cl).
Desarrollado para la Evaluación Parcial N°1 de Ingeniería de Soluciones con IA
(ISY0101), Duoc UC.

- **Informe técnico:** `docs/informe.docx`
- **Diagrama de arquitectura:** `docs/arquitectura.png`
- **Integrantes:** [Nombre integrante 1] — [Nombre integrante 2]

## Para el equipo (trabajo en parejas)

Cada integrante clona el repo por su cuenta y trabaja con su **propia**
API key de Gemini — la clave nunca se comparte por chat ni se sube al
repo, cada uno saca la suya gratis en 2 minutos (ver más abajo).

```bash
git clone https://github.com/E1egant/Rag-Soluciones-Con-Ia.git
cd Rag-Soluciones-Con-Ia
```

Desde aquí, sigue exactamente los pasos de la sección "Instalación y
ejecución paso a paso" de abajo — son los mismos para ambos integrantes.
Si alguien hace cambios en el código, el flujo normal es:

```bash
git pull                      # traer los últimos cambios antes de empezar
# ... hacer cambios ...
git add .
git commit -m "Descripción breve del cambio"
git push
```

## ¿Qué hace?

Responde consultas de clientes sobre qué decant comprar (por notas
olfativas, marca, presupuesto o disponibilidad), combinando dos fuentes de
datos mediante un pipeline RAG:

- **Fuente interna:** `data/catalogo.json` — catálogo real de productos,
  precios, formatos y stock.
- **Fuente externa:** `data/notas_externas.json` — fichas/reseñas públicas
  de perfumería que enriquecen la descripción sensorial.

Flujo completo: `guardrails → retriever híbrido → re-ranker → ensamblador
de contexto → LLM → post-procesado → trazabilidad`. Ver
`docs/arquitectura.png` para el diagrama y `docs/informe.docx` para la
justificación técnica de cada decisión.

## Requisitos previos

- Python 3.10 o superior (probado en 3.12).
- Una API key **gratuita** de Google Gemini para la generación real con LLM
  (instrucciones abajo — toma 2 minutos y no pide tarjeta de crédito).

## Instalación y ejecución paso a paso

```bash
# 1. Clonar el repositorio
git clone <URL-DEL-REPO>
cd elegantdrops-rag

# 2. Crear y activar un entorno virtual
python3 -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar la API key (ver sección "Cómo obtener una API key gratis")
cp .env.example .env
# abrir .env y pegar la clave en GEMINI_API_KEY=...

# 5. Ejecutar la demo
python3 main.py "Busco algo dulce y vainillado, presupuesto bajo 12000 el 10ml"

# 6. Ejecutar las pruebas automatizadas
python3 -m pytest tests/ -v
```

### Salida esperada del comando de demo (con GEMINI_API_KEY configurada)

```
============================================================
CONSULTA: Busco algo dulce y vainillado, presupuesto bajo 12000 el 10ml
============================================================
¡Hola! Para ese presupuesto y ese perfil dulce-vainillado te recomiendo
Black Opium de YSL — tiene un acorde de café tostado con vainilla, ideal
para uso nocturno, y está disponible en 10ml a $12.990. También puedes
partir con un decant de 5ml si quieres probarlo primero. ¿Qué tamaño te
acomoda más? 🌙
------------------------------------------------------------
Fuentes usadas (trazabilidad): ['ed-001', 'ext-001']
Derivado a humano: False
```

*(La redacción exacta variará entre ejecuciones — es un modelo generativo —
pero siempre debe ser una respuesta en lenguaje natural, no un prompt
crudo. Si ves un prompt crudo entre corchetes, revisa la sección
"Solución de problemas": significa que la API key no se está leyendo.)*

### Salida esperada de las pruebas

```
tests/test_pipeline.py::test_validar_consulta_vacia_es_rechazada PASSED
tests/test_pipeline.py::test_validar_consulta_con_intento_de_inyeccion_es_rechazada PASSED
tests/test_pipeline.py::test_retriever_encuentra_producto_por_nota_olfativa PASSED
tests/test_pipeline.py::test_retriever_excluye_agotados_por_defecto PASSED
tests/test_pipeline.py::test_agente_responde_en_modo_demo_sin_api_key PASSED
tests/test_pipeline.py::test_agente_deriva_a_humano_ante_consulta_sin_contexto PASSED

======================== 6 passed ========================
```

(Los tests pasan con o sin API key configurada — están diseñados para
verificar la lógica del pipeline, no la calidad del texto generado por el
LLM, que no es determinística.)

## Cómo obtener una API key gratis (Google Gemini)

Se eligió **Google Gemini** como proveedor por defecto porque, a diferencia
de Anthropic o OpenAI, ofrece una capa gratuita **permanente** (no un
crédito de prueba que se agota) y no pide tarjeta de crédito:

1. Entrar a [Google AI Studio](https://aistudio.google.com/app/apikey) con
   cualquier cuenta Google.
2. Clic en "Create API key".
3. Copiar la clave y pegarla en `.env`, en la línea `GEMINI_API_KEY=`.
4. Listo — el proyecto usa el modelo `gemini-2.5-flash` por defecto
   (`src/generator.py`), que está dentro de la capa gratuita.

El código también soporta Claude (Anthropic) como alternativa de pago si
alguien del equipo ya tiene créditos: basta con completar
`ANTHROPIC_API_KEY` en `.env` en vez de `GEMINI_API_KEY`. `generar_respuesta()`
en `src/generator.py` elige automáticamente cuál usar según qué variable
esté configurada.

## Modo demo: para qué sirve y en qué se diferencia del modo real

Si **ninguna** de las dos claves está configurada, el sistema no se cae:
sigue corriendo en "modo demo" (ver `src/generator.py`). Es importante
entender qué significa exactamente esto, porque son dos comportamientos
distintos con un solo punto en común:

| | Modo demo (sin ninguna API key) | Modo real (con GEMINI_API_KEY o ANTHROPIC_API_KEY) |
|---|---|---|
| Guardrails, retriever, re-ranker, ensamblado de contexto, trazabilidad | ✅ Se ejecutan igual, con la misma lógica | ✅ Se ejecutan igual, con la misma lógica |
| Última etapa (generación de lenguaje natural) | ❌ No llama a ningún LLM; devuelve el prompt final como texto plano, a modo de vista previa | ✅ Llama al LLM real y devuelve la respuesta redactada, lista para el cliente |
| ¿Sirve para entregar el proyecto? | **No** — no demuestra generación aumentada real, solo la mitad "R" de RAG, no la "G" | **Sí** — es el comportamiento que se debe mostrar en la defensa |
| ¿Para qué existe entonces? | Para que alguien pueda clonar el repo, instalar dependencias y correr los tests sin depender de que la API de Gemini esté arriba en ese momento (evita que un corte de servicio externo haga fallar la entrega) | — |

**En resumen: no son "prácticamente lo mismo".** El modo demo prueba que la
mitad de recuperación de datos (RAG) funciona, pero la respuesta que ve el
cliente no existe todavía — es solo el texto que se le iba a mandar al
modelo. Para la entrega y la defensa oral, el repositorio **debe** correr
con `GEMINI_API_KEY` configurada.

## Estructura del repositorio

```
elegantdrops-rag/
├── data/
│   ├── catalogo.json          # fuente interna (productos)
│   └── notas_externas.json    # fuente externa (reseñas/fichas públicas)
├── docs/
│   ├── arquitectura.png       # diagrama de arquitectura
│   └── informe.docx           # informe técnico del encargo
├── src/
│   ├── ingest.py              # carga y normaliza ambas fuentes de datos
│   ├── retriever.py           # recuperación híbrida (TF-IDF + filtro por metadata)
│   ├── context_builder.py     # re-ranking y ensamblado de contexto
│   ├── prompts.py             # plantillas de prompt, con justificación de diseño
│   ├── guardrails.py          # validación de entrada / mitigación de prompt injection
│   ├── generator.py           # llamada al LLM (Gemini por defecto, Claude alternativo)
│   ├── orchestrator.py        # arma el flujo completo del agente
│   └── trazabilidad.py        # registro de qué documentos sustentaron cada respuesta
├── tests/
│   └── test_pipeline.py
├── main.py                    # demo por línea de comandos
├── requirements.txt
├── .env.example
└── .gitignore
```

## Checklist rápido para quien evalúe

- [ ] `pip install -r requirements.txt` no da errores.
- [ ] Con `GEMINI_API_KEY` configurada, `python3 main.py "..."` devuelve una
      respuesta en lenguaje natural (no un prompt crudo entre corchetes).
- [ ] `python3 -m pytest tests/ -v` → 6 tests, todos en verde.
- [ ] `data/catalogo.json` y `data/notas_externas.json` representan las dos
      fuentes (interna/externa) exigidas por IL1.2.
- [ ] `docs/arquitectura.png` coincide con el diagrama defendido (IE4/IE7).
- [ ] `docs/informe.docx` documenta la justificación de cada decisión (IE5).

## Solución de problemas

- **`ModuleNotFoundError: No module named 'sklearn'` / `google.genai`**:
  falta instalar dependencias — repetir el paso 3
  (`pip install -r requirements.txt`) con el entorno virtual activado.
- **Sigue saliendo `[MODO DEMO...]` aunque puse una API key**: revisar que
  el archivo se llame exactamente `.env` (no `.env.example`) y que esté en
  la raíz del repo, junto a `main.py`; revisar que la línea sea
  `GEMINI_API_KEY=AIza...` sin comillas ni espacios extra.
- **Error 403 / "API key not valid" de Gemini**: la clave se copió mal o
  fue revocada — generar una nueva en
  [Google AI Studio](https://aistudio.google.com/app/apikey).
- **Error 429 / "rate limit" de Gemini**: la capa gratuita tiene un límite
  de solicitudes por minuto; esperar unos segundos y reintentar (no es un
  error del código).
- **`git push` rechaza el archivo `.env`**: es intencional, está en
  `.gitignore` para no subir la clave por error — cada persona del equipo
  configura la suya localmente.
- **`error: externally-managed-environment` al hacer `pip install`**: ocurre
  en Ubuntu/Debian recientes si se instala fuera de un entorno virtual.
  Solución: asegurarse de haber activado `.venv` (paso 2) antes del `pip
  install`; si el problema persiste, usar
  `pip install -r requirements.txt --break-system-packages`.

## Uso de Inteligencia Artificial en este proyecto

Se utilizó Claude (Anthropic) como apoyo para: redacción y estructuración del
código base, generación del diagrama de arquitectura y redacción inicial del
informe técnico. El equipo revisó, ajustó y validó cada decisión de diseño;
las conclusiones y las reflexiones individuales del informe fueron
redactadas por los integrantes sin apoyo de IA, conforme a las indicaciones
de uso ético de IA de la evaluación (https://bibliotecas.duoc.cl/ia).

## Notas de diseño y limitaciones

- El retriever usa TF-IDF en lugar de embeddings neuronales para evitar
  dependencias adicionales; la interfaz es intercambiable por un índice
  vectorial real sin tocar el resto del pipeline (ver docstring en
  `retriever.py`).
- El corpus de ejemplo (5 productos, 3 fichas externas) es representativo a
  escala reducida del catálogo real de Elegant Drops (1.376+ productos);
  `ingest.py` está diseñado para escalar reemplazando los JSON por una
  consulta a la base de datos de producción (MySQL).
- La capa gratuita de Gemini tiene límites de solicitudes por minuto/día;
  suficientes para desarrollo y defensa, pero no para un volumen de
  producción real — eso se documenta como limitación conocida en el
  informe técnico, sección 5.1.
- La trazabilidad se guarda en `data/trazabilidad.jsonl` (no versionado, ver
  `.gitignore`); en producción iría a la base de datos ya usada por Elegant
  Drops.
