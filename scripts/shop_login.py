"""Connexion a Shop (shop.com) pour le suivi des livraisons par Jarvis.

    uv run python scripts/shop_login.py

Ouvre une fenetre Chrome visible avec le profil dedie de Jarvis, sur la page
Commandes de Shop. C'est TOI qui te connectes (e-mail puis code recu) : le script
ne tape rien et ne lit aucun identifiant. Des que la page de tes commandes
s'affiche, il ferme la fenetre ; la session reste gardee dans le profil, hors du
depot (%LOCALAPPDATA%\\Jarvis\\profil_shop par defaut). A relancer si Jarvis dit
que la session Shop a expire.
"""
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

URL_COMMANDES = "https://shop.com/orders"
DELAI_MAX = 15 * 60          # secondes laissees pour se connecter


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from playwright.sync_api import sync_playwright
    from tools.shop import page_de_connexion, profil

    dossier = profil()
    dossier.mkdir(parents=True, exist_ok=True)
    print("Une fenêtre Chrome va s'ouvrir sur Shop.")
    print("Connecte-toi (e-mail puis code reçu). Elle se fermera toute seule")
    print("dès que tes commandes s'affichent.")
    with sync_playwright() as p:
        contexte = p.chromium.launch_persistent_context(
            str(dossier), channel="chrome", headless=False, viewport=None)
        page = contexte.pages[0] if contexte.pages else contexte.new_page()
        page.goto(URL_COMMANDES, wait_until="domcontentloaded")
        fin = time.time() + DELAI_MAX
        connecte = False
        while time.time() < fin:
            time.sleep(2)
            try:
                if page.is_closed():
                    break
                texte = page.inner_text("body") or ""
                if "/orders" in page.url and not page_de_connexion(page.url, texte):
                    connecte = True
                    break
            except Exception:
                continue
        if connecte:
            time.sleep(3)            # laisse Shop finir d'enregistrer la session
        contexte.close()
    print("Connexion Shop enregistrée." if connecte
          else "Pas de connexion détectée : relance le script quand tu veux.")


if __name__ == "__main__":
    main()
