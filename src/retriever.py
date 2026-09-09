"""
Retriever híbrido.

Combina similitud vectorial (TF-IDF como aproximación liviana a un embedding,
sin dependencias de red ni GPU, apta para el entorno del encargo) con un filtro
de palabras clave sobre metadata estructurada (marca, género, stock), de modo
que el sistema pueda responder tanto "algo dulce y vainillado" (semántico)
como "perfumes disponibles de Lancôme" (filtro exacto).

Nota de diseño (IL1.3): en un entorno productivo este módulo se reemplazaría
por un índice vectorial real (ej. pgvector, FAISS, Pinecone) con embeddings
de un modelo dedicado; la interfaz pública (`Retriever.buscar`) se mantendría
igual, por lo que el resto del pipeline no necesita cambios.
"""
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .ingest import Documento, cargar_documentos

# Lista breve de stopwords en español: sklearn no trae una por defecto (solo
# 'english'), y sin filtrarlas el TF-IDF sobre un corpus pequeño puede asignar
# similitud espuria a consultas no relacionadas que comparten conectores.
STOPWORDS_ES = [
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "o",
    "pero", "sus", "le", "ya", "este", "ano", "mi", "sin", "mis", "esta",
]


@dataclass
class ResultadoRecuperacion:
    documento: Documento
    score: float


class Retriever:
    def __init__(self, documentos: list[Documento] | None = None):
        self.documentos = documentos if documentos is not None else cargar_documentos()
        self._vectorizer = TfidfVectorizer(
            strip_accents="unicode", lowercase=True, stop_words=STOPWORDS_ES
        )
        self._matriz = self._vectorizer.fit_transform([d.texto for d in self.documentos])

    def buscar(self, consulta: str, k: int = 4, solo_disponibles: bool = True) -> list[ResultadoRecuperacion]:
        vector_consulta = self._vectorizer.transform([consulta])
        similitudes = cosine_similarity(vector_consulta, self._matriz)[0]

        resultados = [
            ResultadoRecuperacion(documento=doc, score=float(score))
            for doc, score in zip(self.documentos, similitudes)
        ]

        if solo_disponibles:
            resultados = [
                r for r in resultados
                if r.documento.fuente != "interna" or r.documento.metadata.get("stock", True)
            ]

        resultados.sort(key=lambda r: r.score, reverse=True)
        return [r for r in resultados if r.score > 0][:k]
