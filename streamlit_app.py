import streamlit as st
import requests
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="AU Arb Finder", page_icon="💰", layout="wide")

# ── Constants ──────────────────────────────────────────────────────────────────
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

AU_BOOKMAKERS = {
    # Major AU corporate bookmakers
    "SportsBet":        "sportsbet",
    "Ladbrokes AU":     "ladbrokes",
    "Neds":             "neds",
    "PointsBet AU":     "pointsbet",
    "TAB":              "tab",
    "Unibet AU":        "unibet",
    "BlueBet":          "bluebet",
    "Betr":             "betr",
    "BetRight":         "betright",
    "Draftstars":       "draftstars",
    # Additional AU bookmakers
    "Bet365 AU":        "bet365",
    "Palmerbet":        "palmerbet",
    "TopSport":         "topsport",
    "BoomBet":          "boombet",
    "Bookmaker.com.au": "bookmaker",
    "Bonus Bet":        "bonusbet",
    "Elitebet":         "elitebet",
    "Tab NZ":           "tab_nz",
}

EXCHANGE_BOOKMAKERS = {
    "Betfair Exchange (Best Odds)":     "betfair_ex_best_odds",
    "Betfair Exchange (Averaged Odds)": "betfair_ex_averaged_odds",
}

SPORTS_OPTIONS = {
    # Australian
    "AFL":                          "aussierules_afl",
    "NRL":                          "rugbyleague_nrl",
    "Soccer - A-League":            "soccer_australia_aleague",
    "Cricket - BBL":                "cricket_big_bash",
    "Basketball - NBL":             "basketball_nbl",
    "Rugby Union - Super Rugby":    "rugbyunion_super_rugby",
    # International popular in AU
    "Soccer - EPL":                 "soccer_epl",
    "Soccer - Champions League":    "soccer_uefa_champs_league",
    "Soccer - Europa League":       "soccer_uefa_europa_conference_league",
    "Soccer - La Liga":             "soccer_spain_la_liga",
    "Soccer - Serie A":             "soccer_italy_serie_a",
    "Soccer - Bundesliga":          "soccer_germany_bundesliga",
    "Soccer - Ligue 1":             "soccer_france_ligue_one",
    "American Football - NFL":      "americanfootball_nfl",
    "American Football - NCAAF":    "americanfootball_ncaaf",
    "Basketball - NBA":             "basketball_nba",
    "Baseball - MLB":               "baseball_mlb",
    "Ice Hockey - NHL":             "icehockey_nhl",
    "Tennis - ATP":                 "tennis_atp_us_open",
    "Tennis - WTA":                 "tennis_wta_us_open",
    "MMA - UFC":                    "mma_mixed_martial_arts",
    "Boxing":                       "boxing_boxing",
    "Golf - PGA Tour":              "golf_pga_tour",
    "Golf - Masters":               "golf_masters_tournament_winner",
}

MARKET_OPTIONS = {
    "Head-to-Head (Win/Loss)":          "h2h",
    "Totals (Over/Under)":              "totals",
    "Spreads (Handicap)":               "spreads",
    "Alternate Totals":                 "alternate_totals",
    "Alternate Spreads":                "alternate_spreads",
    "Draw No Bet":                      "draw_no_bet",
    "Both Teams to Score":              "btts",
    "1st Half - H2H":                   "h2h_h1",
    "1st Half - Totals":                "totals_h1",
    "1st Half - Spreads":               "spreads_h1",
    "1st Quarter - H2H":                "h2h_q1",
    "1st Quarter - Totals":             "totals_q1",
}

MARKET_LABELS = {v: k for k, v in MARKET_OPTIONS.items()}

# ── API helpers ────────────────────────────────────────────────────────────────

def check_credits(api_key: str) -> dict:
    """Hit the sports endpoint and return credit info from headers."""
    r = requests.get(
        f"{ODDS_API_BASE}/sports",
        params={"apiKey": api_key, "all": "false"},
        timeout=10,
    )
    r.raise_for_status()
    return {
        "sports": r.json(),
        "used": int(r.headers.get("x-requests-used", -1)),
        "remaining": int(r.headers.get("x-requests-remaining", -1)),
    }


@st.cache_data(ttl=300, show_spinner=False)
def fetch_active_sports(api_key: str) -> list[dict]:
    """Return sports that currently have upcoming events."""
    return check_credits(api_key)["sports"]


def fetch_odds(api_key: str, sport: str, bookmakers: list[str], markets: list[str]) -> tuple[list[dict], dict]:
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
    credits = {
        "used": int(r.headers.get("x-requests-used", -1)),
        "remaining": int(r.headers.get("x-requests-remaining", -1)),
    }
    return r.json(), credits


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
    For h2h-style markets: one group of all outcomes.
    For totals-style: pair Over X with Under X at the same line.
    For spreads-style: pair Team A -X with Team B +X.
    """
    is_totals = any(market_key.startswith(p) for p in ("totals", "alternate_totals"))
    is_spreads = any(market_key.startswith(p) for p in ("spreads", "alternate_spreads"))
    is_h2h = not is_totals and not is_spreads  # h2h, draw_no_bet, btts, h2h_h1, h2h_q1, etc.

    if is_h2h:
        return [list(best.keys())]

    if is_totals:
        lines: dict[str, list[str]] = {}
        for okey in best:
            parts = okey.rsplit(" ", 1)
            if len(parts) == 2:
                lines.setdefault(parts[1], []).append(okey)
        return [pair for pair in lines.values() if len(pair) == 2]

    if is_spreads:
        lines: dict[str, list[str]] = {}
        for okey in best:
            parts = okey.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    lines.setdefault(str(abs(float(parts[1]))), []).append(okey)
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

    # ── Credits check ──────────────────────────────────────────────────────────
    if api_key:
        try:
            _credit_info = check_credits(api_key)
            _used = _credit_info["used"]
            _remaining = _credit_info["remaining"]
            _active_raw = _credit_info["sports"]
            if _remaining >= 0:
                credit_color = "red" if _remaining < 20 else "orange" if _remaining < 100 else "green"
                st.markdown(
                    f"**API Credits:** :{credit_color}[{_remaining} remaining] / {_used + _remaining} total"
                )
                if _remaining == 0:
                    st.error("No API credits remaining. Scanning will fail until your quota resets.")
            live_sports = {s["title"]: s["key"] for s in sorted(_active_raw, key=lambda s: s["title"])}
            sport_help = f"{len(live_sports)} sports with active events right now"
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                st.error("Invalid API key.")
            else:
                st.warning(f"Could not reach API: {e}")
            live_sports = SPORTS_OPTIONS
            sport_help = "API error — showing default sports list"
        except Exception as e:
            live_sports = SPORTS_OPTIONS
            sport_help = f"Could not load live sports: {e}"
    else:
        live_sports = SPORTS_OPTIONS
        sport_help = "Enter API key to load live sports"

    st.subheader("Sports")

    selected_sport_names = st.multiselect(
        "Sports to scan",
        list(live_sports.keys()),
        default=list(live_sports.keys())[:5],
        help=sport_help,
    )
    selected_sports = {name: live_sports[name] for name in selected_sport_names}

    st.subheader("Markets")
    selected_markets = st.multiselect(
        "Market types",
        list(MARKET_OPTIONS.keys()),
        default=["Head-to-Head (Win/Loss)", "Totals (Over/Under)", "Spreads (Handicap)"],
        help="More markets = more API requests used. Alternate lines & halves often have bigger gaps.",
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
        selected_book_names = st.multiselect(
            "Select AU bookmakers",
            list(AU_BOOKMAKERS.keys()),
            default=list(AU_BOOKMAKERS.keys()),
        )
        selected_books = [AU_BOOKMAKERS[n] for n in selected_book_names]
    else:
        selected_books = []

    all_books = selected_books + (list(EXCHANGE_BOOKMAKERS.values()) if use_betfair else [])

    st.markdown("---")
    min_profit = st.slider("Min profit % to show", 0.0, 5.0, 0.1, 0.05)
    total_stake = st.number_input("Total stake per arb ($)", 10, 100000, 1000, 50)

    scan_btn = st.button("🔍 Scan for Arbs", type="primary", use_container_width=True)
    st.caption("Data via [The Odds API](https://the-odds-api.com)")


# ── Main ───────────────────────────────────────────────────────────────────────
st.title("💰 Australian Sportsbook Arbitrage Finder")
st.caption("18 AU bookmakers · Betfair Exchange · 25+ sports · H2H, Totals, Spreads, Alternate lines, Halves & Quarters")

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
    | **Totals** | Over/Under a point total | Moderate |
    | **Spreads** | Handicap/line betting | Moderate |
    | **Alternate Totals/Spreads** | Non-standard lines | Higher — more pricing disagreement |
    | **Draw No Bet** | Soccer: exclude draw outcome | Moderate |
    | **Both Teams to Score** | Soccer BTTS | Moderate |
    | **1st Half / 1st Quarter** | Period betting | Higher — books price less carefully |

    ### Betfair Exchange
    Betfair lets punters set their own odds, often diverging from bookmaker prices.
    A 5% commission applies to net winnings — the app adjusts odds automatically.
    """)
    st.stop()

if not selected_sport_names or not selected_markets or not all_books:
    missing = []
    if not selected_sport_names: missing.append("sport")
    if not selected_markets: missing.append("market type")
    if not all_books: missing.append("bookmaker")
    st.warning(f"Select at least one {', '.join(missing)} in the sidebar.")
    st.stop()

# ── Scan status strip ──────────────────────────────────────────────────────────
with st.expander("Scan config (click to verify before scanning)", expanded=False):
    st.write(f"**Sports ({len(selected_sports)}):** {', '.join(selected_sports.keys()) or '—'}")
    st.write(f"**Markets ({len(selected_markets)}):** {', '.join(selected_markets) or '—'}")
    st.write(f"**Bookmakers ({len(all_books)}):** {', '.join(all_books) or '—'}")

# ── Scan ───────────────────────────────────────────────────────────────────────
if scan_btn:
    market_keys = [MARKET_OPTIONS[m] for m in selected_markets]
    all_arbs: list[dict] = []
    errors: list[str] = []

    if not selected_sports:
        st.error("No sports selected — pick at least one in the sidebar.")
        st.stop()

    progress = st.progress(0, text="Scanning…")
    status = st.empty()
    for i, (sport_name, sport_key) in enumerate(selected_sports.items()):
        pct = (i + 1) / len(selected_sports)
        progress.progress(pct, text=f"Fetching {sport_name}…")
        status.caption(f"Sport {i+1}/{len(selected_sports)}: {sport_name} ({sport_key})")
        try:
            events, credits = fetch_odds(api_key, sport_key, all_books, market_keys)
            st.session_state["last_credits"] = credits
            arbs = find_arbs(events, betfair_commission, min_profit=0)
            all_arbs.extend(arbs)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 401:
                st.error("Invalid API key — check the key in the sidebar.")
                st.stop()
            elif code == 422:
                errors.append(f"{sport_name}: no current events")
            else:
                errors.append(f"{sport_name}: HTTP {code} — {e}")
        except Exception as e:
            errors.append(f"{sport_name}: {type(e).__name__}: {e}")

    progress.empty()
    status.empty()
    st.session_state["arbs"] = all_arbs
    st.session_state["scan_time"] = datetime.now().strftime("%H:%M:%S")
    st.session_state["scan_errors"] = errors

    if errors:
        with st.expander(f"⚠️ {len(errors)} sport(s) had issues", expanded=True):
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
