"""Startet den Server (Produktion auf dem Raspberry Pi über waitress)."""
import os

from waitress import serve

from anreise import create_app


def load_env_file(path=".env"):
    """Liest KEY=VALUE-Zeilen aus einer lokalen .env-Datei (z. B. zum Testen
    unter Windows). Bereits gesetzte Umgebungsvariablen haben Vorrang."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    load_env_file()
    serve(create_app(),
          host=os.environ.get("ANREISE_HOST", "0.0.0.0"),
          port=int(os.environ.get("ANREISE_PORT", "8080")))
