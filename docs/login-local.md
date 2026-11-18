# Login e ciclo de sessão UERJ

## O que foi observado em 18/11/2026

A entrada pública foi inspecionada com requisições GET, sem enviar credenciais.
O acesso começa em `https://www.alunoonline.uerj.br/`, que cria o cookie `PHPSESSID`
e retorna JavaScript direcionando para `/requisicaoaluno/`. Preservando esse cookie
e a referência à página inicial, a tela seguinte apresenta o formulário de login.
Uma consulta direta ao destino sem a inicialização retornou HTML com “Página não encontrada”,
mesmo com HTTP 200. Por isso, o código respeita a sequência de entrada.

O formulário observado usa POST para a própria página e os campos `matricula`,
`senha`, `_token` e `requisicao`. Os dois últimos são ocultos e obtidos da resposta
atual, sem valores fixos no código. O validador público verifica matrícula numérica
com ao menos doze dígitos. Não foi observado CAPTCHA nesse formulário. Se aparecer
outro campo ou desafio, o programa interrompe o fluxo em vez de tentar resolvê-lo.

Referências públicas inspecionadas: [entrada](https://www.alunoonline.uerj.br/),
[formulário](https://www.alunoonline.uerj.br/requisicaoaluno/) e
[JavaScript do portal](https://www.alunoonline.uerj.br/public/js/alunoOnline.js?v=1.0.1).

## Testar com sua conta no computador

No PowerShell, na pasta do projeto:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor portal check
```

O comando pede matrícula e senha com entrada oculta. Não há argumentos de linha de
comando para essas credenciais. A sessão e seus cookies ficam em memória e são
encerrados ao terminar o teste. O comando não grava HAR, HTML bruto, senha ou cookies.

Para conferir também uma disciplina:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor portal check --discipline-code IME01-06766
```

Um teste concluído imprime somente resumo estruturado: estado da sessão, quantidade
de disciplinas, quantidade com turma indicada e, quando solicitado, números das
turmas e número de observações. Não imprime matrícula, senha ou campos opacos.

O login só é considerado pronto quando a navegação autenticada é reconhecida e a
lista real de disciplinas é extraída com sucesso. HTTP 200 isoladamente não confirma
autenticação. O teste é de uma conta por execução; ele ainda não ativa o bot real.

Se o terminal não suportar entrada oculta, o programa encerra sem pedir a senha de
forma visível. Execute diretamente em um terminal PowerShell interativo.

### Arquivo local opcional

Quem preferir um arquivo local pode preencher `UERJ_MATRICULA` e `UERJ_SENHA` no `.env`
ignorado pelo Git e fornecer o caminho explicitamente:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor portal check --env-file .env
```

Essa leitura não acontece implicitamente e não altera variáveis de ambiente. O arquivo
é texto local; a entrada interativa evita armazenar a senha no disco. Não compartilhe
credenciais no Telegram ou no chat do projeto.

## Expiração e recuperação

`AuthenticatedPortalSession` implementa o contrato de consulta existente. Cada instância
tem seu próprio cliente HTTP, cookies, campos retornados e credenciais. Antes de consultar
uma disciplina, atualiza a lista e usa o identificador e formulário recém-obtidos.
Uma instância não deve ser compartilhada entre pessoas ou usada de forma concorrente.

| Estado | Significado |
|---|---|
| `disconnected` | Login ainda não validado ou em andamento. |
| `ready` | Lista autenticada validada e pronta para consulta. |
| `expired` | Formulário de login reconhecido durante uma consulta. |
| `failed` | Erro, bloqueio, rejeição ou estrutura inesperada interrompeu o acesso. |
| `closed` | Cliente encerrado e referências locais de sessão descartadas. |

Sem opção adicional, a expiração gera erro e interrompe as consultas. O motor de
monitoramento preserva a baseline e não interpreta essa falha como zero vagas.
O acesso ao catálogo em estado inválido também é recusado.

`--recover-once` permite uma única reautenticação durante a vida daquela sessão:

```powershell
.\.venv\Scripts\python.exe -m uerj_monitor portal check --recover-once --discipline-code IME01-06766
```

A recuperação só é acionada ao reconhecer a tela de login, não por qualquer erro de
rede. Ela limpa cookies, repete a entrada pública, obtém campos novos e faz uma tentativa
de login. Uma senha rejeitada ou uma segunda expiração encerra o fluxo. Não há repetição
automática de credenciais inválidas nem consultas contínuas nesse comando.

Redirecionamentos e respostas HTTP não confirmadas interrompem a operação nesta etapa.
Erros têm mensagens sanitizadas; o trace HTTP que poderia registrar `Set-Cookie` fica
suprimido enquanto a instância de autenticação está ativa.

## Evidência, testes e limites atuais

- O formulário público e a sequência de criação do cookie foram observados no portal real.
- A fixture `login_publico_sanitizado.html` deriva desse formulário, com campos opacos
  fictícios e entradas de matrícula/senha vazias.
- O HTML da página autenticada imediatamente após o login **ainda não foi validado com
  credenciais reais neste desenvolvimento**. A fixture `home_autenticada_sintetica.html`
  está identificada como sintética e usa o contrato público de `executaMenu` e os campos
  do HAR já verificados. O comando falha explicitamente se o retorno real for diferente.
- Os testes HTTP simulados cobrem cookies, campos novos, login rejeitado, desafio extra,
  expiração em HTTP 200, recuperação limitada, privacidade e preservação da baseline.
- As consultas reais lista → turmas do dia anterior usaram sessão autorizada dos HARs;
  elas não comprovam o novo fluxo de login por matrícula e senha.

O próximo aceite é executar `portal check` localmente com a conta do usuário e confirmar
a resposta do login e o menu autenticado. Depois disso, falta ligar sessões de usuários
autorizados ao catálogo do bot, definir o vínculo Telegram/conta UERJ e validar expiração
real, renovação e regras de disponibilidade em operação controlada.

Se o teste falhar, informe somente a mensagem sanitizada exibida. Não exporte um HAR de
login contendo senha. Uma eventual inspeção adicional deve abranger a página já
autenticada e os campos estruturais necessários, com valores privados removidos.
