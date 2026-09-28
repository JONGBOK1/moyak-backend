from src.api.routes.vending import _qr_svg


def test_qr_svg_returns_valid_svg_markup():
    svg = _qr_svg({"machine_id": "M1", "qr_token": "abc"})
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_qr_svg_differs_for_different_payloads():
    svg1 = _qr_svg({"machine_id": "M1", "qr_token": "abc"})
    svg2 = _qr_svg({"machine_id": "M1", "qr_token": "xyz"})
    assert svg1 != svg2
