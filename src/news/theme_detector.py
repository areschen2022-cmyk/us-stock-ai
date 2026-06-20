"""Theme detection — map news and symbols to theme pools."""
from __future__ import annotations

from typing import Any

THEME_KEYWORDS: dict[str, list[str]] = {
    "ai_infra": ["nvidia", "gpu", "ai chip", "data center", "inference", "llm", "foundation model", "artificial intelligence"],
    "cloud_saas": ["cloud", "saas", "azure", "aws", "gcp", "software-as-a-service", "subscription revenue"],
    "cybersecurity": ["cybersecurity", "ransomware", "firewall", "endpoint", "zero trust", "soc"],
    "defense": ["defense contract", "pentagon", "military", "drone", "f-35", "munition", "dod"],
    "energy_power": ["nuclear", "power grid", "electricity demand", "data center power", "lng", "natural gas"],
    "crypto_fintech": ["bitcoin", "crypto", "blockchain", "coinbase", "stablecoin", "ethereum"],
    "glp1_biotech": ["glp-1", "obesity", "wegovy", "ozempic", "weight loss drug", "semaglutide", "tirzepatide"],
    "emerging_market": ["latam", "brazil", "mercadolibre", "sea limited", "nubank", "emerging market"],
    "semiconductor": ["semiconductor", "wafer", "tsmc", "asml", "chip shortage", "fab", "foundry"],
}

SYMBOL_THEMES: dict[str, list[str]] = {
    "NVDA": ["ai_infra", "semiconductor"],
    "AMD": ["ai_infra", "semiconductor"],
    "AVGO": ["ai_infra", "semiconductor"],
    "ASML": ["semiconductor"],
    "TSM": ["semiconductor", "ai_infra"],
    "ARM": ["ai_infra", "semiconductor"],
    "CRDO": ["ai_infra", "semiconductor"],
    "ANET": ["ai_infra", "cloud_saas"],
    "SMCI": ["ai_infra"],
    "MSFT": ["cloud_saas", "ai_infra"],
    "GOOGL": ["cloud_saas", "ai_infra"],
    "META": ["ai_infra", "cloud_saas"],
    "AAPL": ["ai_infra"],
    "TSLA": ["ai_infra", "energy_power"],
    "AMZN": ["cloud_saas"],
    "SNOW": ["cloud_saas"],
    "NET": ["cloud_saas", "cybersecurity"],
    "DDOG": ["cloud_saas"],
    "MDB": ["cloud_saas"],
    "CRM": ["cloud_saas"],
    "CRWD": ["cybersecurity"],
    "PANW": ["cybersecurity"],
    "ZS": ["cybersecurity"],
    "AXON": ["defense", "cybersecurity"],
    "LMT": ["defense"],
    "RTX": ["defense"],
    "NOC": ["defense"],
    "GEV": ["energy_power", "defense"],
    "VST": ["energy_power"],
    "CEG": ["energy_power"],
    "NRG": ["energy_power"],
    "COIN": ["crypto_fintech"],
    "MSTR": ["crypto_fintech"],
    "LLY": ["glp1_biotech"],
    "NVO": ["glp1_biotech"],
    "ABBV": ["glp1_biotech"],
    "GILD": ["glp1_biotech"],
    "MRNA": ["glp1_biotech"],
    "MELI": ["emerging_market", "crypto_fintech"],
    "NU": ["emerging_market", "crypto_fintech"],
    "SE": ["emerging_market"],
    "PLTR": ["ai_infra", "defense"],
    "APP": ["ai_infra"],
}


def get_symbol_themes(symbol: str) -> list[str]:
    return SYMBOL_THEMES.get(symbol.upper(), [])


def detect_themes_from_headlines(headlines: list[str]) -> list[str]:
    """Return list of active themes from headline text."""
    active: set[str] = set()
    combined = " ".join(headlines).lower()
    for theme, keywords in THEME_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            active.add(theme)
    return sorted(active)


def theme_catalyst_score(symbol: str, matched_headlines: list[str]) -> int:
    """Bonus score if symbol's themes match hot headlines."""
    sym_themes = set(get_symbol_themes(symbol))
    headline_themes = set(detect_themes_from_headlines(matched_headlines))
    overlap = sym_themes & headline_themes
    return min(len(overlap) * 3, 9)
