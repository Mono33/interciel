"""Suivi Shop : lecture des cartes de commande, resume, et branchement aux colis.

Aucun navigateur n'est lance : la lecture reelle est simulee. Les cartes ci-dessous
reprennent le format reel de shop.com/orders avec des boutiques fictives.
"""
import datetime as dt
import unittest
from pathlib import Path
from unittest.mock import patch

from core import poste_distant, registre
from tools import colis, shop

ROOT = Path(__file__).resolve().parents[1]
AUJOURDHUI = dt.date(2026, 10, 15)


class CarteTest(unittest.TestCase):
    def test_cartes_reelles_de_commandes_passees(self):
        # Format reel des cartes, valeurs entierement inventees (boutiques, accroches,
        # montants et dates) : aucune donnee d'achat reelle dans un depot public.
        cas = {
            "Boutique A - Accessoires et petits cadeaux Livré le 3 mars 2 articles · 19,90 € 1er mars":
                ("Boutique A", "Livré le 3 mars", "", "livre"),
            "boutique-b.example Livré le 6 juin +1 3 articles · 42,00 € 2 juin":
                ("boutique-b.example", "Livré le 6 juin", "", "livre"),
            "Studio C Store - EU Commandé le 12 avr. 1 article · 75,00 € 12 avr.":
                ("Studio C Store", "Commandé le 12 avr.", "", "commande"),
            "Boutique E Commandé le 1er févr. 1 article 1er févr.":
                ("Boutique E", "Commandé le 1er févr.", "", "commande"),
        }
        for texte, attendu in cas.items():
            with self.subTest(texte=texte[:30]):
                c = shop.analyser_carte(texte)
                self.assertEqual((c["marchand"], c["statut"], c["transporteur"], c["etape"]), attendu)

    def test_commandes_en_route(self):
        c = shop.analyser_carte("Boutique D Arrive le 17 oct. UPS Standard 1 article · 20,00 € 12 oct.")
        self.assertEqual((c["marchand"], c["statut"], c["transporteur"], c["etape"]),
                         ("Boutique D", "Arrive le 17 oct.", "UPS Standard", "expedie"))
        self.assertEqual(shop.analyser_carte("Boutique E En cours de livraison 1 article")["etape"],
                         "livraison")
        self.assertIsNone(shop.analyser_carte("Rien à afficher ici"))

    def test_date_des_statuts(self):
        self.assertEqual(shop.date_du_statut("Livré le 14 oct.", AUJOURDHUI), dt.date(2026, 10, 14))
        self.assertEqual(shop.date_du_statut("Livré le 1er févr.", AUJOURDHUI), dt.date(2026, 2, 1))
        self.assertEqual(shop.date_du_statut("Livré le 20 déc.", AUJOURDHUI), dt.date(2025, 12, 20))
        self.assertIsNone(shop.date_du_statut("En transit", AUJOURDHUI))

    def test_page_de_connexion(self):
        self.assertTrue(shop.page_de_connexion("https://shop.app/accounts/login?x=1", ""))
        self.assertTrue(shop.page_de_connexion("https://shop.com/orders",
                                               "Connectez-vous pour suivre vos commandes"))
        self.assertFalse(shop.page_de_connexion("https://shop.com/orders", "Commandes en cours " * 80))


class ResumeTest(unittest.TestCase):
    def test_resume_et_silence_du_brief(self):
        rien = {"en_cours": [], "livrees": []}
        self.assertEqual(shop.resumer(rien), "Shop : aucune commande en cours.")
        self.assertEqual(shop.resumer(rien, bref=True), "")
        commandes = {"en_cours": [shop.analyser_carte("Boutique D Arrive le 17 oct. UPS Standard 1 article")],
                     "livrees": [shop.analyser_carte("Boutique A Livré le 14 oct. 1 article")]}
        self.assertEqual(shop.resumer(commandes, bref=True),
                         "Shop : Boutique D : arrive le 17 oct. (UPS Standard) et Boutique A : livré le 14 oct.")

    def test_erreurs_silencieuses_dans_le_brief(self):
        for erreur in (FileNotFoundError(), PermissionError(), TimeoutError()):
            with self.subTest(erreur=type(erreur).__name__), \
                    patch.object(shop, "lire_commandes", side_effect=erreur):
                self.assertEqual(shop.commandes_shop(bref=True), "")
                self.assertTrue(shop.commandes_shop().startswith(shop.PREFIXE))
        with patch.object(shop, "lire_commandes", side_effect=PermissionError()):
            self.assertIn("Connexion Shop", shop.commandes_shop())

    def test_sans_profil_aucun_navigateur(self):
        with patch.object(shop, "profil", return_value=Path("C:/inexistant/profil_shop")):
            with self.assertRaises(FileNotFoundError):
                shop.lire_commandes()


class BranchementTest(unittest.TestCase):
    def test_outil_execute_sur_le_poste_principal_et_balise(self):
        registre.charger_outils()
        self.assertIn("commandes_shop", poste_distant.OUTILS_POSTE)
        self.assertIn("commandes_shop", registre.SOURCES_EXTERNES)
        outil = registre.get("commandes_shop")
        self.assertFalse(outil.confirmation)
        self.assertFalse(outil.mcp_expose)

    def _shop_distant(self, disponible, reponse, bref):
        with patch.object(colis, "reglage", lambda cle, defaut=None: defaut), \
                patch.object(poste_distant, "_principal", return_value="bureau"), \
                patch.object(poste_distant, "agent_disponible", return_value=disponible), \
                patch.object(poste_distant, "executer_outil_principal", return_value=reponse) as appel:
            return colis._partie_shop(bref=bref), appel

    def test_pc_eteint(self):
        texte, appel = self._shop_distant(False, None, bref=True)
        self.assertEqual(texte, "")
        appel.assert_not_called()
        texte, _ = self._shop_distant(False, None, bref=False)
        self.assertIn("éteint", texte)

    def test_reponse_du_pc_et_erreur_de_transport(self):
        texte, appel = self._shop_distant(True, "Shop : Boutique D : arrive le 17 oct..", bref=True)
        self.assertEqual(texte, "Shop : Boutique D : arrive le 17 oct..")
        self.assertEqual(appel.call_args.args[:2], ("commandes_shop", {"bref": True}))
        self.assertLessEqual(appel.call_args.kwargs["timeout"], 30.0)
        texte, _ = self._shop_distant(True, "Le poste bureau ne répond pas.", bref=True)
        self.assertEqual(texte, "")

    def test_brief_et_suivi_combinent_mails_et_shop(self):
        trouves = [{"marchand": "Vinted", "etape": "relais"}]
        with patch.object(colis.mail, "_mail_configure", return_value=True), \
                patch.object(colis, "colis_en_cours", return_value=trouves), \
                patch.object(colis, "_partie_shop", return_value="Shop : Boutique D : arrive le 17 oct.."):
            self.assertEqual(colis.colis_du_brief(),
                             "Côté colis : Vinted t'attend en point relais. Shop : Boutique D : arrive le 17 oct..")
            self.assertIn("Shop : Boutique D", colis.suivi_colis())

    def test_identifiants_jamais_saisis(self):
        source = (ROOT / "tools" / "shop.py").read_text(encoding="utf-8")
        for interdit in (".fill(", ".type(", ".click(", "press("):
            self.assertNotIn(interdit, source)


if __name__ == "__main__":
    unittest.main()
