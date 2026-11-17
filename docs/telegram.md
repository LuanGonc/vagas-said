# Telegram: configuração local e fila de entrega

Para o produto multiusuário, use [bot.md](bot.md). Este guia cobre os comandos de
diagnóstico e envio para um único chat. No modo `bot`, CHAT_ID é ignorado e cada
pessoa autorizada gerencia suas próprias assinaturas.

## Preparar o bot e o chat

1. No Telegram, fale com **@BotFather**, use `/newbot` e crie seu bot. Guarde o token
   somente no computador onde o monitor vai rodar.
2. Se ainda não existe um `.env`, copie `.env.example` para `.env` e preencha `BOT_TOKEN`.
   `CHAT_ID` pode ficar vazio neste primeiro momento. Não envie o arquivo ou token pelo chat.
3. Abra a conversa com o bot que você criou e envie `/start`.
4. Na pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram chats
```

O comando consulta uma página de atualizações e imprime somente IDs e tipos de chat,
sem nomes ou mensagens. Escolha o ID do seu chat e preencha `CHAT_ID` no `.env`. Se a
lista vier vazia, envie outra mensagem ao bot e tente de novo. Um bot já configurado
com webhook pode recusar a consulta; o comando não remove webhook automaticamente.
Use preferencialmente um bot dedicado ao monitor.

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram test
```

Esse comando envia uma única mensagem de teste. O resultado `sent: true` indica que
a API confirmou o envio; confira também se ela chegou à sua conversa. Ele não altera
o estado de turmas nem enfileira eventos do monitor. Em falha ou timeout, o teste não
repete o envio automaticamente.

Nos comandos `chats` e `test`, o `.env` padrão fica no diretório do terminal. Há também
`--env-file caminho/.env`. Variáveis de ambiente `BOT_TOKEN` e `CHAT_ID` têm prioridade
sobre o arquivo. Nunca inclua o token diretamente em argumentos ou URLs de comandos.

## Ativar notificações de eventos

Copie `config.example.toml` para `config.local.toml`, preservando o arquivo de exemplo,
e ajuste a seção:

```toml
[telegram]
enabled = true
env_file = ".env"
```

O caminho é relativo ao TOML. A primeira execução estabelece a baseline sem alerta.
Só novos eventos criados com essa opção ativa são enfileirados. Histórico anterior
permanece no banco, sem disparar notificações antigas ao ativar a integração.

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor monitor --config config.local.toml --once
```

O monitor tenta entregar a fila após cada ciclo. Cada lote contém até dez mensagens,
separadas por pelo menos três segundos por destino; esse intervalo persiste entre
instâncias. A execução continua sequencial. O modo `--once` retorna 1 se a tentativa
de notificação falhar e inclui o resumo de entrega em `notifications`.

Neste estágio, todas as fontes são arquivos locais: as mensagens indicam demonstração
local, disciplina, turma, tipo de vaga, diferenças numéricas, horário de Brasília e o
link de entrada do Aluno Online. Elas não confirmam possibilidade de matrícula.

## Inspecionar e recuperar a fila

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram queue --config config.local.toml
.\.venv\Scripts\python.exe -m uerj_monitor telegram flush --config config.local.toml
```

`queue` funciona sem credenciais e não acessa a rede. `flush` tenta entregar um lote
ao bot/chat configurados, mesmo que o monitor esteja parado; os prazos de nova tentativa
continuam valendo. Pode retornar zero envios se ainda houver cooldown, se a fila estiver
vazia ou se houver uma entrega presa em andamento. Consulte `queue` para distinguir.

| Estado | Significado |
|---|---|
| `pending` | Aguardando a primeira tentativa ou o prazo de nova tentativa. |
| `sending` | Um processo assumiu a entrega; um encerramento abrupto pode deixá-la assim. |
| `sent` | A API confirmou o envio e o ID da mensagem foi persistido. |
| `failed` | Erro definitivo ou três tentativas recuperáveis esgotadas. |
| `uncertain` | O envio pode ter ocorrido, mas não houve confirmação confiável. |

Falha de conexão antes do envio e rejeições recuperáveis explícitas da API permitem
novas tentativas após 60 e 120 segundos. Um HTTP 429 com `retry_after` adia todo o destino
por pelo menos o prazo informado. Erros de autorização não são repetidos automaticamente.
Timeout de leitura, resposta malformada ou interrupção durante o envio deixam a entrega
incerta; elas não entram em repetição automática.

Pare o monitor e confira a conversa antes de repetir uma entrega `failed`, `uncertain`
ou `sending`. A repetição manual pode duplicar uma mensagem que já tenha chegado:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram retry --config config.local.toml --event-id 1
.\.venv\Scripts\python.exe -m uerj_monitor telegram flush --config config.local.toml
```

`retry` só recoloca a entrada na fila, zerando o contador de tentativas. Não repete
entregas confirmadas como `sent` e não altera o destino originalmente associado.
Não use o comando durante uma tentativa ativa em outro processo.

## Persistência, privacidade e limites

O esquema SQLite atual é v3 e preserva estado e histórico de v1/v2. Evento,
baseline atualizada e item da fila são gravados na mesma transação por turma. O token
e o CHAT_ID não são persistidos na fila; o destino é identificado por um hash do ID do
bot e do chat. Trocar o token do mesmo bot mantém esse destino. Trocar de bot/chat
não redireciona entregas antigas; elas permanecem associadas ao destino original.

Erros e logs HTTP são sanitizados para ocultar o token e a descrição bruta da API não
é reproduzida. A fila guarda status, número de tentativas, código de erro e confirmação,
sem corpo de respostas ou mensagens recebidas na descoberta de chat.

A API de envio não oferece uma chave de idempotência para esta integração. Se o Telegram
aceitar uma mensagem e o processo cair antes de gravar a confirmação, não é possível
deduzir automaticamente se ela chegou. O projeto mantém essa situação para revisão,
em vez de prometer entrega exatamente uma vez. Ainda não há limpeza automática de filas.

## Referências e aceite

A implementação usa [sendMessage e getUpdates da Bot API](https://core.telegram.org/bots/api),
os parâmetros de [resposta a limites](https://core.telegram.org/bots/api#responseparameters)
e a [orientação de frequência do Telegram](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

- [x] Cliente, formatação, envio explícito de teste e descoberta local de chat.
- [x] Fila durável, tentativas limitadas, cooldown e identificação de entrega incerta.
- [x] Testes locais com transporte HTTP simulado, sem chamadas reais à API.
- [ ] Bot e chat reais configurados localmente pelo usuário.
- [ ] Mensagem de teste recebida e conferida no Telegram.

A integração ao portal e a regra de disponibilidade continuam pendentes da descoberta
descrita em [protocolo.md](protocolo.md).
