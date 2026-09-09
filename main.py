"""
Demo de línea de comandos del agente Elegant Drops.

Uso:
    python main.py "Busco algo dulce y vainillado, presupuesto bajo 12000 el 10ml"
"""
import sys

# La consola de Windows suele usar cp1252, que no soporta los emojis que el
# propio prompt de marca permite en las respuestas; sin esto, main.py crashea
# al imprimir en vez de mostrar la respuesta del agente.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()
from src.orchestrator import AgenteElegantDrops


def main():
    consulta = " ".join(sys.argv[1:]) or "Busco un perfume fresco para el día, no muy caro"
    agente = AgenteElegantDrops()
    respuesta = agente.responder(consulta)

    print("=" * 60)
    print("CONSULTA:", consulta)
    print("=" * 60)
    print(respuesta.texto)
    print("-" * 60)
    print("Fuentes usadas (trazabilidad):", respuesta.fuentes_usadas or "ninguna")
    print("Derivado a humano:", respuesta.derivado_a_humano)


if __name__ == "__main__":
    main()
