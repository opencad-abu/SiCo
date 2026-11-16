"""Display branding resolves from the site override or the product fallback."""

from cadgui.branding import logo_text


def test_unconfigured_logo_is_silicon_copilot():
    assert logo_text({}) == "Silicon Copilot"


def test_company_overrides_the_product_fallback():
    assert logo_text({"COMPANY": "Example Fab"}) == "Example Fab"


def test_logo_wins_over_company():
    assert logo_text({"LOGO": "Site Brand", "COMPANY": "Example Fab"}) == "Site Brand"


def test_empty_and_legacy_values_fall_through():
    assert logo_text({"LOGO": "", "COMPANY": "OCAD"}) == "Silicon Copilot"
