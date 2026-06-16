import streamlit as st
import requests
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="AU Arb Finder", page_icon="💰", layout="wide")

# ── Constants ──────────────────────────────────────────────────────────────────
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

AU_BOOKMAKERS = [
    "sportsbet", "ladbrokes", "neds", "pointsbet", "tab",
    "unibet", "bluebet", "betr", "betright", "draftstars",
]

EXCHANGE_BOOKMAKERS = [
    "betfair_ex_best_odds",
    "betfair_ex_averaged_odds",
]

SPORTS_OPTIONS = {
    "AFL": "aussierules_afl",
    "NRL": "rugbyleague_nrl",
    "Soccer - A-League": "soccer_australia_aleague",
    "Cricket - BBL": "cricket_big_bash",
    "Basketball - NBL": "basketball_nbl",
    "Tennis - ATP": "tennis_atp_us_open",
    "Soccer - EPL": "soccer_epl",
    "Soccer - Champions League": "soccer_uefa_champs_league",
    "American Football - NFL": "americanfootball_nfl",
    "MMA - UFC": "mma_mixed_martial_arts",
}

MARKET_OPTIONS = {
    "Head-to-Head (Win/Loss)": "h2h",
    "Totals (Over/Under)": "totals",
    "Spreads (Handicap)": "spreads",
}

MARKET_LABELS = {v: k for k, v in MARKET_OPTIONS.items()}

# ── API helpers ────────────────────────────────────────────────────────────────

def fetch_odds(api_key: str, sport: str, bookmakers: list[str], markets: list[str]) -> list[dict]:
    r = requests.get(
        f"{ODDS_API_BASE}/sports/{sport}/odds",
        params={
            "apiKey": api_key,
            "regions": "au",
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "bookmakers": ",".join(bookmakers),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ── Arb logic ──────────────────────────────────────────────────────────────────

def outcome_key(outcome: dict) -> str:
    """Build a unique key that includes the line value for totals/spreads."""
    name = outcome["name"]
    point = outcome.get("point")
    if point is not None:
        sign = "+" if point > 0 else ""
        return f"{name} {sign}{point}"
    return name


def adjusted_price(price: float, bookie_key: str, commission: float) -> float:
    """Reduce Betfair exchange odds by commission on winnings."""
    if "betfair_ex" in bookie_key and price > 1:
        return 1 + (price - 1) * (1 - commission)
    return price


def find_arbs(events: list[dict], betfair_commission: float, min_profit: float) -> list[dict]:
    arbs = []
    for event in events:
        # markets_data[market_key][outcome_key] = (best_price, bookie_title, bookie_key)
        market_best: dict[str, dict[str, tuple[float, str, str]]] = {}
        # also track how many books cover each market
        market_book_count: dict[str, set] = {}

        for bookie in event.get("bookmakers", []):
            bookie_title = bookie["title"]
            bookie_key = bookie["key"]
            for market in bookie.get("markets", []):
                mkey = market["key"]
                if mkey not in market_best:
                    market_best[mkey] = {}
                    market_book_count[mkey] = set()
                market_book_count[mkey].add(bookie_title)

                for o in market["outcomes"]:
                    okey = outcome_key(o)
                    price = adjusted_price(o["price"], bookie_key, betfair_commission)
                    if okey not in market_best[mkey] or price > market_best[mkey][okey][0]:
                        market_best[mkey][okey] = (price, bookie_title, bookie_key)

        for mkey, best in market_best.items():
            if len(best) < 2:
                continue

            # For totals/spreads: pair Over/Under or home/away with same line
            outcome_pairs = _get_valid_pairs(mkey, best)
            for pair in outcome_pairs:
                implied_sum = sum(1 / best[ok][0] for ok in pair)
                if implied_sum >= 1.0:
                    continue
                profit_pct = (1 / implied_sum - 1) * 100
                if profit_pct < min_profit:
                    continue

                best_odds = {ok: best[ok] for ok in pair}
                arbs.append({
                    "event": event["home_team"] + " vs " + event["away_team"],
                    "sport": event.get("sport_title", ""),
                    "market": MARKET_LABELS.get(mkey, mkey),
                    "market_key": mkey,
                    "commence": event.get("commence_time", ""),
                    "profit_pct": round(profit_pct, 3),
                    "implied_sum": round(implied_sum, 4),
                    "best_odds": best_odds,
                    "n_books": len(market_book_count.get(mkey, [])),
                })

    return sorted(arbs, key=lambda x: x["profit_pct"], reverse=True)


def _get_valid_pairs(market_key: str, best: dict) -> list[list[str]]:
    """
    For h2h: one pair of all outcomes (2 or 3-way).
    For totals/spreads: match Over X with Under X at the same line.
    """
    if market_key == "h2h":
        return [list(best.keys())]

    if market_key == "totals":
        # Group by line value: Over 44.5 pairs with Under 44.5
        lines: dict[str, list[str]] = {}
        for okey in best:
            parts = okey.rsplit(" ", 1)
            if len(parts) == 2:
                line = parts[1]
                lines.setdefault(line, []).append(okey)
        return [pair for pair in lines.values() if len(pair) == 2]

    if market_key == "spreads":
        # Group by absolute line: Team A -3.5 pairs with Team B +3.5
        lines: dict[str, list[str]] = {}
        for okey in best:
            parts = okey.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    line = str(abs(float(parts[1])))
                    lines.setdefault(line, []).append(okey)
                except ValueError:
                    pass
        return [pair for pair in lines.values() if len(pair) == 2]

    return [list(best.keys())]


def calc_stakes(best_odds: dict, total_stake: float, betfair_commission: float) -> pd.DataFrame:
    implied_sum = sum(1 / v[0] for v in best_odds.values())
    rows = []
    for okey, (odds, bookie_title, bookie_key) in best_odds.items():
        is_exchange = "betfair_ex" in bookie_key
        raw_odds = odds / (1 - betfair_commission) + betfair_commission * odds if is_exchange else odds
        stake = total_stake * (1 / odds) / implied_sum
        ret = stake * odds
        profit = ret - total_stake
        row = {
            "Outcome": okey,
            "Bookmaker": bookie_title + (" (exchange)" if is_exchange else ""),
            "Odds (net)": round(odds, 3),
            "Stake ($)": round(stake, 2),
            "Return ($)": round(ret, 2),
            "Profit ($)": round(profit, 2),
        }
        if is_exchange:
            row["Raw Exchange Odds"] = round(raw_odds, 3)
        rows.append(row)
    return pd.DataFrame(rows)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    api_key = st.text_input("The Odds API Key", type="password",
                            help="Free key at https://the-odds-api.com")

    st.markdown("---")
    st.subheader("Sports")
    selected_sports = st.multiselect("Sports to scan", list(SPORTS_OPTIONS.keys()),
                                     default=["AFL", "NRL"])

    st.subheader("Markets")
    selected_markets = st.multiselect(
        "Market types",
        list(MARKET_OPTIONS.keys()),
        default=list(MARKET_OPTIONS.keys()),
        help="Totals and Spreads often have more arbs than H2H",
    )

    st.subheader("Bookmakers")
    use_au_books = st.checkbox("AU bookmakers", value=True)
    use_betfair = st.checkbox("Betfair Exchange", value=True,
                              help="Betfair exchange odds are often the sharpest")
    if use_betfair:
        betfair_commission = st.slider("Betfair commission %", 0.0, 10.0, 5.0, 0.5) / 100
    else:
        betfair_commission = 0.05

    if use_au_books:
        selected_books = st.multiselect("Select AU bookmakers", AU_BOOKMAKERS, default=AU_BOOKMAKERS)
    else:
        selected_books = []

    all_books = selected_books + (EXCHANGE_BOOKMAKERS if use_betfair else [])

    st.markdown("---")
    min_profit = st.slider("Min profit % to show", 0.0, 5.0, 0.1, 0.05)
    total_stake = st.number_input("Total stake per arb ($)", 10, 100000, 1000, 50)

    scan_btn = st.button("🔍 Scan for Arbs", type="primary", use_container_width=True)
    st.caption("Data via [The Odds API](https://the-odds-api.com)")


# ── Main ───────────────────────────────────────────────────────────────────────
st.title("💰 Australian Sportsbook Arbitrage Finder")
st.caption("Scans AU bookmakers + Betfair Exchange across H2H, Totals, and Spreads markets.")

if not api_key:
    st.info("Enter your **The Odds API** key in the sidebar to get started. Free tier = 500 req/month.")
    st.markdown("""
    ### How it works
    1. Enter your API key (free at [the-odds-api.com](https://the-odds-api.com))
    2. Choose sports, market types, and bookmakers
    3. Click **Scan for Arbs**
    4. Expand any opportunity to see exact stakes per outcome

    ### Markets explained
    | Market | Description | Arb frequency |
    |--------|-------------|---------------|
    | **H2H** | Back each team to win | Rare — books sync fast |
    | **Totals** | Over/Under a point total | More common — books price independently |
    | **Spreads** | Handicap/line betting | Most common — larger pricing gaps |

    ### Betfair Exchange
    Betfair lets punters set their own odds, often diverging from bookmaker prices.
    A 5% commission applies to net winnings — the app adjusts odds automatically.
    """)
    st.stop()

if not selected_sports or not selected_markets or not all_books:
    st.warning("Select at least one sport, market type, and bookmaker in the sidebar.")
    st.stop()

# ── Scan ───────────────────────────────────────────────────────────────────────
if scan_btn:
    market_keys = [MARKET_OPTIONS[m] for m in selected_markets]
    all_arbs: list[dict] = []
    errors: list[str] = []

    progress = st.progress(0, text="Scanning…")
    for i, sport_name in enumerate(selected_sports):
        sport_key = SPORTS_OPTIONS[sport_name]
        progress.progress((i + 1) / len(selected_sports), text=f"Fetching {sport_name}…")
        try:
            events = fetch_odds(api_key, sport_key, all_books, market_keys)
            arbs = find_arbs(events, betfair_commission, min_profit=0)  # filter later
            all_arbs.extend(arbs)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 401:
                st.error("Invalid API key.")
                st.stop()
            elif code == 422:
                errors.append(f"{sport_name}: not currently available")
            else:
                errors.append(f"{sport_name}: HTTP {code}")
        except Exception as e:
            errors.append(f"{sport_name}: {e}")

    progress.empty()
    st.session_state["arbs"] = all_arbs
    st.session_state["scan_time"] = datetime.now().strftime("%H:%M:%S")

    if errors:
        with st.expander("⚠️ Errors", expanded=False):
            for err in errors:
                st.warning(err)

# ── Results ────────────────────────────────────────────────────────────────────
arbs: list[dict] = st.session_state.get("arbs", [])
scan_time: str = st.session_state.get("scan_time", "")

if arbs or scan_time:
    filtered = [a for a in arbs if a["profit_pct"] >= min_profit]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Arbs Found", len(filtered))
    col2.metric("Best Profit", f"{filtered[0]['profit_pct']:.3f}%" if filtered else "—")
    col3.metric("Total Scanned", len(arbs))
    col4.metric("Last Scan", scan_time or "—")

    if filtered:
        # Summary table
        summary = pd.DataFrame([{
            "Event": a["event"],
            "Market": a["market"],
            "Sport": a["sport"],
            "Profit %": a["profit_pct"],
            "Implied Sum": a["implied_sum"],
            "Books": a["n_books"],
        } for a in filtered])

        st.markdown("---")

        tab_detail, tab_table = st.tabs(["Expanded View", "Summary Table"])

        with tab_table:
            st.dataframe(
                summary.style.background_gradient(subset=["Profit %"], cmap="Greens"),
                use_container_width=True,
                hide_index=True,
            )

        with tab_detail:
            for arb in filtered:
                profit_color = "green" if arb["profit_pct"] >= 1 else "orange"
                label = (
                    f"**{arb['event']}** — :{profit_color}[+{arb['profit_pct']:.3f}%] "
                    f"| {arb['market']} | {arb['sport']} | {arb['n_books']} books"
                )
                with st.expander(label):
                    try:
                        dt = datetime.fromisoformat(arb["commence"].replace("Z", "+00:00"))
                        st.caption(f"Starts: {dt.strftime('%a %d %b %Y %H:%M UTC')}")
                    except Exception:
                        pass

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Guaranteed Profit %", f"{arb['profit_pct']:.3f}%")
                    c2.metric("Implied Sum", f"{arb['implied_sum']:.4f}")
                    c3.metric("Market", arb["market"])

                    stakes_df = calc_stakes(arb["best_odds"], total_stake, betfair_commission)
                    st.dataframe(stakes_df, use_container_width=True, hide_index=True)

                    guaranteed = stakes_df["Profit ($)"].iloc[0]
                    st.success(
                        f"Stake **${total_stake:,.2f}** total → guaranteed profit "
                        f"**${guaranteed:,.2f}** regardless of outcome"
                    )
    else:
        st.info(
            f"No arbs ≥ {min_profit}% found. Try: lowering the min profit filter, "
            "adding more sports, or enabling Betfair Exchange."
        )
