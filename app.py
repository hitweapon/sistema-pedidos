# app.py — Empório dos Pães Santo Antônio (ENTREGÁVEL / FIX FINAL)
# ✅ RAW direto (sem tela de impressão do navegador)
# ✅ Corte automático ESC/POS
# ✅ Opção 1/2 do prato do dia aparece quando item for Marmita/Marmitex Opção 1/2
# ✅ Cabeçalho: Delivery => "ENTREGA - COZINHA" | Balcão => "RETIRADA - COZINHA"
# ✅ config.json ao lado do EXE primeiro
# ✅ DB ao lado do EXE + backup ao fechar
# ✅ Admin / Caixa / Cozinha / Histórico / Fechar turno
# ✅ Forma de pagamento salva no pedido + imprime na comanda

from flask import Flask, render_template, request, redirect, session
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta
from db import conectar
import json, os, sqlite3, sys, unicodedata

import atexit
import signal

# =====================
# IMPRESSÃO WINDOWS (RAW)
# =====================
HAS_WIN32PRINT = False
try:
    import win32print  # type: ignore
    HAS_WIN32PRINT = True
except Exception:
    HAS_WIN32PRINT = False

# =====================
# PATHS (VSCode / CMD / EXE)
# =====================
IS_FROZEN = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

def base_dir_for_templates_static() -> str:
    if IS_FROZEN:
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def runtime_dir() -> str:
    return os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))

BASE_DIR = base_dir_for_templates_static()
RUNTIME_DIR = runtime_dir()

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

UPLOAD_FOLDER = os.path.join(RUNTIME_DIR, "static", "img")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def resource_path_near_exe_first(rel_path: str) -> str:
    p1 = os.path.join(RUNTIME_DIR, rel_path)
    if os.path.exists(p1):
        return p1

    if IS_FROZEN:
        p2 = os.path.join(sys._MEIPASS, rel_path)
        if os.path.exists(p2):
            return p2

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path)

# =====================
# CONFIG
# =====================
CONFIG_PATH = resource_path_near_exe_first("config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TEST_PRINT = bool(CONFIG.get("modo_teste_impressao", False))
PORTA = int(CONFIG.get("porta", 5000))
PIN = str(CONFIG.get("pin_cozinha", "")).strip()
NOME_ESTAB = CONFIG.get("nome_estabelecimento", "Emporio dos Paes Santo Antonio")

# =====================
# DB PATH (lado do EXE) + export pro db.py
# =====================
DB_FILE = str(CONFIG.get("db_file", "dados.db")).strip() or "dados.db"
DB_PATH = os.path.join(RUNTIME_DIR, DB_FILE)
os.environ["PADARIA_DB_PATH"] = DB_PATH

# =====================
# APP
# =====================
app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)

# =====================
# ROTA INICIAL (LOGIN)
# =====================
@app.route("/")
def home():
    if session.get("logado"):
        return redirect("/caixa")
    return redirect("/login")

app.secret_key = "chave_super_secreta"

try:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
except Exception:
    socketio = SocketIO(app, cors_allowed_origins="*")

# =====================
# HELPERS
# =====================
def _safe(s):
    return (s or "").strip()

def normalize_text(s: str) -> str:
    s = (s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.upper().strip()

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def normalize_pagamento(pg: str) -> str:
    """
    Normaliza a forma de pagamento para guardar no DB e imprimir.
    Você pode ajustar a lista como quiser (PIX, DINHEIRO, DEBITO, CREDITO...).
    """
    pg = (pg or "").strip()
    if not pg:
        return ""
    pgu = normalize_text(pg)
    # alguns aliases comuns
    aliases = {
        "CARTAO": "CARTAO",
        "CARTAO DEBITO": "DEBITO",
        "DEBITO": "DEBITO",
        "CARTAO CREDITO": "CREDITO",
        "CREDITO": "CREDITO",
        "PIX": "PIX",
        "DINHEIRO": "DINHEIRO",
        "VALE": "VALE",
        "VR": "VALE",
        "VA": "VALE",
        "IFood".upper(): "IFOOD",
        "IFOOD": "IFOOD",
        "PAGSEGURO": "PAGSEGURO",
        "MERCADOPAGO": "MERCADOPAGO",
    }
    return aliases.get(pgu, pgu)

# =====================
# IMPRESSÃO - HELPERS
# =====================
def get_printers_list():
    lst = CONFIG.get("impressoras") or CONFIG.get("printers") or None
    if isinstance(lst, list) and len(lst) > 0:
        return [str(x).strip() for x in lst if str(x).strip()]
    return ["CONFEITARIA", "PADARIA", "ELGIN_I8_RAW"]

def get_cashier_printer_name() -> str:
    p = (CONFIG.get("impressora_caixa") or CONFIG.get("printer_caixa") or "ELGIN_I8_RAW")
    return str(p).strip() or "ELGIN_I8_RAW"

# ========= ESC/POS =========
ESC = b"\x1b"
GS  = b"\x1d"

CMD_INIT         = ESC + b"@"
CMD_ALIGN_LEFT   = ESC + b"a" + b"\x00"
CMD_ALIGN_CENTER = ESC + b"a" + b"\x01"
CMD_BOLD_ON      = ESC + b"E" + b"\x01"
CMD_BOLD_OFF     = ESC + b"E" + b"\x00"
CMD_DOUBLE_ON    = GS  + b"!" + b"\x11"   # 2x altura e 2x largura
CMD_DOUBLE_OFF   = GS  + b"!" + b"\x00"
CMD_CUT_FULL     = GS  + b"V" + b"\x00"   # corte total
CMD_FEED         = b"\n" * 6

# largura típica 58mm: 32 colunas
PAPER_COLS = 32

def _ascii_safe(text: str) -> str:
    s = (text or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("´", "'")
    s = s.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
    s = " ".join(s.split())
    return s

def _enc(text: str) -> bytes:
    return (_ascii_safe(text) + "\n").encode("ascii", errors="ignore")

def _line(text: str) -> bytes:
    return _enc(text)

def _hr(ch: str = "-") -> bytes:
    ch = (ch or "-")[:1]
    return _line(ch * PAPER_COLS)

def _wrap(text: str, width: int = PAPER_COLS):
    s = _ascii_safe(text)
    if not s:
        return []
    words = s.split(" ")
    lines = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def _lr(left: str, right: str, width: int = PAPER_COLS) -> str:
    left = _ascii_safe(left)
    right = _ascii_safe(right)
    if len(right) >= width:
        return right[:width]
    max_left = width - len(right) - 1
    if max_left < 0:
        max_left = 0
    if len(left) > max_left:
        left = left[:max_left]
    spaces = " " * (width - len(left) - len(right))
    return f"{left}{spaces}{right}"

def format_money(v: float) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "0.00"

def build_ticket_bytes(pedido_row, itens_rows, prato1: str, prato2: str) -> bytes:
    # ===== helpers locais (não mexe no resto do arquivo) =====
    COLS = 32

    def _clean(text: str) -> str:
        s = (text or "").strip()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        s = " ".join(s.split())
        s = s.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
        return s

    def _upper(text: str) -> str:
        return _clean(text).upper()

    def _wrap(text: str, width: int) -> list:
        t = _upper(text)
        if not t:
            return []
        words = t.split(" ")
        lines = []
        cur = ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def _center(line: str, width: int) -> str:
        line = _upper(line)
        if len(line) >= width:
            return line[:width]
        pad = (width - len(line)) // 2
        return (" " * pad) + line

    def _lr(left: str, right: str, width: int) -> str:
        l = _upper(left)
        r = _upper(right)
        if len(r) > width:
            r = r[-width:]
        space = width - len(r) - 1
        if space < 0:
            space = 0
        if len(l) > space:
            l = l[:space]
        return l + (" " * (width - len(l) - len(r))) + r

    def money(v: float) -> str:
        try:
            return f"{float(v):.2f}"
        except Exception:
            return "0.00"

    # ===== dados do pedido =====
    pid = pedido_row["id"]
    canal = _upper(pedido_row["canal"])
    turno = _upper(pedido_row["turno"])
    status = _upper(pedido_row["status"])
    total = float(pedido_row["total"] or 0)

    cliente = _upper(pedido_row["cliente_nome"])
    tel = _upper(pedido_row["cliente_tel"])
    rua = _upper(pedido_row["endereco"])
    numero = _upper(pedido_row["cliente_numero"])
    bairro = _upper(pedido_row["cliente_bairro"])
    obs = _upper(pedido_row["observacoes"])

    # >>> FORMA DE PAGAMENTO (NOVO)
    forma_pagamento = _upper(pedido_row["forma_pagamento"] if "forma_pagamento" in pedido_row.keys() else "")

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    prato1 = _upper(prato1 or "")
    prato2 = _upper(prato2 or "")

    header = "ENTREGA - COZINHA" if canal == "DELIVERY" else "RETIRADA - COZINHA"

    out = bytearray()
    out += CMD_INIT
    out += CMD_ALIGN_CENTER

    out += _line("=" * COLS)

    out += CMD_BOLD_ON
    out += _line(_center(header, COLS))
    out += CMD_BOLD_OFF

    out += _line("=" * COLS)

    out += _line(_center(agora, COLS))

    nome_lines = _wrap(NOME_ESTAB, COLS)
    if not nome_lines:
        nome_lines = ["EMPORIO"]
    if len(nome_lines) > 2:
        joined = " ".join(nome_lines)
        nome_lines = [joined[:COLS], joined[COLS:COLS*2].strip()]

    out += CMD_BOLD_ON
    for ln in nome_lines:
        out += _line(_center(ln, COLS))
    out += CMD_BOLD_OFF

    out += _line("-" * COLS)

    out += CMD_BOLD_ON + CMD_DOUBLE_ON
    out += _line(f"PEDIDO {pid}")
    out += CMD_DOUBLE_OFF + CMD_BOLD_OFF

    out += _line(_center(f"{canal} | {turno} | {status}", COLS))
    out += _line("-" * COLS)

    # >>> IMPRIME FORMA DE PAGAMENTO (NOVO)
    if forma_pagamento:
        out += CMD_ALIGN_LEFT
        out += CMD_BOLD_ON + _line("PAGAMENTO") + CMD_BOLD_OFF
        out += CMD_BOLD_ON + _line(f"{forma_pagamento}"[:COLS]) + CMD_BOLD_OFF
        out += _line("-" * COLS)

    # ===== ITENS =====
    out += CMD_ALIGN_LEFT
    out += CMD_BOLD_ON + _line("ITENS") + CMD_BOLD_OFF

    if not itens_rows:
        out += _line("(SEM ITENS)")
    else:
        for it in itens_rows:
            nome = _upper(it["nome"])
            qtd = int(it["quantidade"] or 1)
            pesado_valor = float(it["pesado_valor"] or 0)
            subtotal = float(it["subtotal"] or 0)

            preco = pesado_valor if pesado_valor > 0 else subtotal

            left = f"{qtd}X {nome}"
            right = f"R$ {money(preco)}"
            out += _line(_lr(left, right, COLS))

            nrm = normalize_text(nome)
            is_marmita = ("MARMITA" in nrm) or ("MARMITEX" in nrm)
            op1 = ("OPCAO 1" in nrm) or ("OP 1" in nrm) or ("OP1" in nrm)
            op2 = ("OPCAO 2" in nrm) or ("OP 2" in nrm) or ("OP2" in nrm)

            if is_marmita and op1 and prato1:
                out += CMD_BOLD_ON
                out += _line(f"OPCAO 1: {prato1}"[:COLS])
                out += CMD_BOLD_OFF
            elif is_marmita and op2 and prato2:
                out += CMD_BOLD_ON
                out += _line(f"OPCAO 2: {prato2}"[:COLS])
                out += CMD_BOLD_OFF

    out += _line("-" * COLS)

    # ===== CLIENTE / ENTREGA =====
    if canal == "DELIVERY":
        out += CMD_BOLD_ON + _line("CLIENTE / ENTREGA") + CMD_BOLD_OFF

        if cliente:
            out += CMD_BOLD_ON + _line(f"NOME: {cliente}"[:COLS]) + CMD_BOLD_OFF
        if tel:
            out += _line(f"TELEFONE: {tel}"[:COLS])

        if rua:
            out += _line(f"ENDERECO: {rua}"[:COLS])

        linha2 = ""
        if numero:
            linha2 += numero
        if bairro:
            if linha2:
                linha2 += " - "
            linha2 += bairro
        if linha2:
            out += _line(linha2[:COLS])

        out += _line("-" * COLS)
    else:
        if cliente:
            out += CMD_BOLD_ON + _line("CLIENTE") + CMD_BOLD_OFF
            out += CMD_BOLD_ON + _line(f"NOME: {cliente}"[:COLS]) + CMD_BOLD_OFF
            out += _line("-" * COLS)

    # ===== OBS =====
    if obs:
        out += CMD_BOLD_ON + _line("OBS:") + CMD_BOLD_OFF
        for ln in _wrap(obs, COLS):
            out += CMD_BOLD_ON + _line(ln) + CMD_BOLD_OFF
        out += _line("-" * COLS)

    # ===== TAXA DE ENTREGA =====
    taxa_entrega = float(pedido_row["taxa_entrega"] or 0) if "taxa_entrega" in pedido_row.keys() else 0.0
    if taxa_entrega > 0:
        out += _line(_lr("TAXA ENTREGA:", f"R$ {money(taxa_entrega)}", COLS))

    # ===== TOTAL =====
    out += _line(_lr("TOTAL:", f"R$ {money(total)}", COLS))
    out += _line("=" * COLS)

    out += CMD_ALIGN_CENTER
    out += CMD_BOLD_ON + CMD_DOUBLE_ON
    out += _line(f"TOTAL:  R$ {money(total)}")
    out += CMD_DOUBLE_OFF + CMD_BOLD_OFF

    out += _line("=" * COLS)
    out += _line("NAO FISCAL")

    out += CMD_FEED
    out += CMD_CUT_FULL

    return bytes(out)

def print_bytes_to_printer(printer_name: str, payload: bytes):
    if not HAS_WIN32PRINT:
        raise RuntimeError("win32print nao esta disponivel (instale pywin32).")

    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(hPrinter, 1, ("Pedido Padaria", None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, payload)
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)

def print_to_all_printers(payload: bytes):
    if TEST_PRINT:
        try:
            out_path = os.path.join(RUNTIME_DIR, "ultima_comanda_raw.bin")
            with open(out_path, "wb") as f:
                f.write(payload)
            print(f"[MODO TESTE] RAW salvo em: {out_path}")
            return []
        except Exception as e:
            print(f"[MODO TESTE] Falha ao salvar RAW: {e}")
            return [f"TEST_MODE: {e}"]

    errors = []
    for prn in get_printers_list():
        try:
            print_bytes_to_printer(prn, payload)
            print(f"Impresso em: {prn}")
        except Exception as e:
            errors.append(f"{prn}: {e}")
    return errors

def print_to_cashier_printer(payload: bytes):
    if TEST_PRINT:
        try:
            out_path = os.path.join(RUNTIME_DIR, "ultima_comanda_caixa_raw.bin")
            with open(out_path, "wb") as f:
                f.write(payload)
            print(f"[TESTE] RAW CAIXA salvo em: {out_path}")
            return []
        except Exception as e:
            print(f"[TESTE] Falha ao salvar RAW CAIXA: {e}")
            return [f"TEST_MODE: {e}"]

    errors = []
    prn = get_cashier_printer_name()
    try:
        print_bytes_to_printer(prn, payload)
        print(f"Impresso no CAIXA: {prn}")
    except Exception as e:
        errors.append(f"{prn}: {e}")
    return errors

# =====================
# DB
# =====================
def db_conn():
    conn = conectar()
    try:
        conn.row_factory = sqlite3.Row
    except Exception:
        pass
    return conn

def _table_columns(conn, table_name: str):
    cur = conn.cursor()
    rows = cur.execute(f"PRAGMA table_info({table_name})").fetchall()
    cols = set()
    for r in rows:
        try:
            cols.add(r["name"])
        except Exception:
            cols.add(r[1])
    return cols

def _ensure_column(conn, table: str, col: str, coltype: str):
    cols = _table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

def get_config_value(conn, chave: str, default: str = "") -> str:
    cur = conn.cursor()
    row = cur.execute("SELECT valor FROM config_app WHERE chave=?", (chave,)).fetchone()
    if row and row["valor"] is not None:
        return str(row["valor"])
    return default

def set_config_value(conn, chave: str, valor: str):
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES (?, ?)", (chave, valor))

def get_prato_do_dia_opts():
    conn = db_conn()
    try:
        p1 = get_config_value(conn, "prato_do_dia_1", "").strip()
        p2 = get_config_value(conn, "prato_do_dia_2", "").strip()

        antigo = get_config_value(conn, "prato_do_dia", "").strip()
        if (not p1) and antigo:
            p1 = antigo

        return p1, p2
    finally:
        conn.close()

def set_prato_do_dia_opts(p1: str, p2: str):
    conn = db_conn()
    try:
        set_config_value(conn, "prato_do_dia_1", (p1 or "").strip())
        set_config_value(conn, "prato_do_dia_2", (p2 or "").strip())
        conn.commit()
    finally:
        conn.close()

def get_taxa_entrega() -> float:
    conn = db_conn()
    try:
        try:
            return max(0.0, float(get_config_value(conn, "taxa_entrega", "0") or 0))
        except (TypeError, ValueError):
            return 0.0
    finally:
        conn.close()

def set_taxa_entrega(valor: str):
    try:
        taxa = max(0.0, float((valor or "0").replace(",", ".")))
    except (TypeError, ValueError):
        taxa = 0.0
    conn = db_conn()
    try:
        set_config_value(conn, "taxa_entrega", f"{taxa:.2f}")
        conn.commit()
    finally:
        conn.close()

def ensure_schema():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categorias(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        ordem INTEGER DEFAULT 0,
        ativo INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS produtos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        preco REAL DEFAULT 0,
        imagem TEXT,
        categoria_id INTEGER,
        pesado INTEGER DEFAULT 0,
        ativo INTEGER DEFAULT 1,
        FOREIGN KEY (categoria_id) REFERENCES categorias(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS config_app(
        chave TEXT PRIMARY KEY,
        valor TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pedidos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        criado_em TEXT,
        turno TEXT,
        status TEXT,
        destino TEXT,
        canal TEXT,
        cliente_nome TEXT,
        cliente_tel TEXT,
        endereco TEXT,
        cliente_numero TEXT,
        cliente_bairro TEXT,
        observacoes TEXT,
        total REAL DEFAULT 0,
        forma_pagamento TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clientes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        telefone TEXT DEFAULT '',
        endereco TEXT DEFAULT '',
        numero TEXT DEFAULT '',
        bairro TEXT DEFAULT '',
        criado_em TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS itens_pedido(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL,
        produto_id INTEGER,
        nome TEXT NOT NULL,
        quantidade INTEGER DEFAULT 1,
        preco_unit REAL DEFAULT 0,
        subtotal REAL DEFAULT 0,
        pesado_valor REAL DEFAULT 0,
        FOREIGN KEY(pedido_id) REFERENCES pedidos(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS historico_pedidos(
        id INTEGER PRIMARY KEY,
        criado_em TEXT,
        fechado_em TEXT,
        turno TEXT,
        status TEXT,
        destino TEXT,
        canal TEXT,
        cliente_nome TEXT,
        cliente_tel TEXT,
        endereco TEXT,
        cliente_numero TEXT,
        cliente_bairro TEXT,
        observacoes TEXT,
        total REAL DEFAULT 0,
        forma_pagamento TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS historico_itens(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pedido_id INTEGER NOT NULL,
        produto_id INTEGER,
        nome TEXT NOT NULL,
        quantidade INTEGER DEFAULT 1,
        preco_unit REAL DEFAULT 0,
        subtotal REAL DEFAULT 0,
        pesado_valor REAL DEFAULT 0
    )
    """)

    _ensure_column(conn, "pedidos", "cliente_id", "INTEGER")
    _ensure_column(conn, "historico_pedidos", "cliente_id", "INTEGER")
    _ensure_column(conn, "pedidos", "cliente_numero", "TEXT")
    _ensure_column(conn, "pedidos", "cliente_bairro", "TEXT")
    _ensure_column(conn, "historico_pedidos", "cliente_numero", "TEXT")
    _ensure_column(conn, "historico_pedidos", "cliente_bairro", "TEXT")

    # >>> NOVO: garante coluna forma_pagamento (caso DB antigo)
    _ensure_column(conn, "pedidos", "forma_pagamento", "TEXT")
    _ensure_column(conn, "historico_pedidos", "forma_pagamento", "TEXT")
    _ensure_column(conn, "pedidos", "taxa_entrega", "REAL DEFAULT 0")
    _ensure_column(conn, "historico_pedidos", "taxa_entrega", "REAL DEFAULT 0")

    row = cur.execute("SELECT valor FROM config_app WHERE chave='turno_atual'").fetchone()
    if not row:
        cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES ('turno_atual', 'MANHA')")

    row = cur.execute("SELECT valor FROM config_app WHERE chave='prato_do_dia_1'").fetchone()
    if not row:
        cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES ('prato_do_dia_1', '')")

    row = cur.execute("SELECT valor FROM config_app WHERE chave='prato_do_dia_2'").fetchone()
    if not row:
        cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES ('prato_do_dia_2', '')")

    row = cur.execute("SELECT valor FROM config_app WHERE chave='taxa_entrega'").fetchone()
    if not row:
        cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES ('taxa_entrega', '0.00')")

    conn.commit()
    conn.close()

def limpar_historico_antigo():
    limite = (datetime.now() - timedelta(days=92)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db_conn()
    cur = conn.cursor()
    ids = cur.execute("SELECT id FROM historico_pedidos WHERE criado_em < ?", (limite,)).fetchall()
    if ids:
        id_list = [r["id"] for r in ids]
        qmarks = ",".join(["?"] * len(id_list))
        cur.execute(f"DELETE FROM historico_itens WHERE pedido_id IN ({qmarks})", tuple(id_list))
        cur.execute(f"DELETE FROM historico_pedidos WHERE id IN ({qmarks})", tuple(id_list))
    conn.commit()
    conn.close()

def get_turno_global():
    conn = db_conn()
    cur = conn.cursor()
    row = cur.execute("SELECT valor FROM config_app WHERE chave='turno_atual'").fetchone()
    conn.close()
    return (row["valor"] if row and row["valor"] else "MANHA")

def set_turno_global(turno):
    turno = (turno or "MANHA").upper().strip()
    if turno not in ("MANHA", "TARDE", "NOITE"):
        turno = "MANHA"
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO config_app (chave, valor) VALUES ('turno_atual', ?)", (turno,))
    conn.commit()
    conn.close()

def itens_do_pedido(conn, pedido_id):
    cur = conn.cursor()
    return cur.execute(
        "SELECT * FROM itens_pedido WHERE pedido_id=? ORDER BY id ASC",
        (pedido_id,)
    ).fetchall()

def calcular_total_itens(conn, pedido_id):
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COALESCE(SUM(subtotal),0) AS t FROM itens_pedido WHERE pedido_id=?",
        (pedido_id,)
    ).fetchone()
    return float(row["t"] or 0)

def pedido_payload(conn, pedido_id):
    cur = conn.cursor()
    p = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not p:
        return None
    itens = itens_do_pedido(conn, pedido_id)
    return {
        "id": p["id"],
        "criado_em": p["criado_em"],
        "turno": p["turno"],
        "status": p["status"],
        "destino": p["destino"],
        "canal": p["canal"],
        "cliente_nome": p["cliente_nome"] or "",
        "cliente_tel": p["cliente_tel"] or "",
        "endereco": p["endereco"] or "",
        "cliente_numero": p["cliente_numero"] or "",
        "cliente_bairro": p["cliente_bairro"] or "",
        "observacoes": p["observacoes"] or "",
        "total": float(p["total"] or 0),
        "forma_pagamento": (p["forma_pagamento"] or ""),
        "itens": [
            {
                "nome": it["nome"],
                "quantidade": int(it["quantidade"] or 1),
                "subtotal": float(it["subtotal"] or 0),
                "pesado_valor": float(it["pesado_valor"] or 0),
            }
            for it in itens
        ]
    }

def mover_pedido_para_historico(conn, pedido_id, fechado_em=None):
    fechado_em = fechado_em or now_str()
    cur = conn.cursor()

    p = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not p:
        return

    cur.execute("""
        INSERT OR REPLACE INTO historico_pedidos
        (id, criado_em, fechado_em, turno, status, destino, canal,
         cliente_nome, cliente_tel, endereco, cliente_numero, cliente_bairro,
         observacoes, total, forma_pagamento, taxa_entrega)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        p["id"], p["criado_em"], fechado_em, p["turno"], p["status"], p["destino"], p["canal"],
        p["cliente_nome"], p["cliente_tel"], p["endereco"], p["cliente_numero"], p["cliente_bairro"],
        p["observacoes"], p["total"], (p["forma_pagamento"] or ""), (p["taxa_entrega"] or 0)
    ))

    itens = itens_do_pedido(conn, pedido_id)
    for it in itens:
        cur.execute("""
            INSERT INTO historico_itens
            (pedido_id, produto_id, nome, quantidade, preco_unit, subtotal, pesado_valor)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            p["id"], it["produto_id"], it["nome"], it["quantidade"],
            it["preco_unit"], it["subtotal"], it["pesado_valor"]
        ))

    cur.execute("DELETE FROM itens_pedido WHERE pedido_id=?", (pedido_id,))
    cur.execute("DELETE FROM pedidos WHERE id=?", (pedido_id,))

# =====================
# RAW PRINT ENTRYPOINT
# =====================
def imprimir_pedido_raw(pedido_id: int, modo: str = "CAIXA"):
    """
    modo:
      - COZINHA => imprime em todas CONFIG['impressoras']
      - CAIXA   => imprime só em CONFIG['impressora_caixa'] (fallback ELGIN)
    """
    conn = db_conn()
    cur = conn.cursor()
    pedido = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return False

    itens = cur.execute(
        "SELECT * FROM itens_pedido WHERE pedido_id=? ORDER BY id ASC",
        (pedido_id,)
    ).fetchall()
    conn.close()

    prato1, prato2 = get_prato_do_dia_opts()
    payload = build_ticket_bytes(pedido, itens, prato1, prato2)

    try:
        if (modo or "").upper() == "COZINHA":
            erros = print_to_all_printers(payload)
        else:
            erros = print_to_cashier_printer(payload)

        if erros:
            print("Erros ao imprimir:", erros)
    except Exception as e:
        print(f"Falha ao imprimir pedido #{pedido_id} ({modo}): {e}")

    return True

# =====================
# BACKUP AO FECHAR
# =====================
def backup_database_on_exit():
    try:
        src = os.environ.get("PADARIA_DB_PATH")
        if not src or not os.path.exists(src):
            return

        backup_dir = os.path.join(RUNTIME_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(backup_dir, f"padaria_{ts}.db")

        src_conn = sqlite3.connect(src, timeout=30, check_same_thread=False)
        try:
            dst_conn = sqlite3.connect(dst, timeout=30, check_same_thread=False)
            try:
                src_conn.backup(dst_conn)
                dst_conn.commit()
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        print(f"Backup do banco criado em: {dst}")

    except Exception as e:
        print(f"Falha ao criar backup do banco ao sair: {e}")

def _handle_shutdown_signal(signum, frame):
    backup_database_on_exit()
    raise SystemExit(0)

def setup_shutdown_handlers():
    atexit.register(backup_database_on_exit)
    try:
        signal.signal(signal.SIGINT, _handle_shutdown_signal)
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    except Exception:
        pass

# =====================
# AUTH
# =====================
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            session["next_url"] = request.path
            return redirect("/login")
        try:
            limpar_historico_antigo()
        except Exception:
            pass
        return fn(*args, **kwargs)
    return wrapper

# =====================
# LOGIN / LOGOUT
# =====================
@app.route("/login", methods=["GET", "POST"])
def login():
    erro = False
    if request.method == "POST":
        pin = str(request.form.get("pin", "")).strip()
        if PIN and pin == PIN:
            session["logado"] = True
            next_url = session.pop("next_url", None)
            return redirect(next_url or "/caixa")
        erro = True
    return render_template("login.html", erro=erro, nome_estabelecimento=NOME_ESTAB)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# =====================
# PRATO DO DIA (2 OPÇÕES)
# =====================
@app.route("/prato_do_dia")
@login_required
def prato_do_dia_page():
    p1, p2 = get_prato_do_dia_opts()
    return render_template(
        "prato_do_dia.html",
        nome_estabelecimento=NOME_ESTAB,
        prato_do_dia_1=p1,
        prato_do_dia_2=p2,
        taxa_entrega=get_taxa_entrega()
    )

@app.route("/prato_do_dia/salvar", methods=["POST"])
@login_required
def prato_do_dia_salvar():
    p1 = request.form.get("prato_do_dia_1") or ""
    p2 = request.form.get("prato_do_dia_2") or ""
    set_prato_do_dia_opts(p1, p2)
    set_taxa_entrega(request.form.get("taxa_entrega") or "0")
    return redirect("/prato_do_dia")

# =====================
# ADMIN
# =====================
@app.route("/admin")
@login_required
def admin():
    conn = db_conn()
    cur = conn.cursor()
    categorias = cur.execute("SELECT * FROM categorias ORDER BY ordem, nome").fetchall()
    produtos = cur.execute("""
        SELECT p.*, COALESCE(c.nome,'') AS categoria_nome
        FROM produtos p
        LEFT JOIN categorias c ON c.id=p.categoria_id
        ORDER BY p.id DESC
    """).fetchall()

    edit_id = request.args.get("edit")
    produto_edit = None
    if edit_id:
        try:
            produto_edit = cur.execute("SELECT * FROM produtos WHERE id=?", (int(edit_id),)).fetchone()
        except Exception:
            produto_edit = None

    conn.close()
    return render_template(
        "admin.html",
        categorias=categorias,
        produtos=produtos,
        nome_estabelecimento=NOME_ESTAB,
        produto_edit=produto_edit
    )

@app.route("/admin/add_categoria", methods=["POST"])
@login_required
def add_categoria():
    nome = (request.form.get("nome") or "").strip()
    ordem = int(request.form.get("ordem") or 0)
    if not nome:
        return redirect("/admin")
    conn = db_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO categorias (nome, ordem, ativo) VALUES (?, ?, 1)", (nome, ordem))
        conn.commit()
    except Exception:
        pass
    conn.close()
    return redirect("/admin")

@app.route("/admin/remover_categoria/<int:cid>", methods=["POST"])
@login_required
def remover_categoria(cid):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE produtos SET categoria_id=NULL WHERE categoria_id=?", (cid,))
    cur.execute("DELETE FROM categorias WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/add_produto", methods=["POST"])
@login_required
def add_produto():
    nome = (request.form.get("nome") or "").strip()
    preco = float(request.form.get("preco") or 0)
    categoria_id = request.form.get("categoria_id")
    pesado = 1 if (request.form.get("pesado") in ("1", "on", "true", "True")) else 0
    ativo = 1 if (request.form.get("ativo") in ("1", "on", "true", "True")) else 0

    imagem_nome = None
    imagem = request.files.get("imagem")
    if imagem and imagem.filename:
        imagem_nome = secure_filename(imagem.filename)
        imagem.save(os.path.join(UPLOAD_FOLDER, imagem_nome))

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO produtos (nome, preco, imagem, categoria_id, pesado, ativo)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (nome, preco, imagem_nome, int(categoria_id) if categoria_id else None, pesado, ativo))
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/editar_produto/<int:pid>", methods=["POST"])
@login_required
def admin_editar_produto(pid):
    nome = (request.form.get("nome") or "").strip()
    preco = float(request.form.get("preco") or 0)
    categoria_id = request.form.get("categoria_id")
    pesado = 1 if (request.form.get("pesado") in ("1", "on", "true", "True")) else 0
    ativo = 1 if (request.form.get("ativo") in ("1", "on", "true", "True")) else 0

    conn = db_conn()
    cur = conn.cursor()
    produto = cur.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not produto:
        conn.close()
        return redirect("/admin")

    imagem_nome = produto["imagem"]
    imagem = request.files.get("imagem")
    if imagem and imagem.filename:
        imagem_nome = secure_filename(imagem.filename)
        imagem.save(os.path.join(UPLOAD_FOLDER, imagem_nome))

    cur.execute("""
        UPDATE produtos
        SET nome=?, preco=?, imagem=?, categoria_id=?, pesado=?, ativo=?
        WHERE id=?
    """, (
        nome,
        preco,
        imagem_nome,
        int(categoria_id) if categoria_id else None,
        pesado,
        ativo,
        pid
    ))
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/remover_produto/<int:pid>", methods=["POST"])
@login_required
def remover_produto(pid):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/toggle_produto/<int:pid>", methods=["POST"])
@login_required
def toggle_produto(pid):
    conn = db_conn()
    cur = conn.cursor()
    p = cur.execute("SELECT ativo FROM produtos WHERE id=?", (pid,)).fetchone()
    if p:
        novo = 0 if int(p["ativo"] or 0) == 1 else 1
        cur.execute("UPDATE produtos SET ativo=? WHERE id=?", (novo, pid))
        conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/clientes/criar", methods=["POST"])
@login_required
def clientes_criar():
    dados = request.get_json(silent=True) if request.is_json else request.form
    nome = (dados.get("nome") or "").strip()
    telefone = (dados.get("telefone") or "").strip()
    endereco = (dados.get("endereco") or "").strip()
    numero = (dados.get("numero") or "").strip()
    bairro = (dados.get("bairro") or "").strip()
    if not nome:
        if request.is_json:
            return {"ok": False, "erro": "Informe o nome do cliente."}, 400
        return redirect("/caixa?cliente_erro=nome")
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO clientes (nome, telefone, endereco, numero, bairro, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
                (nome, telefone, endereco, numero, bairro, now_str()))
    cliente_id = cur.lastrowid
    conn.commit()
    cliente = dict(cur.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone())
    conn.close()
    if request.is_json:
        return {"ok": True, "cliente": cliente}
    return redirect("/caixa?cliente_criado=1")

# =====================
# CAIXA
# =====================
@app.route("/caixa", methods=["GET", "POST"])
@login_required
def caixa():
    if request.method == "POST" and request.form.get("acao") == "set_turno":
        set_turno_global(request.form.get("turno", "MANHA"))

    turno = get_turno_global()
    filtro = (request.args.get("filtro") or "TODOS").upper().strip()
    if filtro not in ("TODOS", "AGUARDANDO_CAIXA", "PENDENTE", "PRONTO", "FECHADO", "CANCELADO"):
        filtro = "TODOS"

    conn = db_conn()
    cur = conn.cursor()

    categorias = cur.execute("SELECT * FROM categorias WHERE ativo=1 ORDER BY ordem, nome").fetchall()
    produtos = cur.execute("""
        SELECT p.*, COALESCE(c.nome,'') AS categoria_nome, COALESCE(c.ordem, 9999) AS categoria_ordem
        FROM produtos p
        LEFT JOIN categorias c ON c.id=p.categoria_id
        WHERE p.ativo=1
        ORDER BY categoria_ordem, categoria_nome, p.nome
    """).fetchall()

    produtos_por_categoria = {c["id"]: [] for c in categorias}
    produtos_sem_categoria = []
    for p in produtos:
        cid = p["categoria_id"]
        if cid in produtos_por_categoria:
            produtos_por_categoria[cid].append(p)
        else:
            produtos_sem_categoria.append(p)

    where = "WHERE turno=?"
    params = [turno]
    if filtro != "TODOS":
        where += " AND status=?"
        params.append(filtro)

    pedidos = cur.execute(f"""
        SELECT * FROM pedidos
        {where}
        ORDER BY id DESC
    """, tuple(params)).fetchall()

    qtd_aguardando = cur.execute("SELECT COUNT(*) AS c FROM pedidos WHERE turno=? AND status='AGUARDANDO_CAIXA'", (turno,)).fetchone()["c"]
    qtd_pend = cur.execute("SELECT COUNT(*) AS c FROM pedidos WHERE turno=? AND status='PENDENTE'", (turno,)).fetchone()["c"]
    qtd_pronto = cur.execute("SELECT COUNT(*) AS c FROM pedidos WHERE turno=? AND status='PRONTO'", (turno,)).fetchone()["c"]
    qtd_fechado = cur.execute("SELECT COUNT(*) AS c FROM pedidos WHERE turno=? AND status='FECHADO'", (turno,)).fetchone()["c"]

    total_turno = cur.execute("""
        SELECT COALESCE(SUM(total),0) AS t
        FROM pedidos
        WHERE turno=? AND status IN ('PRONTO','FECHADO')
    """, (turno,)).fetchone()["t"] or 0

    pedido_itens = {}
    for p in pedidos:
        pedido_itens[p["id"]] = itens_do_pedido(conn, p["id"])

    conn.close()

    conn_clientes = db_conn()
    clientes = [dict(row) for row in conn_clientes.execute("SELECT * FROM clientes ORDER BY nome COLLATE NOCASE").fetchall()]
    conn_clientes.close()

    prato1, prato2 = get_prato_do_dia_opts()

    return render_template(
        "caixa.html",
        nome_estabelecimento=NOME_ESTAB,
        turno=turno,
        filtro=filtro,
        clientes=clientes,
        taxa_entrega=get_taxa_entrega(),
        categorias=categorias,
        produtos_por_categoria=produtos_por_categoria,
        produtos_sem_categoria=produtos_sem_categoria,
        pedidos=pedidos,
        pedido_itens=pedido_itens,
        qtd_aguardando=qtd_aguardando,
        qtd_pend=qtd_pend,
        qtd_pronto=qtd_pronto,
        qtd_fechado=qtd_fechado,
        total_turno=float(total_turno),
        prato_do_dia_1=prato1,
        prato_do_dia_2=prato2
    )

@app.route("/caixa/criar_pedido", methods=["POST"])
@login_required
def caixa_criar_pedido():
    turno = get_turno_global()

    canal = (request.form.get("canal") or "BALCAO").upper().strip()
    if canal not in ("BALCAO", "DELIVERY"):
        canal = "BALCAO"

    destino = (request.form.get("destino") or "COZINHA").upper().strip()
    if destino not in ("COZINHA", "BALCAO"):
        destino = "COZINHA"

    cliente_id_raw = (request.form.get("cliente_id") or "").strip()
    cliente_id = int(cliente_id_raw) if cliente_id_raw.isdigit() else None
    cliente_nome = (request.form.get("cliente_nome") or "").strip()
    cliente_tel = (request.form.get("cliente_tel") or "").strip()
    endereco = (request.form.get("endereco") or "").strip()
    cliente_numero = (request.form.get("cliente_numero") or "").strip()
    cliente_bairro = (request.form.get("cliente_bairro") or "").strip()
    if cliente_id:
        lookup = db_conn()
        saved = lookup.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,)).fetchone()
        lookup.close()
        if saved:
            cliente_nome = cliente_nome or saved["nome"]
            cliente_tel = cliente_tel or saved["telefone"] or ""
            endereco = endereco or saved["endereco"] or ""
            cliente_numero = cliente_numero or saved["numero"] or ""
            cliente_bairro = cliente_bairro or saved["bairro"] or ""
    observacoes = (request.form.get("observacoes") or "").strip()
    cobrar_taxa = (request.form.get("cobrar_taxa") == "1" and canal == "DELIVERY")
    taxa_entrega = get_taxa_entrega() if cobrar_taxa else 0.0

    # >>> NOVO: forma de pagamento
    forma_pagamento = normalize_pagamento(request.form.get("forma_pagamento") or "")

    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO pedidos (
          criado_em, turno, status, destino, canal,
          cliente_id, cliente_nome, cliente_tel, endereco, cliente_numero, cliente_bairro,
          observacoes, total, forma_pagamento, taxa_entrega
        )
        VALUES (?, ?, 'AGUARDANDO_CAIXA', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
    """, (
        now_str(), turno, destino, canal,
        cliente_id, cliente_nome, cliente_tel, endereco, cliente_numero, cliente_bairro,
        observacoes, forma_pagamento, taxa_entrega
    ))
    pedido_id = cur.lastrowid

    item_ids = request.form.getlist("item_id")
    for pid in item_ids:
        qtd = int(request.form.get(f"qtd_{pid}", 1))
        if qtd <= 0:
            continue
        pr = cur.execute("SELECT * FROM produtos WHERE id=? AND ativo=1", (pid,)).fetchone()
        if pr:
            preco = float(pr["preco"] or 0)
            subtotal = preco * qtd
            cur.execute("""
                INSERT INTO itens_pedido (pedido_id, produto_id, nome, quantidade, preco_unit, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pedido_id, pr["id"], pr["nome"], qtd, preco, subtotal))

    pesado_ids = request.form.getlist("pesado_id")
    for pid in pesado_ids:
        valor = float(request.form.get(f"valor_{pid}") or 0)
        if valor <= 0:
            continue
        pr = cur.execute("SELECT * FROM produtos WHERE id=? AND ativo=1", (pid,)).fetchone()
        if pr:
            cur.execute("""
                INSERT INTO itens_pedido (pedido_id, produto_id, nome, quantidade, subtotal, pesado_valor)
                VALUES (?, ?, ?, 1, ?, ?)
            """, (pedido_id, pr["id"], pr["nome"], valor, valor))

    total_itens = calcular_total_itens(conn, pedido_id)
    total = total_itens + taxa_entrega
    cur.execute("UPDATE pedidos SET total=? WHERE id=?", (total, pedido_id))
    conn.commit()

    socketio.emit("pedido_caixa_novo", pedido_payload(conn, pedido_id))
    conn.close()
    return redirect("/caixa")

@app.route("/caixa/atualizar_obs/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_atualizar_obs(pedido_id):
    obs = (request.form.get("observacoes") or "").strip()
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE pedidos SET observacoes=? WHERE id=?", (obs, pedido_id))
    conn.commit()
    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    conn.close()
    return redirect("/caixa")

@app.route("/caixa/liberar_cozinha/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_liberar_cozinha(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    p = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not p:
        conn.close()
        return redirect("/caixa")

    cur.execute("UPDATE pedidos SET status='PENDENTE', destino='COZINHA' WHERE id=?", (pedido_id,))
    conn.commit()

    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    socketio.emit("novo_pedido", {"id": pedido_id})
    conn.close()
    return redirect("/caixa")

@app.route("/caixa/liberar_imprimir_cozinha/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_liberar_imprimir_cozinha(pedido_id):
    conn = db_conn()
    cur = conn.cursor()

    p = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not p:
        conn.close()
        return redirect("/caixa")

    cur.execute("UPDATE pedidos SET status='PENDENTE', destino='COZINHA' WHERE id=?", (pedido_id,))
    conn.commit()

    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    socketio.emit("novo_pedido", {"id": pedido_id})
    socketio.emit("imprimir_cozinha", {"id": pedido_id})
    conn.close()

    imprimir_pedido_raw(pedido_id, modo="COZINHA")
    return redirect("/caixa")

@app.route("/caixa/marcar_pronto/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_marcar_pronto(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE pedidos SET status='PRONTO' WHERE id=?", (pedido_id,))
    conn.commit()
    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    socketio.emit("pedido_pronto", pedido_id)
    conn.close()
    return redirect("/caixa")

@app.route("/caixa/fechar_pedido/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_fechar_pedido(pedido_id):
    conn = db_conn()
    cur = conn.cursor()

    # marca como fechado
    cur.execute("UPDATE pedidos SET status='FECHADO' WHERE id=?", (pedido_id,))
    conn.commit()

    # 🔥 MOVE PRO HISTÓRICO NA HORA
    mover_pedido_para_historico(conn, pedido_id, fechado_em=now_str())

    conn.commit()

    socketio.emit("pedido_caixa_removido", {"id": pedido_id})

    conn.close()
    return redirect("/caixa")

@app.route("/caixa/cancelar_pedido/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_cancelar_pedido(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE pedidos SET status='CANCELADO' WHERE id=?", (pedido_id,))
    conn.commit()
    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    conn.close()
    return redirect("/caixa")

@app.route("/caixa/remover_pedido/<int:pedido_id>", methods=["POST"])
@login_required
def caixa_remover_pedido(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    pedido = cur.execute("SELECT id FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return redirect("/caixa")
    cur.execute("DELETE FROM itens_pedido WHERE pedido_id=?", (pedido_id,))
    cur.execute("DELETE FROM pedidos WHERE id=?", (pedido_id,))
    conn.commit()
    conn.close()
    socketio.emit("pedido_caixa_removido", {"id": pedido_id})
    return redirect("/caixa")

# =====================
# COZINHA
# =====================
@app.route("/cozinha")
@login_required
def cozinha():
    conn = db_conn()
    cur = conn.cursor()

    pedidos = cur.execute("""
        SELECT * FROM pedidos
        WHERE status='PENDENTE' AND destino='COZINHA'
        ORDER BY id ASC
    """).fetchall()

    pedido_itens = {}
    for p in pedidos:
        pedido_itens[p["id"]] = itens_do_pedido(conn, p["id"])

    conn.close()
    return render_template(
        "cozinha.html",
        nome_estabelecimento=NOME_ESTAB,
        pedidos=pedidos,
        pedido_itens=pedido_itens
    )

@app.route("/finalizar_cozinha/<int:pedido_id>", methods=["POST"])
@login_required
def finalizar_cozinha(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE pedidos SET status='PRONTO' WHERE id=?", (pedido_id,))
    conn.commit()
    socketio.emit("pedido_pronto", pedido_id)
    socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    conn.close()
    return ("", 204)

# =====================
# ROTAS DE IMPRESSÃO (SEM HTML)
# =====================
@app.route("/imprimir_cozinha/<int:pedido_id>", methods=["GET", "POST"])
@login_required
def imprimir_cozinha(pedido_id):
    imprimir_pedido_raw(pedido_id, modo="CAIXA")
    return redirect("/caixa")

@app.route("/imprimir/<int:pedido_id>", methods=["GET", "POST"])
@login_required
def imprimir(pedido_id):
    imprimir_pedido_raw(pedido_id, modo="CAIXA")
    return redirect("/caixa")

# =====================
# FECHAR TURNO
# =====================
@app.route("/fechar_turno", methods=["POST"])
@login_required
def fechar_turno():
    turno = (request.form.get("turno") or get_turno_global()).upper().strip()
    if turno not in ("MANHA", "TARDE", "NOITE"):
        turno = get_turno_global()

    fechado_em = now_str()

    conn = db_conn()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT id FROM pedidos
        WHERE turno=? AND status IN ('AGUARDANDO_CAIXA','PENDENTE','PRONTO','FECHADO','CANCELADO')
        ORDER BY id ASC
    """, (turno,)).fetchall()

    for r in rows:
        mover_pedido_para_historico(conn, r["id"], fechado_em=fechado_em)

    conn.commit()
    conn.close()

    socketio.emit("turno_fechado", {"turno": turno})
    return redirect("/caixa")

# =====================
# HISTÓRICO
# =====================
@app.route("/historico")
@login_required
def historico():
    q = (request.args.get("q") or "").strip()
    canal = (request.args.get("canal") or "TODOS").upper().strip()
    status = (request.args.get("status") or "TODOS").upper().strip()
    periodo = (request.args.get("periodo") or "todos").lower().strip()

    if canal not in ("TODOS", "BALCAO", "DELIVERY"):
        canal = "TODOS"
    if status not in ("TODOS", "AGUARDANDO_CAIXA", "PENDENTE", "PRONTO", "FECHADO", "CANCELADO"):
        status = "TODOS"
    if periodo not in ("todos", "dia", "mes", "ano", "intervalo"):
        periodo = "todos"

    hoje = datetime.now().strftime("%Y-%m-%d")
    data_filtro = request.args.get("data") or hoje
    mes_filtro = request.args.get("mes") or datetime.now().strftime("%Y-%m")
    ano_filtro = request.args.get("ano") or str(datetime.now().year)
    data_inicio = request.args.get("data_inicio") or hoje
    data_fim = request.args.get("data_fim") or hoje
    erro_periodo = ""
    inicio = fim_exclusivo = None

    try:
        if periodo == "dia":
            d = datetime.strptime(data_filtro, "%Y-%m-%d")
            inicio = d.strftime("%Y-%m-%d 00:00:00")
            fim_exclusivo = (d + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        elif periodo == "mes":
            d = datetime.strptime(mes_filtro, "%Y-%m")
            inicio = d.strftime("%Y-%m-01 00:00:00")
            proximo = datetime(d.year + 1, 1, 1) if d.month == 12 else datetime(d.year, d.month + 1, 1)
            fim_exclusivo = proximo.strftime("%Y-%m-%d 00:00:00")
        elif periodo == "ano":
            ano_num = int(ano_filtro)
            if ano_num < 2000 or ano_num > 2100:
                raise ValueError("Ano fora do intervalo permitido.")
            inicio = f"{ano_num:04d}-01-01 00:00:00"
            fim_exclusivo = f"{ano_num + 1:04d}-01-01 00:00:00"
        elif periodo == "intervalo":
            di = datetime.strptime(data_inicio, "%Y-%m-%d")
            df = datetime.strptime(data_fim, "%Y-%m-%d")
            if df < di:
                raise ValueError("A data final precisa ser igual ou posterior à data inicial.")
            inicio = di.strftime("%Y-%m-%d 00:00:00")
            fim_exclusivo = (df + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    except (ValueError, TypeError):
        erro_periodo = "Período inválido. Confira as datas e tente novamente."
        inicio = fim_exclusivo = None

    conn = db_conn()
    cur = conn.cursor()
    where_parts, params = [], []
    if inicio and fim_exclusivo:
        where_parts.append("criado_em >= ? AND criado_em < ?")
        params.extend([inicio, fim_exclusivo])
    if canal != "TODOS":
        where_parts.append("canal=?")
        params.append(canal)
    if status != "TODOS":
        where_parts.append("status=?")
        params.append(status)
    if q:
        like = f"%{q}%"
        where_parts.append("(cliente_nome LIKE ? OR cliente_tel LIKE ? OR endereco LIKE ? OR observacoes LIKE ? OR cliente_bairro LIKE ? OR cliente_numero LIKE ? OR forma_pagamento LIKE ?)")
        params.extend([like, like, like, like, like, like, like])
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    produto_rows = cur.execute("""
        SELECT nome FROM produtos
        UNION SELECT nome FROM historico_itens
        ORDER BY nome COLLATE NOCASE
    """).fetchall()
    nomes_produtos = [r["nome"] for r in produto_rows if r["nome"]]
    nomes_validos = set(nomes_produtos)
    produtos_selecionados = [n for n in request.args.getlist("produtos") if n in nomes_validos]

    pedidos_where_parts = list(where_parts)
    pedidos_params = list(params)
    if produtos_selecionados:
        pedidos_where_parts.append(
            "id IN (SELECT DISTINCT pedido_id FROM historico_itens WHERE nome IN (" +
            ",".join("?" for _ in produtos_selecionados) + "))"
        )
        pedidos_params.extend(produtos_selecionados)
    pedidos_where = ("WHERE " + " AND ".join(pedidos_where_parts)) if pedidos_where_parts else ""

    pedidos = cur.execute(
        f"SELECT * FROM historico_pedidos {pedidos_where} ORDER BY criado_em DESC, id DESC LIMIT 5000",
        tuple(pedidos_params)
    ).fetchall()

    pedido_itens = {}
    for p in pedidos:
        if produtos_selecionados:
            marks = ",".join("?" for _ in produtos_selecionados)
            pedido_itens[p["id"]] = cur.execute(
                f"SELECT * FROM historico_itens WHERE pedido_id=? AND nome IN ({marks}) ORDER BY id ASC",
                (p["id"], *produtos_selecionados)
            ).fetchall()
        else:
            pedido_itens[p["id"]] = cur.execute(
                "SELECT * FROM historico_itens WHERE pedido_id=? ORDER BY id ASC", (p["id"],)
            ).fetchall()

    item_parts, item_params = [], []
    if inicio and fim_exclusivo:
        item_parts.append("p.criado_em >= ? AND p.criado_em < ?")
        item_params.extend([inicio, fim_exclusivo])
    if canal != "TODOS":
        item_parts.append("p.canal=?")
        item_params.append(canal)
    if status != "TODOS":
        item_parts.append("p.status=?")
        item_params.append(status)
    if q:
        like = f"%{q}%"
        item_parts.append("(p.cliente_nome LIKE ? OR p.cliente_tel LIKE ? OR p.endereco LIKE ? OR p.observacoes LIKE ? OR p.cliente_bairro LIKE ? OR p.cliente_numero LIKE ? OR p.forma_pagamento LIKE ?)")
        item_params.extend([like, like, like, like, like, like, like])
    if produtos_selecionados:
        item_parts.append("i.nome IN (" + ",".join("?" for _ in produtos_selecionados) + ")")
        item_params.extend(produtos_selecionados)
    item_where = ("WHERE " + " AND ".join(item_parts)) if item_parts else ""

    resumo_produtos = cur.execute(f"""
        SELECT i.nome AS nome,
               SUM(CASE WHEN COALESCE(i.pesado_valor, 0) > 0 THEN 1
                        ELSE COALESCE(i.quantidade, 1) END) AS quantidade,
               SUM(CASE WHEN COALESCE(i.pesado_valor, 0) > 0
                        THEN COALESCE(i.pesado_valor, i.subtotal, 0)
                        ELSE COALESCE(i.subtotal, 0) END) AS valor
        FROM historico_itens i
        JOIN historico_pedidos p ON p.id=i.pedido_id
        {item_where}
        GROUP BY i.nome
        ORDER BY quantidade DESC, i.nome COLLATE NOCASE
    """, tuple(item_params)).fetchall()

    total_where_parts = list(where_parts)
    total_where_parts.append("status IN ('PRONTO','FECHADO')")
    total_periodo = cur.execute(
        "SELECT COALESCE(SUM(total),0) AS t FROM historico_pedidos WHERE " + " AND ".join(total_where_parts),
        tuple(params)
    ).fetchone()["t"] or 0
    total_unidades = sum(int(row["quantidade"] or 0) for row in resumo_produtos)
    total_produtos_selecionados = sum(float(row["valor"] or 0) for row in resumo_produtos)
    conn.close()

    return render_template(
        "historico.html", nome_estabelecimento=NOME_ESTAB, pedidos=pedidos,
        pedido_itens=pedido_itens, canal=canal, status=status, q=q,
        total_periodo=float(total_periodo), periodo=periodo, data_filtro=data_filtro,
        mes_filtro=mes_filtro, ano_filtro=ano_filtro, data_inicio=data_inicio,
        data_fim=data_fim, erro_periodo=erro_periodo,
        produtos_filtro=[{"nome": n} for n in nomes_produtos],
        produtos_selecionados=produtos_selecionados,
        resumo_produtos=resumo_produtos, total_unidades=total_unidades,
        total_produtos_selecionados=total_produtos_selecionados,
    )

@app.route("/historico/remover/<int:pedido_id>", methods=["POST"])
@login_required
def historico_remover(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM historico_itens WHERE pedido_id=?", (pedido_id,))
    cur.execute("DELETE FROM historico_pedidos WHERE id=?", (pedido_id,))
    conn.commit()
    conn.close()
    return redirect("/historico")

@app.route("/historico/limpar", methods=["POST"])
@login_required
def historico_limpar():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM historico_itens")
    cur.execute("DELETE FROM historico_pedidos")
    conn.commit()
    conn.close()
    return redirect("/historico")


# =====================
# EDITAR PEDIDO
# =====================
@app.route("/caixa/editar/<int:pedido_id>")
@login_required
def tela_editar_pedido(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    pedido = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return redirect("/caixa")

    itens = itens_do_pedido(conn, pedido_id)
    item_por_produto = {
        int(it["produto_id"]): it for it in itens if it["produto_id"] is not None
    }
    categorias = cur.execute(
        "SELECT * FROM categorias WHERE ativo=1 ORDER BY ordem, nome"
    ).fetchall()
    produtos = cur.execute("""
        SELECT p.*, COALESCE(c.nome,'') AS categoria_nome,
               COALESCE(c.ordem, 9999) AS categoria_ordem
        FROM produtos p LEFT JOIN categorias c ON c.id=p.categoria_id
        WHERE p.ativo=1 OR p.id IN (SELECT produto_id FROM itens_pedido WHERE pedido_id=?)
        ORDER BY categoria_ordem, categoria_nome, p.nome
    """, (pedido_id,)).fetchall()

    produtos_por_categoria = {c["id"]: [] for c in categorias}
    produtos_sem_categoria = []
    for produto in produtos:
        cid = produto["categoria_id"]
        if cid in produtos_por_categoria:
            produtos_por_categoria[cid].append(produto)
        else:
            produtos_sem_categoria.append(produto)

    conn.close()
    return render_template(
        "editar_pedido.html", pedido=pedido, itens=itens,
        item_por_produto=item_por_produto, categorias=categorias,
        produtos_por_categoria=produtos_por_categoria,
        produtos_sem_categoria=produtos_sem_categoria,
        taxa_entrega_configurada=get_taxa_entrega(),
        nome_estabelecimento=NOME_ESTAB
    )


@app.route("/caixa/editar_pedido/<int:pedido_id>", methods=["POST"])
@login_required
def salvar_edicao_pedido(pedido_id):
    conn = db_conn()
    cur = conn.cursor()
    pedido_atual = cur.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    if not pedido_atual:
        conn.close()
        return redirect("/caixa")

    try:
        cliente_nome = (request.form.get("cliente_nome") or "").strip()
        cliente_tel = (request.form.get("cliente_tel") or "").strip()
        endereco = (request.form.get("endereco") or "").strip()
        cliente_numero = (request.form.get("cliente_numero") or "").strip()
        cliente_bairro = (request.form.get("cliente_bairro") or "").strip()
        observacoes = (request.form.get("observacoes") or "").strip()
        forma_pagamento = normalize_pagamento(request.form.get("forma_pagamento") or "DINHEIRO")

        canal = (request.form.get("canal") or pedido_atual["canal"] or "BALCAO").upper().strip()
        if canal not in ("BALCAO", "DELIVERY"):
            canal = "BALCAO"
        destino = (request.form.get("destino") or pedido_atual["destino"] or "COZINHA").upper().strip()
        if destino not in ("COZINHA", "BALCAO"):
            destino = "COZINHA"

        cobrar_taxa = request.form.get("cobrar_taxa") == "1" and canal == "DELIVERY"
        taxa_anterior = float(pedido_atual["taxa_entrega"] or 0)
        taxa_entrega = (taxa_anterior if taxa_anterior > 0 else get_taxa_entrega()) if cobrar_taxa else 0.0

        cur.execute("""
            UPDATE pedidos SET canal=?, destino=?, cliente_nome=?, cliente_tel=?,
                endereco=?, cliente_numero=?, cliente_bairro=?, observacoes=?,
                forma_pagamento=?, taxa_entrega=?
            WHERE id=?
        """, (
            canal, destino, cliente_nome, cliente_tel, endereco, cliente_numero,
            cliente_bairro, observacoes, forma_pagamento, taxa_entrega, pedido_id
        ))

        cur.execute("DELETE FROM itens_pedido WHERE pedido_id=?", (pedido_id,))

        for pid in request.form.getlist("item_id"):
            try:
                qtd = int(request.form.get(f"qtd_{pid}", 1))
                product_id = int(pid)
            except (TypeError, ValueError):
                continue
            if qtd <= 0:
                continue
            produto = cur.execute("SELECT * FROM produtos WHERE id=?", (product_id,)).fetchone()
            if not produto or int(produto["pesado"] or 0) == 1:
                continue
            preco = float(produto["preco"] or 0)
            subtotal = preco * qtd
            cur.execute("""
                INSERT INTO itens_pedido
                    (pedido_id, produto_id, nome, quantidade, preco_unit, subtotal, pesado_valor)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (pedido_id, produto["id"], produto["nome"], qtd, preco, subtotal))

        for pid in request.form.getlist("pesado_id"):
            try:
                product_id = int(pid)
                valor = float((request.form.get(f"valor_{pid}") or "0").replace(",", "."))
            except (TypeError, ValueError):
                continue
            if valor <= 0:
                continue
            produto = cur.execute("SELECT * FROM produtos WHERE id=?", (product_id,)).fetchone()
            if not produto or int(produto["pesado"] or 0) != 1:
                continue
            cur.execute("""
                INSERT INTO itens_pedido
                    (pedido_id, produto_id, nome, quantidade, preco_unit, subtotal, pesado_valor)
                VALUES (?, ?, ?, 1, 0, ?, ?)
            """, (pedido_id, produto["id"], produto["nome"], valor, valor))

        total_itens = calcular_total_itens(conn, pedido_id)
        cur.execute("UPDATE pedidos SET total=? WHERE id=?", (total_itens + taxa_entrega, pedido_id))
        conn.commit()
        socketio.emit("pedido_caixa_atualizado", pedido_payload(conn, pedido_id))
    except Exception as e:
        print("ERRO AO EDITAR PEDIDO:", e)
        conn.rollback()
    finally:
        conn.close()
    return redirect("/caixa")


# =====================
# START
# =====================
if __name__ == "__main__":
    ensure_schema()
    setup_shutdown_handlers()
    print("Sistema Emporio dos Paes rodando")
    socketio.run(app, host="0.0.0.0", port=PORTA, debug=False)
