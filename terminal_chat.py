"""
Terminal chat interface for the RAG system.

Usage:
    python terminal_chat.py
    python terminal_chat.py --user-id my_user
"""

import argparse
import logging

from rag import RAGSystem, init_memory_db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with the RAG bot from terminal.")
    parser.add_argument(
        "--user-id",
        default=None,
        help="Optional user id to persist conversation memory in SQLite.",
    )
    parser.add_argument(
        "--upload-folder",
        default="uploads",
        help="Folder containing documents for RAG knowledge base.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = build_parser().parse_args()

    init_memory_db()
    rag_system = RAGSystem(upload_folder=args.upload_folder)

    print("Terminal RAG Chat")
    print("Commands: /exit, /reload")
    if args.user_id:
        print(f"Memory mode: ON (user_id={args.user_id})")
    else:
        print("Memory mode: OFF (ephemeral session)")

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input.lower() in {"/exit", "exit", "quit"}:
            print("Bye.")
            break

        if user_input.lower() == "/reload":
            print("Reloading knowledge base...")
            rag_system.reload()
            print("Knowledge base reloaded.")
            continue

        result = rag_system.query(user_input, user_id=args.user_id)
        if isinstance(result, tuple):
            payload, _status = result
            print(f"Bot: {payload.get('response') or payload.get('error')}")
        else:
            print(f"Bot: {result.get('response') or result.get('error')}")


if __name__ == "__main__":
    main()
