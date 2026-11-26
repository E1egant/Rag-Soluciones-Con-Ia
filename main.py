"""
Demo del agente Elegant Drops.

Uso:
    python main.py                      # chat interactivo (memoria de la sesión)
    python main.py "consulta" [cliente] # un solo turno
    python main.py --olvidar <cliente>  # borra la memoria del cliente
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()
from src.agente import AgenteElegantDrops
from src.memoria import MemoriaLargoPlazo


def turno(agente, consulta, cliente):
    r = agente.responder(consulta, cliente_id=cliente)
    print(f"\n> {consulta}\n{r['texto']}")
    print("-- traza:")
    for t in r["trace"]:
        print("   ", t)
    print("-- fuentes:", r["fuentes"] or "ninguna", "| derivado:", r["derivado_a_humano"])


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--olvidar":
        print("Hechos borrados:", MemoriaLargoPlazo().olvidar(sys.argv[2]))
        return
    agente = AgenteElegantDrops()
    print("Modo:", "LLM (Gemini)" if agente.llm else "determinista (sin GEMINI_API_KEY)")
    if len(sys.argv) > 1:
        turno(agente, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "cliente-demo")
        return
    try:
        cliente = input("ID de cliente [cliente-demo]: ").strip() or "cliente-demo"
        while (consulta := input("\ntú (vacío para salir): ").strip()):
            turno(agente, consulta, cliente)
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
