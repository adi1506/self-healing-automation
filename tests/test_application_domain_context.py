from core.applications import Application, save_application, load_application


def test_domain_context_defaults_none(tmp_path):
    app = Application(id="a1", name="mCAS", base_url_pattern="https://m/")
    assert app.domain_context is None


def test_domain_context_persists(tmp_path):
    app = Application(id="a1", name="mCAS", base_url_pattern="https://m/",
                      domain_context="Indian retail-banking KYC")
    save_application(str(tmp_path), app)
    loaded = load_application(str(tmp_path), "a1")
    assert loaded.domain_context == "Indian retail-banking KYC"


def test_loads_legacy_yaml_without_field(tmp_path):
    (tmp_path / "a1.yaml").write_text(
        "id: a1\nname: mCAS\nbase_url_pattern: https://m/\n", encoding="utf-8")
    loaded = load_application(str(tmp_path), "a1")
    assert loaded.domain_context is None
