# Monitor de Vagas UERJ

O produto será um **bot Telegram para pessoas autorizadas**, com busca de disciplinas,
seleção das turmas existentes e limite de **10 turmas ativas por pessoa**. As decisões
confirmadas estão em [docs/produto.md](docs/produto.md).

O fluxo multiusuário já pode ser exercitado com dados locais:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor bot-demo --config config.bot.example.toml
```

Para configurar o bot real, autorizar convidados e escolher turmas, siga
[docs/bot.md](docs/bot.md). O modo `bot` usa apenas `BOT_TOKEN`; cada conversa tem sua
própria lista e destino. A fonte de turmas continua **sintética**, e o modelo de sessão
UERJ será decidido após a inspeção do portal. Uma consulta real já foi repetida com
sucesso a partir de HARs locais, inclusive o fluxo lista → turmas. O parser reconhece
o HTML observado e há um adaptador HTTP com sessão fornecida pelo chamador; login e
renovação de sessão ainda não foram implementados. Os comandos de arquivo/CHAT_ID abaixo
continuam disponíveis como ferramentas locais de desenvolvimento e diagnóstico.

Implementação inicial do [plano](PLANO_MONITOR_VAGAS_UERJ.md): base Python, modelos,
parser de **HTML sintético**, monitor de arquivos locais, persistência SQLite, CLI
e integração opcional ao Telegram com fila persistente.
A Fase 2 pode ser exercitada com simulações enquanto a descoberta do protocolo segue
pendente. O parser aceita a fixture sintética e a estrutura real verificada, com
teste único de consulta HTTP documentado em [protocolo.md](docs/protocolo.md).
A operação contínua do bot ainda usa arquivos locais. O Telegram permanece desativado por padrão e precisa
de um bot e chat configurados localmente para o teste de entrega real.

## Preparar e executar (PowerShell / Python 3.12+)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uerj_monitor parse tests/fixtures/turmas_sintetico.html --discipline-code TEST001
```

Não é necessário ativar o ambiente virtual. Use `python` associado à versão 3.12 ou
superior. Se houver várias instalações, `py -3.12 -m venv .venv` seleciona explicitamente
a versão 3.12 para criar o ambiente.

O comando imprime JSON com uma observação por turma e tipo de vaga: três observações
na fixture, correspondentes a duas turmas fictícias e dois tipos de vaga. Campos vazios
viram `null`, zero permanece zero e os horários incluem fuso UTC. O código da disciplina
é informado pelo chamador e a fase permanece `unknown`, salvo uso explícito de
`--phase SID` ou `--phase SAID`. Essa opção apenas rotula os dados; não valida o calendário
nem aplica regras de inscrição. O JSON inclui somente campos normalizados, sem HTML bruto.

Para HTML em outra codificação há `--encoding`, por exemplo `--encoding windows-1252`.
HTML fora do contrato sintético termina com código de saída 1 e mensagem de erro.

## Monitor local e persistência

O exemplo usa duas turmas fictícias e lê a mesma página uma vez por ciclo. O código
de disciplina, a turma e o tipo de vaga definem a identidade monitorada; outras turmas
ou tipos presentes no arquivo são ignorados.

```powershell
Copy-Item config.example.toml config.local.toml
.\.venv\Scripts\python.exe -m uerj_monitor monitor --config config.local.toml --once
.\.venv\Scripts\python.exe -m uerj_monitor events --config config.local.toml
```

O primeiro comando de monitoramento grava uma baseline em `data/monitor.sqlite3`, sem
evento. As execuções seguintes mantêm o estado entre reinícios e registram somente
mudanças numéricas em campos conhecidos nas duas capturas. `events` lista os últimos
100 eventos, do mais recente ao mais antigo (`--limit` aceita de 1 a 1000).

Para experimentar uma mudança, faça uma cópia da fixture em `captures/`, aponte `html`
na sua configuração local para essa cópia, execute `monitor --once` e altere uma das
contagens. Outra execução cria um evento `numbers_changed`; repetir sem alteração
mantém o histórico sem duplicidade. Esse evento não confirma vaga utilizável.

Para ler o arquivo continuamente:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor monitor --config config.local.toml
```

O intervalo começa em 60 segundos, com jitter positivo de até 10%. Os caminhos de HTML
e banco são relativos ao arquivo TOML, independentemente do diretório do terminal.
Use aspas nas turmas (`"01"`) para preservar zeros à esquerda. `enabled = false` desativa
uma turma. A configuração rejeita opções desconhecidas, turmas repetidas e intervalos
menores que 60 segundos.

Falhas de leitura, HTML incompatível ou turma ausente preservam o último estado daquela
disciplina e geram um resultado de falha, sem eventos de vaga. As outras disciplinas
podem continuar sendo processadas. Havendo falha em um ciclo, o scheduler aplica backoff
exponencial até o limite configurado. O padrão encerra após cinco ciclos consecutivos
com falhas. Corrija a fonte e reinicie para retomar; o histórico permanece no SQLite.
`Ctrl+C` fecha o banco e encerra com código 130; `--once` retorna 1 em caso de falha
de consulta e a execução contínua retorna 2 ao atingir o limite de falhas.

Estado e evento são gravados em uma única transação por turma. Capturas antigas não
substituem o estado atual. O banco armazena somente a última observação por turma e
o histórico de eventos com os valores anteriores e atuais, sem HTML ou dados de sessão.
A fila opcional de notificações é criada junto com o evento, na mesma transação.

## Configurar o Telegram

O [guia de Telegram](docs/telegram.md) explica como criar o bot no BotFather, preencher
o `.env` local, descobrir o chat e testar uma entrega. Não coloque token no TOML nem
no terminal como argumento; o programa lê `BOT_TOKEN` e `CHAT_ID` do `.env` ou do ambiente.

Após preencher o token no `.env` e enviar `/start` ao seu bot:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram chats
# Preencha CHAT_ID no .env com o ID do seu chat e então execute:
.\.venv\Scripts\python.exe -m uerj_monitor telegram test
```

Para integrar ao monitor, altere `[telegram] enabled = true` na sua cópia local do TOML.
Só eventos novos, criados com a integração ativa, entram na fila. A baseline e eventos
históricos não são enviados. Como a fonte ainda é local, as notificações trazem o título
“Demonstração local” e informam mudanças nas contagens sem afirmar vaga utilizável.
Os horários dos alertas são exibidos em Brasília; os dados persistidos mantêm fuso UTC.

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor telegram queue --config config.local.toml
.\.venv\Scripts\python.exe -m uerj_monitor telegram flush --config config.local.toml
```

Há até três tentativas automáticas para falhas recuperáveis. Entregas já confirmadas
não são repetidas após reinícios. Se a resposta de um possível envio se perder, a entrega
fica marcada como incerta e exige revisão; não há promessa de entrega exatamente uma vez.
Detalhes dos estados e da recuperação manual estão no guia.

## Verificar a implementação

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Os testes cobrem associação entre turma/tipo/tabelas, cabeçalhos agrupados, colunas
reordenadas, campos vazios, zero, estruturas ambíguas, erros sem exposição de HTML e
comparação sem confundir solicitações com vagas disponíveis. Também verificam reinício,
rollback de transação, baseline, filtragem das turmas, backoff e desligamento. Todos rodam
localmente; relógio e espera são controlados nos testes, sem acessar o portal.

O teste de aceite usa uma **regra fictícia, presente apenas nos testes**: a sequência
`indisponível -> disponível -> disponível -> indisponível -> disponível` produz dois
eventos, inclusive com reinício no meio. Desconhecimento temporário não rearma um alerta
já emitido. Alterar a fase ou a identificação da regra estabelece outra baseline de
disponibilidade. A interface `AvailabilityRule` permite adicionar a regra real depois
que ela for comprovada; a CLI atual não ativa nenhuma regra de matrícula.

## Significado dos dados

| Campo JSON em `numbers` | Origem |
|---|---|
| `offered` | Oferecidas em Vagas Atualizadas da Turma |
| `occupied` | Ocupadas em Vagas Atualizadas da Turma |
| `requested_offered` | Oferecidas em Vagas para Solicitação de Inscrição |
| `requested_total` | Solicitadas / Total |
| `requested_preferential` | Solicitadas / Preferenciais |

Esses campos não são intercambiáveis. O parser preserva as contagens, sem calcular
disponibilidade. `parse_status=ok` indica preenchimento dos cinco campos, não confirma
possibilidade de matrícula. Uma tabela ou contagem ausente produz `partial`; colunas
desconhecidas, contagens inválidas ou duplicidades provocam erro explícito.

`compare_observations` estabelece baseline na primeira captura e identifica mudanças
numéricas apenas em campos conhecidos nas duas capturas. Mudança de fase, captura antiga
ou perda/recuperação de dados sem outra alteração conhecida retorna `incomparable`.
O monitor persiste essa comparação como eventos numéricos locais. A infraestrutura
de rearmamento é exercitada por uma regra injetada nos testes; a interpretação real
de disponibilidade SID/SAID ainda não foi implementada.

## Próximo passo

Seguir [o roteiro de descoberta do protocolo](docs/protocolo.md) para obter uma amostra
sanitizada e validar a navegação autorizada. A Fase 0 permanece pendente de validação
no portal, apesar de já existir extração programática da amostra sintética.

Depois disso: adaptar o parser e implementar `PortalClient`, validar uma consulta única
e a regra de disponibilidade e integrar o monitor ao portal. O tratamento de HTTP 429/5xx,
timeout de rede e expiração de sessão depende desse adaptador e ainda está pendente.
O aceite real da Fase 3 depende de receber a mensagem de teste no Telegram. A integração
e a fila já podem ser exercitadas com respostas simuladas. O adaptador HTTP do portal
ou de navegador será escolhido quando o protocolo for conhecido.

`.env.example` documenta os nomes das credenciais; preencha somente a cópia local `.env`.
Arquivos de sessão, HTML bruto e HAR devem ficar somente em `captures/` local, ignorado
pelo `.gitignore`. A fixture versionável deve ser revisada manualmente e conter apenas
dados fictícios. Não compartilhe senha, cookies, tokens ou um HAR completo no chat.
