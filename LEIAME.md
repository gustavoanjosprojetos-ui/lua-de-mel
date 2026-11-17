# Monitor de promoções — Lua de mel em Aruba e Curaçao 🌴

O monitor roda **de hora em hora** (00h17, 01h17 … 23h17), inclusive de madrugada. Os avisos que chegam entre 00h e 07h vêm **sem som**, para não acordar ninguém. Esse horário de silêncio pode ser ajustado em `quiet_hours`, no `config.json`.

Quando uma execução não encontra nada novo, o bot manda uma mensagem **sem som** avisando que rodou, com os **3 melhores preços do momento** (um por mês, para mostrar datas diferentes). Para desligar esse aviso, coloque `"report_empty_runs": false` no `config.json`.

Em cada execução, ele faz três coisas:

1. **Sites de promoção:** lê o Melhores Destinos, o Passageiro de Primeira, o Mestre das Milhas e o Viaje na Viagem. Quando sai um post sobre Aruba ou Curaçao, você recebe um aviso.
2. **Aviasales (o ano todo):** procura os preços de cada dia dos próximos 12 meses saindo de São Paulo (GRU, CGH ou VCP), em viagens de 7 a 15 dias. Ele monta estas combinações:
   - SP → Aruba … Curaçao → SP
   - SP → Curaçao … Aruba → SP
   - ida e volta só para Aruba, ou só para Curaçao

   Quando a viagem mais barata de um mês fica **até R$ 8.000 para o casal** (R$ 4.000 por pessoa), chega um aviso. O mesmo mês só gera outro aviso se o preço cair pelo menos 3%.
3. **Google Flights (confirmação ao vivo):** a cada execução, confere no Google Flights as melhores datas de um mês, em rodízio. Se o preço confirmar abaixo do teto, chega um aviso ✅ com o link.

Para mudar o teto, os dias de viagem, os meses ou os sites, edite o `config.json`.

---

## Configuração (uns 20 minutos, feita uma vez só)

### 1. Criar o bot no Telegram
1. No Telegram, abra uma conversa com **@BotFather** e mande `/newbot`.
2. Escolha um nome (por exemplo, *Lua de Mel Promo*). Ele vai responder com um **token** parecido com `123456:ABC-...`. Guarde esse token.
3. Crie um **grupo** com você e sua noiva e adicione o bot nele.
4. Mande qualquer mensagem no grupo (por exemplo, "oi bot").
5. Descubra o ID do grupo rodando, no PowerShell, dentro desta pasta:
   ```powershell
   $env:TELEGRAM_BOT_TOKEN="SEU_TOKEN"; python monitor.py --chat-id
   ```
   Vai aparecer um número negativo, como `-1001234567890`. Esse é o **chat ID**.

### 2. Criar o token da Travelpayouts (preços do Aviasales, gratuito)
1. Crie uma conta em https://www.travelpayouts.com.
2. Copie o **API token** que aparece no seu perfil, na seção *API token*.

### 3. Criar a chave da SerpApi (Google Flights, gratuito com limite)
1. Crie uma conta em https://serpapi.com.
2. Copie a **API Key** que aparece no painel.

O monitor distribui as buscas ao longo do mês para caber no plano gratuito, usando até 95 por mês (cerca de 3 por dia). Se o seu plano tiver outro limite, ajuste `serpapi_monthly_budget` no `config.json`.

### 4. Colocar para rodar sozinho no GitHub
1. Crie um repositório **privado** no GitHub (ex.: `lua-de-mel`) e envie todos os arquivos desta pasta, incluindo a pasta `.github`.
2. No repositório, vá em **Settings → Secrets and variables → Actions → New repository secret** e crie:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TRAVELPAYOUTS_TOKEN`
   - `SERPAPI_KEY`
3. Vá na aba **Actions**, escolha **Monitor lua de mel** e clique em **Run workflow** para testar na hora.

---

## Testar no seu computador
```powershell
python monitor.py --dry-run
```
Esse comando mostra os avisos na tela, sem mandar nada para o Telegram. Ele usa as chaves que estiverem definidas como variáveis de ambiente.

## E Skyscanner, Decolar e Kayak?
Esses sites não oferecem acesso aberto para consultar preços por programa. Copiar os dados direto das páginas também não funciona: eles bloqueiam robôs e isso vai contra os termos de uso. As fontes usadas aqui cobrem o mesmo tipo de busca:
- **Aviasales:** é um comparador do mesmo tipo do Skyscanner e do Kayak, que junta preços de companhias e agências.
- **Google Flights:** também mostra opções de agências.

Se quiserem, dá para complementar com os **alertas de preço próprios** desses sites. São gratuitos e chegam por e-mail:
- **Skyscanner e Kayak:** busquem a rota e cliquem em "Criar alerta de preço".
- **Decolar:** tem alerta de preço no app.

## Bom saber
- **O teto é para o casal:** R$ 8.000 no total, ida e volta, ou seja, R$ 4.000 por pessoa. Os avisos mostram o total do casal e o valor por pessoa. Para mudar, ajuste `max_price_couple_brl` no `config.json`.
- **Resumo semanal:** todo domingo, a partir das 09h, chega uma mensagem com o menor preço de cada mês, mesmo os que estão acima do teto. Os meses dentro do teto aparecem com ✅. Use esse resumo para calibrar o teto. Para mudar o dia ou a hora, ajuste `weekly_summary` no `config.json`.
- **O voo entre Aruba e Curaçao não está incluído.** É um trecho curto, de uns 30 minutos, que costuma ser comprado à parte (Divi Divi Air, EZ Air, Winair).
- **Os preços do Aviasales vêm de buscas recentes de outros usuários** (até 48h). Por isso, o valor pode ter mudado quando vocês clicarem. Os avisos ✅ do Google Flights são conferidos na hora.
