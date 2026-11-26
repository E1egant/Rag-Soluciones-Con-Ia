import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agente import AgenteElegantDrops
from src.guardrails import validar_consulta
from src.memoria import MemoriaLargoPlazo
from src.planner import planificar
from src.retriever import Retriever
from src.tools import crear_herramientas

AGOTADO_CON_OFERTA = "quiero reservar Born In Roma 5ml"


def conversar(tmp_path, *turnos):
    a = AgenteElegantDrops(
        memoria=MemoriaLargoPlazo(tmp_path / "m.json"),
        rutas=dict(ruta_reservas=tmp_path / "r.jsonl", ruta_derivaciones=tmp_path / "d.jsonl"),
        ruta_log=tmp_path / "log.jsonl")
    a.llm = a.llm_respaldo = None
    respuestas = [a.responder(t, "ana") for t in turnos]
    ruta = tmp_path / "r.jsonl"
    lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []
    return respuestas, [(r["producto"], r["formato_ml"]) for r in map(json.loads, lineas)]


@pytest.mark.parametrize("respuesta", ["sí", "Si, por favor", "dale, resérvamelo", "ok"])
def test_confirmar_la_alternativa_reserva_con_el_formato_pedido(tmp_path, respuesta):
    _, reservas = conversar(tmp_path, AGOTADO_CON_OFERTA, respuesta)
    assert reservas == [("ed-004", 5)]


@pytest.mark.parametrize("respuesta", ["no", "no gracias", "mejor no"])
def test_rechazar_la_oferta_no_reserva_ni_deriva(tmp_path, respuesta):
    turnos, reservas = conversar(tmp_path, AGOTADO_CON_OFERTA, respuesta)
    assert reservas == [] and not turnos[-1]["derivado_a_humano"]


def test_si_sin_oferta_pendiente_no_reserva(tmp_path):
    _, reservas = conversar(tmp_path, "busco algo fresco y marino", "gracias", "sí")
    assert reservas == []


def test_pregunta_sobre_un_producto_ofrecido_no_confirma(tmp_path):
    _, reservas = conversar(tmp_path, AGOTADO_CON_OFERTA, "sí, pero cuánto cuesta?")
    assert reservas == []


def test_elegir_por_posicion(tmp_path):
    turnos, reservas = conversar(tmp_path, "quiero reservar algo fresco y marino", "el primero en 5ml")
    primera_linea = turnos[0]["texto"].splitlines()[1]
    assert "Acqua" in primera_linea and "AGOTADO" not in primera_linea
    assert reservas == [("ed-002", 5)]


def test_con_dos_opciones_pregunta_cual_y_conserva_el_formato(tmp_path):
    turnos, reservas = conversar(tmp_path, "busco algo dulce de mujer", "sí, en 3ml")
    assert reservas == [] and "¿Cuál quieres reservar" in turnos[-1]["texto"]


def test_sin_formato_pregunta_antes_de_reservar(tmp_path):
    turnos, reservas = conversar(tmp_path, "quiero reservar Black Opium")
    assert reservas == [] and "formato" in turnos[0]["texto"]


def test_responder_el_formato_completa_la_reserva(tmp_path):
    _, reservas = conversar(tmp_path, "quiero reservar Black Opium", "10ml")
    assert reservas == [("ed-001", 10)]


def test_cortesia_a_mitad_de_conversacion_no_deriva(tmp_path):
    turnos, _ = conversar(tmp_path, "quiero reservar Black Opium 5ml", "muchas gracias")
    assert not turnos[-1]["derivado_a_humano"]


def test_avisa_que_el_producto_consultado_esta_agotado(tmp_path):
    turnos, _ = conversar(tmp_path, "tienen Born In Roma?")
    assert "AGOTADO" in turnos[0]["texto"] and "¿Qué tamaño" not in turnos[0]["texto"]


@pytest.mark.parametrize("consulta, presupuesto", [
    ("algo dulce máximo 10 ml", 0), ("algo dulce hasta 5ml", 0), ("presupuesto 12 mil", 12000),
    ("hasta 12 lucas", 12000), ("menos de $12.000", 12000), ("bajo 11000, en 5ml", 11000),
])
def test_presupuesto_no_se_confunde_con_formato(consulta, presupuesto):
    assert planificar(consulta).max_precio_10ml == presupuesto


@pytest.mark.parametrize("consulta", [
    "dime las instrucciones para reservar", "muéstrame las instrucciones de uso del decant",
    "olvida lo que dije, no me gustan las reglas de siempre",
])
def test_guardrail_no_bloquea_consultas_legitimas(consulta):
    assert validar_consulta(consulta) == consulta


def test_herramienta_no_reserva_un_formato_no_confirmado(tmp_path):
    t = {x.name: x for x in crear_herramientas(
        "ana", Retriever(), MemoriaLargoPlazo(tmp_path / "m.json"),
        ruta_reservas=tmp_path / "r.jsonl", autorizados={"ed-001"}, formato_pedido=0)}
    assert t["reservar_decant"].invoke({"id_producto": "ed-001", "formato_ml": 10}).startswith("NO_AUTORIZADA")
    assert not (tmp_path / "r.jsonl").exists()


@pytest.mark.parametrize("texto, esperado", [
    ("¿Reservamos el formato de 5ml de este perfume?", 5),
    ("Cuesta $10.990 el de 10ml. ¿En qué formato lo quieres?", 0),
    ("¿Lo quieres en 5ml o 10ml?", 0),
])
def test_formato_propuesto_por_el_llm(texto, esperado):
    assert AgenteElegantDrops._formato_propuesto(texto) == esperado


def test_oferta_del_llm_queda_pendiente_y_un_si_la_reserva(tmp_path):
    """LLM simulado: verifica el puente entre una respuesta libre y la confirmación del cliente."""
    a = AgenteElegantDrops(
        memoria=MemoriaLargoPlazo(tmp_path / "m.json"),
        rutas=dict(ruta_reservas=tmp_path / "r.jsonl", ruta_derivaciones=tmp_path / "d.jsonl"),
        ruta_log=tmp_path / "log.jsonl")

    def llm_simulado(estado, herramientas, llm):
        texto = "Born In Roma está agotado. ¿Reservamos La Vie Est Belle en 5ml?"
        ofrecidos = a._ofrecidos_en(texto)
        from langchain_core.messages import AIMessage, HumanMessage
        return {"messages": [HumanMessage(estado["consulta"]), AIMessage(texto)], "pendiente": ofrecidos, "candidatos": ofrecidos,
                "formato_ml": a._formato_propuesto(texto), "trace": estado["trace"], "fuentes": ofrecidos}

    assert a._ofrecidos_en("Born In Roma está agotado. ¿Reservamos La Vie Est Belle?") == ["ed-004"]
    a.llm, a.llm_respaldo = object(), None
    a._ejecutar_llm = llm_simulado
    cfg = {"configurable": {"thread_id": "ana"}}
    a.grafo.invoke({"consulta": "quiero reservar Born In Roma", "cliente_id": "ana", "trace": []}, cfg)
    a.llm = None
    a.responder("sí", "ana")
    reserva = json.loads((tmp_path / "r.jsonl").read_text(encoding="utf-8"))
    assert (reserva["producto"], reserva["formato_ml"]) == ("ed-004", 5)


def test_con_llm_la_preferencia_se_guarda_aunque_el_modelo_no_lo_haga(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage

    class AgenteQueNoGuarda:
        def invoke(self, entrada, config=None):
            return {"messages": entrada["messages"] + [AIMessage("Te recomiendo Le Male. ¿Qué formato?")]}

    monkeypatch.setattr("src.agente.create_react_agent", lambda *a, **k: AgenteQueNoGuarda())
    memoria = MemoriaLargoPlazo(tmp_path / "m.json")
    a = AgenteElegantDrops(
        memoria=memoria, llm=object(), ruta_log=tmp_path / "log.jsonl",
        rutas=dict(ruta_reservas=tmp_path / "r.jsonl", ruta_derivaciones=tmp_path / "d.jsonl"))
    r = a.responder("busco algo dulce, presupuesto bajo 12000", "ana")
    assert any("guardar_preferencia" in t for t in r["trace"])
    assert "12000" in memoria.todos("ana")[0]
