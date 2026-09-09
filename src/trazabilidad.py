"""
Trazabilidad de datos (IL1.4).

Registra, para cada consulta procesada, qué documentos fueron recuperados y
con qué score, de modo que cualquier respuesta del agente pueda auditarse
hacia su fuente de origen (interna o externa). Se persiste en JSONL por
simplicidad; en producción se reemplazaría por una tabla en la base de datos
de la organización.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "trazabilidad.jsonl"


def registrar(consulta: str, contexto_usado: list[dict], respuesta: str) -> None:
    entrada = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "consulta": consulta,
        "documentos_recuperados": contexto_usado,
        "respuesta": respuesta,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
