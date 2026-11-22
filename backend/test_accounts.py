"""Account-save regressions for portfolios that predate the old edit limit."""

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from main import SaveAccountsRequest, SubscribeRequest


def test_account_edit_preserves_large_portfolios_and_deduplicates():
    companies = [f"Company {index}" for index in range(28)]
    request = SaveAccountsRequest(
        email="person@solace.com", token="session", companies=companies + [" company   0 "]
    )
    assert request.companies == companies


def test_signup_reports_the_limit_instead_of_silently_dropping_accounts():
    with pytest.raises(ValidationError, match="up to 10 companies"):
        SubscribeRequest(
            email="person@solace.com", product="", companies=[f"Company {i}" for i in range(11)]
        )
