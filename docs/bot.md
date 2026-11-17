# Bot Telegram com múltiplos usuários

O bot já permite autorização, busca, seleção e gerenciamento de assinaturas usando
o catálogo de demonstração. As disciplinas vêm da configuração e as turmas são extraídas
do HTML local a cada consulta. A ligação ao catálogo real da UERJ ainda está pendente.

## Experimentar sem Telegram

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor bot-demo --config config.bot.example.toml
```

O comando simula buscar uma disciplina, abrir suas turmas, adicionar uma e listar as
assinaturas em um banco em memória. Não usa token, rede ou o banco persistente.
O código `IME01-06766` segue o exemplo do produto; as turmas e contagens no exemplo são
**fictícias**, sem consulta real ao Aluno Online.

## Preparar uma execução no Telegram

1. Crie seu bot no `@BotFather` e preencha `BOT_TOKEN` no `.env` local. Para o modo `bot`,
   **CHAT_ID não é necessário e é ignorado**: o destino vem da conversa de cada pessoa.
2. Copie `config.bot.example.toml` para `config.local.bot.toml` (ignorado pelo Git).
3. Inicie:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor bot --config config.local.bot.toml
```

4. Envie `/id` na conversa privada com seu bot. Pare o processo com `Ctrl+C` e coloque
   esse ID na sua configuração local:

```toml
[bot]
admin_user_ids = [123456789] # substitua pelo seu ID
```

5. Reinicie o processo. Você poderá usar o catálogo e autorizar as outras pessoas.
   Cada convidado envia `/id` e informa esse número ao responsável, que usa
   `/autorizar ID`. Não são pedidas senha ou sessão UERJ no Telegram.

Sem administradores configurados, ninguém consegue autorizar pessoas ou cadastrar
turmas. Apenas a consulta ao próprio ID e a orientação de acesso ficam disponíveis.
O exemplo mantém `[telegram] enabled = false` para os comandos antigos; executar `bot`
é a ativação explícita da interface multiusuário e dos alertas das suas assinaturas.
Não execute duas instâncias consumidoras de `getUpdates` para o mesmo bot.

## Comandos e comportamento

| Comando | Uso |
|---|---|
| `/id` | Mostrar apenas o próprio identificador Telegram. |
| `/start`, `/ajuda` | Orientar o uso do bot. |
| `/buscar Cálculo II` | Buscar pelo nome, sem distinguir acentos e caixa. |
| `/buscar IME01-06766` | Buscar pelo código. Texto livre também faz busca. |
| `/turmas IME01-06766` | Consultar as turmas de uma disciplina do catálogo. |
| `/minhas` | Ver somente suas turmas e o uso do limite de dez. |
| `/parar` | Mostrar botões para remover uma das suas turmas. |
| `/autorizar ID` | Liberar uma pessoa; somente administradores. |
| `/revogar ID` | Revogar acesso e desativar as assinaturas da pessoa; somente administradores. |

Escolher “Monitorar turma 01” adiciona essa turma à lista pessoal. Repetir a escolha
não duplica a assinatura. O limite é transacional: duas seleções concorrentes não
conseguem ultrapassar dez. Depois de receber um alerta, a turma continua ativa.

Os botões expiram após quinze minutos, são verificados por dono e sobrevivem a reinícios
enquanto válidos. Os números das turmas não são inventados pelo bot. Uma turma que saiu
da consulta não pode ser adicionada por um botão antigo. Atualmente a seleção oferece
o tipo de vaga UERJ; outras categorias dependem das regras ainda não validadas.

## Monitoramento e entregas

O monitor consulta a fonte local a cada intervalo configurado e cria baseline por
assinatura, sem alerta inicial. Cada usuário recebe eventos associados à sua assinatura
na própria conversa. O motor reserva um contexto de consulta para cada usuário; a
possibilidade de compartilhar consultas entre contas só será decidida após a inspeção.

Remover uma turma ou revogar acesso cancela suas entregas ainda não iniciadas. Uma
mensagem que já esteja em envio pode chegar mesmo após a remoção. Reautorizar uma pessoa
não restaura automaticamente suas antigas assinaturas.

O banco guarda IDs Telegram, permissões, assinaturas, ações temporárias e offset do bot,
além dos eventos e da fila. Não guarda histórico de conversas, nomes de usuário, token
do bot ou senha UERJ. Botões de paginação podem guardar temporariamente o critério de
busca. IDs e banco são locais e não devem ser versionados. A versão do esquema
é v3 e preserva os dados anteriores na atualização.

O offset é confirmado localmente antes de responder. Se uma resposta da conversa se
perder, ela não é reenviada automaticamente: a pessoa pode usar `/minhas` ou repetir
o comando. As mutações de inclusão/remoção são idempotentes. A fila de alertas usa as
regras de repetição e entrega incerta descritas em [telegram.md](telegram.md).

`bot --once` processa uma página de updates e um ciclo local de manutenção, útil para
verificação. Sem `--once`, usa polling Telegram, intervalo de monitoramento e backoff.
`Ctrl+C` fecha os recursos. Conversas em grupo e callbacks de outro usuário são ignorados.

Antes de produção ainda faltam o protocolo real do Aluno Online, a decisão de sessão,
a regra comprovada de disponibilidade e o teste de recebimento com várias pessoas.
