"""
Greeks (central difference), scenario analysis, P&L decomposition,
and beta-weighted portfolio aggregation.

Central difference:
  f'(x)  ≈ [f(x+h) - f(x-h)] / 2h          (first derivative — delta, vega)
  f''(x) ≈ [f(x+h) - 2f(x) + f(x-h)] / h²  (second derivative — gamma)

Theta uses a forward difference because time only moves forward:
  theta ≈ f(T - 1day) - f(T)

P&L decomposition (Taylor expansion through second order):
  ΔV ≈ delta·ΔS + ½·gamma·ΔS² + theta·Δt + vega·Δσ

Beta-weighted Greeks (portfolio level):
  BW delta = Σ delta_i · S_i · beta_i / SPY_price · multiplier_i
  BW gamma = Σ gamma_i · S_i² · beta_i² / SPY_price² · multiplier_i
  Theta and vega are summed as raw dollar values — beta-weighting
  them has no conceptual meaning.
"""

from situational.pricing import gbs_price

_MULTIPLIER = 100  # shares per contract


# ─── Per-position Greeks ───────────────────────────────────────────────────────

def calculate_greeks(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    contracts: int = 1,
) -> dict:
    """
    Central difference Greeks for a single option position.

    Dollar values (theta_per_day, vega_per_pct) are for the total position
    (contracts × 100 multiplier), not per share.

    Returns:
        delta:          ∂V/∂S  per share (0–1 for calls, -1–0 for puts)
        gamma:          ∂²V/∂S²  per share
        theta_per_day:  $ P&L change per calendar day (total position, negative = decay)
        vega_per_pct:   $ P&L change per +1% absolute IV increase (total position)
        option_price:   theoretical price per share
        position_value: total position value (contracts × 100 × price)
    """
    mult = contracts * _MULTIPLIER

    # Perturbation sizes
    dS     = max(S * 0.01, 0.01)   # 1% of spot, floored at $0.01
    dSigma = 0.001                  # 0.1 vol point (central)
    dT     = 1 / 365                # 1 calendar day (forward only)

    base   = gbs_price(option_type, S,      K, T,        r, q, sigma)
    up_s   = gbs_price(option_type, S + dS, K, T,        r, q, sigma)
    dn_s   = gbs_price(option_type, S - dS, K, T,        r, q, sigma)
    up_v   = gbs_price(option_type, S,      K, T,        r, q, sigma + dSigma)
    dn_v   = gbs_price(option_type, S,      K, T,        r, q, sigma - dSigma)
    fwd_t  = gbs_price(option_type, S,      K, max(T - dT, 1e-8), r, q, sigma)

    delta = (up_s - dn_s) / (2 * dS)
    gamma = (up_s - 2 * base + dn_s) / (dS ** 2)

    # Theta: actual $ decay per day for the whole position
    theta_per_day = (fwd_t - base) * mult

    # Vega: $ change per 1% (0.01) IV move for the whole position
    vega_per_share = (up_v - dn_v) / (2 * dSigma)
    vega_per_pct   = vega_per_share * mult * 0.01

    return {
        "delta":          round(delta, 4),
        "gamma":          round(gamma, 6),
        "theta_per_day":  round(theta_per_day, 2),
        "vega_per_pct":   round(vega_per_pct, 2),
        "option_price":   round(base, 4),
        "position_value": round(base * mult, 2),
    }


# ─── P&L decomposition ────────────────────────────────────────────────────────

def pnl_decomposition(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    contracts: int,
    entry_price: float,
    price_move: float = 0.0,
    iv_change_abs: float = 0.0,
    days_elapsed: int = 0,
) -> dict:
    """
    Breaks P&L into per-Greek contributions for a given scenario.

    Inputs describe what *changed* relative to the position entry:
        price_move:    $ change in underlying (e.g. +1.50 or -3.00)
        iv_change_abs: absolute IV change in decimal (e.g. -0.10 means IV fell 10 vol pts)
        days_elapsed:  calendar days since the position was opened

    Returns dollar amounts for the total position (contracts × 100).
    Total (approx) = delta + gamma + theta + vega terms.
    Total (exact)  = full GBS reprice — shows residual higher-order terms.
    """
    mult   = contracts * _MULTIPLIER
    greeks = calculate_greeks(option_type, S, K, T, r, q, sigma, contracts)

    T_new     = max(T - days_elapsed / 365, 1e-8)
    sigma_new = max(sigma + iv_change_abs, 0.001)
    S_new     = S + price_move

    delta_pnl = greeks["delta"] * price_move * mult
    gamma_pnl = 0.5 * greeks["gamma"] * (price_move ** 2) * mult
    theta_pnl = greeks["theta_per_day"] * days_elapsed
    # vega_per_pct is $/1% move; iv_change_abs is in decimal so multiply by 100
    vega_pnl  = greeks["vega_per_pct"] * (iv_change_abs * 100)

    total_approx = delta_pnl + gamma_pnl + theta_pnl + vega_pnl
    exact_price  = gbs_price(option_type, S_new, K, T_new, r, q, sigma_new)
    total_exact  = (exact_price - entry_price) * mult

    return {
        "inputs": {
            "price_move":    round(price_move, 4),
            "iv_change_pct": round(iv_change_abs * 100, 2),
            "days_elapsed":  days_elapsed,
        },
        "breakdown": {
            "delta":    round(delta_pnl, 2),
            "gamma":    round(gamma_pnl, 2),
            "theta":    round(theta_pnl, 2),
            "vega":     round(vega_pnl, 2),
        },
        "total_approx": round(total_approx, 2),
        "total_exact":  round(total_exact, 2),
        "residual":     round(total_exact - total_approx, 2),
    }


# ─── Scenario grid ─────────────────────────────────────────────────────────────

def run_scenario_analysis(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    contracts: int,
    entry_price: float,
    days_forward: int = 0,
) -> dict:
    """
    Full scenario analysis: price-move × IV-regime grid plus P&L decomposition
    at three representative price moves.

    Scenario grid axes:
        Price moves:  -15%, -10%, -5%, 0%, +5%, +10%, +15%
        IV regimes:   crush (-30% relative), unchanged, expansion (+30% relative)

    Decomposition scenarios (at days_forward, IV unchanged):
        -5% move, flat, +5% move
    """
    mult  = contracts * _MULTIPLIER
    T_fwd = max(T - days_forward / 365, 1e-8)

    greeks = calculate_greeks(option_type, S, K, T, r, q, sigma, contracts)

    # Scenario grid ─────────────────────────────────────────────────
    price_moves  = [-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15]
    iv_regimes   = {
        "iv_crush":     sigma * 0.70,   # -30% relative (post-earnings typical)
        "iv_unchanged": sigma,
        "iv_expansion": sigma * 1.30,   # +30% relative
    }

    grid = []
    for dm in price_moves:
        new_S = S * (1 + dm)
        row = {"price_move_pct": int(dm * 100)}
        for regime_label, new_sigma in iv_regimes.items():
            new_price = gbs_price(option_type, new_S, K, T_fwd, r, q, new_sigma)
            row[regime_label] = round((new_price - entry_price) * mult, 2)
        grid.append(row)

    # P&L decomposition at three moves ──────────────────────────────
    decomp = {}
    for dm in [-0.05, 0.0, 0.05]:
        dS       = S * dm
        d_pnl    = greeks["delta"] * dS * mult
        g_pnl    = 0.5 * greeks["gamma"] * (dS ** 2) * mult
        th_pnl   = greeks["theta_per_day"] * days_forward
        v_pnl    = 0.0   # IV held constant in decomposition scenarios
        approx   = d_pnl + g_pnl + th_pnl
        exact_p  = gbs_price(option_type, S * (1 + dm), K, T_fwd, r, q, sigma)
        exact    = (exact_p - entry_price) * mult
        label    = f"{'plus' if dm > 0 else 'minus' if dm < 0 else 'flat'}_{abs(int(dm*100))}pct"
        decomp[label] = {
            "delta":        round(d_pnl, 2),
            "gamma":        round(g_pnl, 2),
            "theta":        round(th_pnl, 2),
            "vega":         0.00,
            "total_approx": round(approx, 2),
            "total_exact":  round(exact, 2),
        }

    return {
        "greeks":           greeks,
        "scenario_grid":    grid,
        "pnl_decomposition": decomp,
        "days_forward":     days_forward,
    }


# ─── Portfolio aggregation ────────────────────────────────────────────────────

def aggregate_portfolio_greeks(positions: list, spy_price: float) -> dict:
    """
    Beta-weighted portfolio Greeks across multiple option positions.

    Beta-weighted delta and gamma normalise each position to SPY-equivalent
    units so they can be meaningfully summed across different underlyings.

    Theta and vega are raw dollar sums — weighting them by beta has no
    conceptual value (they're already in $/day and $/vol-pt respectively).

    Each position dict must contain:
        option_type, S, K, T, r, q, sigma, contracts, beta, ticker
    """
    total_bw_delta = 0.0
    total_bw_gamma = 0.0
    total_theta    = 0.0
    total_vega     = 0.0
    details        = []

    for pos in positions:
        beta = pos.get("beta", 1.0)

        if pos.get("position_type") == "equity":
            # Stocks and ETFs: delta=1/share, no gamma/theta/vega
            shares   = pos["shares"]
            bw_delta = shares * pos["S"] * beta / spy_price
            bw_gamma = 0.0
            details.append({
                "ticker":        pos.get("ticker", "?"),
                "position_type": "equity",
                "shares":        shares,
                "delta":         1.0,
                "gamma":         0.0,
                "theta_per_day": 0.0,
                "vega_per_pct":  0.0,
                "bw_delta":      round(bw_delta, 4),
                "bw_gamma":      0.0,
            })
        else:
            # Options: full Greeks via GBS central difference
            g    = calculate_greeks(
                pos["option_type"], pos["S"], pos["K"], pos["T"],
                pos["r"],           pos["q"], pos["sigma"], pos["contracts"],
            )
            mult     = pos["contracts"] * _MULTIPLIER
            bw_delta = g["delta"] * pos["S"] * beta / spy_price * mult
            bw_gamma = g["gamma"] * (pos["S"] ** 2) * (beta ** 2) / (spy_price ** 2) * mult

            total_theta += g["theta_per_day"]
            total_vega  += g["vega_per_pct"]
            details.append({
                "ticker":        pos.get("ticker", "?"),
                "position_type": "option",
                "option_type":   pos["option_type"],
                "strike":        pos["K"],
                "contracts":     pos["contracts"],
                "delta":         g["delta"],
                "gamma":         g["gamma"],
                "theta_per_day": g["theta_per_day"],
                "vega_per_pct":  g["vega_per_pct"],
                "bw_delta":      round(bw_delta, 4),
                "bw_gamma":      round(bw_gamma, 6),
            })

        total_bw_delta += bw_delta
        total_bw_gamma += bw_gamma

    return {
        "spy_price_used": round(spy_price, 2),
        "summary": {
            "beta_weighted_delta": round(total_bw_delta, 4),
            "beta_weighted_gamma": round(total_bw_gamma, 6),
            "total_theta_per_day": round(total_theta, 2),
            "total_vega_per_pct":  round(total_vega, 2),
        },
        "positions": details,
    }


# ─── Hypothetical addition ─────────────────────────────────────────────────────

def calculate_hypothetical_impact(
    existing_positions: list,
    new_position: dict,
    spy_price: float,
) -> dict:
    """
    Shows how adding a hypothetical option position shifts portfolio-level Greeks.

    Computes Greeks for:
      - existing portfolio (before)
      - new position in isolation
      - combined portfolio (after = before + new)
      - delta between before and after

    All three states use the same spy_price for consistent beta-weighting.

    new_position must contain the same fields as positions in aggregate_portfolio_greeks:
        option_type, S, K, T, r, q, sigma, contracts, beta, ticker
    """
    before = aggregate_portfolio_greeks(existing_positions, spy_price)
    after  = aggregate_portfolio_greeks(existing_positions + [new_position], spy_price)

    # New position in isolation for per-Greek attribution
    isolated = aggregate_portfolio_greeks([new_position], spy_price)
    new_greeks = isolated["positions"][0] if isolated["positions"] else {}

    def _diff(after_summary: dict, before_summary: dict) -> dict:
        return {k: round(after_summary[k] - before_summary[k], 6)
                for k in after_summary}

    return {
        "spy_price_used": round(spy_price, 2),
        "new_position": {
            "ticker":      new_position.get("ticker", "?"),
            "option_type": new_position["option_type"],
            "strike":      new_position["K"],
            "expiry":      new_position.get("expiry", ""),
            "contracts":   new_position["contracts"],
            "greeks": {
                "delta":          new_greeks.get("delta"),
                "gamma":          new_greeks.get("gamma"),
                "theta_per_day":  new_greeks.get("theta_per_day"),
                "vega_per_pct":   new_greeks.get("vega_per_pct"),
                "bw_delta":       new_greeks.get("bw_delta"),
                "bw_gamma":       new_greeks.get("bw_gamma"),
            },
        },
        "portfolio": {
            "before": before["summary"],
            "after":  after["summary"],
            "change": _diff(after["summary"], before["summary"]),
        },
    }
