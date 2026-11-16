# Monitor de Vagas — Aluno Online UERJ

> **Documento de contexto e plano de implementação para o Codex**  
> Data: 16/11/2026  
> Estado: descoberta técnica inicial; nenhum código de monitoramento validado ainda.

## 1. Objetivo

Construir um programa **próprio**, em Python, que monitore periodicamente as **disciplinas e turmas específicas selecionadas pelo usuário** no Aluno Online da UERJ e envie uma **notificação rápida pelo Telegram** ao identificar que uma vaga potencialmente utilizável ficou disponível.

**Fora do escopo do MVP:** efetuar inscrição automaticamente, contornar CAPTCHA, burlar autenticação, limites de acesso ou mecanismos de proteção do portal.

### Experiência desejada

1. O usuário cadastra uma ou mais disciplinas e turmas, identificadas preferencialmente por código da disciplina + número da turma.
2. O programa consulta a disponibilidade dessas turmas em intervalos configuráveis.
3. Compara o estado atual com o último estado conhecido e identifica uma mudança relevante para disponibilidade.
4. Dispara um alerta no Telegram com disciplina, turma, evidência da mudança, horário e link para acessar o Aluno Online.
5. Continua monitorando, sem enviar mensagens repetidas para o mesmo estado; pode alertar de novo se a disponibilidade desaparecer e reaparecer.

**Critério de valor:** reduzir o tempo entre a mudança registrada no portal e o recebimento do alerta, sem prometer que a vaga permanecerá disponível nem que será possível efetivar a inscrição.

## 2. Evidências obtidas até agora

As observações abaixo são fundamentadas **nos prints enviados pelo usuário em 13/11/2026**, durante navegação autenticada no Firefox.

| Item | Observação confirmada | Consequência |
|---|---|---|
| Portal | URL visível: `https://www.alunoonline.uerj.br/requisicaoaluno/` | Ponto de partida para inspeção manual do fluxo. |
| Navegação | A página inicial possui `Consulta Acadêmica > Disciplinas do Currículo/A Cursar` e opções de inscrição como `SID` e `SAID` | O fluxo de consulta de turmas está acessível após login; os tipos de inscrição devem ser tratados separadamente. |
| Lista de disciplinas | Uma tela mostra código e nome da disciplina e indicador `Turma no Período?` | Possível origem da lista de disciplinas monitoráveis. |
| Detalhe de turmas | Uma tela intitulada `Turmas da Disciplina` mostra `TURMA`, horários, local, professor e tabelas de vagas | O HTML retornado deve ser examinado para extrair as informações. |
| Tabela 1 | `Vagas Atualizadas da Turma`: colunas `Tipo de Vaga`, `Oferecidas`, `Ocupadas` | Pode permitir cálculo de vagas não ocupadas, se os campos estiverem preenchidos e as regras permitirem. |
| Tabela 2 | `Vagas para Solicitação de Inscrição`: `Oferecidas` e `Solicitadas` (`Total`, `Preferenciais`) | É uma métrica **diferente** de vagas ocupadas. Não tratá-la automaticamente como disponibilidade real. |
| Requisição | DevTools > Rede mostrou `POST` para `/requisicaoaluno/`, `200`, tipo `document`, com HTML; CSS/JS carregados separadamente | Forte indício de navegação com POST e renderização server-side, **não** de uma API JSON. |
| Exemplos visuais | Algumas turmas aparecem com 25 vagas oferecidas para UERJ; uma tem 18 solicitações e outra 3 | Demonstram a estrutura, **não** comprovam disponibilidade atual nem acesso à inscrição. |

### Hipóteses ainda não validadas

- Talvez seja possível reproduzir as consultas com `httpx` mantendo uma sessão autorizada, em vez de usar Playwright em todas as verificações.
- O portal pode usar formulários HTML, campos ocultos, cookies, identificadores de página ou tokens antifraude/CSRF nos `POST`s.
- As tabelas podem ter significados e atualização diferentes conforme a fase de inscrição (SID/SAID) e o tipo de vaga. **Não assumir que `oferecidas - solicitadas` é uma vaga livre.**
- O portal pode encerrar sessões ou bloquear verificações frequentes. Deve-se investigar o comportamento de modo conservador.

### Lacunas críticas

**Ainda não temos:** corpo da requisição POST, nomes dos campos de formulário, trecho de HTML da resposta, regras concretas de disponibilidade durante SAID, detalhes do login e da expiração de sessão, limites de frequência e política de uso do portal. Não inventar payloads, endpoints secundários ou seletores HTML.

## 3. Decisões de engenharia propostas

**Stack do MVP**

- Python 3.12+;
- `httpx` para HTTP, se a navegação puder ser reproduzida legitimamente;
- `beautifulsoup4` e/ou `lxml` para parsear HTML;
- Playwright apenas como fallback quando indispensável para navegação/autenticação, usando sessão autorizada;
- Telegram Bot API para alertas;
- SQLite para persistir estado, histórico mínimo e deduplicação;
- `pytest` para testes, `ruff` para lint, `.env` para segredos locais, com `.env.example` sem credenciais.

**Ordem de preferência de acesso:** (1) consulta pública/autorizada e documentada, caso exista; (2) requisições observadas legitimamente na sessão do próprio usuário; (3) automação de navegador com Playwright. Evitar qualquer mecanismo de evasão de controles.

### Modelo lógico

```text
configuração de turmas
        |
        v
scheduler -> cliente do portal -> parser HTML -> observações normalizadas
                                               |
                                               v
                              comparação + regras de disponibilidade
                                               |
                          mudança relevante / alerta não duplicado
                                               v
                                         Telegram Bot
                                               |
                                               v
                                           SQLite/logs
```

### Modelo de dados mínimo

- `Watch`: `discipline_code`, `discipline_name` (opcional), `class_number`, `vacancy_type` (`UERJ` ou outro, quando suportado), `enabled`.
- `Observation`: `watch_id`, `captured_at` (ISO 8601 com fuso), `offered`, `occupied` (nullable), `requested_total` (nullable), `requested_preferential` (nullable), `source_phase` (`SID`, `SAID`, `unknown`), `raw_status`/`parse_status`.
- `Alert`: `watch_id`, `trigger_type`, `observation_id`, `sent_at`, `delivery_status`.

Não gravar HTML com informações pessoais ou cookies em logs. Se for útil reter fixtures HTML para testes, **sanitizar antes**.

## 4. Estratégia de detecção

O principal risco do projeto é gerar um falso positivo. Separar **observações** de **interpretação**.

1. O parser extrai apenas valores que existam e sejam semanticamente identificáveis na página.
2. Um módulo independente interpreta os dados conforme a **fase da inscrição**.
3. Sem regra validada de elegibilidade, reportar apenas: **“houve mudança nos números de vagas”**, não “há vaga garantida”.
4. Quando `ocupadas` estiver disponível e for pertinente à fase, uma hipótese a testar é `vagas_não_ocupadas = oferecidas - ocupadas`; verificar antes se tal número corresponde a vagas efetivamente inscrevíveis.
5. O primeiro ciclo estabelece o baseline e **não dispara alerta** automaticamente.
6. Alertar quando a condição definida mudar de `false` para `true`, ou quando ocorrer aumento relevante da disponibilidade confirmada. Se voltar a `false`, rearmar o gatilho.
7. Se houver falha de login, parse ou timeout, registrar erro sem transformar ausência de dados em “zero vagas” nem enviar alerta de vaga.

Mensagem proposta:

```text
🚨 Monitor UERJ — alteração de vagas
Disciplina: <nome> (<código>)
Turma: <número> | Tipo: UERJ
Disponibilidade: <valor observado / status validado>
Detectado em: <data e hora no horário de Brasília>
Abrir portal: https://www.alunoonline.uerj.br/requisicaoaluno/
```

O link pode exigir login e navegação manual. Só fornecer deep link se comprovadamente válido.

## 5. Frequência, confiabilidade e segurança

- **Início conservador:** intervalo configurável de **60 segundos**, com opção de 30 segundos **somente se tolerado pelo portal e permitido por suas regras**. Não fazer polling por segundo.
- Adicionar pequena variação aleatória do intervalo (jitter), timeout, tratamento de HTTP 429/5xx, backoff exponencial e pausa após repetidas falhas.
- Evitar consultar a mesma página repetidamente para várias turmas quando uma única resposta contiver dados de todas elas.
- Uma única sessão por instância; não executar consultas concorrentes agressivas.
- Persistir estado entre reinícios e registrar tempos de captura/notificação para medir latência real.
- Segredos via variáveis de ambiente ou armazenamento de credenciais apropriado. **Nunca versionar matrícula, senha, cookie, tokens, corpo de requisições com dados pessoais ou capturas identificáveis.**
- Não solicitar que o usuário compartilhe cookies de sessão ou senhas no chat/issue. O programa deve autenticar localmente de maneira legítima.
- Respeitar Termos de Uso, controles de acesso e capacidade do servidor universitário. Sem contornar CAPTCHA, rate limits ou bloqueios.
- Verificar se o período de inscrição aplicável está ativo antes de interpretar um número como oportunidade de matrícula.

## 6. Roadmap de implementação

### Fase 0 — Descoberta do protocolo (bloqueadora)

**Objetivo:** entender com precisão uma navegação legítima entre lista de disciplinas e página de turmas.

Tarefas:

1. No Firefox, DevTools > Rede > `Tudo` (não filtrar apenas XHR, porque o portal usa `document`).
2. Limpar a lista de requisições, clicar em uma disciplina da lista e selecionar o `POST /requisicaoaluno/` correspondente.
3. Examinar **Método**, **Parâmetros/Corpo da requisição** e **Resposta HTML**.
4. Identificar os **nomes** dos campos enviados, ações, identificadores da disciplina e campos ocultos; **mascarar valores sensíveis**.
5. Verificar se clicar novamente na mesma disciplina faz nova requisição e se os dados mudam.
6. Inspecionar o HTML e descobrir seletores robustos que associem cada `TURMA` à sua tabela, sem depender da posição visual.
7. Documentar a sessão e a expiração de login **sem exportar credenciais**.

**Entregáveis:** `docs/protocolo.md` com fluxo verificado e `tests/fixtures/turmas_sanitizado.html` (ou HTML sintético representativo) sem informações privadas.

**Aceite:** identificar, em ambiente autenticado do usuário, a requisição que abre uma página de turmas e extrair programaticamente pelo menos uma linha de dados de uma turma de um HTML sanitizado.

### Fase 1 — Cliente e parser

- Implementar um adaptador `PortalClient` com interface `fetch_discipline_classes(discipline_code) -> HTML` (assinatura ajustável ao protocolo real).
- Implementar `parse_classes(html) -> list[ClassObservation]`.
- Tratar valores vazios e estrutura inesperada explicitamente.
- Testar parser com fixtures de pelo menos duas turmas e ambas as tabelas.
- Se `httpx` não for viável sem quebrar fluxos autorizados, implementar adaptador Playwright mantendo a mesma interface.

**Aceite:** saída estruturada em JSON local de uma disciplina escolhida, sem alertas nem consultas em loop.

### Fase 2 — Monitor e persistência

- Criar `config.yaml` ou arquivo equivalente com lista de disciplinas/turmas.
- Implementar scheduler não concorrente, SQLite, baseline, deduplicação e rearmamento.
- Configurar intervalo, timeout, backoff e desligamento seguro.
- Testar com respostas simuladas e relógio controlado, sem necessidade de chamar o portal.

**Aceite:** simulação `indisponível -> disponível -> disponível -> indisponível -> disponível` resulta em **dois** eventos de alerta, não três; erros de rede não geram alertas de vaga.

### Fase 3 — Telegram

- Criar bot pelo BotFather e obter `BOT_TOKEN`.
- Descobrir o `CHAT_ID` do chat do usuário localmente, sem colocá-lo no repositório.
- Implementar teste explícito de envio, formatação da mensagem e tratamento de falha de entrega.
- Enfileirar ou tentar novamente notificações com limite controlado, evitando duplicidade.

**Aceite:** alerta de teste chega ao Telegram; token não aparece nos logs.

### Fase 4 — Operação e endurecimento

- Logs estruturados, métricas básicas (`last_success_at`, `last_changed_at`, `last_notification_at`, falhas consecutivas).
- Aviso separado de “monitor desconectado/sessão expirada”, sem expor dados pessoais.
- Processo local persistente (primeiro) e opção de VPS/container no futuro.
- Documentar startup, shutdown, recuperação de sessão e atualização do monitor.

**Aceite:** execução estável por período de teste acordado, sem alertas falsos e com reestabelecimento controlado após falhas.

## 7. Estrutura sugerida do repositório

```text
uerj-vagas-monitor/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config.example.yaml
├── docs/
│   ├── plano.md
│   └── protocolo.md
├── src/uerj_monitor/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── portal_client.py
│   ├── parser.py
│   ├── availability.py
│   ├── storage.py
│   ├── notifier.py
│   ├── monitor.py
│   └── cli.py
└── tests/
    ├── fixtures/
    ├── test_parser.py
    ├── test_availability.py
    └── test_monitor.py
```

A estrutura é proposta, não obrigatória. Criar somente os módulos necessários conforme o código surgir.

## 8. Perguntas abertas que orientam o desenvolvimento

1. Qual é o corpo exato do `POST` que abre `Turmas da Disciplina` (apenas **nomes de parâmetros** e valores não sensíveis)?
2. Como o portal mantém autenticação e quais campos dinâmicos devem ser obtidos da página imediatamente anterior?
3. Em qual página e fase o número de **vagas de fato inscrevíveis** é exibido? Como se distingue `oferecidas`, `ocupadas`, `solicitadas` e `preferenciais`?
4. Os dados da página de turmas mudam em tempo real ou só em atualizações periódicas do sistema?
5. O usuário deseja monitorar somente turmas, ou qualquer turma compatível de uma disciplina?
6. Existem horários em que o portal não permite inscrição, mesmo com vagas apresentadas?
7. Qual política de consulta é permitida e tecnicamente sustentável?

Não bloquear a implementação do parser e dos testes artificiais enquanto itens de negócio permanecem em aberto; **não** assumir respostas.

## 9. Instruções para o Codex

**Você é um agente de desenvolvimento neste repositório.** Use este documento como fonte inicial de requisitos. Não afirme ter descoberto protocolo, endpoints, parâmetros, seletores ou regras de inscrição sem evidência obtida em execução autorizada e documentada. Não invente dados de rede.

**Prioridade imediata:** criar o scaffold mínimo, tipos/modelos e **parser testável por fixtures**, além de um guia objetivo para coletar uma amostra HTML **sanitizada** e os nomes dos campos POST. Não implemente polling agressivo, login com credenciais embutidas ou inscrições automáticas.

**Fluxo de trabalho esperado:**

1. Leia este documento e liste suposições pendentes.
2. Crie módulos pequenos, com interfaces claras, type hints e testes.
3. Se ainda não houver fixture real sanitizada, construa uma fixture sintética semelhante à estrutura visual, identificando-a claramente como sintética.
4. Escreva testes de parser e lógica de mudança de estado antes de consultar o portal ao vivo.
5. Solicite apenas a evidência técnica mínima necessária para implementar o cliente HTTP corretamente.
6. Documente comandos de execução, dependências, riscos e limitações.
7. **Não** inclua cookies, tokens ou informações pessoais em commits, fixtures ou logs.

### Primeiro prompt sugerido ao Codex

> Leia `docs/plano.md` integralmente. Estamos criando em Python um monitor de vagas por turma do Aluno Online da UERJ, com futuro alerta via Telegram. Comece pelo esqueleto mínimo do projeto, modelos tipados, parser HTML testável e testes unitários com fixture sintética explícita. Não invente payloads nem seletores do portal real. Faça o parser ter uma interface clara, documente como inspecionar com segurança um POST HTML no Firefox e quais dados sanitizados ainda precisamos coletar. Não implemente matrícula automática, nem consultas repetidas ao site nesta etapa. Ao finalizar, execute os testes e relate o resultado e as lacunas bloqueadoras.

## 10. Definição de MVP concluído

O MVP estará concluído quando for possível configurar ao menos uma disciplina + turma, observar alterações válidas de disponibilidade em uma sessão autorizada, registrar o estado localmente e receber **uma** notificação Telegram por evento relevante, com recuperação adequada de falhas, sem expor segredos e sem automatizar a matrícula.

---

**Nota de privacidade:** os prints originais continham dados pessoais no cabeçalho da página. Eles não são necessários para o desenvolvimento nem foram reproduzidos neste documento. Se forem compartilhados com o Codex ou versionados, devem ser anonimizados antes.
