from core.recording import ElementFingerprint


def test_element_fingerprint_round_trip_dict():
    fp = ElementFingerprint(
        id="el-001",
        primary_locator={"strategy": "id", "value": "pan"},
        fallback_locators=[
            {"strategy": "name", "value": "pan"},
            {"strategy": "css", "value": "input[name='pan']"},
        ],
        attributes={
            "tag": "input",
            "type": "text",
            "id": "pan",
            "nearest_label_text": "PAN",
            "html5_constraints": {"required": True, "maxlength": 10},
        },
        page_context={"url": "https://example.com/kyc", "section_label": "KYC"},
    )
    d = fp.to_dict()
    fp2 = ElementFingerprint.from_dict(d)
    assert fp2 == fp


def test_element_fingerprint_minimum_required_fields():
    fp = ElementFingerprint(
        id="el-002",
        primary_locator={"strategy": "css", "value": "button.submit"},
        fallback_locators=[],
        attributes={"tag": "button"},
        page_context={"url": "https://example.com"},
    )
    assert fp.id == "el-002"
    assert fp.fallback_locators == []
