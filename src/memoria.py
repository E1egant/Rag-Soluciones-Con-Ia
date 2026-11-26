"""
Memoria de largo plazo del agente (IE3 / IE4).

Guarda hechos duraderos por cliente (gustos, presupuesto, alergias) en JSON y
los recupera por similitud semántica (TF-IDF + coseno) con la consulta
actual. La memoria de corto plazo (turnos de la conversación) la maneja el
checkpointer de LangGraph por `thread_id`; ver `agente.py`.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .retriever import STOPWORDS_ES

RUTA_POR_DEFECTO = Path(__file__).resolve().parent.parent / "data" / "memoria_clientes.json"
UMBRAL_RECUERDO = 0.05


class MemoriaLargoPlazo:
    def __init__(self, ruta: Path | None = None):
        self.ruta = Path(ruta) if ruta else RUTA_POR_DEFECTO

    def _cargar(self) -> dict[str, list[dict]]:
        if not self.ruta.exists():
            return {}
        with open(self.ruta, encoding="utf-8") as f:
            return json.load(f)

    def guardar(self, cliente_id: str, hecho: str) -> None:
        datos = self._cargar()
        hechos = datos.setdefault(cliente_id, [])
        if any(h["hecho"].lower() == hecho.lower() for h in hechos):
            return
        hechos.append({"hecho": hecho, "fecha": datetime.now(timezone.utc).isoformat()})
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)

    def olvidar(self, cliente_id: str) -> int:
        """Derecho de supresión: borra todo lo recordado del cliente y devuelve cuántos hechos eran."""
        datos = self._cargar()
        borrados = len(datos.pop(cliente_id, []))
        if borrados:
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2)
        return borrados

    def todos(self, cliente_id: str) -> list[str]:
        return [h["hecho"] for h in self._cargar().get(cliente_id, [])]

    def recuperar(self, cliente_id: str, consulta: str, k: int = 3) -> list[str]:
        hechos = self.todos(cliente_id)
        if not hechos:
            return []
        vectorizer = TfidfVectorizer(strip_accents="unicode", stop_words=STOPWORDS_ES)
        try:
            matriz = vectorizer.fit_transform(hechos + [consulta])
        except ValueError:
            return []
        sims = cosine_similarity(matriz[-1], matriz[:-1])[0]
        ranking = sorted(zip(hechos, sims), key=lambda x: x[1], reverse=True)
        return [h for h, s in ranking[:k] if s > UMBRAL_RECUERDO]
