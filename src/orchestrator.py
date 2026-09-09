"""
Orquestador del agente (IL1.3 — arquitectura de solución).

Coordina: guardrails -> reescritura de consulta -> retriever -> re-ranker ->
ensamblador de contexto -> generación -> post-procesado -> trazabilidad. Es
el único punto de entrada público del pipeline, para que el canal de
contacto (WhatsApp, web chat) no necesite conocer los módulos internos.
"""
from dataclasses import dataclass

from . import prompts, trazabilidad
from .context_builder import ensamblar_contexto, rerank
from .generator import generar_respuesta
from .guardrails import ConsultaRechazada, validar_consulta
from .retriever import Retriever

UMBRAL_CONFIANZA_MINIMA = 0.12  # bajo este score de similitud máxima, se considera "sin contexto suficiente"


@dataclass
class RespuestaAgente:
    texto: str
    fuentes_usadas: list[str]
    derivado_a_humano: bool = False


class AgenteElegantDrops:
    def __init__(self, retriever: Retriever | None = None):
        self.retriever = retriever or Retriever()

    def responder(self, consulta_cliente: str, restricciones: str = "ninguna") -> RespuestaAgente:
        print("DEBUG: validando consulta...", flush=True)
        try:
            consulta = validar_consulta(consulta_cliente)
        except ConsultaRechazada as e:
            return RespuestaAgente(
                texto=f"No puedo procesar esta consulta: {e}. Te derivo con un asesor humano.",
                fuentes_usadas=[],
                derivado_a_humano=True,
            )

        print("DEBUG: llamando a reescritura...", flush=True)
        prompt_reescritura = prompts.PROMPT_REESCRITURA.format(consulta=consulta)
        palabras_clave = generar_respuesta(prompts.SYSTEM_PROMPT, prompt_reescritura, max_tokens=300)
        print(f"DEBUG: palabras_clave = {palabras_clave}", flush=True)
        if not palabras_clave:
            palabras_clave = consulta

        print("DEBUG: buscando en retriever...", flush=True)
        resultados = self.retriever.buscar(palabras_clave)
        print(f"DEBUG: {len(resultados)} resultados", flush=True)
        resultados = rerank(resultados)

        if not resultados or resultados[0].score < UMBRAL_CONFIANZA_MINIMA:
            prompt_usuario = prompts.PROMPT_SIN_CONTEXTO_SUFICIENTE.format(consulta=consulta)
            texto = generar_respuesta(prompts.SYSTEM_PROMPT, prompt_usuario)
            trazabilidad.registrar(consulta, [], texto)
            return RespuestaAgente(texto=texto, fuentes_usadas=[], derivado_a_humano=True)

        contexto = ensamblar_contexto(resultados)
        prompt_usuario = prompts.PROMPT_RECOMENDACION.format(
            contexto=contexto, consulta=consulta, restricciones=restricciones
        )
        texto = generar_respuesta(prompts.SYSTEM_PROMPT, prompt_usuario)

        fuentes = [r.documento.id for r in resultados]
        trazabilidad.registrar(
            consulta,
            [{"id": r.documento.id, "fuente": r.documento.fuente, "score": round(r.score, 4)} for r in resultados],
            texto,
        )
        return RespuestaAgente(texto=texto, fuentes_usadas=fuentes, derivado_a_humano=False)