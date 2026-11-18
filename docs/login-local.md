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

No mesmo dia, o usuário executou `portal check --discipline-code IME01-06766` com sua
conta e informou `authenticated: true`, estado `ready`, 76 disciplinas, 53 com turmas,
turmas `1`, `2`, `3`, `4` e oito observações. Isso confirma o login completo e a consulta
nesse teste real. A expiração, a renovação e a operação contínua ainda não foram validadas
no portal real.

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

### Variáveis de ambiente

Também é possível definir `UERJ_MATRICULA` e `UERJ_SENHA` no ambiente do processo.
No PowerShell, para preencher sem incluir a senha literal no comando:

```powershell
$env:UERJ_MATRICULA = Read-Host "Matrícula UERJ"
$senhaUerj = Read-Host "Senha UERJ" -AsSecureString
$env:UERJ_SENHA = [System.Net.NetworkCredential]::new("", $senhaUerj).Password
.\.venv\Scripts\python.exe -m uerj_monitor portal check --from-env --discipline-code IME01-06766
```

As variáveis valem para esse PowerShell e os programas iniciados por ele. Essa opção
não cria arquivo de credenciais. `--from-env` lê somente o ambiente; `--env-file` lê
somente o arquivo escolhido, sem sobreposição. As opções não podem ser combinadas.
Sem ambas, o comando continua pedindo entrada oculta, mesmo que haja variáveis definidas.
Se o modo ambiente for escolhido e faltar uma das variáveis, o programa encerra antes
de tentar login, sem mudar automaticamente para outra fonte.

Para o bot central, configure `from_env = true` em `[portal]`, sem `env_file`. As mesmas
variáveis serão lidas ao iniciar. O token Telegram também pode vir de `BOT_TOKEN` no ambiente.

## Expiração e recuperação

`AuthenticatedPortalSession` implementa o contrato de consulta existente. Cada instância
tem seu próprio cliente HTTP, cookies, campos retornados e credenciais. Antes de consultar
uma disciplina, atualiza a lista e usa o identificador e formulário recém-obtidos.
Cada instância representa uma única conta UERJ e não deve ser usada de forma concorrente.
No bot central, uma instância do responsável atende sequencialmente os convidados,
mantendo as listas e entregas separadas pela identidade Telegram. Não são criadas
sessões UERJ por convidado. Veja a configuração em [bot.md](bot.md).

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
- O usuário confirmou o login completo e a consulta de turmas no portal real em 18/11.
  A fixture `home_autenticada_sintetica.html` continua sintética, construída a partir
  do contrato público de `executaMenu` e dos campos do HAR. O HTML autenticado privado
  não foi incorporado ao repositório. Retornos incompatíveis continuam gerando erro.
- Os testes HTTP simulados cobrem cookies, campos novos, login rejeitado, desafio extra,
  expiração em HTTP 200, recuperação limitada, privacidade e preservação da baseline.
- As consultas reais lista → turmas do dia anterior usaram sessão autorizada dos HARs;
  elas não comprovam o novo fluxo de login por matrícula e senha.

O catálogo central já está ligado à conta única do responsável. Os próximos aceites
são testar o bot com os convidados, validar expiração real, renovação e regras de
disponibilidade em operação controlada.

Se o teste falhar, informe somente a mensagem sanitizada exibida. Não exporte um HAR de
login contendo senha. Uma eventual inspeção adicional deve abranger a página já
autenticada e os campos estruturais necessários, com valores privados removidos.
