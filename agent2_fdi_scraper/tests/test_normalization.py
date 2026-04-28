"""Sanity tests for the deterministic parts of Agent 2 / Agent 3."""
from datetime import date

from agent2_fdi_scraper.upsert import normalize_name
from agent3_people_discovery.base import normalize_role, role_seniority
from agent3_people_discovery.extractors.email_finder import candidate_locals


def test_normalize_name_strips_legal_suffixes():
    assert (
        normalize_name("Samsung SDI Vietnam Co., Ltd")
        == normalize_name("Samsung SDI Vietnam")
    )
    assert (
        normalize_name("Công ty TNHH Samsung Display Vietnam")
        == normalize_name("Samsung Display Vietnam")
    )


def test_normalize_name_strips_diacritics():
    assert "viet nam" in normalize_name("Việt Nam")
    assert "bac ninh" in normalize_name("Bắc Ninh")


def test_normalize_role_categories():
    assert normalize_role("CIO") == "cio"
    assert normalize_role("Chief Information Officer") == "cio"
    assert normalize_role("Head of IT") == "head_of_it"
    assert normalize_role("IT Director") == "it_director"
    assert normalize_role("Head of SAP") == "head_of_sap"
    assert normalize_role("ERP Manager") == "erp_manager"
    assert normalize_role("IT Manager") == "it_manager"
    assert normalize_role("Software Engineer") == "other"


def test_role_seniority():
    assert role_seniority("cio") == "c_level"
    assert role_seniority("head_of_it") == "director"
    assert role_seniority("it_manager") == "manager"


def test_email_candidates_for_vietnamese_name():
    locals_ = candidate_locals("Trần Văn An")
    # Should produce both Vietnamese (an.tran) and Western (tran.an) interpretations
    assert "an.tran" in locals_
    assert "tran.an" in locals_
    assert "atran" in locals_ or "tran" in locals_


def test_email_candidates_for_western_name():
    locals_ = candidate_locals("John Smith")
    assert "john.smith" in locals_
    assert "jsmith" in locals_
    assert "johnsmith" in locals_
