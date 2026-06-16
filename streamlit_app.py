import streamlit as st
import requests
import pandas as pd
from itertools import combinations
from datetime import datetime

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AU Arb Finder",
    page_icon="💰",
    layout="wide",
)

# ── Constants ──────────────────────────────────────────────────────────────────
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

AU_BOOKMAKERS = [
    "sportsbet", "ladbrokes", "neds", "pointsbet", "tab",
    "unibet", "bluebet", "betr", "betright", "draftstars",
]

SPORTS_OPTIONS = {
    "AFL": "aussierules_afl",
    "NRL": "rugbyleague_nrl",
    "Soccer - A-League": "soccer_australia_aleague",
    "Cricket - BBL": "cricket_big_bash",
    "Tennis - ATP": "tennis_atp_french_open",
    "Basketball - NBL": "basketball_nbl",
    "Soccer - EPL": "soccer_epl",
    "Soccer - Champions League": "soccer_uefa_champs_league",
    "American Football - NFL": "americanfootball_nfl",
    "MMA - UFC": "mma_mixed_martial_arts",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch_sports(api_key: str) -> list[dict]:
    r = requests.get(
        f"{ODDS_API_BASE}/sports",
        params={"apiKey": api_key},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def fetch_odds(api_key: str, sport: str, bookmakers: list[str]) -> list[dict]:
    r = requests.get(
        f"{ODDS_API_BASE}/sports/{sport}/odds",
        params={
            "apiKey": api_key,
            "regions": "au",
            "markets": "h2h",
            "oddsFormat": "decimal",
            "bookmakers": ",".join(bookmakers),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def find_arbs(events: list[dict]) -> list[dict]:
    """Find two-outcome (H2H) arbitrage opportunities across bookmakers."""
    arbs = []
    for event in events:
        outcomes_by_book: dict[str, dict[str, float]] = {}
        for bookie in event.get("bookmakers", []):
            for market in bookie.get("markets", []):
                if market["key"] != "h2h":
                    continue
                outcomes_by_book[bookie["title"]] = {
                    o["name"]: o["price"] for o in market["outcomes"]
                }

        if len(outcomes_by_book) < 2:
            continue

        # Collect all outcome names (handles 2- and 3-way markets)
        all_outcomes = set()
        for odds_map in outcomes_by_book.values():
            all_outcomes.update(odds_map.keys())

        # For each outcome find the best (highest) odds across all books
        best: dict[str, tuple[float, str]] = {}  # outcome -> (odds, bookie)
        for outcome in all_outcomes:
            for bookie, odds_map in outcomes_by_book.items():
                if outcome in odds_map:
                    if outcome not in best or odds_map[outcome] > best[outcome][0]:
                        best[outcome] = (odds_map[outcome], bookie)

        if len(best) < 2:
            continue

        implied_sum = sum(1 / odds for odds, _ in best.values())

        if implied_sum < 1.0:
            profit_pct = (1 / implied_sum - 1) * 100
            arbs.append({
                "event": event["home_team"] + " vs " + event["away_team"],
                "sport": event.get("sport_title", ""),
                "commence": event.get("commence_time", ""),
                "profit_pct": round(profit_pct, 2),
                "implied_sum": round(implied_sum, 4),
                "best_odds": best,
                "n_books": len(outcomes_by_book),
            })

    return sorted(arbs, key=lambda x: x["profit_pct"], reverse=True)


def calc_stakes(best_odds: dict[str, tuple[float, str]], total_stake: float) -> pd.DataFrame:
    implied_sum = sum(1 / o for o, _ in best_odds.values())
    rows = []
    for outcome, (odds, bookie) in best_odds.items():
        stake = total_stake * (1 / odds) / implied_sum
        profit = stake * odds - total_stake
        rows.append({
            "Outcome": outcome,
            "Bookmaker": bookie,
            "Odds": odds,
            "Stake ($)": round(stake, 2),
            "Return ($)": round(stake * odds, 2),
            "Profit ($)": round(profit, 2),
        })
    return pd.DataFrame(rows)


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    api_key = st.text_input(
        "The Odds API Key",
        type="password",
        help="Get a free key at https://the-odds-api.com",
    )

    st.markdown("---")
    st.subheader("Sports")
    selected_sports = st.multiselect(
        "Select sports to scan",
        options=list(SPORTS_OPTIONS.keys()),
        default=["AFL", "NRL"],
    )

    st.subheader("Bookmakers")
    selected_books = st.multiselect(
        "Australian bookmakers",
        options=AU_BOOKMAKERS,
        default=AU_BOOKMAKERS,
        help="Select which bookmakers to include",
    )

    st.markdown("---")
    min_profit = st.slider("Min profit % to show", 0.0, 5.0, 0.5, 0.1)
    total_stake = st.number_input("Total stake per arb ($)", min_value=10, max_value=100000, value=1000, step=50)

    scan_btn = st.button("🔍 Scan for Arbs", type="primary", use_container_width=True)

    st.markdown("---")
    st.caption("Data via [The Odds API](https://the-odds-api.com). AU bookmakers only.")


# ── Main ───────────────────────────────────────────────────────────────────────
st.title("💰 Australian Sportsbook Arbitrage Finder")
st.caption("Scans multiple Australian bookmakers and flags bets where combined implied probability < 100%.")

if not api_key:
    st.info("Enter your **The Odds API** key in the sidebar to get started. Free tier gives 500 requests/month.")
    st.markdown(
        """
        ### How it works
        1. Enter your API key (free at [the-odds-api.com](https://the-odds-api.com))
        2. Choose sports and bookmakers to scan
        3. Click **Scan for Arbs**
        4. Expand any opportunity to see exact stake sizes

        ### What is arbitrage?
        An arbitrage (arb) exists when the **sum of implied probabilities** across the best available odds for every outcome **is less than 100%**.
        By backing each outcome at its best available odds, you guarantee a profit regardless of the result.

        > **Example:** Outcome A @ 2.10, Outcome B @ 2.10
        > Implied sum = 1/2.10 + 1/2.10 = 0.952 → **4.8% guaranteed profit**
        """
    )
    st.stop()

if not selected_sports:
    st.warning("Select at least one sport in the sidebar.")
    st.stop()

if not selected_books:
    st.warning("Select at least one bookmaker in the sidebar.")
    st.stop()

# ── Scan ───────────────────────────────────────────────────────────────────────
if scan_btn:
    all_arbs: list[dict] = []
    errors: list[str] = []
    requests_used = 0

    progress = st.progress(0, text="Scanning...")
    for i, sport_name in enumerate(selected_sports):
        sport_key = SPORTS_OPTIONS[sport_name]
        progress.progress((i + 1) / len(selected_sports), text=f"Fetching {sport_name}…")
        try:
            events = fetch_odds(api_key, sport_key, selected_books)
            arbs = find_arbs(events)
            all_arbs.extend(arbs)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                st.error("Invalid API key. Check the key in the sidebar.")
                st.stop()
            elif e.response is not None and e.response.status_code == 422:
                errors.append(f"{sport_name}: sport not currently available")
            else:
                errors.append(f"{sport_name}: {e}")
        except Exception as e:
            errors.append(f"{sport_name}: {e}")

    progress.empty()
    st.session_state["arbs"] = all_arbs
    st.session_state["scan_time"] = datetime.now().strftime("%H:%M:%S")

    if errors:
        with st.expander("⚠️ Some sports had errors", expanded=False):
            for err in errors:
                st.warning(err)

# ── Results ────────────────────────────────────────────────────────────────────
arbs: list[dict] = st.session_state.get("arbs", [])
scan_time: str = st.session_state.get("scan_time", "")

if arbs or scan_time:
    filtered = [a for a in arbs if a["profit_pct"] >= min_profit]

    col1, col2, col3 = st.columns(3)
    col1.metric("Arbs Found", len(filtered))
    col2.metric(
        "Best Profit",
        f"{filtered[0]['profit_pct']:.2f}%" if filtered else "—",
    )
    col3.metric("Last Scan", scan_time or "—")

    if not filtered:
        st.info(f"No arbitrage opportunities ≥ {min_profit}% found. Try lowering the minimum profit threshold or scanning more sports/bookmakers.")
    else:
        st.markdown("---")
        st.subheader(f"Opportunities ({len(filtered)})")

        for arb in filtered:
            profit_color = "green" if arb["profit_pct"] >= 2 else "orange"
            label = (
                f"**{arb['event']}** — :{profit_color}[+{arb['profit_pct']:.2f}%] "
                f"| {arb['sport']} | {arb['n_books']} bookmakers"
            )
            with st.expander(label):
                # Commence time
                try:
                    dt = datetime.fromisoformat(arb["commence"].replace("Z", "+00:00"))
                    st.caption(f"Starts: {dt.strftime('%a %d %b %Y %H:%M UTC')}")
                except Exception:
                    pass

                col_a, col_b = st.columns(2)
                col_a.metric("Guaranteed Profit %", f"{arb['profit_pct']:.3f}%")
                col_b.metric("Implied Sum", f"{arb['implied_sum']:.4f}")

                stakes_df = calc_stakes(arb["best_odds"], total_stake)
                st.dataframe(stakes_df, use_container_width=True, hide_index=True)

                guaranteed = stakes_df["Profit ($)"].iloc[0]
                st.success(
                    f"Stake **${total_stake:,.2f}** total → guaranteed profit **${guaranteed:,.2f}** "
                    f"(regardless of outcome)"
                )

elif scan_time == "":
    pass  # initial state — instructions already shown above if no api key
