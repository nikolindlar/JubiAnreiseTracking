"""Startet den Server (Produktion auf dem Raspberry Pi über waitress)."""
import os

from waitress import serve

from anreise import create_app

if __name__ == "__main__":
    serve(create_app(),
          host=os.environ.get("ANREISE_HOST", "0.0.0.0"),
          port=int(os.environ.get("ANREISE_PORT", "8080")))
