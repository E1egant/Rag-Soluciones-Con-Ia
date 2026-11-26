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
from .normalizacion import es_referencia, expandir, productos_mencionados
from .planner import Plan, planificar, replanificar
from .retriever import Retriever
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

PLAN: {plan}
PREFERENCIAS PREVIAS DEL CLIENTE: {preferencias}
"""


class Estado(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    cliente_id: str
    consulta: str
    preferencias: list[str]
    plan: Plan
    candidatos: list[str]
    formato_ml: int
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
        """Productos que el cliente pidió reservar: nombrados o aludidos del turno anterior."""
        if s["plan"].intencion != "reservar":
            return set()
        nombrados = productos_mencionados(s["consulta"], self._productos())
        if nombrados:
            return set(nombrados)
        previos = s.get("candidatos") or []
        if not es_referencia(s["consulta"]):
            return set()
        return set(previos if self.llm is not None else previos[:1])

    def _herramientas(self, cliente_id: str, autorizados: set[str]) -> dict:
        tools = crear_herramientas(cliente_id, self.retriever, self.memoria,
                                   autorizados=autorizados, **self.rutas)
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
                    "terminar": True, "trace": ["guardrails: consulta rechazada"], "fuentes": []}
        return {"consulta": redactar(consulta), "terminar": False, "derivado": False,
                "trace": ["guardrails: ok"]}

    def _nodo_contexto(self, s: Estado) -> Estado:
        prefs = self.memoria.recuperar(s["cliente_id"], expandir(s["consulta"]))
        primer_turno = not s.get("messages")
        trace = s["trace"] + [f"memoria largo plazo: {len(prefs)} preferencias recuperadas"]
        if primer_turno and not prefs and es_consulta_vaga(s["consulta"]):
            texto = ("¡Hola! Para recomendarte bien necesito saber qué buscas: ¿notas que te gusten "
                     "(dulce, fresco, amaderado), para qué ocasión y qué presupuesto tienes? 🙂")
            return {"messages": [HumanMessage(s["consulta"]), AIMessage(texto)], "terminar": True,
                    "trace": trace + ["consulta vaga: se pide más información"], "fuentes": []}
        return {"preferencias": prefs, "trace": trace}

    def _nodo_planificar(self, s: Estado) -> Estado:
        plan = planificar(s["consulta"])
        if not plan.max_precio_10ml:
            for pref in s.get("preferencias") or []:
                previo = planificar(pref).max_precio_10ml
                if previo:
                    plan.max_precio_10ml = previo
                    break
        if not plan.formato_explicito and s.get("formato_ml") and es_referencia(s["consulta"]):
            plan.formato_ml, plan.formato_explicito = s["formato_ml"], True
        formato = plan.formato_ml if plan.formato_explicito else s.get("formato_ml", 0)
        return {"plan": plan, "formato_ml": formato, "trace": s["trace"] + [f"plan ({plan.intencion}): {plan.nombres()}"]}

    def _nodo_ejecutar(self, s: Estado) -> Estado:
        herramientas = self._herramientas(s["cliente_id"], self._autorizados(s))
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

    def _ejecutar_llm(self, s: Estado, herramientas: dict, llm) -> Estado:
        plan: Plan = s["plan"]
        sistema = PROMPT_AGENTE.format(
            plan=" -> ".join(plan.nombres()),
            preferencias="; ".join(s.get("preferencias") or []) or "ninguna",
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
        fuentes = list(dict.fromkeys(_ids(salidas)))
        return {
            "messages": [HumanMessage(s["consulta"]), AIMessage(texto)],
            "candidatos": fuentes or s.get("candidatos", []),
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
        excluidos = set()

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
                heredar = (plan.intencion == "reservar" and not nombrados and candidatos
                           and es_referencia(s["consulta"]))
                if heredar:
                    trace.append(f"memoria corto plazo: se usa el candidato previo {candidatos[0]}")
                    salida = f"[{candidatos[0]}]"
                else:
                    salida = usar(h, consulta=s["consulta"], max_precio_10ml=plan.max_precio_10ml,
                                  incluir_agotados=plan.intencion == "reservar")
                encontrados = [i for i in _ids(salida) if i in prod]
                if encontrados:
                    candidatos, elegido = encontrados, encontrados[0]
                if plan.intencion == "reservar" and nombrados:
                    candidatos, elegido, encontrados = nombrados, nombrados[0], nombrados
                msg = replanificar(plan, h, "SIN_RESULTADOS" if not encontrados else salida)
                if msg:
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                elif plan.intencion == "reservar" and not nombrados and not heredar:
                    plan.pasos = [p for p in plan.pasos
                                  if p.herramienta not in ("consultar_stock", "reservar_decant")]
                    trace.append("REPLANIFICACIÓN: producto sin identificar: se pide confirmación")
            elif h == "comparar_productos":
                texto = usar(h, ids=candidatos[:3])
            elif h == "consultar_stock":
                salida = usar(h, id_producto=elegido)
                msg = replanificar(plan, h, salida)
                if msg:
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                    excluidos.add(elegido)
            elif h == "reservar_decant":
                formato = plan.formato_ml
                salida = usar(h, id_producto=elegido, formato_ml=formato)
                texto = (f"¡Listo! Dejé reservado {prod[elegido]['nombre']} en {formato}ml. "
                         f"Un asesor te contactará para el pago 🙌") if salida.startswith("RESERVA_OK") else ""
                if not texto and formato not in prod[elegido]["formatos_ml"]:
                    disponibles = ", ".join(f"{m}ml" for m in prod[elegido]["formatos_ml"])
                    texto = (f"{prod[elegido]['nombre']} no tiene formato {formato}ml. "
                             f"Formatos disponibles: {disponibles}. ¿Cuál prefieres?")
                    trace.append("REPLANIFICACIÓN: formato inexistente: se piden alternativas de formato")
                elif not texto:
                    msg = replanificar(plan, h, salida)
                    trace.append(f"REPLANIFICACIÓN: {msg}")
                    excluidos.add(elegido)
            elif h == "alternativa":
                notas = prod[elegido]["notas_corazon"] + prod[elegido]["notas_fondo"]
                salida = usar("buscar_catalogo", consulta=" ".join(notas))
                alt = [i for i in _ids(salida) if i in prod and i not in excluidos and prod[i]["stock"]]
                if alt:
                    elegido = alt[0]
                    candidatos = alt
                    texto = (f"{prod[min(excluidos)]['nombre']} no está disponible. Te sugiero "
                             f"{prod[elegido]['nombre']} ({prod[elegido]['marca']}), con perfil parecido, "
                             f"10ml a ${prod[elegido]['precio_10ml_clp']}. ¿Lo reservo?")
                else:
                    plan.pasos.append(type(paso)(1, "derivar_a_humano", "sin alternativa disponible"))
            elif h == "guardar_preferencia":
                hecho = expandir(s["consulta"])
                if plan.max_precio_10ml and "presupuesto" not in hecho.lower():
                    hecho += f" presupuesto bajo {plan.max_precio_10ml}"
                usar(h, hecho=hecho)
            elif h == "derivar_a_humano":
                usar(h, motivo=f"sin candidatos para: {s['consulta']}")
                derivado = True
                texto = ("No encontré en el catálogo algo que calce con tu consulta. "
                         "Te derivo con un asesor humano por WhatsApp 🙋")

        if not texto and elegido:
            top = [prod[i] for i in candidatos[:2] if i in prod]
            texto = "Te recomiendo:\n" + "\n".join(
                f"- {p['nombre']} ({p['marca']}): 10ml ${p['precio_10ml_clp']}, notas "
                f"{', '.join(p['notas_corazon'] + p['notas_fondo'][:1])}" for p in top
            ) + ("\n¿Cuál quieres reservar?" if plan.intencion == "reservar" else "\n¿Qué tamaño te acomoda?")
        fuentes = list(candidatos) if not derivado else []
        return {
            "messages": [HumanMessage(s["consulta"]), AIMessage(texto)],
            "candidatos": candidatos, "fuentes": fuentes, "derivado": derivado, "trace": trace,
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
