# Decisões do produto — 17/11/2026

O produto será um bot Telegram usado por várias pessoas. Cada pessoa busca uma
disciplina por código ou nome (exemplo: `IME01-06766 Cálculo II`), consulta as turmas
mapeadas na fonte e escolhe quais acompanhar.

Decisões confirmadas pelo usuário:

- Limite inicial de **10 turmas ativas no total por pessoa**, somando disciplinas.
- Escolher uma turma já ativa não ocupa outra posição.
- O monitoramento continua após o alerta, até a pessoa remover a turma.
- Remover uma turma libera uma posição. Reativá-la estabelece uma nova baseline.
- Acesso somente para pessoas autorizadas pelo responsável.
- Sessão UERJ individual versus conta central: **decisão adiada até inspecionar o portal**.

Implementação atual:

- Conversas privadas; identidade verificada pelo ID do remetente e do chat Telegram.
- Administradores definidos em configuração local; `/autorizar ID` e `/revogar ID`
  controlam o acesso persistido no banco. `/id` funciona antes da autorização.
- `/buscar`, texto livre, seleção de disciplina e de turma por botões, `/minhas` e
  `/parar`. As listas têm paginação e os botões pertencem ao usuário e expiram.
- Mapeamento de turmas a partir da resposta do catálogo, sem lista fixa de números.
  A turma é consultada novamente antes de confirmar sua inclusão.
- Estado e baseline por assinatura. Duas pessoas na mesma turma têm estados e
  entregas independentes. Novas assinaturas não herdam a baseline de outra pessoa.
- Alertas continuam indicando mudanças observadas, sem inferir inscrição possível.
- A fonte atual é HTML sintético local; a busca de disciplinas usa os códigos e nomes
  configurados. Não há descoberta do catálogo completo da UERJ implementada ainda.

Pendências relevantes:

1. Ligar o catálogo observado ao contexto autenticado de cada usuário. A lista de
   disciplinas, origem dos identificadores e detalhe das turmas já foram verificados;
   o fluxo lista → turmas foi repetido com sucesso e há parsers/fixtures sanitizadas.
2. Definir o modelo de sessão após essa descoberta. O catálogo/monitor recebem contexto
   do usuário para permitir sessões separadas; nenhuma conta central foi presumida.
3. Validar regras de disponibilidade por fase, calendário e elegibilidade.
4. Validar o bot em conversas reais e testar operação contínua com vários usuários.

Esta atualização complementa o plano original. Os testes locais não concluem os aceites
de consulta autenticada, disponibilidade real ou recebimento de alerta real.
