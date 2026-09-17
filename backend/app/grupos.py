"""Os cargos do FechaCaixa, em um lugar so.

Ja foram quatro: havia um "Responsavel", o login proprio da loja, de quando
ela entrava com usuario e senha. Hoje quem lanca entra pelo codigo da empresa
no aparelho, e a loja nao tem login nenhum.

O grupo aqui e um ROTULO DE CARGO, nao um conjunto de poderes. A autorizacao
inteira vive em app/permissions.py e pergunta pelo nome do grupo; nada no
projeto chama has_perm. As permissoes do Django so aparecem dentro do /admin/,
e quem as concede e o comando ensure_admin.

Existe porque o nome estava escrito solto em mais de vinte lugares. O risco
nao e teorico: "Funcionario" e "Funcionário" sao strings diferentes, e um
acento a mais em qualquer um desses pontos faz a checagem devolver False sem
erro nenhum — a pessoa entra e nao enxerga nada, e o log nao diz por que.
"""

GRUPO_ADMIN = "Admin"
GRUPO_GERENTE = "Gerente"
GRUPO_FUNCIONARIO = "Funcionario"

# Os que a fixture cria no boot. Sem isso, um grupo so passava a existir
# quando alguem cadastrava a primeira pessoa dele — o Funcionario nasceu
# assim, e num banco novo simplesmente nao estava la.
TODOS_OS_GRUPOS = (
    GRUPO_ADMIN,
    GRUPO_GERENTE,
    GRUPO_FUNCIONARIO,
)
