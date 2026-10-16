# Empório dos Pães — Sistema Web

Sistema local de pedidos, caixa e cozinha desenvolvido em Flask.

## Iniciar no Windows

1. Instale o Python 3.11 ou mais recente pelo site oficial: https://www.python.org/downloads/windows/
2. Na instalação, marque **Add Python to PATH**.
3. Conecte o computador à internet na primeira inicialização.
4. Abra `iniciar.bat`. O script instala/verifica as dependências listadas em `requirements.txt` e inicia o servidor.
5. Acesse http://127.0.0.1:5000/login.

Mantenha a janela do terminal aberta enquanto o sistema estiver em uso. Para encerrar, use Ctrl+C nessa janela.

## Estrutura principal

- `app.py`: rotas e regras da aplicação.
- `db.py`: conexão com SQLite.
- `init_db.py`: inicialização/estrutura do banco.
- `templates/`: telas HTML.
- `static/style.css`: identidade visual e estilos.
- `static/img/`: imagens usadas nas telas e produtos.
- `static/sound/`: som de notificação.
- `requirements.txt`: dependências Python.
- `iniciar.bat`: inicialização para Windows.

## Dados e segurança

- `dados.db` contém os dados locais da aplicação. **Não substitua por um banco vazio ao atualizar o código.**
- `config.json` guarda configurações específicas da instalação, incluindo impressoras e PIN da cozinha. Não sobrescreva esse arquivo sem conferir os valores do computador.
- Faça cópia do banco antes de atualizações e nunca teste limpeza de histórico no banco de produção.
- A aplicação foi desenhada para uso local/rede confiável. Não exponha a porta do Flask diretamente à internet sem adicionar autenticação e configuração de produção adequadas.

## Atualização

Para atualizar uma instalação, feche o servidor e atualize apenas os arquivos de código/telas necessários. Preserve `dados.db`, `config.json` e imagens personalizadas da padaria. Depois abra `iniciar.bat` novamente.

## Estado da versão

A identidade visual foi refinada com CSS puro para evitar bibliotecas de interface adicionais. Os fluxos de caixa, cozinha, impressão e histórico devem ser testados no computador de desenvolvimento antes de substituir a versão em uso.
