"""ICP scorer tests — pure function tests, no DB required."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional


@dataclass
class _FakeCompany:
    """Stand-in for the SQLAlchemy Company model."""

    legal_name: str = "Test Co"
    display_name: str = "Test Co"
    vn_province: Optional[str] = None
    hq_country: Optional[str] = None
    industry_label: Optional[str] = None
    investment_usd: Optional[float] = None
    first_licensed: Optional[date] = None
    tech_stack: dict[str, Any] = None
    target_priority: int = 0
    status: str = "active"

    def __post_init__(self):
        if self.tech_stack is None:
            self.tech_stack = {}


def test_default_icp_passes_recent_factory():
    from shared.icp import ICPConfig, score_company

    cfg = ICPConfig.default()
    co = _FakeCompany(
        vn_province="Bắc Ninh",
        hq_country="KR",
        industry_label="Electronics manufacturing",
        investment_usd=80_000_000,
        first_licensed=date.today() - timedelta(days=365),
    )
    score = score_company(co, cfg)
    # Should hit recency, investment tier, geography, industry, origin
    assert score >= 70, f"Expected hot lead, got {score}"


def test_old_factory_filtered_by_gate():
    from shared.icp import ICPConfig, score_company

    cfg = ICPConfig.default()
    co = _FakeCompany(
        vn_province="Bắc Ninh",
        first_licensed=date.today() - timedelta(days=365 * 10),  # 10 years old
    )
    assert score_company(co, cfg) == 0


def test_unknown_country_lowers_score():
    from shared.icp import ICPConfig, score_company

    cfg = ICPConfig.default()
    base = _FakeCompany(
        vn_province="Bắc Ninh",
        industry_label="Electronics manufacturing",
        investment_usd=80_000_000,
        first_licensed=date.today() - timedelta(days=365),
    )
    target = _FakeCompany(
        **{**base.__dict__, "hq_country": "KR"}
    )
    non_target = _FakeCompany(
        **{**base.__dict__, "hq_country": "ZW"}  # Zimbabwe, not in target list
    )
    assert score_company(target, cfg) > score_company(non_target, cfg)


def test_decisionmaker_bonus():
    from shared.icp import ICPConfig, score_company

    cfg = ICPConfig.default()
    co = _FakeCompany(
        vn_province="Bắc Ninh",
        hq_country="JP",
        first_licensed=date.today() - timedelta(days=365),
    )
    no_dm = score_company(co, cfg, has_decisionmaker=False)
    with_dm = score_company(co, cfg, has_decisionmaker=True)
    assert with_dm == no_dm + 10


def test_score_capped_at_100():
    from shared.icp import ICPConfig, score_company

    cfg = ICPConfig.default()
    co = _FakeCompany(
        vn_province="Bắc Ninh",
        hq_country="KR",
        industry_label="Semiconductor",
        investment_usd=500_000_000,
        first_licensed=date.today(),
        tech_stack={"sap_s4hana": True},
    )
    assert (
        score_company(co, cfg, has_decisionmaker=True, signal_score=999)
        <= 100
    )


def test_canonical_name_strips_legal_suffix():
    from shared.embeddings import canonical_name_for_embedding

    assert canonical_name_for_embedding("Samsung SDI Vietnam Co., Ltd") == "Samsung SDI Vietnam"
    assert canonical_name_for_embedding("Bosch Vietnam Limited") == "Bosch Vietnam"
    # With country
    assert canonical_name_for_embedding("Bosch Vietnam", country="DE") == "Bosch Vietnam (DE)"
