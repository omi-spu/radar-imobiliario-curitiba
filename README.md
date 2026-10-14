# Radar Imobiliário Curitiba

Sistema local para acompanhar **preços pedidos** em anúncios de casas à venda em Curitiba. Ele coleta imóveis dos sites próprios da Apolar, Innova e Habitec, conserva mudanças de preço e apresenta comparáveis por bairro e características. Preços anunciados não são valores de venda concluída.

## Requisitos e instalação

- Windows com Microsoft Edge instalado (necessário para renderizar as páginas da Apolar).
- Python 3.12 a 3.14 e [`uv`](https://docs.astral.sh/uv/) instalados.

No PowerShell, dentro desta pasta:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv sync
uv run radar collect --limit 3 # Coletar anúncios
uv run radar status # Atualizar estimativa
uv run radar dashboard # Abrir painel
```

O `--limit 3` coleta uma amostra de cada imobiliária para testar o sistema. A coleta completa é `uv run radar collect`. O painel abre em `http://localhost:8501`. O banco fica em `data/radar.sqlite3`, fora do Git.

Se uma fonte ficar desativada por regras de acesso ou HTTP 403, consulte `uv run radar status` antes de tentar novamente com `uv run radar collect --source NOME --retry-blocked`.

## Execução manual

O sistema só executa quando você inicia um comando. Não há tarefa agendada, inicialização com o Windows ou coleta automática em segundo plano. A coleta termina ao concluir as fontes. O painel permanece aberto enquanto seu comando estiver rodando; use `Ctrl+C` no terminal para encerrá-lo.

Para coletar e depois treinar o modelo:

```powershell
uv run radar collect
uv run radar train
```

Opcionalmente, `uv run radar daily` executa uma única rodada de coleta pendente e atualização do modelo, encerrando em seguida. Apesar do nome, ele não cria um agendamento: pula fontes com varredura integral nas últimas 20 horas e atualiza o modelo se tiver mais de sete dias ou estiver indisponível.

Uma execução com `--limit` ou com falhas não marca anúncios como retirados. Após duas coletas **completas** consecutivas sem encontrar um anúncio, ele passa a inativo. Apenas mudanças de preço criam eventos no histórico.

## Dados e estimativa

O banco guarda fonte, referência, URL, título, subtipo, bairro, endereço disponível e precisão, quartos, banheiros, área construída, área do terreno, preço, datas e situação. Campo não publicado fica vazio; a expressão “área total” sozinha não define se é terreno ou construção. Imóveis de fontes diferentes só são agrupados quando endereço numerado e características coincidem com segurança.

O painel filtra anúncios ativos e não reservados. Ele mostra quantidade, mediana, faixa central de preços e mediana por m² construído, com aviso enquanto a cobertura das fontes estiver incompleta. A estimativa usa regressão sobre bairros, subtipos, quartos, banheiros e áreas; só usa imóveis recentes de fontes com varredura integral (até 5% de páginas com falha), e fica oculta se houver menos de 100 imóveis válidos, menos de cinco comparáveis locais, erro mediano de validação acima de 25% ou modelo com mais de oito dias.

Cada conector consulta `robots.txt` antes da coleta, respeita as rotas publicadas e faz requisições espaçadas. Falhas individuais ficam registradas por URL no banco e aparecem no painel; restrições de acesso são registradas sem tentativa de contorná-las.

## Adicionar uma imobiliária

Crie um coletor em `src/radar_imobiliario/collectors/` com as rotas de sitemap, o filtro de URLs de casas à venda e a extração dos campos publicados. Registre a classe em `collectors/__init__.py` e adicione um teste com uma página representativa. O coletor base já verifica `robots.txt`, espaça requisições e acompanha redirecionamentos.

## Verificação

```powershell
uv run pytest
```
