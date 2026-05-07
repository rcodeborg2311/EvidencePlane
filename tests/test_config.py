from __future__ import annotations

from app.config import Settings


def test_secret_file_environment_values_are_supported(tmp_path, monkeypatch):
    database_url_file = tmp_path / "database_url"
    hmac_secret_file = tmp_path / "hmac_secret"
    admin_token_file = tmp_path / "admin_token"
    database_url_file.write_text("sqlite:///secret-file.db\n", encoding="utf-8")
    hmac_secret_file.write_text("secret-from-file\n", encoding="utf-8")
    admin_token_file.write_text("admin-from-file\n", encoding="utf-8")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("EVIDENCEPLANE_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL_FILE", str(database_url_file))
    monkeypatch.setenv("EVIDENCEPLANE_HMAC_SECRET_FILE", str(hmac_secret_file))
    monkeypatch.setenv("ADMIN_TOKEN_FILE", str(admin_token_file))

    settings = Settings.from_env()

    assert settings.database_url == "sqlite:///secret-file.db"
    assert settings.hmac_secret == "secret-from-file"
    assert settings.admin_token == "admin-from-file"
