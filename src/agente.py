"""
Agente conversacional Elegant Drops (IE1, IE2, IE3, IE5).

Grafo LangGraph:  guardrails -> contexto -> planificar -> ejecutar -> END

- Memoria de corto plazo: `messages` y `candidatos` viven en el estado del
  grafo, persistido por `MemorySaver` bajo un `thread_id` (una conversación).
- Memoria de largo plazo: `MemoriaLargoPlazo`, por `cliente_id`, consultada
  semánticamente en el nodo `contexto`.
- Ejecución: ReAct con las herramientas de `tools.py` si hay LLM; si no hay
  clave o el LLM falla, un ejecutor determinista sigue el mismo plan.
"""
import os
import re
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

from . import observabilidad as obs
from . import prompts
from .guardrails import ConsultaRechazada, es_consulta_vaga, validar_consulta
from .memoria import MemoriaLargoPlazo
from .normalizacion import es_referencia, expandir, productos_mencionados, sin_tildes
from .planner import Plan, planificar, replanificar
from .retriever import Retriever
from .seguimiento import es_cortesia, resolver
from .seguridad import redactar
from .tools import crear_herramientas

MODELO_POR_DEFECTO = "gemini-3.6-flash"
MODELO_RESPALDO = "gemini-3.1-flash-lite"
TIMEOUT_LLM_S = 20
# Tras una falla, el modelo se omite este tiempo para no pagar el timeout en cada turno.
PAUSA_CIRCUITO_S = 120
TURNOS_CORTO_PLAZO = 10
PATRON_ID = re.compile(r"^\[(ed-\d+)\]", re.MULTILINE)

PROMPT_AGENTE = prompts.SYSTEM_PROMPT + """
Eres un agente con herramientas. Sigue el PLAN en orden de prioridad y no \
omitas la verificación de stock antes de reservar. Si una herramienta \
informa AGOTADO, RECHAZADA o SIN_RESULTADOS, ajusta el plan: ofrece una \
alternativa disponible o deriva a humano.

Nunca reserves sin que el cliente haya indicado producto y formato: si falta \
alguno, pregúntalo. Termina con una pregunta cuando ofrezcas productos.
Si el producto pedido está agotado, busca en el mismo turno una alternativa \
disponible con sus notas, nómbrala y pregunta si la reservas.
Solo puedes buscar, comparar, reservar, recordar preferencias y derivar a un \
asesor. No ofrezcas nada más: no existen avisos de reposición, pagos ni envíos.

PLAN: {plan}
PREFERENCIAS PREVIAS DEL CLIENTE: {preferencias}
PRODUCTOS OFRECIDOS EN EL TURNO ANTERIOR: {ofrecidos}
RESERVA CONFIRMADA EN ESTE TURNO: {confirmada}
"""


class Estado(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    cliente_id: str
    consulta: str
    preferencias: list[str]
    plan: Plan
    candidatos: list[str]
    formato_ml: int
    pendiente: list[str]  # ids ofrecidos que esperan respuesta
    trace: list[str]
    derivado: bool
    fuentes: list[str]
    terminar: bool


def _ids(texto: str) -> list[str]:
    return PATRON_ID.findall(texto)


class AgenteElegantDrops:
    def __init__(self, retriever: Retriever | None = None, memoria: MemoriaLargoPlazo | None = None,
                 llm=None, rutas: dict | None = None, ruta_log=None, llm_respaldo=None):
        self.ruta_log = ruta_log or obs.RUTA_LOG
        self.retriever = retriever or Retriever()
        self.memoria = memoria or MemoriaLargoPlazo()
        self.rutas = rutas or {}
        self.llm = llm if llm is not None else self._crear_llm()
        if llm is None and self.llm is not None and llm_respaldo is None:
            llm_respaldo = self._crear_llm(os.environ.get("GEMINI_MODEL_RESPALDO", MODELO_RESPALDO))
        self.llm_respaldo = llm_respaldo
        self._pausados: dict[int, float] = {}
        self.grafo = self._construir_grafo()

    @staticmethod
    def _crear_llm(modelo: str | None = None):
        if not os.environ.get("GEMINI_API_KEY"):
            return None
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=modelo or os.environ.get("GEMINI_MODEL", MODELO_POR_DEFECTO),
            google_api_key=os.environ["GEMINI_API_KEY"], temperature=0,
            timeout=float(os.environ.get("LLM_TIMEOUT_S", TIMEOUT_LLM_S)), max_retries=0,
        )

    def _productos(self) -> dict[str, dict]:
        return {d.id: d.metadata for d in self.retriever.documentos if d.fuente == "interna"}

    def _autorizados(self, s: Estado) -> set[str]:
        """Productos que el cliente pidió reservar: confirmados, nombrados o aludidos."""
        if s["plan"].intencion != "reservar":
            return set()
        if s["plan"].objetivo:
            return set(s["plan"].objetivo)
        nombrados = productos_mencionados(s["consulta"], self._productos())
        if nombrados:
            return set(nombrados)
        previos = s.get("candidatos") or []
        return set(previos[:1]) if es_referencia(s["consulta"]) else set()

    def _herramientas(self, cliente_id: str, autorizados: set[str], formato: int) -> dict:
        tools = crear_herramientas(cliente_id, self.retriever, self.memoria,
                                   autorizados=autorizados, formato_pedido=formato, **self.rutas)
        return {t.name: t for t in tools}

    def _construir_grafo(self):
        g = StateGraph(Estado)
        g.add_node("guardrails", self._instrumentar("guardrails", self._nodo_guardrails))
        g.add_node("contexto", self._instrumentar("contexto", self._nodo_contexto))
        g.add_node("planificar", self._instrumentar("planificar", self._nodo_planificar))
        g.add_node("ejecutar", self._instrumentar("ejecutar", self._nodo_ejecutar))
        g.add_edge(START, "guardrails")
        g.add_conditional_edges("guardrails", lambda s: END if s.get("terminar") else "contexto")
        g.add_conditional_edges("contexto", lambda s: END if s.get("terminar") else "planificar")
        g.add_edge("planificar", "ejecutar")
        g.add_edge("ejecutar", END)
        return g.compile(checkpointer=MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=[("src.planner", "Plan"), ("src.planner", "Paso")])))

    @staticmethod
    def _instrumentar(nombre: str, nodo):
        def envuelto(estado: Estado) -> Estado:
            with obs.span("nodo", nombre):
                return nodo(estado)
        return envuelto

    def _nodo_guardrails(self, s: Estado) -> Estado:
        try:
            consulta = validar_consulta(s["consulta"])
        except ConsultaRechazada as e:
            texto = f"No puedo procesar esta consulta: {str(e).rstrip('.')}. Te derivo con un asesor humano."
            return {"messages": [HumanMessage(redactar(s["consulta"])), AIMessage(texto)], "derivado": True,
                    "terminar": True, "trace": ["guardrails: consulta rechazada"], "fuentes": [], "pendiente": []}
        return {"consulta": redactar(consulta), "terminar": False, "derivado": False,
                "trace": ["guardrails: ok"]}

    def _nodo_contexto(self, s: Estado) -> Estado:
        prefs = self.memoria.recuperar(s["cliente_id"], expandir(s["consulta"]))
        primer_turno = not s.get("messages")
        trace = s["trace"] + [f"memoria largo plazo: {len(prefs)} preferencias recuperadas"]
        if resolver(s["consulta"], s.get("pendiente") or [], self._productos())[0] == "rechazar":
            texto = "¡Sin problema! No reservé nada. ¿Quieres que busquemos otra opción?"
            return {"messages": [HumanMessage(s["consulta"]), AIMessage(texto)], "terminar": True,
                    "trace": trace + ["memoria corto plazo: el cliente descarta la oferta"], "fuentes": [],
                    "pendiente": [], "formato_ml": 0}
        if not primer_turno and es_cortesia(s["consulta"]):
            texto = "¡Con gusto! Si quieres buscar o reservar otro decant, dime qué te gustaría 🙂"
            return {"messages": [HumanMessage(s["consulta"]), AIMessage(texto)], "terminar": True,
                    "trace": trace + ["cortesía: no requiere búsqueda"], "fuentes": []}
        if primer_turno and not prefs and es_consulta_vaga(s["consulta"]):
            texto = ("¡Hola! Para recomendarte bien necesito saber qué buscas: ¿notas que te gusten "
                     "(dulce, fresco, amaderado), para qué ocasión y qué presupuesto tienes? 🙂")
            return {"messages": [HumanMessage(s["consulta"]), AIMessage(texto)], "terminar": True,
                    "trace": trace + ["consulta vaga: se pide más información"], "fuentes": []}
        return {"preferencias": prefs, "trace": trace}

    def _nodo_planificar(self, s: Estado) -> Estado:
        accion, objetivo = resolver(s["consulta"], s.get("pendiente") or [], self._productos())
        confirma = accion == "confirmar"
        plan = planificar(f"reservar {s['consulta']}" if confirma else s["consulta"])
        plan.objetivo = objetivo
        if not plan.max_precio_10ml:
            for pref in s.get("preferencias") or []:
                previo = planificar(pref).max_precio_10ml
                if previo:
                    plan.max_precio_10ml = previo
                    break
        # El formato solo se hereda mientras se responde a lo ya conversado.
        seguimiento = confirma or es_referencia(s["consulta"])
        if not plan.formato_explicito and seguimiento and s.get("formato_ml"):
            plan.formato_ml, plan.formato_explicito = s["formato_ml"], True
        formato = plan.formato_ml if plan.formato_explicito else 0
        return {"plan": plan, "formato_ml": formato, "trace": s["trace"] + [f"plan ({plan.intencion}): {plan.nombres()}"]}

    def _preguntar_cual(self, s: Estado) -> Estado | None:
        """Con más de un producto posible no se elige por el cliente."""
        plan: Plan = s["plan"]
        if plan.intencion != "reservar":
            return None
        prod = self._productos()
        opciones = plan.objetivo or productos_mencionados(s["consulta"], prod)
        if len(opciones) < 2:
            return None
        nombres = " o ".join(prod[i]["nombre"] for i in opciones)
        return {
            "messages": [HumanMessage(s["consulta"]), AIMessage(f"¿Cuál quieres reservar: {nombres}?")],
            "candidatos": opciones, "pendiente": opciones, "fuentes": opciones, "derivado": False,
            "trace": s["trace"] + ["REPLANIFICACIÓN: hay varias opciones: se pide elegir una"],
        }

    def _ofrecidos_en(self, texto: str) -> list[str]:
        """Productos disponibles que la respuesta del LLM deja ofrecidos, en el orden en que los nombra."""
        if "?" not in texto:
            return []
        prod = self._productos()
        plano = sin_tildes(texto)
        hallados = [i for i in productos_mencionados(texto, prod) if prod[i]["stock"]]
        return sorted(hallados, key=lambda i: plano.find(sin_tildes(prod[i]["nombre"]).split()[-1]))

    @staticmethod
    def _formato_propuesto(texto: str) -> int:
        """Formato que la respuesta deja propuesto (0 si en sus preguntas no hay exactamente uno)."""
        preguntas = " ".join(re.findall(r"[^.!?\n]*\?", texto))
        formatos = set(re.findall(r"(\d+)\s*ml\b", preguntas, flags=re.IGNORECASE))
        return int(formatos.pop()) if len(formatos) == 1 else 0

    def _nodo_ejecutar(self, s: Estado) -> Estado:
        pregunta = self._preguntar_cual(s)
        if pregunta:
            return pregunta
        herramientas = self._herramientas(s["cliente_id"], self._autorizados(s), s.get("formato_ml", 0))
        if self.llm is not None:
            for llm in (m for m in (self.llm, self.llm_respaldo) if m is not None):
                if time.monotonic() < self._pausados.get(id(llm), 0):
                    s = {**s, "trace": s["trace"] + ["cortacircuito: modelo en pausa, se omite"]}
                    continue
                try:
                    return self._ejecutar_llm(s, herramientas, llm)
                except Exception as e:
                    self._pausados[id(llm)] = time.monotonic() + PAUSA_CIRCUITO_S
                    with obs.span("evento", "fallback_llm") as ev:
                        if ev:
                            ev.fallar(e)
                    s = {**s, "trace": s["trace"] + [f"LLM no disponible ({type(e).__name__})"]}
            s = {**s, "trace": s["trace"] + ["se usa el ejecutor determinista"]}
        return self._ejecutar_determinista(s, herramientas)

    @staticmethod
    def _hecho_a_recordar(consulta: str, plan: Plan) -> str:
        hecho = expandir(consulta)
        if plan.max_precio_10ml and "presupuesto" not in hecho.lower():
            hecho += f" presupuesto bajo {plan.max_precio_10ml}"
        return hecho

    def _describir(self, ids: list[str]) -> str:
        prod = self._productos()
        return ", ".join(f"{i} {prod[i]['nombre']}" for i in ids) or "ninguno"

    def _ejecutar_llm(self, s: Estado, herramientas: dict, llm) -> Estado:
        plan: Plan = s["plan"]
        sistema = PROMPT_AGENTE.format(
            plan=" -> ".join(plan.nombres()),
            preferencias="; ".join(s.get("preferencias") or []) or "ninguna",
            ofrecidos=self._describir(s.get("pendiente") or []),
            confirmada=(f"{self._describir(plan.objetivo)}, formato {s.get('formato_ml') or 'sin indicar'}"
                        if plan.objetivo else "ninguna"),
        )
        agente = create_react_agent(llm, list(herramientas.values()), prompt=SystemMessage(sistema))
        historial = list(s.get("messages") or [])[-TURNOS_CORTO_PLAZO:]
        # recursion_limit corta bucles ReAct antes de que consuman cuota indefinidamente.
        res = agente.invoke({"messages": historial + [HumanMessage(s["consulta"])]},
                            {"recursion_limit": 12})
        nuevos = res["messages"][len(historial) + 1:]
        usadas = [t["name"] for m in nuevos if isinstance(m, AIMessage) for t in m.tool_calls]
        salidas = "\n".join(m.content for m in nuevos if isinstance(m, ToolMessage))
        texto = res["messages"][-1].content
        if isinstance(texto, list):
            texto = "".join(p.get("text", "") for p in texto if isinstance(p, dict))
        if not texto.strip():
            raise ValueError("el LLM devolvió una respuesta vacía")
        fuentes = list(dict.fromkeys(_ids(salidas)))
        # La memoria no puede depender de que el modelo decida guardarla.
        if "guardar_preferencia" in plan.nombres() and "guardar_preferencia" not in usadas:
            herramientas["guardar_preferencia"].invoke(
                {"hecho": self._hecho_a_recordar(s["consulta"], plan)})
            usadas.append("guardar_preferencia")
        cerrado = "RESERVA_OK" in salidas or "derivar_a_humano" in usadas
        pendiente = [] if cerrado else self._ofrecidos_en(texto)
        # Si propuso "¿reservamos el de 5ml?", un "sí" debe referirse a ese formato.
        propuesto = self._formato_propuesto(texto) if pendiente else 0
        return {
            **({"formato_ml": propuesto} if propuesto else {}),
            "messages": [HumanMessage(s["consulta"]), AIMessage(texto)],
            "candidatos": pendiente or fuentes or s.get("candidatos", []),
            "pendiente": pendiente,
            "fuentes": fuentes,
            "derivado": "derivar_a_humano" in usadas,
            "trace": s["trace"] + [f"LLM ejecutó herramientas: {usadas}"],
        }

    def _ejecutar_determinista(self, s: Estado, herr: dict) -> Estado:
        plan: Plan = s["plan"]
        trace = list(s["trace"])
        prod = self._productos()
        candidatos = s.get("candidatos", [])
        elegido = None
        derivado = False
        texto = ""
        excluidos: list[str] = []
        pendiente: list[str] = []

        def usar(nombre, **args):
            salida = herr[nombre].invoke(args)
            trace.append(f"{nombre}({', '.join(f'{k}={v!r}' for k, v in args.items())}) -> {salida.splitlines()[0][:70]}")
            return salida

        while (paso := plan.siguiente()) is not None:
            plan.completar(paso)
            h = paso.herramienta
            if h == "recordar_preferencias":
                usar(h, consulta=s["consulta"])
            elif h == "buscar_catalogo":
                nombrados = productos_mencionados(s["consulta"], prod)
                if plan.objetivo:
                    candidatos = plan.objetivo
                heredar = bool(plan.objetivo) or (plan.intencion == "reservar" and not nombrados
                                                  and candidatos and es_referencia(s["consulta"]))
                if heredar:
                    trace.append(f"memoria corto plazo: se usa el candidato previo {candidatos[0]}")
                    salida = f"[{candidatos[0]}]"
                else:
                    salida = usar(h, consulta=s["consulta"], max_precio_10ml=plan.max_precio_10ml,
                                  incluir_agotados=plan.intencion == "reservar")
                encontrados = [i for i in _ids(salida) if i in prod]
                if encontrados:
                    candidatos, elegido = encontrados, encontrados[0]
                if plan.intencion == "reservar" and nombrados and not plan.objetivo:
                    candidatos, elegido, encontrados = nombrados, nombrados[0], nombrados
                msg = replanificar(plan, h, "SIN_RESULTADOS" if not encontrados else salida)
                if msg:
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                elif plan.intencion == "reservar" and not nombrados and not heredar:
                    plan.pasos = [p for p in plan.pasos
                                  if p.herramienta not in ("consultar_stock", "reservar_decant")]
                    trace.append("REPLANIFICACIÓN: producto sin identificar: se pide confirmación")
                    pendiente = [i for i in candidatos[:2] if prod[i]["stock"]]
            elif h == "comparar_productos":
                texto = usar(h, ids=candidatos[:3])
            elif h == "consultar_stock":
                salida = usar(h, id_producto=elegido)
                msg = replanificar(plan, h, salida)
                if msg:
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                    excluidos.append(elegido)
            elif h == "reservar_decant":
                if not s.get("formato_ml"):
                    disponibles = ", ".join(f"{m}ml" for m in prod[elegido]["formatos_ml"])
                    texto = (f"{prod[elegido]['nombre']} está disponible en {disponibles}. "
                             f"¿En qué formato lo reservo?")
                    pendiente = [elegido]
                    trace.append("REPLANIFICACIÓN: falta el formato: se pregunta antes de reservar")
                    continue
                formato = s["formato_ml"]
                salida = usar(h, id_producto=elegido, formato_ml=formato)
                texto = (f"¡Listo! Dejé reservado {prod[elegido]['nombre']} en {formato}ml. "
                         f"Un asesor te contactará para el pago 🙌") if salida.startswith("RESERVA_OK") else ""
                if not texto and formato not in prod[elegido]["formatos_ml"]:
                    disponibles = ", ".join(f"{m}ml" for m in prod[elegido]["formatos_ml"])
                    texto = (f"{prod[elegido]['nombre']} no tiene formato {formato}ml. "
                             f"Formatos disponibles: {disponibles}. ¿Cuál prefieres?")
                    pendiente = [elegido]
                    trace.append("REPLANIFICACIÓN: formato inexistente: se piden alternativas de formato")
                elif not texto:
                    msg = replanificar(plan, h, salida)
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                    excluidos.append(elegido)
            elif h == "alternativa":
                notas = prod[elegido]["notas_corazon"] + prod[elegido]["notas_fondo"]
                salida = usar("buscar_catalogo", consulta=" ".join(notas))
                alt = [i for i in _ids(salida) if i in prod and i not in excluidos and prod[i]["stock"]]
                if alt:
                    elegido = alt[0]
                    candidatos, pendiente = alt, [elegido]
                    texto = (f"{prod[excluidos[-1]]['nombre']} no está disponible. Te sugiero "
                             f"{prod[elegido]['nombre']} ({prod[elegido]['marca']}), con perfil parecido, "
                             f"10ml a ${prod[elegido]['precio_10ml_clp']}. ¿Lo reservo?")
                else:
                    plan.pasos.append(type(paso)(1, "derivar_a_humano", "sin alternativa disponible"))
            elif h == "guardar_preferencia":
                usar(h, hecho=self._hecho_a_recordar(s["consulta"], plan))
            elif h == "derivar_a_humano":
                usar(h, motivo=f"sin candidatos para: {s['consulta']}")
                derivado = True
                texto = ("No encontré en el catálogo algo que calce con tu consulta. "
                         "Te derivo con un asesor humano por WhatsApp 🙋")

        if not texto and elegido:
            # Disponibles primero: así "el primero" coincide con lo que el cliente ve.
            mostrados = sorted((i for i in candidatos[:2] if i in prod), key=lambda i: not prod[i]["stock"])
            pendiente = [i for i in mostrados if prod[i]["stock"]]
            lineas = [
                f"- {p['nombre']} ({p['marca']}): 10ml ${p['precio_10ml_clp']}, notas "
                f"{', '.join(p['notas_corazon'] + p['notas_fondo'][:1])}"
                + ("" if p["stock"] else " (AGOTADO por ahora)")
                for p in (prod[i] for i in mostrados)
            ]
            if not pendiente:
                cierre = "\n¿Quieres que busque una alternativa parecida?"
            elif plan.intencion == "reservar":
                cierre = "\n¿Cuál quieres reservar?"
            else:
                cierre = "\n¿Qué tamaño te acomoda?"
            encabezado = "Te recomiendo:" if pendiente else "Esto encontré:"
            texto = encabezado + "\n" + "\n".join(lineas) + cierre
        if plan.intencion == "comparar":
            pendiente = [i for i in candidatos[:2] if i in prod and prod[i]["stock"]]
        fuentes = list(candidatos) if not derivado else []
        return {
            "messages": [HumanMessage(s["consulta"]), AIMessage(texto)],
            "candidatos": candidatos, "fuentes": fuentes, "derivado": derivado, "trace": trace,
            "pendiente": [] if derivado else pendiente,
        }

    def responder(self, consulta: str, cliente_id: str = "cliente-demo", thread_id: str | None = None) -> dict:
        hilo = thread_id or cliente_id
        cfg = {"configurable": {"thread_id": hilo}, "callbacks": [obs.TrazadorCallbacks()]}
        with obs.trazar(self.ruta_log, hilo, cliente_id, consulta=consulta,
                        modo="llm" if self.llm is not None else "determinista") as raiz:
            estado = self.grafo.invoke(
                {"consulta": consulta, "cliente_id": cliente_id, "trace": [], "terminar": False}, cfg
            )
            trace = estado["trace"]
            resultado = {
                "texto": estado["messages"][-1].content,
                "fuentes": estado.get("fuentes", []),
                "derivado_a_humano": estado.get("derivado", False),
                "trace": trace,
            }
            raiz.atributos.update(
                respuesta=resultado["texto"], fuentes=resultado["fuentes"],
                derivado=resultado["derivado_a_humano"],
                rechazado="guardrails: consulta rechazada" in trace,
                fallback=any("ejecutor determinista" in t for t in trace),
                modelo_degradado=any(t.startswith(("LLM no disponible", "cortacircuito")) for t in trace),
                replanificaciones=sum(t.startswith("REPLANIFICACIÓN") for t in trace),
            )
            resultado["trace_id"] = raiz.traza.id
        return resultado
