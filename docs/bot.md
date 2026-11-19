# Bot Telegram com múltiplos usuários

O bot permite autorização, busca, seleção e gerenciamento de assinaturas em dois modos:
demonstração local e catálogo real com uma conta UERJ central do responsável. Cada
convidado autorizado tem sua lista de até dez turmas e não informa login UERJ.
O usuário confirmou o login completo e a consulta real em 18/11/2026. O fluxo do bot
está testado com respostas simuladas; ainda falta validar sua operação no Telegram real.

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

## Usar uma única conta UERJ

Depois de configurar `BOT_TOKEN` e obter seu ID Telegram pelos passos anteriores:

1. Crie `.env` na raiz a partir de `.env.example` (preserve seu arquivo se já existir)
   e preencha os três campos. Escreva matrícula e senha entre as aspas:

```dotenv
BOT_TOKEN=seu_token_do_bot
UERJ_MATRICULA='sua_matricula'
UERJ_SENHA='sua_senha'
```

   O arquivo `.env` é local e ignorado pelo Git. Para validar o acesso:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor portal check --env-file .env --discipline-code IME01-06766
```

2. Copie a configuração central:

```powershell
Copy-Item config.bot.central.example.toml config.local.central.toml
```

3. Preencha `admin_user_ids` na cópia com seu ID Telegram. O exemplo usa outro banco,
   `data/bot-central.sqlite3`, para começar sem as assinaturas fictícias da demonstração.
4. Inicie:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor bot --config config.local.central.toml
```

O exemplo central já lê matrícula e senha do `.env` ao iniciar, sem digitação no terminal.
A sessão fica em memória até encerrar. Os convidados usam somente o Telegram; você os libera
com `/autorizar ID`. A busca mostra as disciplinas com turmas na lista da sua conta.
As consultas do portal são sequenciais, usando a mesma sessão do responsável.

A configuração usa `[portal] env_file = ".env"`. Não é necessário definir variáveis
no PowerShell. Outras formas de fornecer credenciais continuam descritas em
[login-local.md](login-local.md).

O exemplo central permite **uma** renovação automática por execução, apenas quando a
tela de login reaparece. Credenciais rejeitadas não são repetidas. Uma segunda expiração
exige reiniciar o processo; as assinaturas continuam no banco. Veja
[login-local.md](login-local.md) para os limites dessa validação.

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

## Aviso de novas turmas

Quando a pessoa acompanha pelo menos uma turma de uma disciplina, o bot também
acompanha o aparecimento de outras turmas UERJ dessa disciplina. Não é preciso ativar
um comando adicional. Exemplo: quem acompanha Cálculo II / turma 1 recebe um aviso
se uma turma 5 aparecer na consulta, mesmo sem mudança nas contagens da turma 1.

O aviso identifica código, nome e número da turma e orienta usar `/turmas CODIGO`
para escolhê-la. **A turma nova não é cadastrada automaticamente** e o aviso não
consome nenhuma das dez posições. Ele também funciona para quem já está no limite.
O aparecimento de uma turma não comprova vaga disponível nem possibilidade de inscrição.

A primeira consulta válida registra todas as turmas conhecidas sem enviar avisos.
Depois disso, cada número novo gera um evento `class_discovered` por pessoa/disciplina,
mesmo que a pessoa acompanhe várias turmas da mesma matéria. O estado e a fila são
persistidos juntos, preservando avisos pendentes e a deduplicação após reinícios.
Uma turma que desaparece e reaparece com o mesmo número não gera outro aviso.
Falhas de consulta ou ausência da disciplina não apagam as turmas conhecidas.

Remover a última turma acompanhada da matéria ou revogar o acesso encerra esses avisos,
cancela as entregas ainda não iniciadas e limpa a baseline da disciplina. Se a pessoa
voltar a acompanhá-la, a primeira consulta estabelece uma baseline nova. Mudanças de
fase configurada também estabelecem baseline sem anunciar todas as turmas atuais.
O recurso usa a consulta já feita pelo monitor; não acrescenta consultas ao portal.

Para ativar após atualizar o código, pare o bot com `Ctrl+C` e inicie novamente com
o comando habitual. O banco é atualizado automaticamente, mantendo as assinaturas.

Os botões expiram após quinze minutos, são verificados por dono e sobrevivem a reinícios
enquanto válidos. Os números das turmas não são inventados pelo bot. Uma turma que saiu
da consulta não pode ser adicionada por um botão antigo. Atualmente a seleção oferece
o tipo de vaga UERJ; outras categorias dependem das regras ainda não validadas.

## Monitoramento e entregas

O monitor consulta a fonte a cada intervalo configurado e cria baseline por assinatura,
sem alerta inicial. No modo central, uma disciplina é consultada uma vez por ciclo,
independentemente de quantas pessoas acompanhem suas turmas. A mesma captura alimenta
estados independentes e cada pessoa recebe apenas seus próprios alertas. Nenhuma captura
é reutilizada no ciclo seguinte. Falhas preservam o estado anterior, sem gerar vagas
ou eventos artificiais.

Remover uma turma ou revogar acesso cancela suas entregas ainda não iniciadas. Uma
mensagem que já esteja em envio pode chegar mesmo após a remoção. Reautorizar uma pessoa
não restaura automaticamente suas antigas assinaturas.

O banco guarda IDs Telegram, permissões, assinaturas, ações temporárias e offset do bot,
além dos eventos e da fila. Não guarda histórico de conversas, nomes de usuário, token
do bot ou senha UERJ. Botões de paginação podem guardar temporariamente o critério de
busca. IDs e banco são locais e não devem ser versionados. A versão do esquema
é v4 e preserva os dados anteriores na atualização. Os eventos de novas turmas têm
`previous: null`, pois representam descoberta, sem inventar uma observação anterior.

O offset é confirmado localmente antes de responder. Se uma resposta da conversa se
perder, ela não é reenviada automaticamente: a pessoa pode usar `/minhas` ou repetir
o comando. As mutações de inclusão/remoção são idempotentes. A fila de alertas usa as
regras de repetição e entrega incerta descritas em [telegram.md](telegram.md).

`bot --once` processa uma página de updates e um ciclo de manutenção, útil para
verificação. Sem `--once`, usa polling Telegram, intervalo de monitoramento e backoff.
`Ctrl+C` fecha os recursos. Conversas em grupo e callbacks de outro usuário são ignorados.
Após o limite de ciclos consecutivos com falhas (cinco por padrão), o bot encerra com
código 2. Confira a conexão e a sessão e reinicie para retomar.

Faltam validar a expiração e renovação reais, a operação contínua e o recebimento
com os convidados. Os alertas atuais indicam mudanças numéricas ou novas turmas observadas; a regra
de vaga utilizável por fase e elegibilidade ainda precisa ser comprovada.
