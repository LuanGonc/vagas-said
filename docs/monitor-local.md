# Estado do monitor local

## Implementado

- Configuração TOML com seleção de disciplina/turma/tipo, ativação por turma e caminhos
  relativos ao arquivo de configuração.
- Adaptador de arquivos locais, preservando a interface `PortalClient`. Uma leitura
  por disciplina em cada ciclo, sem concorrência.
- SQLite com versão de esquema, baseline, último estado e histórico de eventos.
  Atualização de estado e criação de evento ocorrem na mesma transação por turma.
- Deduplicação entre reinícios e rejeição de observações antigas ou com mesmo horário.
- Comparação numérica conservadora como comportamento padrão. O evento
  `numbers_changed` informa a diferença observada; não representa elegibilidade.
- Interface de regra de disponibilidade, usada somente com regras fictícias nos testes.
  Disponibilidade desconhecida preserva o último valor conhecido na mesma fase/regra.
  Mudança de fase ou de regra reinicia a baseline de disponibilidade.
- Scheduler com intervalo mínimo de 60 segundos, jitter positivo, backoff limitado,
  encerramento após falhas consecutivas e interrupção por `Ctrl+C`.
- Comandos `monitor --once`, `monitor` e `events`, com fonte de disciplinas local.
- Telegram opcional: entrega de teste, descoberta de chat, formatação de eventos,
  fila persistente, tentativas limitadas e recuperação manual de envios incertos.
  A configuração padrão não faz requisições de rede.

## Verificações de aceite

Os testes usam HTML sintético e relógio/espera controlados. A sequência fictícia
`indisponível -> disponível -> disponível -> indisponível -> disponível` gera dois
eventos com rearmamento, incluindo reinício do banco e do monitor entre as capturas.
Falha de leitura, timeout simulado ou HTML inválido não geram eventos de vaga e não
apagam a baseline. Os testes também forçam falha na inserção de um evento para verificar
que a transação não avança o estado sem salvar o evento.

Sem uma regra validada configurada, qualquer mudança numérica conhecida pode produzir
um evento local, inclusive uma redução de disponibilidade ou uma alteração de solicitações.
Isso é diferente do teste fictício de disponibilidade e está indicado no tipo do evento.

## Pendências para operação real

A descoberta da Fase 0 e o aceite da Fase 1 continuam pendentes, conforme
[protocolo.md](protocolo.md). Não há payload de POST, seletor real ou regra de inscrição
presumidos nesta etapa. A Fase 2 está preparada para testes locais, sem aceite de
operação integrada ao Aluno Online.

Ainda faltam o adaptador autenticado, timeout e tratamento de erros HTTP, identificação
da sessão expirada e a interpretação comprovada de disponibilidade no calendário vigente.
A implementação de Telegram está descrita em [telegram.md](telegram.md), incluindo a
fila e as novas tentativas. O recebimento real ainda deve ser validado pelo usuário,
com as credenciais locais do bot. A Fase 3 não tem aceite real concluído.
O histórico de eventos ainda não tem limpeza automática; a retenção deverá ser definida
antes de uma execução contínua longa.
