"""Tuiles du tableau de bord du serveur vers la fenetre du bureau (hors reseau)."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import satellite as serveur
from core import tableau_de_bord as tdb

ROOT = Path(__file__).resolve().parents[1]

TUILES = [
    {"id": "agenda", "titre": "Agenda", "etat": "ok", "valeur": "1"},
    {"id": "factures", "titre": "Factures", "etat": "ok", "valeur": "400 €"},
    {"id": "loopstr", "titre": "Loopstr", "etat": "vide", "message": "Rien."},
]


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, ROOT / chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ws(hote, schema="ws", entetes=None):
    return SimpleNamespace(scope={"scheme": schema}, client=SimpleNamespace(host=hote),
                           headers=entetes or {})


class AutorisationTests(unittest.TestCase):
    def test_choix_du_poste(self):
        self.assertIs(serveur._choix_tableau(True), True)
        self.assertEqual(serveur._choix_tableau(["Agenda", " loopstr ", ""]),
                         ["agenda", "loopstr"])
        self.assertFalse(serveur._choix_tableau([]))
        self.assertFalse(serveur._choix_tableau("oui"))
        self.assertFalse(serveur._choix_tableau(None))

    def test_jamais_en_clair_sur_le_reseau(self):
        self.assertTrue(serveur._tableau_autorise(_ws("192.168.1.20", "wss"), True))
        self.assertFalse(serveur._tableau_autorise(_ws("192.168.1.20", "ws"), True))
        self.assertFalse(serveur._tableau_autorise(_ws("100.90.80.70", "ws"), ["agenda"]))
        self.assertTrue(serveur._tableau_autorise(_ws("127.0.0.1", "ws"), True))
        self.assertFalse(serveur._tableau_autorise(_ws("127.0.0.1", "wss"), False))
        # Tunnel ngrok : uvicorn reecrit le schema en wss d'apres X-Forwarded-Proto.
        self.assertFalse(serveur._tableau_autorise(
            _ws("127.0.0.1", "wss", {"x-forwarded-proto": "https"}), True))

    def test_filtrer(self):
        self.assertEqual(tdb.filtrer(TUILES, True), TUILES)
        self.assertEqual([t["id"] for t in tdb.filtrer(TUILES, ["loopstr", "AGENDA"])],
                         ["agenda", "loopstr"])
        self.assertEqual(tdb.filtrer(TUILES, []), [])


class AbonnementsTests(unittest.TestCase):
    def setUp(self):
        self._abonnes = list(tdb._ABONNES)
        tdb._ABONNES.clear()

    def tearDown(self):
        tdb._ABONNES.clear()
        tdb._ABONNES.extend(self._abonnes)

    def test_une_seule_boucle_pour_plusieurs_destinataires(self):
        recus_hud, recus_poste = [], []
        with patch.object(tdb, "_EN_COURS", {"thread": None, "assembleur": None}), \
                patch("threading.Thread") as fil:
            self.assertTrue(tdb.demarrer(recus_hud.append, reglages={"actif": True}))
            self.assertTrue(tdb.demarrer(recus_poste.append))
        self.assertEqual(fil.call_count, 1)
        tdb._publier_tous(TUILES)
        self.assertEqual(recus_hud, [TUILES])
        self.assertEqual(recus_poste, [TUILES])

    def test_un_destinataire_en_echec_ne_prive_pas_les_autres(self):
        recus = []
        tdb.abonner(MagicMock(side_effect=RuntimeError("poste parti")))
        tdb.abonner(recus.append)
        tdb._publier_tous(TUILES)
        self.assertEqual(recus, [TUILES])
        tdb.desabonner(recus.append)
        tdb._publier_tous(TUILES)
        self.assertEqual(recus, [TUILES])

    def test_inactif_n_abonne_personne(self):
        with patch.object(tdb, "_EN_COURS", {"thread": None, "assembleur": None}):
            self.assertFalse(tdb.demarrer(print, reglages={"actif": False}))
        self.assertEqual(tdb._ABONNES, [])


class SessionServeurTests(unittest.TestCase):
    """La route /satellite avec le client de test FastAPI (pas de vraie socket)."""

    def _satellites(self, tableau):
        return {"bureau": {"piece": "bureau", "token": "jeton-factice", "wake": "appareil",
                           "priorite_micro": 1.0, "brief_au_demarrage": False,
                           "actions_critiques": True, "tableau": tableau}}

    def _session(self, url, tableau=("agenda", "loopstr")):
        app = FastAPI()
        serveur.monter_routes(app)
        demarrer = MagicMock(return_value=True)
        self.tuiles = list(TUILES)
        correctifs = [
            patch.object(serveur, "_origine_locale_ou_lan", return_value=True),
            patch.object(serveur, "_satellites",
                         return_value=self._satellites(list(tableau) if tableau else False)),
            patch.object(serveur, "statut_affichage", return_value={}),
            patch.object(tdb, "demarrer", demarrer),
            patch.object(tdb, "tuiles", lambda: self.tuiles),
        ]
        for c in correctifs:
            c.start()
            self.addCleanup(c.stop)
        # Un vrai poste du LAN : le refus en clair tient au schema, pas a l'adresse.
        client = TestClient(app, client=("192.168.1.20", 50000))
        return client.websocket_connect(url), demarrer

    def _bonjour(self, ws):
        ws.send_text(json.dumps({"type": "hello", "satellite": "bureau",
                                 "token": "jeton-factice"}))
        return [json.loads(ws.receive_text()) for _ in range(2)]   # pret, etat

    def test_au_repos_le_poste_recoit_ses_tuiles_sur_wss(self):
        connexion, demarrer = self._session("wss://testserver/satellite")
        with connexion as ws:
            debut = self._bonjour(ws)
            tableau = json.loads(ws.receive_text())
        self.assertEqual([m["type"] for m in debut], ["pret", "etat"])
        self.assertEqual(tableau["type"], "tableau")
        self.assertEqual([t["id"] for t in tableau["tuiles"]], ["agenda", "loopstr"])
        demarrer.assert_called_once()

    def test_une_tuile_changee_au_repos_est_poussee(self):
        connexion, demarrer = self._session("wss://testserver/satellite")
        with connexion as ws:
            self._bonjour(ws)
            ws.receive_text()                                    # tuiles initiales
            self.tuiles = [dict(TUILES[0], valeur="3")] + TUILES[1:]
            abonne = demarrer.call_args.args[0]
            abonne(self.tuiles)                                  # depuis la boucle de fond
            pousse = json.loads(ws.receive_text())
        self.assertEqual(pousse["tuiles"][0]["valeur"], "3")

    def test_rien_pendant_une_conversation(self):
        connexion, demarrer = self._session("wss://testserver/satellite")
        reglages = {"satellite_lan.accuse_reveil_vocal": False}
        with patch.object(serveur, "reglage", lambda cle, defaut=None: reglages.get(cle, defaut)), \
                patch("core.arbitrage_micro.reserver_reveil", return_value=True), connexion as ws:
            self._bonjour(ws)
            ws.receive_text()                                    # tuiles initiales
            ws.send_text(json.dumps({"type": "reveil", "score": 1.0, "id": "r1"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "reveil_accepte")
            demarrer.call_args.args[0](TUILES)                  # une tuile change
            ws.send_text(json.dumps({"type": "ping"}))
            suivant = json.loads(ws.receive_text())
        self.assertEqual(suivant["type"], "pong")                # rien ne s'est intercale

    def test_hey_jarvis_sans_question_rend_le_repos(self):
        connexion, demarrer = self._session("wss://testserver/satellite")
        reglages = {"satellite_lan.accuse_reveil_vocal": False}
        with patch.object(serveur, "reglage", lambda cle, defaut=None: reglages.get(cle, defaut)), \
                patch("core.arbitrage_micro.reserver_reveil", return_value=True), connexion as ws:
            self._bonjour(ws)
            ws.receive_text()                                    # tuiles initiales
            ws.send_text(json.dumps({"type": "reveil", "score": 1.0, "id": "r1"}))
            ws.receive_text()                                    # reveil_accepte
            ws.send_text(json.dumps({"type": "reveil_sans_suite"}))
            suite = [json.loads(ws.receive_text()) for _ in range(2)]
        self.assertEqual(suite[0], {"type": "etat", "etat": "veille"})
        self.assertEqual(suite[1]["type"], "tableau")            # les tuiles repartent

    def test_rien_sur_une_connexion_en_clair(self):
        connexion, demarrer = self._session("ws://testserver/satellite")
        with connexion as ws:
            self._bonjour(ws)
            ws.send_text(json.dumps({"type": "ping"}))
            suivant = json.loads(ws.receive_text())
        self.assertEqual(suivant["type"], "pong")                # aucune tuile avant
        demarrer.assert_not_called()

    def test_poste_sans_tableau(self):
        connexion, demarrer = self._session("wss://testserver/satellite", tableau=None)
        with connexion as ws:
            self._bonjour(ws)
            ws.send_text(json.dumps({"type": "ping"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "pong")
        demarrer.assert_not_called()


class RelaisPosteTests(unittest.TestCase):
    def test_le_client_satellite_relaie_le_tableau(self):
        source = (ROOT / "satellite_pi" / "jarvis_satellite.py").read_text(encoding="utf-8")
        self.assertIn('elif t == "tableau":\n                    _evenement("tableau", d)',
                      source)
        # Le message « veille forcee » accompagne veille_forcee, pas chaque releve.
        self.assertIn('_evenement("statut", d)\n                elif', source)
        # « Hey Jarvis » sans question : repos local et prevenir le serveur.
        self.assertIn('_evenement("etat", "veille")\n'
                      '                self.file.put({"type": "reveil_sans_suite"})', source)

    def test_l_agent_transmet_les_tuiles_au_hud(self):
        agent = _charger("jarvis_desktop_agent_tableau", "desktop_agent/jarvis_desktop_agent.py")
        hud, audio = MagicMock(), {}
        agent.brancher_affichage(audio, {}, hud_module=hud)
        audio["_evenement_callback"]("tableau", {"type": "tableau", "tuiles": TUILES[:1]})
        audio["_evenement_callback"]("tableau", "pas un dict")
        hud.tableau.assert_called_once_with(TUILES[:1])


if __name__ == "__main__":
    unittest.main()
