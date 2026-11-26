import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from streamlit.testing.v1 import AppTest

from src.analisis import analizar


def evento(span, padre, tipo, nombre, ms, **extra):
    return {"trace_id": "t", "span_id": span, "parent_span_id": padre, "tipo": tipo, "nombre": nombre,
            "duracion_ms": ms, "estado": "ok", "error": None, "atributos": {}, **extra}


def test_analisis_detecta_cuello_de_botella_error_y_bucle():
    eventos = [evento("r", None, "agente", "turno", 900)]
    eventos += [evento("l", "r", "llm", "modelo", 800, estado="error", error="Timeout: 504")]
    eventos += [evento(f"h{i}", "r", "herramienta", "buscar_catalogo", 5) for i in range(3)]
    a = analizar(eventos)
    assert a["cuellos_de_botella"][0]["span"] == "llm:modelo"
    assert a["errores"][0]["veces"] == 1 and a["bucles"][0]["veces"] == 3


@pytest.mark.parametrize("fuente", ["base", "mejorado", "holdout", "llm", "en vivo (uso real)"])
def test_dashboard_renderiza_cada_fuente_sin_excepciones(fuente):
    app = AppTest.from_file(str(RAIZ / "dashboard.py"), default_timeout=60).run()
    if fuente not in app.sidebar.selectbox[0].options:
        pytest.skip(f"no existe la corrida {fuente}")
    app.sidebar.selectbox[0].select(fuente).run()
    assert not app.exception
