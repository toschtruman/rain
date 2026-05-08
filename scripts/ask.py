"""
Interactive CLI to ask the ops agent questions.

Usage:
    python scripts/ask.py "How many open searches do we have in leadership?"
    python scripts/ask.py "Which deals have gone stale in the last two weeks?"
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent.ops_agent import run_agent


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/ask.py \"your question here\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"Question: {question}\n")
    answer = run_agent(question)
    print(answer)


if __name__ == "__main__":
    main()
