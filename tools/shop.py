"""Suivi des livraisons Shop (shop.com) — LECTURE SEULE, sur le poste principal.

La session Shop vit dans un profil Chrome dedie a Jarvis, sur le PC qui a un
ecran (hors du depot : %LOCALAPPDATA%\\Jarvis\\profil_shop). L'utilisatrice s'y
connecte elle-meme avec scripts/shop_login.py ; Jarvis ne saisit jamais rien,
ne clique sur rien et ne fait que lire les pages Commandes.

Sur un Jarvis a deux machines (cerveau sur un serveur, corps sur le PC),
l'outil fait partie de core/poste_distant.OUTILS_POSTE : le serveur demande, le
PC lit avec sa propre session. Le contenu des commandes n'est jamais journalise.
"""
import datetime as dt
import os
import re
import threading
from pathlib import Path

from core.config import reglage
from core.registre import outil

URL_COMMANDES = "https://shop.com/orders"
URL_PASSEES = "https://shop.com/orders/past"
_INDICES_LOGIN = ("connectez-vous", "se connecter", "connexion", "adresse e-mail",
                  "sign in", "log in", "continuer avec")
# Debut de chaque reponse reussie ou explicative : le cerveau distingue ainsi une
# vraie reponse Shop d'une erreur de transport du poste distant.
PREFIXE = "Shop"
_VERROU = threading.Lock()          # un profil Chrome n'accepte qu'un seul navigateur

_MOIS = {"janv": 1, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7,
         "aout": 8, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_DATE = r"\d{1,2}(?:er)? [A-Za-zéûÉÛ]+\.?"
_STATUT = re.compile(
    r"(?P<statut>(?:Livrée?|Commandée?|Expédiée?|Arrive|Arrivée? prévue) le " + _DATE
    + r"|Arrive (?:aujourd[’']hui|demain)|En cours de livraison|En transit"
    + r"|Prête? (?:pour le|à) retrait|Retardée?|Annulée?|Remboursée?)",
    re.IGNORECASE)
_FIN = re.compile(r"\s(?:\+\d+\s+)?\d+ articles?\b", re.IGNORECASE)


def profil():
    """Dossier du profil Chrome dedie a Shop (cookies de session, hors du depot)."""
    perso = str(reglage("shop.profil_dir", "") or "").strip()
    if perso:
        return Path(perso)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Jarvis" / "profil_shop"


def page_de_connexion(url, texte):
    """Vrai si Shop a renvoye vers sa connexion (session absente ou expiree)."""
    url = (url or "").lower()
    if any(x in url for x in ("/accounts/login", "/login", "signin", "sign_in")):
        return True
    bas = (texte or "").lower()
    return "connectez-vous pour suivre vos commandes" in bas or (
        len(bas) < 600 and any(m in bas for m in _INDICES_LOGIN))


def _plat(texte):
    from core.util import sans_accents
    return sans_accents(texte or "")


def _etape(statut):
    """Etape comparable a celles du suivi Gmail (tools/colis.py)."""
    p = _plat(statut)
    if p.startswith("livre"):
        return "livre"
    if "cours de livraison" in p or "aujourd" in p:
        return "livraison"
    if "retrait" in p:
        return "relais"
    if p.startswith(("annule", "rembourse")):
        return "annule"
    if p.startswith("commande"):
        return "commande"
    return "expedie"


def analyser_carte(texte):
    """Carte « Boutique - accroche  Statut  [transporteur]  N articles · prix  date ».

    Renvoie {marchand, statut, transporteur, etape} ou None si aucun statut lisible.
    """
    texte = " ".join(str(texte or "").split())
    m = _STATUT.search(texte)
    if not m:
        return None
    marchand = texte[:m.start()].strip(" -·")
    marchand = marchand.split(" - ")[0].strip() or "Une boutique"
    reste = texte[m.end():]
    fin = _FIN.search(reste)
    transporteur = (reste[:fin.start()] if fin else "").strip(" ·")
    statut = m.group("statut").strip()
    return {"marchand": marchand[:60], "statut": statut, "transporteur": transporteur[:40],
            "etape": _etape(statut)}


def date_du_statut(statut, aujourd_hui=None):
    """« Livré le 21 sept. » -> date (annee deduite : jamais dans le futur lointain)."""
    aujourd_hui = aujourd_hui or dt.date.today()
    m = re.search(r"(\d{1,2})(?:er)? ([a-z]+)", _plat(statut).lower())
    if not m or m.group(2)[:4] not in {k[:4] for k in _MOIS}:
        return None
    mois = next(v for k, v in _MOIS.items() if k[:4] == m.group(2)[:4])
    try:
        date = dt.date(aujourd_hui.year, mois, int(m.group(1)))
    except ValueError:
        return None
    if date > aujourd_hui + dt.timedelta(days=60):
        date = date.replace(year=date.year - 1)
    return date


_JS_CARTES = """() => [...document.querySelectorAll('a[href]')]
  .filter(a => /\\/orders\\/\\d+/.test(a.getAttribute('href')))
  .map(a => a.innerText.replace(/\\s+/g, ' ').trim())"""


def _lire(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    texte = page.inner_text("body") or ""
    if page_de_connexion(page.url, texte):
        raise PermissionError("session Shop absente ou expiree")
    return page.evaluate(_JS_CARTES)


def lire_commandes():
    """{'en_cours': [...], 'livrees': [...]} lus dans la session Shop du profil dedie.

    Leve FileNotFoundError (jamais connecte), PermissionError (session expiree) ou
    TimeoutError (profil deja utilise / page trop lente)."""
    if not profil().exists():
        raise FileNotFoundError("profil Shop absent")
    if not _VERROU.acquire(timeout=40):
        raise TimeoutError("lecture Shop deja en cours")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            contexte = p.chromium.launch_persistent_context(
                str(profil()), channel="chrome", headless=True)
            try:
                page = contexte.new_page()
                en_cours = [c for c in map(analyser_carte, _lire(page, URL_COMMANDES)) if c]
                passees = [c for c in map(analyser_carte, _lire(page, URL_PASSEES)) if c]
            finally:
                contexte.close()
    finally:
        _VERROU.release()
    hier = dt.date.today() - dt.timedelta(days=1)
    livrees = [c for c in passees if c["etape"] == "livre"
               and (date_du_statut(c["statut"]) or dt.date.min) >= hier]
    return {"en_cours": en_cours, "livrees": livrees}


def _decrire(c):
    statut = c["statut"][0].lower() + c["statut"][1:]
    return f"{c['marchand']} : {statut}" + (f" ({c['transporteur']})" if c["transporteur"] else "")


def resumer(commandes, bref=False):
    en_cours = [c for c in commandes["en_cours"] if c["etape"] not in {"livre", "annule"}]
    morceaux = [_decrire(c) for c in en_cours] + [_decrire(c) for c in commandes["livrees"]]
    if not morceaux:
        return "" if bref else f"{PREFIXE} : aucune commande en cours."
    liste = morceaux[0] if len(morceaux) == 1 else "; ".join(morceaux[:-1]) + " et " + morceaux[-1]
    return f"{PREFIXE} : {liste}" + ("" if liste.endswith(".") else ".")


@outil(
    nom="commandes_shop",
    description="Ou en sont les commandes suivies dans l'appli Shop (commandes en cours, "
                "date d'arrivee, transporteur, livraisons d'hier). LECTURE SEULE. Pour "
                "« mes commandes Shop », « ma commande Shop arrive quand ». Pour tous les "
                "colis (mails + Shop), utilise plutot suivi_colis.",
    parametres={"type": "object", "properties": {
        "bref": {"type": "boolean",
                 "description": "Usage interne du brief : phrase courte, vide si rien."}}},
    lent=True,
    phrase_attente="Je regarde tes commandes Shop.",
    mcp_expose=False,
)
def commandes_shop(bref: bool = False) -> str:
    try:
        return resumer(lire_commandes(), bref=bref)
    except FileNotFoundError:
        return "" if bref else (f"{PREFIXE} n'est pas encore connecté : lance « Connexion Shop » "
                                "(scripts/shop_login.py) sur ton PC.")
    except PermissionError:
        return "" if bref else (f"{PREFIXE} : ta session a expiré. Relance « Connexion Shop » "
                                "(scripts/shop_login.py) sur ton PC.")
    except Exception as e:
        return "" if bref else f"{PREFIXE} : lecture impossible ({type(e).__name__})."
