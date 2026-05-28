"""Generates .streamlit/secrets.toml from Render environment variables."""
import os
import pathlib

client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
cookie_secret = os.environ.get("AUTH_COOKIE_SECRET", "changeme-set-in-render")
app_url = os.environ.get("APP_URL", "https://medthief-eihf.onrender.com")
redirect_uri = app_url.rstrip("/") + "/oauth2callback"

if not client_id:
    print("GOOGLE_CLIENT_ID not set — skipping secrets.toml creation")
else:
    secrets_dir = pathlib.Path(".streamlit")
    secrets_dir.mkdir(exist_ok=True)
    content = f'''[auth]
redirect_uri = "{redirect_uri}"
cookie_secret = "{cookie_secret}"

[auth.google]
client_id = "{client_id}"
client_secret = "{client_secret}"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
'''
    (secrets_dir / "secrets.toml").write_text(content)
    print(f"secrets.toml created (redirect: {redirect_uri})")
