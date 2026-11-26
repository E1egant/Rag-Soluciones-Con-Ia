"""
Observabilidad del agente (IL3.1, IL3.2): logs JSON estructurados y trazas.

Una sesión (`session_id`) agrupa varias trazas (`trace_id`, una por turno) y
cada traza es un árbol de spans (`span_id`, `parent_span_id`): agente -> nodos
del grafo -> herramientas / retriever / LLM. Cada span cerrado es una línea
del JSONL. Todo texto libre se redacta antes de escribirse.
"""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import psutil
from langchain_core.callbacks import BaseCallbackHandler

from .seguridad import redactar, seudonimizar

RUTA_LOG = Path(__file__).resolve().parent.parent / "data" / "logs" / "agente.jsonl"
MAX_TEXTO = 300

_traza: ContextVar["Traza | None"] = ContextVar("traza", default=None)
_padre: ContextVar[str | None] = ContextVar("span_padre", default=None)
_candado = threading.Lock()


def _limpiar(valor):
    if isinstance(valor, str):
        return redactar(valor)[:MAX_TEXTO]
    if isinstance(valor, dict):
        return {k: _limpiar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpiar(v) for v in valor]
    return valor


class Span:
    def __init__(self, traza: "Traza", tipo: str, nombre: str, padre: str | None, atributos: dict):
        self.traza, self.tipo, self.nombre, self.padre = traza, tipo, nombre, padre
        self.id = uuid.uuid4().hex[:16]
        self.atributos = atributos
        self.estado, self.error = "ok", None
        self.inicio = datetime.now(timezone.utc).isoformat()
        self._t0 = time.perf_counter()

    def fallar(self, error: BaseException) -> None:
        self.estado, self.error = "error", f"{type(error).__name__}: {error}"

    def cerrar(self) -> None:
        self.traza.escribir({
            "timestamp": self.inicio, "session_id": self.traza.session_id, "trace_id": self.traza.id,
            "span_id": self.id, "parent_span_id": self.padre, "cliente": self.traza.cliente,
            "tipo": self.tipo, "nombre": self.nombre,
            "duracion_ms": round((time.perf_counter() - self._t0) * 1000, 3),
            "estado": self.estado, "error": _limpiar(self.error), "atributos": _limpiar(self.atributos),
        })


class Traza:
    def __init__(self, ruta: Path, session_id: str, cliente_id: str):
        self.ruta = ruta
        self.id = uuid.uuid4().hex
        self.cliente = seudonimizar(cliente_id)
        # El thread_id suele ser el propio cliente_id: nunca se escribe en claro.
        self.session_id = seudonimizar(session_id).replace("cli_", "ses_")
        self.tokens_entrada = self.tokens_salida = self.llamadas_llm = 0
        self.errores = 0

    def escribir(self, evento: dict) -> None:
        if evento["estado"] == "error":
            self.errores += 1
        # Un fallo del log nunca debe tumbar la atención al cliente.
        try:
            with _candado:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                with open(self.ruta, "a", encoding="utf-8") as f:
                    f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass


@contextmanager
def span(tipo: str, nombre: str, **atributos):
    """Abre un span hijo; no hace nada si no hay una traza activa."""
    traza = _traza.get()
    if traza is None:
        yield None
        return
    s = Span(traza, tipo, nombre, _padre.get(), atributos)
    token = _padre.set(s.id)
    try:
        yield s
    except BaseException as e:
        s.fallar(e)
        raise
    finally:
        _padre.reset(token)
        s.cerrar()


@contextmanager
def trazar(ruta: Path, session_id: str, cliente_id: str, **atributos):
    """Abre la traza de un turno y su span raíz con consumo de recursos."""
    traza = Traza(Path(ruta), session_id, cliente_id)
    token = _traza.set(traza)
    proceso = psutil.Process(os.getpid())
    cpu0 = time.process_time()
    try:
        with span("agente", "turno", **atributos) as raiz:
            try:
                yield raiz
            finally:
                precio_in = float(os.environ.get("PRECIO_USD_MTOK_ENTRADA", 0))
                precio_out = float(os.environ.get("PRECIO_USD_MTOK_SALIDA", 0))
                raiz.atributos.update(
                    cpu_ms=round((time.process_time() - cpu0) * 1000, 3),
                    memoria_mb=round(proceso.memory_info().rss / 1_048_576, 1),
                    tokens_entrada=traza.tokens_entrada, tokens_salida=traza.tokens_salida,
                    llamadas_llm=traza.llamadas_llm, spans_con_error=traza.errores,
                    costo_usd=round((traza.tokens_entrada * precio_in
                                     + traza.tokens_salida * precio_out) / 1_000_000, 6),
                )
    finally:
        _traza.reset(token)


class TrazadorCallbacks(BaseCallbackHandler):
    """Crea spans de herramientas y LLM a partir de los callbacks de LangChain."""

    def __init__(self):
        self._abiertos: dict = {}

    def _abrir(self, run_id, tipo: str, nombre: str, **atributos) -> None:
        traza = _traza.get()
        if traza is not None:
            self._abiertos[run_id] = Span(traza, tipo, nombre, _padre.get(), atributos)

    def _cerrar(self, run_id, error: BaseException | None = None, **atributos) -> None:
        s = self._abiertos.pop(run_id, None)
        if s is None:
            return
        s.atributos.update(atributos)
        if error is not None:
            s.fallar(error)
        s.cerrar()

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        self._abrir(run_id, "herramienta", (serialized or {}).get("name", "herramienta"),
                    entrada=inputs or input_str)

    def on_tool_end(self, output, *, run_id, **kwargs):
        texto = str(getattr(output, "content", output))
        primera = texto.splitlines()[0] if texto else ""
        self._cerrar(run_id, resultado=primera[:120])

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._cerrar(run_id, error=error)

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._abrir(run_id, "llm", (kwargs.get("metadata") or {}).get("ls_model_name", "llm"))

    def on_llm_end(self, response, *, run_id, **kwargs):
        entrada = salida = 0
        for generaciones in response.generations:
            for g in generaciones:
                uso = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                entrada += uso.get("input_tokens", 0)
                salida += uso.get("output_tokens", 0)
        traza = _traza.get()
        if traza is not None:
            traza.tokens_entrada += entrada
            traza.tokens_salida += salida
            traza.llamadas_llm += 1
        self._cerrar(run_id, tokens_entrada=entrada, tokens_salida=salida)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._cerrar(run_id, error=error)
