from db import conectar

conn = conectar()
cur = conn.cursor()

# Config app (turno global, etc)
cur.execute("""
CREATE TABLE IF NOT EXISTS config_app (
  chave TEXT PRIMARY KEY,
  valor TEXT
)
""")

# Categorias
cur.execute("""
CREATE TABLE IF NOT EXISTS categorias (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL UNIQUE,
  ordem INTEGER DEFAULT 0,
  ativo INTEGER DEFAULT 1
)
""")

# Produtos
cur.execute("""
CREATE TABLE IF NOT EXISTS produtos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT NOT NULL,
  preco REAL DEFAULT 0,
  imagem TEXT,
  categoria_id INTEGER,
  pesado INTEGER DEFAULT 0,  -- 1 = preço variável no caixa
  ativo INTEGER DEFAULT 1,
  FOREIGN KEY (categoria_id) REFERENCES categorias(id) ON DELETE SET NULL
)
""")

# Marmitex do dia (base p/ futuro)
cur.execute("""
CREATE TABLE IF NOT EXISTS marmitex_dia (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  data TEXT NOT NULL, -- YYYY-MM-DD
  titulo TEXT NOT NULL,
  descricao TEXT,
  preco REAL DEFAULT 0,
  ativo INTEGER DEFAULT 1
)
""")

# Pedidos
cur.execute("""
CREATE TABLE IF NOT EXISTS pedidos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  criado_em TEXT NOT NULL,
  turno TEXT DEFAULT 'MANHA',
  status TEXT DEFAULT 'AGUARDANDO_CAIXA',  -- AGUARDANDO_CAIXA, PENDENTE, PRONTO, FECHADO, CANCELADO
  destino TEXT DEFAULT 'COZINHA',          -- COZINHA ou BALCAO
  canal TEXT DEFAULT 'BALCAO',             -- BALCAO ou DELIVERY
  cliente_nome TEXT,
  cliente_tel TEXT,
  endereco TEXT,
  observacoes TEXT,
  total REAL DEFAULT 0
)
""")

# Itens do pedido
cur.execute("""
CREATE TABLE IF NOT EXISTS itens_pedido (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pedido_id INTEGER NOT NULL,
  produto_id INTEGER,
  nome TEXT NOT NULL,
  quantidade INTEGER DEFAULT 1,
  preco_unit REAL,
  subtotal REAL DEFAULT 0,
  pesado_valor REAL,
  obs_item TEXT,
  FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,
  FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE SET NULL
)
""")

# turno default
cur.execute("INSERT OR IGNORE INTO config_app (chave, valor) VALUES ('turno_atual', 'MANHA')")

# categorias default (se não tiver)
defaults = ["Pães", "Salgados", "Doces", "Bebidas", "Marmitex", "Outros"]
for i, nome in enumerate(defaults):
    cur.execute("INSERT OR IGNORE INTO categorias (nome, ordem, ativo) VALUES (?, ?, 1)", (nome, i))

conn.commit()
conn.close()

print("✅ Banco inicializado / atualizado com sucesso!")
