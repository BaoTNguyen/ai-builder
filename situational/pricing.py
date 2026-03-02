"""
Generalized Black-Scholes (Merton continuous-dividend model).

GBS extends Black-Scholes by replacing the risk-free rate with a cost-of-carry
term b = r - q, where q is the continuous dividend yield.  This correctly
handles dividend-paying equities without requiring discrete dividend adjustments.

For non-dividend stocks q = 0, so b = r and GBS collapses to plain Black-Scholes.
"""

import math
from scipy.stats import norm


def gbs_price(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
) -> float:
    """
    Theoretical price via Generalized Black-Scholes (Merton model).

    Args:
        option_type: 'call' or 'put'
        S:     current underlying price
        K:     strike price
        T:     time to expiry in years (e.g. 30/365)
        r:     risk-free rate, annualised decimal (e.g. 0.043)
        q:     continuous dividend yield, annualised decimal (e.g. 0.015)
        sigma: implied volatility, annualised decimal (e.g. 0.45)

    Returns:
        Theoretical option price per share.
    """
    if T <= 0:
        intrinsic = (S - K) if option_type == "call" else (K - S)
        return max(0.0, intrinsic)

    if sigma <= 0:
        intrinsic = (S - K) if option_type == "call" else (K - S)
        return max(0.0, intrinsic)

    b = r - q  # cost of carry

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (b + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if option_type == "call":
        return (
            S * math.exp((b - r) * T) * norm.cdf(d1)
            - K * math.exp(-r * T) * norm.cdf(d2)
        )
    else:
        return (
            K * math.exp(-r * T) * norm.cdf(-d2)
            - S * math.exp((b - r) * T) * norm.cdf(-d1)
        )
