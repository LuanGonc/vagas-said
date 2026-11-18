# Decisões do produto — atualização de 18/11/2026

Em 18/11/2026 o usuário confirmou que o acesso pede matrícula e senha, sem etapa extra.
O projeto é pessoal, para aproximadamente cinco conhecidos. O usuário escolheu uma
**única conta UERJ do responsável**, pois os convidados usam basicamente o mesmo
catálogo. Os convidados não fornecem matrícula ou senha. A autenticação local e o
tratamento de sessão estão descritos em [login-local.md](login-local.md).

O produto será um bot Telegram usado por várias pessoas. Cada pessoa busca uma
disciplina por código ou nome (exemplo: `IME01-06766 Cálculo II`), consulta as turmas
mapeadas na fonte e escolhe quais acompanhar.

Decisões confirmadas pelo usuário:

- Limite inicial de **10 turmas ativas no total por pessoa**, somando disciplinas.
- Escolher uma turma já ativa não ocupa outra posição.
- O monitoramento continua após o alerta, até a pessoa remover a turma.
- Remover uma turma libera uma posição. Reativá-la estabelece uma nova baseline.
- Acesso somente para pessoas autorizadas pelo responsável.
- Uma conta UERJ central; o catálogo fica limitado ao que o responsável consegue consultar.
- Cada pessoa continua com identidade Telegram, permissões, lista e alertas próprios.
- O uso previsto é pequeno; cinco pessoas não é um limite rígido implementado.

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
- Há dois modos explícitos: demonstração com HTML local e catálogo central autenticado.
  No central, a busca atualiza a lista de disciplinas com turmas do responsável e
  extrai as turmas da consulta real, sem cadastro manual de códigos ou identificadores.
- O monitor central consulta cada disciplina uma vez por ciclo para todos os assinantes,
  preservando estados e entregas individuais. O cache termina ao concluir o ciclo.
- As credenciais do responsável vêm de entrada oculta, arquivo local explícito ou
  variáveis de ambiente com `from_env = true`. As fontes não se misturam. Senha e
  cookies não entram no banco, no Telegram ou no Git.
- O usuário confirmou `portal check` no portal real: estado `ready`, 76 disciplinas,
  53 com turmas e turmas `1` a `4` da disciplina consultada (oito observações).
  A integração do bot está implementada e testada com transportes simulados.

Pendências relevantes:

1. Validar o modo central com o portal e o Telegram reais, incluindo expiração e retomada.
2. Validar regras de disponibilidade por fase, calendário e elegibilidade.
3. Validar o bot em conversas reais e testar operação contínua com vários usuários.

Esta atualização complementa o plano original. O login e a consulta autenticada foram
confirmados pelo usuário; os testes locais não comprovam disponibilidade utilizável
ou recebimento de alerta real.
