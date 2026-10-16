import os
import sqlite3
import sys

IS_FROZEN = getattr(sys, "frozen", False)

def runtime_dir() -> str:
    return os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))

def get_db_path() -> str:
    # app.py define PADARIA_DB_PATH apontando pro banco ao lado do EXE/pasta
    p = os.environ.get("PADARIA_DB_PATH")
    if p:
        return p
    return os.path.join(runtime_dir(), "dados.db")

def conectar():
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Melhor concorrência e estabilidade em uso real (caixa/cozinha)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass

    return conn
