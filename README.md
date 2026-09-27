# Organizador — assistente pessoal no Discord

Bot Discord que funciona como organizador pessoal: você conversa em linguagem
natural no canal `#assistente` e ele decide sozinho (via Gemini) se aquilo é
uma tarefa, lembrete, meta, hábito recorrente, conta a pagar ou gasto — e
pergunta de volta quando falta alguma informação (prazo, horário, valor etc).

## O que ele faz

- **Tarefas** — anota e posta em `#tarefas`.
- **Lembretes** (únicos e recorrentes) — te avisa no horário certo, com menção.
- **Metas** — semanais/mensais/anuais, postadas em `#metas`.
- **Hábitos recorrentes** — cobra se você não confirmar que fez, com humor.
- **Contas a pagar** — avisa alguns dias antes do vencimento (`#contas`).
- **Gastos e limite mensal** — avisa se o ritmo de gastos estiver acima do esperado.
- **Painel** (`/#geral`, todo dia às 10h ou quando você pedir "resumo") — visão geral de tudo.
- **Excluir** — pedir para apagar algo pede confirmação antes.

Tudo isso é decidido pelo modelo Gemini a partir da sua mensagem — não existem
comandos fixos, é conversa normal no canal `#assistente`.

## Estrutura

- `bot.py` — cliente Discord, loops de verificação (lembretes, hábitos, contas, painel) e toda a lógica de resposta.
- `assistente.py` — chama a API do Gemini e transforma a mensagem do usuário em ações estruturadas (JSON).
- `db.py` — todo o acesso ao Supabase (tabelas: `tarefas`, `lembretes`, `lembretes_recorrentes`, `execucoes_habito`, `metas`, `contas`, `gastos`, `config_financeiro`).
- `deploy/organizador-bot.service` — unit do systemd para rodar 24/7 na VM.
- `.github/workflows/deploy.yml` — deploy automático na VM a cada push na `main`.

## Canais do Discord necessários

Crie manualmente (o bot só cria `#contas` sozinho se faltar):

- `#assistente` — único canal que o bot lê/responde
- `#tarefas`
- `#lembretes`
- `#metas`
- `#geral` — recebe o painel diário

## Configuração (`.env`)

Copie `.env.example` para `.env` e preencha:

```
DISCORD_TOKEN=token do bot no Discord Developer Portal
GEMINI_API_KEY=sua chave da API Gemini
SUPABASE_URL=já vem preenchido (projeto organizador-discord)
SUPABASE_SECRET_KEY=service_role key do projeto no Supabase (Settings > API)
```

Opcional: `FUSO_HORARIO` (padrão `America/Sao_Paulo`).

**Nunca** commite o `.env` — já está no `.gitignore`.

## Deploy na VM (Hostinger, Ubuntu 24.04)

```bash
# 1. dependências do sistema (uma vez só)
sudo apt update && sudo apt install -y python3-venv python3-pip git

# 2. usuário dedicado pro bot (uma vez só)
sudo useradd -m -s /bin/bash organizador
sudo su - organizador

# 3. clonar e preparar o ambiente
git clone https://github.com/Sharkcleiton/organizador-discord-bot.git
cd organizador-discord-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. configurar segredos
cp .env.example .env
nano .env   # cole DISCORD_TOKEN, GEMINI_API_KEY e SUPABASE_SECRET_KEY

# 5. testar manualmente antes de virar serviço
python bot.py
# Ctrl+C depois de ver "Conectado como <nome do bot>" e testar no Discord
```

Se conectou e respondeu no `#assistente`, sai do usuário `organizador` (`exit`)
e registra o serviço para rodar 24/7:

```bash
sudo cp /home/organizador/organizador-discord-bot/deploy/organizador-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now organizador-bot
sudo systemctl status organizador-bot     # deve mostrar "active (running)"
journalctl -u organizador-bot -f          # acompanhar logs em tempo real
```

## Deploy automático (a cada `git push`)

Depois do setup inicial acima, um GitHub Action (`.github/workflows/deploy.yml`)
conecta na VM via SSH e roda `git pull` + `pip install` + `systemctl restart`
sozinho a cada push na branch `main`. Configuração única, na própria VM:

```bash
# como o usuário organizador
sudo su - organizador

# 1. gerar uma chave SSH só pra isso (sem senha)
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""

# 2. autorizar essa chave a logar como organizador nesta mesma VM
cat ~/.ssh/deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# 3. mostrar a chave PRIVADA pra copiar (só vai pro GitHub, nunca pro chat/repo)
cat ~/.ssh/deploy_key
exit
```

```bash
# 4. permitir reiniciar o serviço sem senha (fora do usuário organizador, como root/sudo)
echo "organizador ALL=(root) NOPASSWD: $(which systemctl) restart organizador-bot" | sudo tee /etc/sudoers.d/organizador-deploy
sudo visudo -c   # valida a sintaxe antes de sair
```

No GitHub, vá em **Settings → Secrets and variables → Actions** do repositório
e crie 3 *repository secrets* (nunca cole isso aqui no chat):

| Secret       | Valor                                              |
|--------------|-----------------------------------------------------|
| `VM_HOST`    | IP ou domínio da VM                                 |
| `VM_USER`    | `organizador`                                       |
| `VM_SSH_KEY` | conteúdo completo de `~/.ssh/deploy_key` (a privada) |

A partir daí, todo `git push` na `main` já atualiza e reinicia o bot sozinho —
acompanhe em **Actions** no GitHub se rodou certo. Se algo falhar, dá pra sempre
cair de volta no método manual (`git pull` + `systemctl restart` direto na VM).

## Checklist antes de ligar em produção

- [ ] Confirmar que o modelo `gemini-3.5-flash-lite` (definido em `assistente.py`) existe e responde com a chave usada — ajustar `MODELO` se a API retornar erro de modelo inválido.
- [ ] Confirmar no Supabase (SQL Editor) que existe uma constraint única em `execucoes_habito (recorrente_id, data)`, exigida pelo `upsert` em `db.criar_execucao_habito`:
  ```sql
  select conname from pg_constraint where conrelid = 'execucoes_habito'::regclass;
  -- se não houver nenhuma envolvendo recorrente_id/data, rodar:
  alter table execucoes_habito add constraint execucoes_habito_recorrente_data_key unique (recorrente_id, data);
  ```
- [ ] Apagar os dados de teste inseridos manualmente durante o setup (tabelas `tarefas`, `lembretes`, `contas`, `gastos`, `config_financeiro` no projeto `organizador-discord`), se ainda existirem.
