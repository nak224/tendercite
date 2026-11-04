"""Opt-in official daily Open Data read; never part of ordinary offline CI."""

import os
from datetime import date

import pytest

from tendercite.domain.bkms import LoadInput
from tendercite.services.bkms import BkmsClient


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("TENDERCITE_LIVE_BKMS_DISCOVERY") != "1",
    reason="Explicit official Open Data opt-in required",
)
def test_official_completed_day():
    day = date.fromisoformat(os.environ["TENDERCITE_LIVE_BKMS_DAY"])
    with BkmsClient() as client:
        catalog = client.load(LoadInput(export_day=day))
    assert catalog.export_day == day
    assert all(
        r.export_day == day and r.source == "BEKANNTMACHUNGSSERVICE" for r in catalog.records
    )
