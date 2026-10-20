"""Correctifs de l'audit de securite (lot 3) : TLS epingle pour le LAN, jeton MCP."""
import asyncio
import shutil
import socket
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import tls_lan
from satellite_pi.jarvis_satellite import contexte_tls


def _certificat_dans(dossier):
    with patch.object(tls_lan, "DOSSIER", dossier), \
            patch.object(tls_lan, "CERTIFICAT", dossier / "jarvis-lan.crt"), \
            patch.object(tls_lan, "CLE", dossier / "jarvis-lan.key"):
        return tls_lan.assurer_certificat()


def _serveur_tls(certificat, cle):
    """Petit serveur TLS local qui renvoie « ok » : (port, fil). Meme contexte que
    le listener LAN de production."""
    contexte = tls_lan.contexte_serveur(certificat, cle)
    ecoute = socket.socket()
    ecoute.bind(("127.0.0.1", 0))
    ecoute.listen(1)
    ecoute.settimeout(10)

    def servir():
        try:
            brut, _ = ecoute.accept()
            with contexte.wrap_socket(brut, server_side=True) as s:
                s.sendall(b"ok")
        except (ssl.SSLError, OSError):
            pass
        finally:
            ecoute.close()

    fil = threading.Thread(target=servir, daemon=True)
    fil.start()
    return ecoute.getsockname()[1], fil


def _dialoguer(port, contexte):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as brut:
        with contexte.wrap_socket(brut) as s:
            return s.recv(2)


class TlsLanTest(unittest.TestCase):
    def setUp(self):
        self.dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)

    def test_certificat_cree_une_seule_fois(self):
        certificat, cle = _certificat_dans(self.dossier / "a")
        premier = certificat.read_bytes()
        self.assertEqual(_certificat_dans(self.dossier / "a")[0].read_bytes(), premier)
        self.assertIn(b"PRIVATE KEY", cle.read_bytes())

    def test_le_client_accepte_le_vrai_serveur(self):
        certificat, cle = _certificat_dans(self.dossier / "a")
        port, fil = _serveur_tls(certificat, cle)
        contexte = contexte_tls("wss://192.168.1.10:8792/satellite", str(certificat))
        self.assertEqual(_dialoguer(port, contexte), b"ok")
        fil.join(5)

    def test_le_client_refuse_un_imposteur(self):
        vrai, _ = _certificat_dans(self.dossier / "vrai")
        faux, faux_cle = _certificat_dans(self.dossier / "faux")
        port, fil = _serveur_tls(faux, faux_cle)
        contexte = contexte_tls("wss://192.168.1.10:8792/satellite", str(vrai))
        with self.assertRaises(ssl.SSLError):
            _dialoguer(port, contexte)
        fil.join(5)

    def test_ws_reste_possible_et_wss_exige_un_certificat(self):
        self.assertIsNone(contexte_tls("ws://192.168.1.10:8791/satellite", None))
        with self.assertRaises(ValueError):
            contexte_tls("wss://192.168.1.10:8792/satellite", "")

    def test_serveur_ouvre_le_port_chiffre(self):
        source = (Path(__file__).resolve().parents[1] / "core" / "satellite.py").read_text(encoding="utf-8")
        self.assertIn('reglage("satellite_lan.tls", True)', source)
        self.assertIn("ssl_certfile=str(certificat), ssl_keyfile=str(cle)", source)


class TruststoreTest(unittest.TestCase):
    """Jarvis injecte truststore dans tout le processus : le TLS du LAN doit tenir."""

    def setUp(self):
        import truststore
        self.dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        if ssl.SSLContext is tls_lan.classe_ssl():          # pas deja injecte
            truststore.inject_into_ssl()
            self.addCleanup(truststore.extract_from_ssl)

    def test_dialogue_epingle_apres_injection(self):
        self.assertIsNot(ssl.SSLContext, tls_lan.classe_ssl())
        certificat, cle = _certificat_dans(self.dossier / "a")
        port, fil = _serveur_tls(certificat, cle)
        contexte = contexte_tls("wss://192.168.1.10:8792/satellite", str(certificat))
        self.assertIs(type(contexte), tls_lan.classe_ssl())
        self.assertEqual(contexte.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(_dialoguer(port, contexte), b"ok")
        fil.join(5)

    def test_imposteur_toujours_refuse_apres_injection(self):
        vrai, _ = _certificat_dans(self.dossier / "vrai")
        faux, faux_cle = _certificat_dans(self.dossier / "faux")
        port, fil = _serveur_tls(faux, faux_cle)
        contexte = contexte_tls("wss://192.168.1.10:8792/satellite", str(vrai))
        with self.assertRaises(ssl.SSLError):
            _dialoguer(port, contexte)
        fil.join(5)

    def test_le_listener_lan_utilise_le_contexte_d_origine(self):
        from unittest.mock import MagicMock
        from core import satellite
        certificat, cle = _certificat_dans(self.dossier / "a")
        reglages = {"satellite_lan.clair": False, "satellite_lan.accuse_reveil_vocal": False}
        configs = []
        with patch.object(satellite, "_satellites", return_value={"bureau": {}}), \
                patch.object(satellite, "reglage",
                             lambda cle_, defaut=None: reglages.get(cle_, defaut)), \
                patch.object(satellite, "_SERVEUR_LAN", None), \
                patch.object(tls_lan, "assurer_certificat", return_value=(certificat, cle)), \
                patch("uvicorn.Server", side_effect=lambda config: configs.append(config)), \
                patch.object(satellite.threading, "Thread", return_value=MagicMock()), \
                patch.object(satellite.socket, "create_connection", return_value=MagicMock()):
            satellite.demarrer_lan()                  # ne doit plus lever NameError
        self.assertEqual([c.port for c in configs], [8792])
        self.assertIs(type(configs[0].ssl), tls_lan.classe_ssl())


class JetonMcpTest(unittest.TestCase):
    def _appel(self, entetes):
        from jarvis.mcp_server import _ExigerJeton
        recu = []

        async def app(scope, receive, send):
            recu.append(scope["type"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        envois = []

        async def send(message):
            envois.append(message)

        scope = {"type": "http", "headers": entetes}
        asyncio.run(_ExigerJeton(app, "secret-de-test")(scope, None, send))
        return envois[0]["status"], recu

    def test_jeton_exige(self):
        self.assertEqual(self._appel([]), (401, []))
        self.assertEqual(self._appel([(b"authorization", b"Bearer faux")]), (401, []))
        self.assertEqual(self._appel([(b"authorization", b"Bearer secret-de-test")]), (200, ["http"]))

    def test_refus_hors_loopback_sans_jeton(self):
        from jarvis import mcp_server
        reglages = {"mcp.host": "0.0.0.0", "mcp.port": 8765}
        with patch.object(mcp_server, "reglage", lambda cle, defaut=None: reglages.get(cle, defaut)), \
                patch.dict("os.environ", {}, clear=False) as env, \
                patch.object(mcp_server.server, "run") as run:
            env.pop("JARVIS_MCP_TOKEN", None)
            with self.assertRaises(SystemExit):
                mcp_server._lancer_http("streamable-http")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
