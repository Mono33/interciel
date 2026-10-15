"""Micro des satellites et de l'agent Windows : choix par nom, reprise apres panne.

Panne reelle : le micro Bluetooth par defaut a disparu (PortAudio « There is no
driver installed », MME error 6), le fil d'ecoute est mort et Jarvis est reste
connecte mais sourd. Et un emoji dans une reponse faisait planter le journal.
"""
import importlib.util
import io
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jarvis_satellite_micro_test",
                                               ROOT / "satellite_pi" / "jarvis_satellite.py")
sat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sat)

MME, WASAPI = 0, 1
APPAREILS = [
    {"name": "Mappeur de sons Microsoft - Input", "hostapi": MME, "max_input_channels": 2, "default_samplerate": 44100},
    {"name": "Microphone sur casque (Bose Rev", "hostapi": MME, "max_input_channels": 1, "default_samplerate": 16000},
    {"name": "Microphone (Shure MVX2U)", "hostapi": MME, "max_input_channels": 1, "default_samplerate": 16000},
    {"name": "MOTIV Mix Virtual Output (Shure", "hostapi": MME, "max_input_channels": 2, "default_samplerate": 16000},
    {"name": "MOTIV Mix Virtual Output (Shure MOTIV Mix)", "hostapi": WASAPI, "max_input_channels": 2, "default_samplerate": 48000},
    {"name": "Haut-parleurs (Bose Revolve+ II)", "hostapi": MME, "max_input_channels": 0, "default_samplerate": 44100},
]


class PortAudioError(Exception):
    pass


class FluxFactice:
    """Faux flux d'entree : rejoue une suite de blocs ou d'exceptions."""
    ouverts = []

    def __init__(self, scenario, micro_ref, **options):
        self.scenario = list(scenario)
        self.micro_ref = micro_ref
        self.options = options
        self.ferme = False
        FluxFactice.ouverts.append(self)

    def start(self):
        pass

    def read(self, n):
        if not self.scenario:
            self.micro_ref[0].stop.set()
            return np.full((n, 1), 0.01, dtype=np.float32), False
        etape = self.scenario.pop(0)
        if isinstance(etape, Exception):
            raise etape
        return np.full((n, 1), etape, dtype=np.float32), False

    def stop(self):
        pass

    def close(self):
        self.ferme = True


def _faux_sd(scenarios, micro_ref, ouverture_ratee=0):
    """Module sounddevice factice : chaque ouverture consomme un scenario."""
    restant = {"ratees": ouverture_ratee}

    def query_devices(device=None, kind=None):
        if device is None and kind is None:
            return APPAREILS
        if device is None:
            return {"name": "defaut", "default_samplerate": 16000}
        return APPAREILS[device]

    def input_stream(**options):
        if restant["ratees"]:
            restant["ratees"] -= 1
            raise PortAudioError("There is no driver installed on your system.")
        return FluxFactice(scenarios.pop(0) if scenarios else [], micro_ref, **options)

    return SimpleNamespace(query_devices=query_devices, default=SimpleNamespace(hostapi=MME),
                           InputStream=input_stream, PortAudioError=PortAudioError)


class ReveilFactice:
    def predict(self, _):
        return {"hey_jarvis": 0.0}

    def reset(self):
        pass


def _lancer(choix, scenarios, ouverture_ratee=0, secondes_muet=None):
    FluxFactice.ouverts = []
    micro_ref = [None]
    faux = _faux_sd(scenarios, micro_ref, ouverture_ratee)
    with patch.object(sat, "sd", faux), \
            patch.object(sat, "_silence_numerique", return_value=False), \
            patch.object(sat, "_reinitialiser_portaudio") as reinit, \
            patch.object(sat, "CONF", {}):
        micro = sat.Micro({"micro": choix}, None, threading.Event())
        micro_ref[0] = micro
        micro._reveil = ReveilFactice
        if secondes_muet is not None:
            micro.SECONDES_MUET_MAX = secondes_muet
        fil = threading.Thread(target=micro.run, daemon=True)
        fil.start()
        fil.join(10)
    return micro, fil, reinit


class ResolutionMicroTest(unittest.TestCase):
    def _resoudre(self, choix, muets=()):
        faux = _faux_sd([], [None])
        with patch.object(sat, "sd", faux), \
                patch.object(sat, "_silence_numerique", side_effect=lambda i: i in muets):
            return sat._resoudre_micro(choix)

    def test_defaut_et_index_inchanges(self):
        self.assertIsNone(self._resoudre(None))
        self.assertEqual(self._resoudre(2), 2)

    def test_nom_cherche_dans_l_api_par_defaut_seulement(self):
        # Le meme micro existe aussi sous WASAPI : on garde l'entree MME.
        self.assertEqual(self._resoudre("MOTIV Mix"), 3)
        self.assertEqual(self._resoudre("motiv mix"), 3)

    def test_liste_par_ordre_de_preference(self):
        self.assertEqual(self._resoudre(["Absent", "Shure MVX2U"]), 2)
        self.assertEqual(self._resoudre(["MOTIV Mix", "Shure MVX2U"]), 3)

    def test_un_micro_muet_cede_la_place_au_suivant(self):
        self.assertEqual(self._resoudre(["MOTIV Mix", "Shure MVX2U"], muets={3}), 2)
        # Le dernier candidat est garde meme muet : mieux qu'un micro au hasard.
        self.assertEqual(self._resoudre(["MOTIV Mix"], muets={3}), 3)

    def test_aucun_nom_trouve_micro_par_defaut(self):
        self.assertIsNone(self._resoudre(["Inexistant"]))

    def test_haut_parleur_jamais_pris_pour_un_micro(self):
        self.assertIsNone(self._resoudre("Haut-parleurs (Bose"))


class RepriseMicroTest(unittest.TestCase):
    def test_micro_qui_disparait_est_rouvert(self):
        panne = PortAudioError("There is no driver installed on your system.")
        micro, fil, reinit = _lancer(None, [[0.01, 0.01, panne], [0.01, 0.01]])
        self.assertFalse(fil.is_alive(), "le fil d'ecoute doit s'arreter proprement")
        self.assertEqual(len(FluxFactice.ouverts), 2)          # rouvert apres la panne
        self.assertTrue(all(f.ferme for f in FluxFactice.ouverts))
        reinit.assert_not_called()                              # la reouverture a suffi

    def test_un_echec_d_ouverture_reessaie_sans_relire_les_peripheriques(self):
        # Relire la liste (sd._terminate) couperait un son en cours : pas au 1er echec.
        micro, fil, reinit = _lancer(None, [[0.01]], ouverture_ratee=1)
        self.assertFalse(fil.is_alive())
        reinit.assert_not_called()
        self.assertEqual(len(FluxFactice.ouverts), 1)

    def test_deux_echecs_de_suite_relisent_les_peripheriques(self):
        micro, fil, reinit = _lancer(None, [[0.01]], ouverture_ratee=2)
        self.assertFalse(fil.is_alive())
        self.assertEqual(reinit.call_count, 1)
        self.assertEqual(len(FluxFactice.ouverts), 1)

    def test_jamais_de_relecture_pendant_une_reponse(self):
        sat.LECTURE_EN_COURS.set()
        try:
            micro, fil, reinit = _lancer(None, [[0.01]], ouverture_ratee=2)
        finally:
            sat.LECTURE_EN_COURS.clear()
        self.assertFalse(fil.is_alive())
        reinit.assert_not_called()

    def test_lecture_signale_qu_un_son_est_en_cours(self):
        vu = []
        with patch.object(sat, "_jouer_pcm", side_effect=lambda *a: vu.append(sat.LECTURE_EN_COURS.is_set())):
            sat._jouer(b"\0\0", 16000)
        self.assertEqual(vu, [True])
        self.assertFalse(sat.LECTURE_EN_COURS.is_set())

    def test_silence_numerique_prolonge_passe_au_micro_suivant(self):
        # 0.2 s -> 2 blocs de 80 ms : au 3e bloc muet, on rouvre.
        micro, fil, _ = _lancer(["MOTIV Mix", "Shure MVX2U"],
                                [[0.0, 0.0, 0.0, 0.0], [0.01, 0.01]], secondes_muet=0.2)
        self.assertFalse(fil.is_alive())
        self.assertEqual(len(FluxFactice.ouverts), 2)

    def test_un_seul_micro_n_est_pas_surveille(self):
        micro, fil, _ = _lancer("Shure MVX2U", [[0.0] * 6], secondes_muet=0.2)
        self.assertFalse(fil.is_alive())
        self.assertEqual(len(FluxFactice.ouverts), 1)


class EncodageTest(unittest.TestCase):
    def test_emoji_dans_un_journal_cp1252(self):
        journal = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with patch.object(sys, "stdout", journal), patch.object(sys, "stderr", journal):
            sat.sorties_utf8()
            print("  [réponse] Ton brief 🎬 est prêt 🚀")
            journal.flush()
            contenu = journal.buffer.getvalue().decode("utf-8")
        self.assertIn("🎬", contenu)

    def test_lecture_audio_hors_de_la_boucle_reseau(self):
        # Panne reelle : brief de 45 s lu dans la boucle -> pings sans reponse ->
        # « 1011 keepalive ping timeout », connexion coupee en pleine reponse.
        source = (ROOT / "satellite_pi" / "jarvis_satellite.py").read_text(encoding="utf-8")
        self.assertIn("await asyncio.to_thread(_jouer, bytes(audio), freq)", source)
        self.assertNotIn("_jouer(bytes(audio), freq); audio = bytearray()", source)

    def test_agent_windows_en_utf8(self):
        source = (ROOT / "desktop_agent" / "jarvis_desktop_agent.py").read_text(encoding="utf-8")
        self.assertIn('flux.reconfigure(encoding="utf-8", errors="replace")', source)


if __name__ == "__main__":
    unittest.main()
