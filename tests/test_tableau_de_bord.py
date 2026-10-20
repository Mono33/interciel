"""Tests hors reseau du tableau de bord du HUD (donnees factices uniquement)."""
import datetime as dt
import http.client
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import hud
from core import tableau_de_bord as tdb
from core import tableau_factures as tf

FUSEAU = dt.timezone(dt.timedelta(hours=2))
MAINTENANT = dt.datetime(2026, 10, 20, 9, 30, tzinfo=FUSEAU)    # un mardi


class Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _source(nom, lire, ttl=600):
    return tdb.Source(nom, nom.capitalize(), lire, ttl)


def _assembleur(sources, horloge=None):
    return tdb.Assembleur(sources, horloge=horloge or Horloge(), maintenant=lambda: MAINTENANT)


def _reglages(valeurs):
    return lambda chemin, defaut=None: valeurs.get(chemin, defaut)


class AssembleurTests(unittest.TestCase):
    def test_une_source_en_echec_ne_casse_pas_les_autres(self):
        def casse():
            raise RuntimeError("https://api.exemple/?access_token=SECRET-FACTICE")

        a = _assembleur([
            _source("agenda", lambda: {"valeur": "2", "lignes": [tdb._ligne("Réunion", "14h")]}),
            _source("factures", casse),
            _source("loopstr", lambda: {"etat": tdb.VIDE, "message": "Rien."}),
        ])
        with self.assertLogs("jarvis", level="INFO") as journal:
            a.rafraichir()
        tuiles = {t["id"]: t for t in a.tuiles()}

        self.assertEqual([t["id"] for t in a.tuiles()], ["agenda", "factures", "loopstr"])
        self.assertEqual(tuiles["agenda"]["etat"], tdb.OK)
        self.assertEqual(tuiles["factures"]["etat"], tdb.INDISPONIBLE)
        self.assertEqual(tuiles["loopstr"]["etat"], tdb.VIDE)
        self.assertNotIn("SECRET", json.dumps(a.tuiles()))
        self.assertNotIn("SECRET", "\n".join(journal.output))

    def test_source_relue_seulement_apres_son_delai(self):
        horloge, appels = Horloge(), []
        a = _assembleur([_source("factures", lambda: appels.append(1) or {"valeur": "1"},
                                 ttl=3600)], horloge)
        a.rafraichir()
        horloge.t = 1800
        a.rafraichir()
        self.assertEqual(len(appels), 1)          # afficher ne relit pas les factures
        horloge.t = 3601
        a.rafraichir()
        self.assertEqual(len(appels), 2)

    def test_echec_retente_plus_tot_et_rappelle_la_derniere_lecture(self):
        horloge, appels = Horloge(), []

        def lire():
            appels.append(1)
            if len(appels) > 1:
                raise TimeoutError()
            return {"valeur": "3"}

        a = _assembleur([_source("factures", lire, ttl=3600)], horloge)
        a.rafraichir()
        horloge.t = 3601
        a.rafraichir()
        self.assertIn("Dernière lecture à 09:30", a.tuiles()[0]["message"])
        horloge.t = 3601 + 299
        a.rafraichir()
        self.assertEqual(len(appels), 2)
        horloge.t = 3601 + 301
        a.rafraichir()
        self.assertEqual(len(appels), 3)

    def test_chrome_ferme_n_est_pas_une_derniere_lecture(self):
        horloge, reponses = Horloge(), [{"etat": tdb.INDISPONIBLE, "message": "Chrome fermé."}]

        def lire():
            if reponses:
                return reponses.pop()
            raise TimeoutError()

        a = _assembleur([_source("factures", lire, ttl=3600)], horloge)
        a.rafraichir()
        horloge.t = 3601
        a.rafraichir()
        self.assertEqual(a.tuiles()[0]["etat"], tdb.INDISPONIBLE)
        self.assertNotIn("Dernière lecture", a.tuiles()[0]["message"])

    def test_source_non_configuree(self):
        def lire():
            raise tdb.NonConfigure("Flux Loopstr non configuré.")

        a = _assembleur([_source("loopstr", lire)])
        a.rafraichir()
        self.assertEqual(a.tuiles()[0]["etat"], tdb.NON_CONFIGURE)
        self.assertEqual(a.tuiles()[0]["message"], "Flux Loopstr non configuré.")

    def test_publie_apres_chaque_tuile_changee_seulement(self):
        publications = []
        a = _assembleur([_source("agenda", lambda: {"valeur": "1"}),
                         _source("meteo", lambda: {"valeur": "12°"})])
        a.rafraichir(publier=publications.append)
        self.assertEqual([len(p) for p in publications], [1, 2])
        a.rafraichir(force=True, publier=publications.append)
        self.assertEqual(len(publications), 2)    # rien n'a change

    def test_sources_configurees(self):
        sources = tdb.sources_configurees(
            {"sources": ["meteo", "inconnue", "factures", "meteo"], "factures_minutes": 5})
        self.assertEqual([s.id for s in sources], ["meteo", "factures"])
        self.assertEqual(sources[1].ttl, 15 * 60)            # plancher de 15 minutes
        defaut = tdb.sources_configurees({})
        self.assertEqual([s.id for s in defaut], list(tdb.SOURCES_PAR_DEFAUT))
        self.assertEqual(defaut[1].ttl, 3600)

    def test_inactif_par_defaut(self):
        with patch.object(tdb, "_EN_COURS", {"thread": None, "assembleur": None}), \
                patch.object(tdb, "_reglage", return_value=None), \
                patch("threading.Thread") as fil:
            self.assertFalse(tdb.demarrer(lambda tuiles: None))
            self.assertFalse(tdb.demarrer(lambda tuiles: None, reglages={"actif": False}))
        fil.assert_not_called()


class FacturesTests(unittest.TestCase):
    TEXTE = ("Tableau de bord\nChiffre d'affaires\n12 400,00 €\nFactures impayées\n3\n"
             "1 250,00 €\nEn retard\n1 facture\n400,00 €\nClient Exemple SARL\n")

    def test_resumer_reconnait_impayes_et_retard(self):
        resume = tf.resumer(self.TEXTE)
        self.assertEqual(resume["impayees"], {"nombre": 3, "montant": 1250.0})
        self.assertEqual(resume["retard"], {"nombre": 1, "montant": 400.0})
        self.assertFalse(resume["aucun"])

    def test_resumer_aucun_impaye(self):
        resume = tf.resumer("Tableau de bord\nAucune facture impayée\nBravo")
        self.assertTrue(resume["aucun"])
        self.assertIsNone(resume["impayees"])

    def test_formats_de_montant(self):
        self.assertEqual(tf._montant("1.250,50 €"), 1250.5)
        self.assertEqual(tf._montant("1 250 €"), 1250.0)
        self.assertEqual(tf._montant("1250.00 EUR"), 1250.0)
        self.assertEqual(tf._montant("99,9 €"), 99.9)
        self.assertIsNone(tf._montant("3 factures"))

    def test_session_expiree(self):
        self.assertTrue(tf.session_expiree("Mot de passe\nSe connecter", "https://x.exemple/app"))
        self.assertTrue(tf.session_expiree("x" * 900, "https://x.exemple/login"))
        self.assertFalse(tf.session_expiree(self.TEXTE * 20, "https://x.exemple/tableau"))

    def test_tuile_factures(self):
        resume = {"etat": "ok", "impayees": {"nombre": 3, "montant": 1250.0},
                  "retard": {"nombre": 1, "montant": 400.0}, "aucun": False}
        vu = {}

        def lecteur(url, port):
            vu.update(url=url, port=port)
            return resume

        with patch.object(tdb, "_reglage", side_effect=_reglages({
                "factures.url": "https://facture.exemple/tableau", "navigateur.cdp_port": 9333})):
            tuile = tdb.source_factures(lecteur)

        self.assertEqual(vu, {"url": "https://facture.exemple/tableau", "port": 9333})
        self.assertEqual(tuile["valeur"], "1 250 €")
        self.assertEqual([l["texte"] for l in tuile["lignes"]],
                         ["1 facture en retard", "3 factures impayées"])
        self.assertTrue(tuile["lignes"][0]["alerte"])
        self.assertFalse(tuile["lignes"][1]["alerte"])

    def test_tuile_factures_chrome_ferme_ou_session_expiree(self):
        with patch.object(tdb, "_reglage", side_effect=_reglages({
                "factures.url": "https://facture.exemple/tableau"})):
            ferme = tdb.source_factures(lambda url, port: {"etat": "chrome_ferme"})
            expiree = tdb.source_factures(lambda url, port: {"etat": "session_expiree"})
            with self.assertRaises(RuntimeError):
                tdb.source_factures(lambda url, port: {"etat": "erreur", "type": "TimeoutError"})
        self.assertEqual(ferme["etat"], tdb.INDISPONIBLE)
        self.assertIn("Chrome + Jarvis", ferme["message"])
        self.assertIn("expirée", expiree["message"])

    def test_factures_non_configurees(self):
        with patch.object(tdb, "_reglage", side_effect=_reglages({})):
            with self.assertRaises(tdb.NonConfigure):
                tdb.source_factures(lambda url, port: self.fail("ne doit pas lire"))

    def test_lecture_isolee_passe_l_adresse_par_stdin(self):
        fini = MagicMock(stdout='bruit\n{"etat": "ok", "impayees": null, '
                                '"retard": null, "aucun": true}\n')
        with patch("subprocess.run", return_value=fini) as run:
            resume = tdb.lire_factures_isole("https://facture.exemple/tableau", 9222)
        args, options = run.call_args
        self.assertEqual(args[0][1:], ["-m", "core.tableau_factures"])
        self.assertNotIn("facture.exemple", " ".join(args[0]))
        self.assertIn("facture.exemple", options["input"])
        self.assertTrue(resume["aucun"])

    def test_processus_refuse_une_adresse_non_https(self):
        with patch("sys.stdin", io.StringIO('{"url": "http://facture.exemple"}')), \
                patch("sys.stdout", new_callable=io.StringIO) as sortie, \
                patch.object(tf, "lire") as lire:
            tf.main()
        lire.assert_not_called()
        self.assertEqual(json.loads(sortie.getvalue()), {"etat": "erreur", "type": "url"})


class DonneesStructureesTests(unittest.TestCase):
    def test_noms_sans_emoji_decoratif(self):
        self.assertEqual(tdb._propre("💬・général"), "général")
        self.assertEqual(tdb._propre("🐀Serveur démo"), "Serveur démo")
        self.assertEqual(tdb._propre("👍🏽 #annonces"), "#annonces")
        self.assertEqual(tdb._marque_tache("🚀 Marque A — Publication"), ("Marque A", "Publication"))
        self.assertEqual(tdb._marque_tache("✍️ Marque-B - Script"), ("Marque-B", "Script"))
        self.assertEqual(tdb._marque_tache("Tournage libre"), ("Tournage libre", ""))

    def test_donnees_transmises_par_l_assembleur(self):
        a = _assembleur([_source("meteo", lambda: {"valeur": "12°", "donnees": {"temperature": 12}}),
                         _source("agenda", lambda: {"valeur": "1", "donnees": "pas un dict"})])
        a.rafraichir()
        meteo, agenda = a.tuiles()
        self.assertEqual(meteo["donnees"], {"temperature": 12})
        self.assertNotIn("donnees", agenda)


class SourcesTests(unittest.TestCase):
    def test_agenda_ne_lance_jamais_l_autorisation_interactive(self):
        with patch("tools.agenda._SERVICE", None), \
                patch("tools.agenda._chemin", return_value=Path("introuvable/google_token.json")), \
                patch("tools.agenda._service") as service:
            with self.assertRaises(tdb.NonConfigure):
                tdb.source_agenda(maintenant=MAINTENANT)
        service.assert_not_called()

    def test_tuile_agenda_sans_les_echeances_loopstr(self):
        service = MagicMock()
        service.events.return_value.list.return_value.execute.return_value = {"items": [
            {"summary": "Dentiste", "start": {"dateTime": "2026-10-22T09:00:00+02:00"}},
            {"summary": "Point équipe", "start": {"dateTime": "2026-10-20T14:00:00+02:00"}},
        ]}
        calendriers = {"perso": {"nom": "Perso"}, "loop": {"nom": "Loopstr deadlines"}}
        with patch("tools.agenda._SERVICE", service), \
                patch("tools.agenda._calendriers", return_value=calendriers):
            tuile = tdb.source_agenda(maintenant=MAINTENANT)

        lus = [c.kwargs["calendarId"] for c in service.events.return_value.list.call_args_list]
        self.assertEqual(lus, ["perso"])
        self.assertEqual(tuile["valeur"], "1")
        self.assertEqual([(l["texte"], l["detail"]) for l in tuile["lignes"]],
                         [("Point équipe", "aujourd'hui · 14h"), ("Dentiste", "jeu. 22 · 9h")])

    def test_tuile_loopstr(self):
        evenements = [
            (dt.datetime(2026, 10, 19, 10, tzinfo=FUSEAU), "deadline", "Hier"),
            (dt.datetime(2026, 10, 20, tzinfo=FUSEAU), "deadline", "Marque A - publication"),
            (dt.datetime(2026, 10, 21, tzinfo=FUSEAU), "absence", "Vacances"),
            (dt.datetime(2026, 10, 23, tzinfo=FUSEAU), "deadline", "Marque B - brief"),
            (dt.datetime(2026, 10, 29, tzinfo=FUSEAU), "deadline", "Marque C - V1"),
            (dt.datetime(2026, 11, 10, tzinfo=FUSEAU), "deadline", "Trop loin"),
        ]
        with patch.object(tdb, "_reglage", return_value="https://loopstr.exemple/flux.ics"), \
                patch("tools.loopstr._charger", return_value=evenements):
            tuile = tdb.source_loopstr(maintenant=MAINTENANT)
        self.assertEqual([l["texte"] for l in tuile["lignes"]],
                         ["Marque A - publication", "Marque B - brief", "Marque C - V1"])
        self.assertEqual(tuile["valeur"], "2")
        self.assertEqual(tuile["lignes"][0]["detail"], "aujourd'hui")
        self.assertTrue(tuile["lignes"][0]["alerte"])
        self.assertEqual(tuile["lignes"][1]["detail"], "ven. 23")
        self.assertFalse(tuile["lignes"][1]["alerte"])
        premiere = tuile["donnees"]["echeances"][0]
        self.assertEqual((premiere["marque"], premiere["tache"]), ("Marque A", "publication"))
        self.assertTrue(premiere["debut"].startswith("2026-10-20"))
        self.assertEqual(len(tuile["donnees"]["echeances"]), 3)

    def test_tuile_instagram_lit_sans_ecrire_l_historique(self):
        comptes = [{"nom": "principal", "user_id": "1", "token": "JETON-FACTICE"}]
        historique = {"principal": {"2026-10-19": {"abonnes": 4192},
                                    "2026-10-20": {"abonnes": 4200}}}
        with patch("tools.instagram._comptes", return_value=comptes), \
                patch("tools.instagram._charger_historique", return_value=historique), \
                patch("tools.instagram._get", return_value={
                    "username": "compte_demo", "followers_count": 4210}) as get, \
                patch("tools.instagram._sauver_historique") as sauver:
            tuile = tdb.source_instagram(aujourd_hui=dt.date(2026, 10, 20))
        sauver.assert_not_called()
        self.assertEqual(get.call_count, 1)                  # pas d'insights par video
        self.assertEqual(tuile["valeur"], "+18")
        self.assertEqual(tuile["lignes"][0]["texte"], "@compte_demo")
        self.assertIn("+18 depuis hier", tuile["lignes"][0]["detail"])
        self.assertNotIn("JETON", json.dumps(tuile))
        fiche = tuile["donnees"]["comptes"][0]
        self.assertEqual((fiche["compte"], fiche["abonnes"], fiche["delta"], fiche["depuis"]),
                         ("compte_demo", 4210, 18, "hier"))
        self.assertEqual(fiche["serie"], [4192, 4210])     # releves passes + valeur du jour

    def test_instagram_en_echec_reste_sans_secret(self):
        comptes = [{"nom": "principal", "user_id": "1", "token": "JETON-FACTICE"}]
        with patch("tools.instagram._comptes", return_value=comptes), \
                patch("tools.instagram._charger_historique", return_value={}), \
                patch("tools.instagram._get",
                      side_effect=RuntimeError("?access_token=JETON-FACTICE")):
            a = _assembleur([tdb.Source("instagram", "Instagram", tdb.source_instagram, 3600)])
            a.rafraichir()
        self.assertEqual(a.tuiles()[0]["etat"], tdb.INDISPONIBLE)
        self.assertNotIn("JETON", json.dumps(a.tuiles()))

    def test_tuile_discord_sans_contenu_de_message(self):
        stats = {"messages": 42, "salons": 3, "mentions": 2,
                 "actifs": [{"serveur": "🐀Serveur demo", "salon": "💬・general", "messages": 30},
                            {"serveur": "🐀Serveur demo", "salon": "debats", "messages": 9}]}
        with patch("tools.discord_bot._configure", return_value=True), \
                patch("tools.discord_bot.digest_journal", return_value=(stats, None)):
            tuile = tdb.source_discord()
        self.assertEqual(tuile["valeur"], "2")
        self.assertEqual(tuile["lignes"][0]["texte"], "42 messages")
        self.assertEqual(tuile["lignes"][1]["texte"], "#general")
        self.assertEqual(tuile["donnees"]["serveurs"], [{"nom": "Serveur demo", "salons": [
            {"nom": "general", "messages": 30}, {"nom": "debats", "messages": 9}]}])
        self.assertEqual(tuile["donnees"]["mentions"], 2)

    def test_tuile_colis(self):
        en_cours = [{"marchand": "Boutique demo", "etape": "livraison"},
                    {"marchand": "Autre demo", "etape": "expedie"}]
        with patch("tools.mail._mail_configure", return_value=True), \
                patch("tools.colis.colis_en_cours", return_value=en_cours):
            tuile = tdb.source_colis()
        self.assertEqual(tuile["valeur"], "2")
        self.assertEqual(tuile["lignes"][0]["detail"], "arrive aujourd'hui")
        self.assertTrue(tuile["lignes"][0]["alerte"])

    def test_tuile_meteo(self):
        reponse = {"current": {"temperature_2m": 12.4, "weather_code": 3},
                   "daily": {"temperature_2m_min": [8.2], "temperature_2m_max": [15.6],
                             "precipitation_probability_max": [70]}}
        with patch("tools.meteo._open_meteo", return_value=reponse), \
                patch("tools.meteo._LIEU", {"ville": "Ville demo"}):
            tuile = tdb.source_meteo()
        self.assertEqual((tuile["valeur"], tuile["legende"]), ("12°", "couvert"))
        self.assertEqual(tuile["lignes"][0]["detail"], "8° / 16°")
        self.assertTrue(tuile["lignes"][1]["alerte"])

    def test_tuile_spotify(self):
        etat = {"is_playing": True, "device": {"name": "Bureau"},
                "item": {"name": "Titre demo", "artists": [{"name": "Artiste demo"}]}}
        with patch("tools.spotify._configure", return_value=True), \
                patch("tools.spotify._etat_lecture", return_value=etat):
            tuile = tdb.source_spotify()
        self.assertEqual(tuile["lignes"][0], tdb._ligne("Titre demo", "Artiste demo"))


class HudTableauTests(unittest.TestCase):
    def setUp(self):
        self._sauve = (dict(hud._TABLEAU), list(hud._ETAT["tableau"]))
        hud._TABLEAU.clear()
        hud._ETAT["tableau"] = []

    def tearDown(self):
        hud._TABLEAU.clear()
        hud._TABLEAU.update(self._sauve[0])
        hud._ETAT["tableau"] = self._sauve[1]

    def test_fusion_par_id(self):
        with patch.object(hud, "_diffuser") as diffuser:
            hud.tableau([{"id": "agenda", "etat": "ok"}, {"id": "factures", "etat": "ok"}])
            hud.tableau([{"id": "agenda", "etat": "vide"}, {"sans_id": True}, "texte"])
        self.assertEqual([t["id"] for t in hud._ETAT["tableau"]], ["agenda", "factures"])
        self.assertEqual(hud._ETAT["tableau"][0]["etat"], "vide")
        self.assertEqual(diffuser.call_args[0][0]["t"], "tableau")

    def test_echec_du_tableau_local_ne_bloque_pas_le_hud(self):
        with patch("core.tableau_de_bord.demarrer", side_effect=ImportError("absent")):
            self.assertFalse(hud._demarrer_tableau())

    def test_rejoue_le_tableau_a_la_connexion(self):
        serveur = hud._Serveur(("127.0.0.1", 0), hud._Poignee)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        try:
            with patch.object(hud, "_diffuser"):
                hud.tableau([{"id": "agenda", "titre": "Agenda", "etat": "ok"}])
            connexion = http.client.HTTPConnection("127.0.0.1", serveur.server_address[1],
                                                   timeout=5)
            connexion.request("GET", "/flux")
            reponse = connexion.getresponse()
            evenements = []
            for _ in range(200):
                ligne = reponse.fp.readline().decode("utf-8").strip()
                if ligne.startswith("data: "):
                    evenements.append(json.loads(ligne[6:]))
                    if evenements[-1].get("t") == "tableau":
                        break
            connexion.close()
        finally:
            serveur.shutdown()
            serveur.server_close()
        self.assertEqual(evenements[-1], {"t": "tableau", "tuiles": [
            {"id": "agenda", "titre": "Agenda", "etat": "ok"}]})
        self.assertEqual(evenements[0], {"t": "version", "v": hud._version_page()})

    def test_une_page_modifiee_est_annoncee_au_battement(self):
        versions = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
        actuelle = {"v": next(versions)}
        serveur = hud._Serveur(("127.0.0.1", 0), hud._Poignee)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        try:
            with patch.object(hud, "_BATTEMENT", 0.2), \
                    patch.object(hud, "_version_page", lambda contenu=None: actuelle["v"]):
                connexion = http.client.HTTPConnection("127.0.0.1", serveur.server_address[1],
                                                       timeout=5)
                connexion.request("GET", "/flux")
                reponse = connexion.getresponse()
                for _ in range(400):                    # premier battement : boucle en cours
                    if reponse.fp.readline().startswith(b": battement"):
                        break
                actuelle["v"] = next(versions)          # hud.html change sur le disque
                annonces = []
                for _ in range(400):
                    ligne = reponse.fp.readline().decode("utf-8").strip()
                    if ligne.startswith("data: ") and json.loads(ligne[6:]).get("t") == "version":
                        annonces.append(json.loads(ligne[6:])["v"])
                        break
                connexion.close()
        finally:
            serveur.shutdown()
            serveur.server_close()
        self.assertEqual(annonces[-1], "bbbbbbbbbbbb")

    def test_la_page_porte_la_version_annoncee_par_le_flux(self):
        serveur = hud._Serveur(("127.0.0.1", 0), hud._Poignee)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        try:
            connexion = http.client.HTTPConnection("127.0.0.1", serveur.server_address[1], timeout=5)
            connexion.request("GET", "/")
            page = connexion.getresponse().read().decode("utf-8")
            connexion.close()
        finally:
            serveur.shutdown()
            serveur.server_close()
        version = hud._version_page()
        self.assertRegex(version, r"^[0-9a-f]{12}$")
        self.assertIn(f'<meta name="jarvis-version" content="{version}">', page)
        self.assertNotIn("__VERSION_PAGE__", page)


TUILES_FACTICES = [
    {"id": "agenda", "titre": "Agenda", "etat": "ok", "valeur": "1", "legende": "aujourd'hui",
     "lignes": [{"texte": "<img src=x onerror=alert(1)>", "detail": "14h", "alerte": True}]},
    {"id": "factures", "titre": "Factures", "etat": "indisponible", "message": ""},
]


class HudPageBasculeTests(unittest.TestCase):
    """La page elle-meme, dans un Chromium headless (ignore s'il n'est pas installe)."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright absent")
        cls.pw = sync_playwright().start()
        try:
            cls.navigateur = cls.pw.chromium.launch()
        except Exception as e:
            cls.pw.stop()
            raise unittest.SkipTest(f"chromium indisponible ({type(e).__name__})")
        cls.serveur = hud._Serveur(("127.0.0.1", 0), hud._Poignee)
        threading.Thread(target=cls.serveur.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.serveur.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()
        cls.serveur.server_close()
        cls.navigateur.close()
        cls.pw.stop()

    def setUp(self):
        self._sauve = (dict(hud._TABLEAU), list(hud._ETAT["tableau"]), hud._ETAT["etat"])
        hud._TABLEAU.clear()
        hud.etat("veille")
        hud.tableau(TUILES_FACTICES)
        self.page = self.navigateur.new_page()
        self.page.goto(self.url)

    def tearDown(self):
        self.page.close()
        hud._TABLEAU.clear()
        hud._TABLEAU.update(self._sauve[0])
        hud._ETAT["tableau"] = self._sauve[1]
        hud._ETAT["etat"] = self._sauve[2]

    def _attendre_mode(self, actif, delai=6000):
        condition = "document.body.classList.contains('mode-tableau')"
        self.page.wait_for_function(condition if actif else "!" + condition, timeout=delai)

    def _mode(self):
        return self.page.evaluate("document.body.classList.contains('mode-tableau')")

    def test_reacteur_grand_des_qu_elle_parle_puis_tableau_au_repos(self):
        self._attendre_mode(True)
        self.assertEqual(self.page.locator("#tbSuivi .tb-etat").count(), 1)
        for etat in ("ecoute", "reflexion", "parole"):
            hud.etat(etat)
            self._attendre_mode(False, delai=2000)
            hud.etat("veille")
            self.page.wait_for_timeout(600)
            self.assertFalse(self._mode())        # une veille breve ne fait pas clignoter
            hud.etat(etat)
        hud.etat("veille")
        self._attendre_mode(True)

    def test_bouton_masque_et_reaffiche(self):
        self._attendre_mode(True)
        bouton = self.page.locator("#basculerTableau")
        bouton.click()
        self.assertFalse(self._mode())
        self.assertEqual(bouton.get_attribute("aria-pressed"), "false")
        bouton.click()
        self.assertTrue(self._mode())
        self.assertEqual(bouton.get_attribute("aria-pressed"), "true")

    def test_le_bouton_affiche_le_tableau_meme_hors_repos(self):
        # Cas reel : « Hey Jarvis » sans question laissait la fenetre en ecoute.
        hud.etat("ecoute")
        self._attendre_mode(False, delai=2000)
        bouton = self.page.locator("#basculerTableau")
        bouton.click()
        self.assertTrue(self._mode())                       # tout de suite
        self.assertEqual(bouton.get_attribute("aria-pressed"), "true")
        hud.etat("parole")                                  # elle reparle
        self._attendre_mode(False, delai=2000)
        hud.etat("veille")
        self._attendre_mode(True)                           # et il revient au repos

    def test_masque_au_repos_jusqu_au_prochain_clic(self):
        self._attendre_mode(True)
        bouton = self.page.locator("#basculerTableau")
        bouton.click()
        self.assertFalse(self._mode())
        hud.etat("ecoute")
        hud.etat("veille")
        self.page.wait_for_timeout(2500)
        self.assertFalse(self._mode())                      # le choix est retenu
        bouton.click()
        self.assertTrue(self._mode())

    def test_tuiles_rendues_comme_du_texte(self):
        self._attendre_mode(True)
        self.assertEqual(self.page.locator("#tableau img").count(), 0)
        self.assertIn("<img", self.page.locator("#tbTitre").inner_text())
        self.assertIn("Indisponible", self.page.locator("#tbSuivi .tb-etat").inner_text())

    def _echeances(self):
        demain = (dt.datetime.now().astimezone() + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return [{"debut": (demain + dt.timedelta(days=k)).isoformat(), "marque": f"Marque {c}",
                 "tache": "Publication"} for k, c in enumerate("ABC")]

    def test_donnees_structurees_en_vedette_affiches_et_cartes(self):
        self._attendre_mode(True)
        hud.tableau([
            {"id": "loopstr", "titre": "Loopstr", "etat": "ok", "donnees": {"echeances": self._echeances()}},
            {"id": "instagram", "titre": "Instagram", "etat": "ok", "donnees": {"comptes": [
                {"compte": "compte.demo", "abonnes": 1036121, "delta": 2308, "depuis": "hier",
                 "serie": [1000000, 1020000, 1036121]}]}},
        ])
        # Les echeances datees passent avant le resume sans date de l'agenda.
        self.page.wait_for_function(
            "document.getElementById('tbTitre').textContent === 'Publication Marque A'", timeout=4000)
        self.assertEqual(self.page.locator("#tbAvenir .tb-affiche").count(), 3)   # B, C, agenda
        self.page.wait_for_function(
            "(document.querySelector('#tbSuivi .tb-insta .tb-grand') || {}).textContent"
            "?.replace(/\\s/g, ' ') === '1,04 M'", timeout=4000)
        self.assertIn("+2", self.page.locator("#tbSuivi .tb-insta .tb-delta").inner_text())
        self.assertEqual(self.page.locator("#tbSuivi .tb-insta .tb-courbe").count(), 1)

    def test_vue_detaillee_au_clic_et_echap(self):
        self._attendre_mode(True)
        hud.tableau([{"id": "loopstr", "titre": "Loopstr", "etat": "ok",
                      "donnees": {"echeances": self._echeances()}}])
        affiche = self.page.locator("#tbAvenir .tb-affiche").first
        affiche.wait_for(timeout=4000)
        affiche.click()
        self.page.locator(".tb-detail").wait_for(timeout=3000)
        self.assertGreaterEqual(self.page.locator(".tb-detail .ligne-d").count(), 3)
        self.page.keyboard.press("Escape")
        self.page.locator(".tb-detail").wait_for(state="detached", timeout=3000)


if __name__ == "__main__":
    unittest.main()
