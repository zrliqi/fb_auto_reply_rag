"""
Utility script to manage local Ollama server for this project.

Examples:
    python ollama_server.py status
    python ollama_server.py start
    python ollama_server.py ensure --model qwen2.5:3b
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def find_ollama_cmd() -> str | None:
    # 1) Direct PATH lookup
    try:
        completed = subprocess.run(
            ["where", "ollama"] if sys.platform == "win32" else ["which", "ollama"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            line = completed.stdout.strip().splitlines()[0].strip()
            if line:
                return line
    except Exception:
        pass

    # 2) Common Windows install paths
    if sys.platform == "win32":
        candidates = [
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
            Path("C:/Program Files (x86)/Ollama/ollama.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    return None


def is_ollama_running(timeout: float = 1.5) -> bool:
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def run_pull(model: str) -> int:
    ollama_cmd = find_ollama_cmd()
    if not ollama_cmd:
        print("Error: 'ollama' command not found. Install Ollama first.")
        return 127
    print(f"Pulling model: {model}")
    return subprocess.call([ollama_cmd, "pull", model])


def command_status() -> int:
    if is_ollama_running():
        print("Ollama server is running on http://127.0.0.1:11434")
        return 0
    print("Ollama server is NOT running.")
    return 1


def command_start() -> int:
    print("Starting Ollama server (foreground)...")
    print("Keep this terminal open while chatting.")
    ollama_cmd = find_ollama_cmd()
    if not ollama_cmd:
        print("Error: 'ollama' command not found. Install Ollama first.")
        return 127
    try:
        return subprocess.call([ollama_cmd, "serve"])
    except FileNotFoundError:
        print("Error: 'ollama' command not found. Install Ollama first.")
        return 127


def command_ensure(model: str | None, wait_seconds: int) -> int:
    ollama_cmd = find_ollama_cmd()
    if not ollama_cmd:
        print("Error: 'ollama' command not found. Install Ollama first.")
        return 127

    if is_ollama_running():
        print("Ollama server already running.")
    else:
        print("Ollama server not running. Launching background server...")
        try:
            # Start detached background server for this terminal session.
            subprocess.Popen(
                [ollama_cmd, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            print("Error: 'ollama' command not found. Install Ollama first.")
            return 127

        started = False
        for _ in range(wait_seconds):
            if is_ollama_running():
                started = True
                break
            time.sleep(1)

        if not started:
            print("Error: Ollama server did not become ready in time.")
            return 1

        print("Ollama server is ready.")

    if model:
        rc = run_pull(model)
        if rc != 0:
            print("Model pull failed.")
            return rc

    print("Ready. You can now run: python terminal_chat.py")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local Ollama server.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check if Ollama server is running.")
    sub.add_parser("start", help="Run 'ollama serve' in foreground.")

    ensure = sub.add_parser("ensure", help="Ensure server is running and optionally pull model.")
    ensure.add_argument("--model", default=None, help="Optional model name to pull (e.g. qwen2.5:3b).")
    ensure.add_argument("--wait", type=int, default=20, help="Seconds to wait for server readiness.")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "status":
        return command_status()
    if args.command == "start":
        return command_start()
    if args.command == "ensure":
        return command_ensure(args.model, args.wait)

    return 2


if __name__ == "__main__":
    # IDE-friendly default: if no args are provided, ensure Ollama is running
    # and the default chat model is available.
    if len(sys.argv) == 1:
        raise SystemExit(command_ensure("qwen2.5:3b", 20))
    raise SystemExit(main())
