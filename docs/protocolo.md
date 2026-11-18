# Descoberta do protocolo do Aluno Online

Atualização de 18/11/2026: a entrada pública e o formulário de login foram inspecionados.
Foi implementado um teste local com matrícula/senha e tratamento de sessão. O login
real com credenciais e o HTML de retorno pós-login ainda precisam de aceite local;
veja [login-local.md](login-local.md) para evidências e limites.

## Estado da descoberta

Este documento separa observações verificadas de hipóteses. Em 17/11/2026, um HAR local
fornecido pelo usuário permitiu examinar a resposta real e repetir **uma única consulta
autenticada de turmas**. A requisição repetida recebeu HTTP 200 e o parser reconheceu
quatro turmas e oito observações (UERJ e Vestibular), sem executar consultas em loop.
Depois, dois novos HARs cobriram a abertura da lista e a seleção de outra disciplina.
O programa repetiu o fluxo **lista → turmas** em duas consultas: recebeu HTTP 200 na
lista, mapeou 76 disciplinas (53 com turma indicada) e abriu Álgebra (`IME02-10815`),
reconhecendo as turmas 1 e 2. Os campos do segundo POST vieram da resposta atualizada
da lista. Esses testes validam consultas com as sessões fornecidas; não validam login,
renovação de sessão expirada ou operação contínua.

| Item | Evidência atual | Situação |
|---|---|---|
| Entrada | `https://www.alunoonline.uerj.br/requisicaoaluno/` no plano | Registrada nos prints; não retestada |
| Navegação de turmas | POST de documento HTML para `/requisicaoaluno/`, HTTP 200 | Verificada no HAR e repetida uma vez pelo programa |
| Nome das tabelas | Vagas Atualizadas da Turma; Vagas para Solicitação de Inscrição | Registrado no plano |
| Campos enviados | `_token`, `requisicao`, `matricula`, `disciplinas[0]` | Nomes verificados; valores privados não reproduzidos |
| Identificador da disciplina | Argumento numérico de `consultarDisciplina(output, id)` no link da lista | Correspondência verificada com `disciplinas[0]` e em nova navegação real |
| Lista de disciplinas | 76 códigos e nomes no currículo da conta da captura; 53 com oferta indicada | Parser e fixture sanitizada; não representa o catálogo completo da UERJ |
| Sessão | Cookie `PHPSESSID`, matrícula e campos opacos no POST | Uso verificado; login, renovação e expiração pendentes |
| HTML real | Quatro turmas, duas tabelas por turma, categorias UERJ e Vestibular | Parser testado com resposta real e fixture sanitizada |
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
| `_token` | Campo opaco; mesmo valor reaparece no formulário da resposta observada | Não determinado com uma consulta | Foi enviado; não testamos sua remoção |
| `requisicao` | Identificador opaco da navegação observada | Não determinado | Foi enviado; não testamos sua remoção |
| `matricula` | Identificador pessoal enviado pelo navegador | Por usuário | Foi enviado; não testamos sua remoção |
| `disciplinas[0]` | Identificador interno numérico da disciplina consultada | Por disciplina; origem na lista ainda pendente | Foi enviado; não testamos sua remoção |

O primeiro HAR contém um POST de turmas e quatro recursos auxiliares, incluindo um
recurso 404. Esse 404 não impediu a resposta HTML nem a extração programática.
Os dois arquivos posteriores acrescentam a abertura da lista e o clique em Álgebra.
Não há captura de login autenticado ou sessão expirada. Os arquivos posteriores foram salvos sem
extensão; o `.gitignore` também cobre o prefixo de arquivo do navegador, além de `*.har`.
Os campos opacos não são tratados como constantes nem têm valores registrados aqui.
Uma repetição bem-sucedida não comprova que possam ser reaproveitados indefinidamente.

## Estrutura real validada e teste único

O documento identifica a disciplina em um bloco com `b` de rótulo `Disciplina:`.
Somente esse bloco é usado para verificar o código solicitado; referências a disciplinas
equivalentes em outras partes da página não mudam a identidade da consulta.

Cada turma fica em uma linha de tabela própria com o rótulo `b` `TURMA:` e seu número.
Dentro dessa linha estão as duas tabelas de vagas. A captura usa células `td` inclusive
nos cabeçalhos, não possui `thead`/`tbody` e contém `caption` mal fechado. O adaptador
reconhece essa estrutura e a converte para o contrato interno, validando títulos,
cabeçalhos agrupados e associação por linha, sem depender de índices globais de tabela.
Turmas/linhas ambíguas, código de disciplina diferente ou contagem inválida falham
explicitamente. Ausência de contagem permanece desconhecida.

`tests/fixtures/turmas_real_sanitizado.html` preserva a estrutura relevante observada,
com código de disciplina, textos e contagens substituídos por dados fictícios. Não
contém cabeçalho pessoal, cookies, campos de formulário, scripts ou links de sessão.
A fixture sintética original continua suportada e testada separadamente.

A consulta única usou os campos e a sessão do HAR local, sem inventar payloads ou
alterar a disciplina selecionada. O resultado reconhecido foi a disciplina do exemplo
`IME01-06766` com turmas 1, 2, 3 e 4. As contagens não demonstram por si só elegibilidade
de matrícula nem o calendário vigente; a fase continua desconhecida no modelo.

## Lista e construção da consulta verificadas

O POST que abre a lista envia `flg_logado`, `_token`, `target`, `hRef` e `requisicao`.
Seus valores continuam privados e seu ciclo de renovação ainda não foi validado.
A resposta contém um formulário POST com `_token`, `requisicao` e `matricula`, cujo
destino é `/requisicaoaluno/`. Esses três valores coincidem com os enviados pelo
navegador no clique seguinte. O identificador numérico do link escolhido coincide
com o valor enviado em `disciplinas[0]`. A coincidência também foi validada em uma
nova navegação real: obter lista e consultar Álgebra com os campos dela.

A tabela da lista possui os cabeçalhos `Disciplina`, `Período`, `Atendida?`, `Tipo`,
`Ramif.`, `Cred.`, `CH Total`, `Trava Crédito` e `Turma no Período?`. O código e o nome
estão juntos em um `span` da primeira célula. Os links observados usam
`javascript:consultarDisciplina(output, <identificador>); return false;`.

`parse_discipline_list` extrai somente código, nome, identificador e indicador de
oferta (`Sim`/`Não`). Não extrai as colunas de histórico/situação acadêmica. A fixture
`tests/fixtures/disciplinas_real_sanitizado.html` usa nomes, IDs, códigos e campos de
formulário fictícios e remove os valores acadêmicos das linhas originais.

`ObservedPortalClient` recebe um cliente HTTP com sessão autorizada e o HTML da lista.
Ele resolve o código para o identificador observado, obtém os campos do formulário,
efetua uma consulta de turmas e valida a disciplina retornada. Não segue redirecionamentos
nem transforma login/erro em lista vazia. O chamador deve manter a sessão isolada por
conta UERJ. Este adaptador não implementa login nem decide o modelo de conta.

Os 76 itens são do currículo da conta logada, não um índice público global. Em
18/11/2026 o usuário escolheu uma conta central para aproximadamente cinco conhecidos
e confirmou que usam basicamente o mesmo catálogo. `CentralPortalCatalog` liga essa
sessão ao bot; as assinaturas continuam individuais. Disciplinas fora da lista do
responsável não passam a ser consultáveis por esse compartilhamento.

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

O contrato `PortalClient` possui agora um adaptador HTTP baseado na lista observada,
mas depende de sessão autorizada fornecida pelo chamador. O protótipo de login e
recuperação limitada foi adicionado em 18/11 e aguarda teste com credenciais reais.
Os testes reais de consulta já realizados usam dados locais fora do
repositório. A identificação específica de login expirado ainda precisa ser validada;
a rejeição genérica de resposta incompatível não substitui essa descoberta.

## Aceite da Fase 0

- [x] Extração local programática de duas turmas e ambas as tabelas em HTML sintético.
- [x] Campos ausentes/zero e origens das contagens preservados distintamente.
- [x] Requisição que abre turmas verificada e repetida uma vez com sessão do usuário.
- [x] Campos do POST documentados sem valores privados.
- [x] Parser adaptado e testado contra HTML real sanitizado.
- [x] Origem dos identificadores e campos do formulário da lista verificada.
- [x] Fluxo lista → turmas repetido com campos obtidos da resposta atualizada.
- [ ] Autenticação e renovação/expiração de sessão documentadas.
- [ ] Regras de inscrição e limites de consulta esclarecidos.

O aceite mínimo de identificar a consulta e extrair linhas reais foi atingido. A
descoberta necessária à operação contínua permanece incompleta nos itens marcados.
