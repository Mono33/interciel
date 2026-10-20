"""Lecture des factures (facture.net) pour la tuile du tableau de bord, isolee.

Lance en sous-processus par core.tableau_de_bord : Playwright est lie a un thread,
et l'instance partagee de Jarvis (core.playwright_partage) sert aux outils
navigateur declenches a la voix. Ce processus a donc sa propre instance, se
connecte au Chrome dedie DEJA ouvert (jamais de lancement automatique), lit le
tableau de bord dans un onglet en arriere-plan puis le referme.

Entree (stdin, JSON) : {"url": ..., "port": 9222}. Sortie (stdout, une ligne JSON) :
uniquement des compteurs et des montants, jamais de nom de client ni de texte brut.
LECTURE SEULE : aucun clic, aucune saisie.
"""
import json
import re
import sys
import unicodedata

# Indices qu'on est retombe sur une page de connexion (session expiree).
_INDICES_LOGIN = ("mot de passe", "se connecter", "connexion", "identifiant",
                  "s'identifier", "login", "sign in", "password")

_RETARD = ("en retard", "retard")
_IMPAYE = ("impaye", "non paye", "a encaisser", "en attente de paiement",
           "reste a payer", "a recevoir", "en attente de reglement")
_AUCUN = re.compile(r"\baucune? (facture )?(impayee?s?|en retard)\b|\b0 (facture )?impayee?s?\b")
_MONTANT = re.compile(
    r"(\d{1,3}(?:[   .]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b)",
    re.I)
_NOMBRE = re.compile(r"^\s*(\d{1,4})\s*$|\b(\d{1,4})\s+factures?\b|\((\d{1,4})\)")


def _plat(texte):
    texte = unicodedata.normalize("NFD", str(texte or "").lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def _montant(ligne):
    """Premier montant en euros d'une ligne (float), ou None."""
    m = _MONTANT.search(ligne)
    if not m:
        return None
    brut = re.sub(r"[   ]", "", m.group(1))
    if "," in brut:
        brut = brut.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{3}$", brut):            # 1.250 = mille deux cent cinquante
        brut = brut.replace(".", "")
    try:
        return float(brut)
    except ValueError:
        return None


def _nombre(ligne):
    m = _NOMBRE.search(ligne)
    return int(next(g for g in m.groups() if g)) if m else None


def resumer(texte):
    """Indicateurs d'impayes reconnus dans le texte du tableau de bord. Fonction pure.

    Une etiquette (« Impayees », « En retard »...) est suivie de son nombre et de son
    montant sur la meme ligne ou sur les deux suivantes, comme dans la plupart des
    tableaux de bord. Renvoie {"impayees": {...}|None, "retard": {...}|None,
    "aucun": bool} ; chaque indicateur vaut {"nombre": int|None, "montant": float|None}.
    """
    lignes = [l.strip() for l in _plat(texte).splitlines() if l.strip()]
    trouves = {"impayees": None, "retard": None}
    for i, ligne in enumerate(lignes):
        if _AUCUN.search(ligne):
            continue
        cle = ("retard" if any(m in ligne for m in _RETARD)
               else "impayees" if any(m in ligne for m in _IMPAYE) else None)
        if cle is None or trouves[cle] is not None or len(ligne) > 120:
            continue
        voisines = [ligne] + lignes[i + 1:i + 3]
        nombre = next((n for n in map(_nombre, voisines) if n is not None), None)
        montant = next((m for m in map(_montant, voisines) if m is not None), None)
        if nombre is not None or montant is not None:
            trouves[cle] = {"nombre": nombre, "montant": montant}
    aucun = bool(_AUCUN.search("\n".join(lignes))) and not any(trouves.values())
    return {**trouves, "aucun": aucun}


def session_expiree(texte, url_finale):
    bas = _plat(texte)
    url = str(url_finale or "").lower()
    return (any(x in url for x in ("login", "connexion", "signin", "sign-in"))
            or (len(texte) < 500 and any(m in bas for m in _INDICES_LOGIN)))


def _onglet_discret(navigateur, contexte):
    """Nouvel onglet ouvert en arriere-plan (sans voler le premier plan si possible)."""
    try:
        session = navigateur.new_browser_cdp_session()
        with contexte.expect_page(timeout=5000) as attente:
            session.send("Target.createTarget", {"url": "about:blank", "background": True})
        return attente.value
    except Exception:
        return contexte.new_page()


def lire(url, port):
    """Lit le tableau de bord dans le Chrome dedie deja ouvert. Lecture seule."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            navigateur = pw.chromium.connect_over_cdp(f"http://localhost:{int(port)}",
                                                      timeout=3000)
        except Exception:
            return {"etat": "chrome_ferme"}
        if not navigateur.contexts:
            return {"etat": "chrome_ferme"}
        contexte = navigateur.contexts[0]
        page = _onglet_discret(navigateur, contexte)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1800)          # laisser le tableau de bord (JS) se remplir
            texte = (page.inner_text("body") or "").strip()
            url_finale = page.url or ""
        finally:
            try:
                page.close()
            except Exception:
                pass
    if session_expiree(texte, url_finale):
        return {"etat": "session_expiree"}
    if len(texte) < 40:
        return {"etat": "vide"}
    return {"etat": "ok", **resumer(texte)}


def main():
    try:
        demande = json.loads(sys.stdin.read() or "{}")
        url = str(demande.get("url", "")).strip()
        if not url.lower().startswith("https://"):
            resultat = {"etat": "erreur", "type": "url"}
        else:
            resultat = lire(url, int(demande.get("port", 9222) or 9222))
    except Exception as e:
        # Le type seul : un message d'erreur Playwright peut citer la page.
        resultat = {"etat": "erreur", "type": type(e).__name__}
    sys.stdout.write(json.dumps(resultat) + "\n")


if __name__ == "__main__":
    main()
