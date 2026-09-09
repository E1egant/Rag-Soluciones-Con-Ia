"""
Demo de línea de comandos del agente Elegant Drops.

Uso:
    python main.py "Busco algo dulce y vainillado, presupuesto bajo 12000 el 10ml"
"""
import sys

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
