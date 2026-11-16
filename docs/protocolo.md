# Descoberta do protocolo do Aluno Online

## Estado da descoberta

Este documento separa as observações registradas no plano das partes que ainda precisam
de validação. Nenhuma consulta autenticada foi executada nesta implementação.

| Item | Evidência atual | Situação |
|---|---|---|
| Entrada | `https://www.alunoonline.uerj.br/requisicaoaluno/` no plano | Registrada nos prints; não retestada |
| Navegação de turmas | POST de documento HTML para `/requisicaoaluno/` | Registrada no plano; corpo desconhecido |
| Nome das tabelas | Vagas Atualizadas da Turma; Vagas para Solicitação de Inscrição | Registrado no plano |
| Ação e campos do formulário | Ainda não identificados | Pendente |
| Identificador da disciplina | Mapeamento para o POST desconhecido | Pendente |
| Campos ocultos, CSRF e sessão | Nomes, renovação e expiração desconhecidos | Pendente |
| Estrutura HTML / seletores reais | Nenhuma resposta sanitizada disponível | Pendente |
| Semântica SID/SAID e calendário | Disponibilidade utilizável não comprovada | Pendente |
| Limites e condições de consulta | Frequência permitida desconhecida | Pendente |

## Roteiro local de inspeção

1. Autentique-se manualmente no Firefox e abra a lista de disciplinas. Não exporte nem
   compartilhe credenciais para fazer a descoberta.
2. Abra DevTools > Rede, selecione **Tudo** e limpe a lista. Clique em uma disciplina.
3. Selecione o POST de documento para `/requisicaoaluno/`. Examine método, destino,
   corpo/parâmetros e resposta HTML. Não limite a busca a XHR.
4. Registre **nomes** dos campos, tipos, propósito observado e se mudam em cada consulta.
   Substitua valores por marcadores fictícios. Nunca copie valores de senha, cookies,
   autorização, matrícula ou tokens para este documento. Não use “Copiar como cURL”
   para compartilhar a requisição, pois o comando pode conter a sessão inteira.
5. Na resposta, identifique como turma, código, tipo de vaga e tabelas se relacionam no
   DOM. Verifique agrupamentos, células mescladas, linhas vazias e codificação.
6. Repita manualmente a abertura da mesma disciplina e depois de outra. Registre quais
   campos mudam, sem aumentar a frequência de acesso para testar limites.
7. Observe como o portal sinaliza sessão expirada (pode retornar login com HTTP 200).
   Documente o sinal e o fluxo de nova autenticação manual, sem exportar a sessão.
8. Confirme as regras aplicáveis à fase e período de inscrição antes de definir uma
   regra que interprete as contagens como oportunidade de matrícula.

Não precisa enviar HAR completo, senha ou cookie. O insumo necessário é a estrutura
sanitizada de uma resposta e os nomes/papéis dos campos observados.

## Registro a preencher após a inspeção

| Nome do campo observado | Papel comprovado | Muda por consulta? | Obrigatório? |
|---|---|---|---|
| A identificar | A identificar | A verificar | A verificar |

Registrar também: tela de origem, ação manual, destino comprovado, estrutura de resposta,
codificação, sinal de sessão expirada e data da validação. Usar somente valores fictícios.
Não implementar POST de produção a partir de suposições sobre esses campos.

## Preparação da fixture real

Trabalhe no HTML bruto apenas em `captures/`, local e ignorado pelo Git. Construa uma cópia
mínima para `tests/fixtures/turmas_sanitizado.html`, removendo cabeçalhos pessoais, scripts,
comentários, links/atributos identificáveis, campos ocultos e valores de sessão. Preserve
somente o DOM necessário para associar as turmas às tabelas e troque identificadores por
dados fictícios. Revise o arquivo inteiro antes de versionar; não há sanitizador automático
implementado. Adaptar o parser deve acompanhar testes que usem essa estrutura real.

## Contrato sintético implementado

`tests/fixtures/turmas_sintetico.html` foi escrito à mão. Não é uma captura do portal.
O parser `parse_classes(html, *, discipline_code, captured_at=None, source_phase=unknown)`
suporta o seguinte contrato exclusivamente para iniciar e testar a implementação:

- Cada turma ocupa um `section`, com `h2` ou `h3` direto no formato `TURMA 01` ou
  `TURMA: 01`. O código é texto: zeros à esquerda são preservados.
- As tabelas diretas desse bloco têm `caption` identificando o tipo da tabela. Rótulos
  ignoram variação de caixa, espaços e acentos; não usam posições fixas de colunas.
- `thead` contém `th` com os rótulos do plano. A tabela de solicitações tem `Solicitadas`
  agrupando `Total` e `Preferenciais`. `rowspan`/`colspan` são expandidos no cabeçalho.
- `tbody` contém uma linha por tipo de vaga. Não há células de dados mescladas nem linhas
  de totalização. Uma linha é associada por turma e rótulo de tipo, nunca por posição.
- São aceitos inteiros não negativos. Vazio, espaço, `-` e `—` viram `None`. Uma tabela
  ausente preserva os dados da outra e marca a observação como parcial.
- Tipo de vaga, tabela ou turma repetidos, contagens inválidas e estrutura incompatível
  geram `ParseError`, sem incorporar HTML bruto à mensagem. A falha não vira lista vazia.
- O chamador fornece o código da disciplina e, opcionalmente, fase e instante de captura.
  Por padrão, usa fase desconhecida e o horário local de leitura em UTC; esse horário
  não informa quando o servidor atualizou os dados.

O contrato `PortalClient` existe apenas como interface tipada e não possui adaptador HTTP
ou Playwright. A identificação de login expirado deverá ser validada no adaptador real;
a rejeição de HTML fora do contrato sintético não substitui essa validação.

## Aceite da Fase 0

- [x] Extração local programática de duas turmas e ambas as tabelas em HTML sintético.
- [x] Campos ausentes/zero e origens das contagens preservados distintamente.
- [ ] Requisição que abre turmas validada em sessão autorizada do usuário.
- [ ] Campos do POST e fluxo de sessão documentados sem segredos.
- [ ] Parser adaptado e testado contra HTML real sanitizado.
- [ ] Regras de inscrição e limites de consulta esclarecidos.

A Fase 0 não está concluída. A saída JSON atual demonstra o contrato de teste local.
