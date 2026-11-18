"""Monitor de promoções para a lua de mel em Aruba e Curaçao.

Fontes:
  - sites de promoção (RSS), procurando posts sobre Aruba/Curaçao;
  - Aviasales (API de dados da Travelpayouts): preços do ano todo, por dia;
  - Google Flights (via SerpApi): confere ao vivo as melhores datas de cada mês.
Avisa no Telegram. Configuração em config.json. Segredos por variáveis de ambiente:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRAVELPAYOUTS_TOKEN e SERPAPI_KEY.

Uso:
  python monitor.py             roda uma vez e envia os avisos
  python monitor.py --dry-run   roda uma vez e só imprime os avisos na tela
  python monitor.py --chat-id   mostra o ID dos chats que falaram com o bot
"""
import calendar
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_FILE = ROOT / "state.json"
DRY_RUN = "--dry-run" in sys.argv
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TRAVELPAYOUTS_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
NAMES = {"AUA": "Aruba", "CUR": "Curaçao"}
SENT = 0  # mensagens enviadas nesta execução


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (monitor-lua-de-mel)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def load_state():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    state.setdefault("seen_posts", [])
    state.setdefault("alerted", {})
    state.setdefault("serpapi", {"month": "", "used": 0, "cursor": 0})
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def brasilia_now():
    return datetime.now(timezone.utc) - timedelta(hours=3)


def send(text, silent=False):
    global SENT
    SENT += 1
    if DRY_RUN or not TELEGRAM_TOKEN:
        print(text, "\n" + "-" * 40)
        return
    # De madrugada a mensagem chega sem som.
    quiet = silent or CONFIG["quiet_hours"]["start"] <= brasilia_now().hour < CONFIG["quiet_hours"]["end"]
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
        "disable_notification": "true" if quiet else "false",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data, timeout=30)


def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def brl(value):
    return f"R$ {value:,.0f}".replace(",", ".")


def link(url, label):
    return f'<a href="{html.escape(url)}">{label}</a>'


def per_person_limit():
    """As APIs dão o preço por pessoa; o teto do config é para o casal."""
    return CONFIG["flights"]["max_price_couple_brl"] / CONFIG["flights"]["travelers"]


def price_line(price):
    total = price * CONFIG["flights"]["travelers"]
    return f"<b>{brl(total)} o casal</b> ({brl(price)} por pessoa)"


def trip_label(trip):
    days = (trip["back"] - trip["depart"]).days
    return (f"SP → {NAMES[trip['first']]} {trip['depart']:%d/%m/%Y} · "
            f"{NAMES[trip['last']]} → SP {trip['back']:%d/%m/%Y} ({days} dias)")


MONTH_NAMES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
               "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def is_highlight(trip):
    return trip["depart"].month in CONFIG["flights"].get("highlight_months", [])


def highlight_banner(trip):
    return f"⭐ <b>{MONTH_NAMES[trip['depart'].month - 1].upper()}!</b> ⭐\n" if is_highlight(trip) else ""


# ---------- Voo entre as ilhas ----------

HOPS = {}  # (ilha de saída, dia) -> trecho mais barato entre Aruba e Curaçao


def fetch_island_hops():
    """Preços de um trecho só entre as ilhas, por dia, nos próximos meses (Aviasales)."""
    cfg = CONFIG["flights"]
    first, second = cfg["islands"]
    for month in months_ahead(cfg["months_ahead"]):
        for frm, to in ((first, second), (second, first)):
            try:
                days = tp_get("/aviasales/v3/grouped_prices", origin=frm, destination=to,
                              departure_at=month, group_by="departure_at")
            except Exception as exc:
                print(f"Falha Aviasales {frm}→{to} {month}: {exc}", file=sys.stderr)
                continue
            for ticket in days.values():
                key = (frm, day_of(ticket["departure_at"]))
                if not ticket.get("return_at") and (key not in HOPS or ticket["price"] < HOPS[key]["price"]):
                    HOPS[key] = ticket


def hop_text(trip):
    """Linha com o voo entre as ilhas para essa viagem: preço, se houver, ou um link de busca."""
    if trip["first"] == trip["last"]:
        return ""
    route = f"{NAMES[trip['first']]} → {NAMES[trip['last']]}"
    stay = (trip["back"] - trip["depart"]).days
    options = [HOPS[(trip["first"], trip["depart"] + timedelta(days=d))] for d in range(1, stay)
               if (trip["first"], trip["depart"] + timedelta(days=d)) in HOPS]
    if options:
        hop = min(options, key=lambda t: t["price"])
        text = (f"🛩️ Voo entre ilhas {route}: {brl(hop['price'])}/pessoa em {day_of(hop['departure_at']):%d/%m} · "
                f"{link('https://www.aviasales.com' + hop['link'], 'ver')}")
        if trip.get("price"):
            total = (trip["price"] + hop["price"]) * CONFIG["flights"]["travelers"]
            text += f"\n     Total com ele: <b>{brl(total)} o casal</b>"
        return text
    middle = trip["depart"] + timedelta(days=stay // 2)
    query = f"one way flights from {trip['first']} to {trip['last']} on {middle.isoformat()}"
    url = "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": query, "hl": "pt-BR", "curr": "BRL"})
    return f"🛩️ Voo entre ilhas {route}: sem preço recente · {link(url, 'buscar no Google Flights')}"


# ---------- Sites de promoção ----------

def check_feeds(state):
    seen = state["seen_posts"]
    patterns = [re.compile(r"\b" + re.escape(normalize(k)) + r"\b") for k in CONFIG["keywords"]]
    for feed in CONFIG["feeds"]:
        try:
            root = ET.fromstring(http_get(feed))
        except Exception as exc:
            print(f"Falha ao ler {feed}: {exc}", file=sys.stderr)
            continue
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            url = item.findtext("link") or ""
            guid = item.findtext("guid") or url
            if guid in seen:
                continue
            text = normalize(title + " " + (item.findtext("description") or ""))
            if any(p.search(text) for p in patterns):
                send(f"🌴 <b>Promoção encontrada</b>\n{html.escape(title)}\n{html.escape(url)}")
                seen.append(guid)
    state["seen_posts"] = seen[-500:]


# ---------- Aviasales (Travelpayouts) ----------

def tp_get(path, **params):
    params.update(token=TRAVELPAYOUTS_TOKEN, currency="brl", market=CONFIG["flights"]["market"])
    body = json.loads(http_get(f"https://api.travelpayouts.com{path}?" + urllib.parse.urlencode(params)))
    if not body.get("success"):
        raise RuntimeError(body.get("error"))
    return body["data"]


def months_ahead(count):
    year, month = date.today().year, date.today().month
    for _ in range(count):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month > 12:
            year, month = year + 1, 1


def day_of(timestamp):
    return date.fromisoformat(timestamp[:10])


def collect_trips():
    """Monta as viagens possíveis: ida por uma ilha e volta pela outra e, se permitido, ida e volta simples."""
    cfg = CONFIG["flights"]
    origin, islands = cfg["origin"], cfg["islands"]
    both = cfg.get("require_both_islands", False)
    lengths = range(cfg["min_days"], cfg["max_days"] + 1)
    outbound, inbound, trips = {}, {}, []
    for month in months_ahead(cfg["months_ahead"]):
        for island in islands:
            for frm, to, store in ((origin, island, outbound), (island, origin, inbound)):
                try:
                    days = tp_get("/aviasales/v3/grouped_prices", origin=frm, destination=to,
                                  departure_at=month, group_by="departure_at")
                except Exception as exc:
                    print(f"Falha Aviasales {frm}→{to} {month}: {exc}", file=sys.stderr)
                    continue
                for ticket in days.values():
                    if not ticket.get("return_at"):  # só trechos de ida
                        store[(island, day_of(ticket["departure_at"]))] = ticket
            if both:
                continue  # ida e volta para a mesma ilha não serve
            try:
                roundtrips = tp_get("/aviasales/v3/prices_for_dates", origin=origin, destination=island,
                                    departure_at=month, one_way="false", sorting="price", limit=1000)
            except Exception as exc:
                print(f"Falha Aviasales ida e volta {island} {month}: {exc}", file=sys.stderr)
                roundtrips = []
            for ticket in roundtrips:
                if not ticket.get("return_at"):
                    continue
                depart, back = day_of(ticket["departure_at"]), day_of(ticket["return_at"])
                if (back - depart).days in lengths:
                    trips.append({"first": island, "last": island, "depart": depart, "back": back,
                                  "price": ticket["price"], "legs": [ticket]})
    for (first, depart), out in outbound.items():
        for last in islands:
            if both and last == first:
                continue
            for days in lengths:
                back = depart + timedelta(days=days)
                ret = inbound.get((last, back))
                if ret:
                    trips.append({"first": first, "last": last, "depart": depart, "back": back,
                                  "price": out["price"] + ret["price"], "legs": [out, ret]})
    return trips


def trip_links(trip):
    """Links para comprar: os trechos no Aviasales ou a busca no Google Flights."""
    legs = [leg for leg in trip.get("legs", []) if leg.get("link")]
    if len(legs) == 1:
        return link("https://www.aviasales.com" + legs[0]["link"], "Ver no Aviasales")
    if len(legs) == 2:
        return (link("https://www.aviasales.com" + legs[0]["link"], "Ida (Aviasales)") + " · "
                + link("https://www.aviasales.com" + legs[1]["link"], "Volta (Aviasales)"))
    if trip.get("url"):
        return link(trip["url"], "Ver no Google Flights")
    return ""


def check_aviasales(state):
    """Avisa a viagem mais barata de cada mês quando fica abaixo do teto. Devolve as melhores por mês."""
    if not TRAVELPAYOUTS_TOKEN:
        print("TRAVELPAYOUTS_TOKEN não definido; pulando o Aviasales.")
        return {}
    limit = per_person_limit()
    best = {}
    for trip in collect_trips():
        month = trip["depart"].strftime("%Y-%m")
        if month not in best or trip["price"] < best[month]["price"]:
            best[month] = trip
    alerted = state["alerted"]
    for month, trip in sorted(best.items()):
        print(f"Aviasales {month}: {brl(trip['price'])} · {trip_label(trip)}")
        key = f"aviasales|{month}"
        # Repete o aviso do mesmo mês só se cair pelo menos 3%.
        if trip["price"] <= limit and trip["price"] < alerted.get(key, float("inf")) * 0.97:
            legs = trip["legs"]
            links = trip_links(trip)
            if len(legs) == 1:
                detail = "ida e volta na mesma passagem"
                stops = f"{legs[0].get('transfers', '?')} ida / {legs[0].get('return_transfers', '?')} volta"
            else:
                detail = f"ida {brl(legs[0]['price'])} + volta {brl(legs[1]['price'])}"
                stops = f"{legs[0].get('transfers', '?')} ida / {legs[1].get('transfers', '?')} volta"
            airlines = ", ".join(sorted({leg.get("airline", "?") for leg in legs}))
            send(
                highlight_banner(trip)
                + f"✈️ <b>Abaixo de {brl(CONFIG['flights']['max_price_couple_brl'])} o casal!</b> (Aviasales)\n"
                f"{trip_label(trip)}\n"
                f"{price_line(trip['price'])}\n"
                f"Por pessoa: {detail}\n"
                f"Cias: {airlines} · conexões: {stops}\n{links}\n"
                + (f"{hop_text(trip)}\n" if hop_text(trip) else "")
                + "<i>Preço visto nas últimas 48h; confira antes de comprar.</i>"
            )
            alerted[key] = trip["price"]
    return best


# ---------- Google Flights (SerpApi) ----------

def serpapi_allowed(state):
    """Distribui a cota mensal da SerpApi ao longo do mês."""
    budget = state["serpapi"]
    today = date.today()
    if budget["month"] != today.strftime("%Y-%m"):
        budget.update(month=today.strftime("%Y-%m"), used=0)
    allowed = CONFIG["flights"]["serpapi_monthly_budget"] * today.day / calendar.monthrange(today.year, today.month)[1]
    return budget["used"] < allowed


def search_google_flights(trip):
    cfg = CONFIG["flights"]
    legs = [
        {"departure_id": cfg["google_origins"], "arrival_id": trip["first"], "date": trip["depart"].isoformat()},
        {"departure_id": trip["last"], "arrival_id": cfg["google_origins"], "date": trip["back"].isoformat()},
    ]
    params = {
        "engine": "google_flights",
        "type": 3,  # multi-cidades
        "multi_city_json": json.dumps(legs),
        "adults": 1,
        "currency": "BRL",
        "hl": "pt-BR",
        "gl": "br",
        "api_key": SERPAPI_KEY,
    }
    result = json.loads(http_get("https://serpapi.com/search.json?" + urllib.parse.urlencode(params)))
    options = [o for o in result.get("best_flights", []) + result.get("other_flights", []) if o.get("price")]
    return (min(options, key=lambda o: o["price"]) if options else None), result


def google_candidates(best_by_month):
    """Uma viagem por mês para conferir no Google Flights: a melhor do Aviasales ou, sem ela, datas padrão."""
    cfg = CONFIG["flights"]
    islands = cfg["islands"]
    days = (cfg["min_days"] + cfg["max_days"]) // 2
    candidates = {}
    for index, month in enumerate(months_ahead(cfg["months_ahead"])):
        if month in best_by_month:
            candidates[month] = best_by_month[month]
            continue
        depart = max(date.fromisoformat(f"{month}-10"), date.today() + timedelta(days=7))
        # Alterna o sentido a cada mês: Aruba→Curaçao num, Curaçao→Aruba no seguinte.
        first, last = islands if index % 2 == 0 else islands[::-1]
        candidates[month] = {"first": first, "last": last, "depart": depart,
                             "back": depart + timedelta(days=days), "price": None}
    return candidates


def check_google_flights(state, best_by_month):
    """A cada execução (dentro da cota), confere ao vivo as melhores datas de um mês, em rodízio."""
    if not SERPAPI_KEY:
        return
    if not serpapi_allowed(state):
        print("Cota da SerpApi deste período já usada; Google Flights fica para a próxima.")
        return
    budget = state["serpapi"]
    candidates = google_candidates(best_by_month)
    months = sorted(candidates)
    month = months[budget["cursor"] % len(months)]
    trip = candidates[month]
    budget["cursor"] = (budget["cursor"] + 1) % len(months)
    budget["used"] += 1
    try:
        found, result = search_google_flights(trip)
    except Exception as exc:
        print(f"Falha no Google Flights: {exc}", file=sys.stderr)
        return
    if not found:
        print(f"Google Flights sem resultados: {trip_label(trip)}")
        return
    price, limit = found["price"], per_person_limit()
    print(f"Google Flights: {brl(price)} · {trip_label(trip)}")
    state.setdefault("google_best", {})[month] = {
        "first": trip["first"], "last": trip["last"], "depart": trip["depart"].isoformat(),
        "back": trip["back"].isoformat(), "price": price, "checked": date.today().isoformat(),
        "url": result.get("search_metadata", {}).get("google_flights_url", ""),
    }
    key = f"google|{trip['depart']}|{trip['back']}|{trip['first']}|{trip['last']}"
    if price <= limit and price < state["alerted"].get(key, float("inf")) * 0.97:
        airlines = sorted({seg["airline"] for seg in found.get("flights", []) if seg.get("airline")})
        url = result.get("search_metadata", {}).get("google_flights_url", "")
        send(
            highlight_banner(trip)
            + f"✅ <b>Confirmado no Google Flights: abaixo de {brl(CONFIG['flights']['max_price_couple_brl'])} o casal!</b>\n"
            f"{trip_label(trip)}\n"
            f"{price_line(price)}, ida e volta\n"
            f"Cias (ida): {', '.join(airlines) or '?'}\n{link(url, 'Abrir no Google Flights') if url else ''}"
            + (f"\n{hop_text(dict(trip, price=price))}" if hop_text(dict(trip, price=price)) else "")
        )
        state["alerted"][key] = price


def merged_best(state, best_by_month):
    """Junta as melhores do Aviasales com os preços conferidos no Google Flights nos últimos 7 dias."""
    merged = dict(best_by_month)
    both = CONFIG["flights"].get("require_both_islands", False)
    for month, saved in state.get("google_best", {}).items():
        trip = dict(saved, depart=date.fromisoformat(saved["depart"]), back=date.fromisoformat(saved["back"]))
        if date.fromisoformat(saved["checked"]) < date.today() - timedelta(days=7) or trip["depart"] < date.today():
            continue
        if both and trip["first"] == trip["last"]:
            continue
        if month not in merged or trip["price"] < merged[month]["price"]:
            merged[month] = trip
    return merged


# ---------- Resumo semanal ----------

WEEKDAYS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def weekly_summary(state, best_by_month):
    """Uma vez por semana, manda o menor preço de cada mês, mesmo acima do teto."""
    cfg = CONFIG["weekly_summary"]
    now = brasilia_now()
    week = now.strftime("%G-W%V")
    due = now.weekday() == WEEKDAYS.index(normalize(cfg["weekday"])) and now.hour >= cfg["hour"]
    if not best_by_month or not due or state.get("last_summary") == week:
        return
    limit = per_person_limit()
    travelers = CONFIG["flights"]["travelers"]
    lines = []
    for month, trip in sorted(best_by_month.items()):
        mark = ("✅" if trip["price"] <= limit else "▫️") + ("⭐" if is_highlight(trip) else "")
        lines.append(
            f"{mark} <b>{trip['depart']:%m/%Y}</b>: {brl(trip['price'] * travelers)} o casal "
            f"({brl(trip['price'])}/pessoa) · {NAMES[trip['first']]} {trip['depart']:%d/%m} → "
            f"{NAMES[trip['last']]} {trip['back']:%d/%m}"
            + (f"\n     {trip_links(trip)}" if trip_links(trip) else "")
        )
    cheapest = min(best_by_month.values(), key=lambda t: t["price"])
    send(
        f"📊 <b>Resumo semanal: menor preço por mês</b>\n"
        f"Teto atual: {brl(CONFIG['flights']['max_price_couple_brl'])} o casal\n\n"
        + "\n".join(lines)
        + f"\n\n🏆 Mais barato: {cheapest['depart']:%d/%m/%Y}, {brl(cheapest['price'] * travelers)} o casal"
    )
    state["last_summary"] = week


def report_empty_run(best_by_month):
    """Avisa, sem som, que a execução rodou e não achou nada novo."""
    text = f"🔎 Rodei às {brasilia_now():%Hh%M} e não encontrei promoção nova."
    # As 3 melhores, uma por mês, para mostrar opções de datas diferentes.
    top = sorted(best_by_month.values(), key=lambda t: t["price"])[:3]
    if top:
        text += "\n\n<b>3 melhores preços agora:</b>"
        entries = list(zip("🥇🥈🥉", top))
        # Se nenhuma das 3 for no mês em destaque (setembro), mostra também a melhor dele.
        highlighted = sorted((t for t in best_by_month.values() if is_highlight(t)), key=lambda t: t["price"])
        if highlighted and not any(is_highlight(t) for t in top):
            entries.append(("⭐", highlighted[0]))
        for rank, trip in entries:
            tag = f" ⭐ <b>{MONTH_NAMES[trip['depart'].month - 1]}</b>" if is_highlight(trip) else ""
            text += f"\n\n{rank} {trip_label(trip)}{tag}\n     {price_line(trip['price'])}"
            if trip_links(trip):
                text += f"\n     {trip_links(trip)}"
            if hop_text(trip):
                text += f"\n     {hop_text(trip)}"
    send(text, silent=True)


# ---------- Telegram ----------

def show_chat_ids():
    updates = json.loads(http_get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"))
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("title") or u["message"]["chat"].get("first_name")
             for u in updates.get("result", []) if "message" in u}
    if not chats:
        print("Nenhuma mensagem encontrada. Mande uma mensagem no grupo (ou para o bot) e tente de novo.")
    for chat_id, name in chats.items():
        print(f"{chat_id}  ({name})")


def main():
    if "--chat-id" in sys.argv:
        show_chat_ids()
        return
    state = load_state()
    # Salva o estado mesmo se algo falhar no meio: assim um erro não faz repetir
    # avisos já enviados nem gastar de novo a cota da SerpApi sem registrar.
    try:
        check_feeds(state)
        if TRAVELPAYOUTS_TOKEN:
            fetch_island_hops()
        best_by_month = check_aviasales(state)
        check_google_flights(state, best_by_month)
        overview = merged_best(state, best_by_month)
        weekly_summary(state, overview)
        if SENT == 0 and CONFIG.get("report_empty_runs", True):
            report_empty_run(overview)
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
