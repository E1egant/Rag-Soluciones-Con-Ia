"""
Planificación de tareas (IE5).

Convierte la consulta en un plan ordenado por prioridad (1 = primero) a
partir de la intención detectada, y lo ajusta con `replanificar` cuando una
observación cambia las condiciones (producto agotado, sin resultados,
reserva rechazada).
"""
import re
from dataclasses import dataclass, field

from .normalizacion import sin_tildes

PATRON_RESERVA = r"\b(reserv\w*|apart\w*|comprar|quiero\s+(el|ese|este|la)\b|lo\s+quiero)"
PATRON_NEGACION = r"\bno\s+(\w+\s+){0,2}?(reserv|apart|compr)\w*"
PATRON_COMPARAR = r"\b(compar\w*|diferencias?|vs|versus|cu[aá]l\s+es\s+mejor)\b"
PATRON_PREFERENCIA = r"\b(me\s+gusta\w*|prefiero|al[eé]rgic\w*|siempre\s+uso|no\s+me\s+gusta\w*|presupuesto)\b"
# Un número seguido de "ml" es un formato, no un presupuesto.
PATRON_PRESUPUESTO = (r"(?:bajo|hasta|m[aá]ximo|presupuesto(?:\s+de)?|menos\s+de)\s*\$?\s*"
                      r"(\d+(?:\.\d{3})*)(?![\d.]*\s*ml\b)(?:\s*(mil|lucas?|k)\b)?")
PRESUPUESTO_MINIMO_CLP = 1000
PATRON_FORMATO = r"(\d+)\s*ml"


@dataclass
class Paso:
    prioridad: int
    herramienta: str
    motivo: str


@dataclass
class Plan:
    intencion: str
    pasos: list[Paso] = field(default_factory=list)
    max_precio_10ml: int = 0
    formato_ml: int = 10
    formato_explicito: bool = False
    objetivo: list[str] = field(default_factory=list)

    def siguiente(self) -> Paso | None:
        return min(self.pasos, key=lambda p: p.prioridad) if self.pasos else None

    def completar(self, paso: Paso) -> None:
        self.pasos.remove(paso)

    def nombres(self) -> list[str]:
        return [p.herramienta for p in sorted(self.pasos, key=lambda p: p.prioridad)]


def detectar_intencion(consulta: str) -> str:
    t = sin_tildes(consulta)
    if re.search(PATRON_COMPARAR, t):
        return "comparar"
    if re.search(PATRON_RESERVA, t) and not re.search(PATRON_NEGACION, t):
        return "reservar"
    return "recomendar"


def planificar(consulta: str) -> Plan:
    t = consulta.lower()
    intencion = detectar_intencion(consulta)
    plan = Plan(intencion=intencion)

    m = re.search(PATRON_PRESUPUESTO, t)
    if m:
        monto = int(m.group(1).replace(".", "")) * (1000 if m.group(2) else 1)
        if monto >= PRESUPUESTO_MINIMO_CLP:
            plan.max_precio_10ml = monto
    m = re.search(PATRON_FORMATO, t)
    if m:
        plan.formato_ml = int(m.group(1))
        plan.formato_explicito = True

    plan.pasos.append(Paso(1, "recordar_preferencias", "recuperar gustos previos del cliente"))
    plan.pasos.append(Paso(2, "buscar_catalogo", "obtener candidatos que calcen con la consulta"))
    if intencion == "comparar":
        plan.pasos.append(Paso(3, "comparar_productos", "contrastar candidatos"))
    elif intencion == "reservar":
        plan.pasos.append(Paso(3, "consultar_stock", "confirmar stock antes de escribir"))
        plan.pasos.append(Paso(4, "reservar_decant", "registrar la reserva"))
    if re.search(PATRON_PREFERENCIA, t) or plan.max_precio_10ml:
        plan.pasos.append(Paso(5, "guardar_preferencia", "persistir dato duradero del cliente"))
    return plan


def replanificar(plan: Plan, herramienta: str, observacion: str) -> str | None:
    """Ajusta el plan según la observación; devuelve la razón del cambio o None."""
    if herramienta == "buscar_catalogo" and observacion == "SIN_RESULTADOS":
        plan.pasos = [Paso(1, "derivar_a_humano", "catálogo sin candidatos: derivar")]
        return "sin resultados: se cancela el plan y se deriva a humano"
    if herramienta in ("consultar_stock", "reservar_decant") and (
        "AGOTADO" in observacion or "RECHAZADA" in observacion
    ):
        plan.pasos = [p for p in plan.pasos if p.herramienta != "reservar_decant"]
        plan.pasos.append(Paso(3, "alternativa", "producto no reservable: ofrecer alternativa disponible"))
        return "producto no reservable: se reemplaza la reserva por una alternativa"
    return None
