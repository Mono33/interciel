# Tableau de bord dans la fenêtre Jarvis (HUD)

Au repos, la fenêtre Jarvis devient un écran d'information façon Netflix : le
réacteur se range dans le coin en haut à gauche, une grande **bannière** met en
scène ce qui arrive, puis deux **rangées de cartes** montrent le reste (Loopstr,
agenda, réseaux, factures…). Dès que tu parles à Jarvis, le réacteur revient en
grand ; le tableau revient quand il a fini.

## Présentation (direction « Cinéma »)

- **Bannière** : la prochaine échéance Loopstr ou le prochain rendez-vous, en très
  grand, avec son jour, le délai (« dans 2 h ») et les deux suivants. Le fond prend
  la couleur de la source. Sans rien à venir, elle dit « Rien à l'horizon » et
  pourquoi (sources muettes ou non reliées).
- **Jarvis en fond** : le réacteur ne disparaît pas, il recule derrière la bannière,
  en grand et en transparence ; dès que tu parles, il revient au centre, net.
- **Coup d'œil** (dans la bannière) : météo, titre Spotify en cours, colis en route,
  si ces sources sont actives.
- **À venir** : une affiche par échéance ou rendez-vous suivant ; leur nombre
  s'adapte à la largeur, le surplus est regroupé dans une carte « +N ».
- **Réseaux et suivi** : une carte par compte Instagram (abonnés en format court,
  écart depuis le dernier relevé, petite courbe), Discord (mentions, salons les plus
  actifs, serveur nommé une seule fois), factures. Les sources absentes ou en panne
  sont regroupées dans une seule carte « Sources ».
- **Souris** : une carte survolée grossit et s'éclaire, les autres s'estompent ; une
  affiche révèle son délai. Un clic ouvre une vue détaillée animée (Échap ou clic à
  côté pour fermer). Tout est en lecture seule.
- **Écrans** : pensé pour 1920×1080 ; au-delà de 2200×1200 tout grandit d'un quart ;
  en portrait (écran vertical), les cartes passent sur deux colonnes ; dans une
  fenêtre étroite (moins de 700 px), boutons en icônes, une carte par ligne et
  défilement vertical.
- **Mises à jour** : les cartes sont mises à jour en place, les chiffres s'animent.
  Quand `hud.html` change sur le disque, la fenêtre ouverte se recharge d'elle-même
  dans les 15 secondes, sans redémarrer Jarvis.

Tout se passe dans la **même fenêtre** que le réacteur (`hud.html`, servie sur
`http://127.0.0.1:8770`). Le cockpit (`/cockpit`) reste une page à part.

## Comportement

| État de Jarvis | Affichage |
|---|---|
| `veille` (ou micro coupé), depuis au moins 2 s | réacteur dans le coin + tuiles |
| `ecoute`, `reflexion`, `parole` | réacteur en grand, tout de suite |

- Les 2 s de veille continue évitent que le tableau clignote entre deux phrases
  d'une même réponse.
- Le bouton **▦ Tableau de bord**, à côté de **⚙ Config**, agit tout de suite,
  quel que soit l'état de Jarvis : il affiche le tableau (Jarvis revient en grand
  dès que tu lui reparles, puis le tableau revient au repos) ou le masque (il ne
  revient plus au repos jusqu'au prochain clic). Ce choix est retenu par la
  fenêtre (stockage local du navigateur).
- Un « Hey Jarvis » sans question remet la fenêtre au repos, et donc le tableau.
- Le bouton n'apparaît que lorsque des tuiles existent : sans réglage, la fenêtre
  reste exactement comme avant.

## Activer

Dans la section `hud:` de la configuration du processus qui affiche la fenêtre :

```yaml
hud:
  tableau_de_bord:
    actif: true
    sources: [agenda, factures, loopstr, instagram, discord]   # ordre d'affichage
    factures_minutes: 60
```

Sources disponibles :

| Source | Contenu de la tuile | Relecture |
|---|---|---|
| `agenda` | événements des 7 prochains jours (hors agenda Loopstr) | 10 min |
| `factures` | factures impayées et en retard, montant à encaisser | `factures_minutes` (60 par défaut, 15 au minimum) |
| `loopstr` | échéances de partenariats sur 14 jours | 15 min |
| `instagram` | abonnés de chaque compte et variation depuis le dernier relevé | 1 h |
| `discord` | mentions, messages et salons actifs des dernières 24 h | 30 min |
| `colis` (facultatif) | colis en route d'après les mails | 30 min |
| `meteo` (facultatif) | température, ciel, min/max, risque de pluie | 30 min |
| `spotify` (facultatif) | titre en cours et appareil | 1 min |

Une source non configurée affiche « Non configuré » ; une source en échec affiche
« Indisponible » (avec l'heure de la dernière lecture réussie) sans toucher aux
autres tuiles. Elle est retentée au bout de 5 minutes.

Pour voir le rendu sans aucune donnée réelle : `uv run python hud.py` joue un
scénario de démonstration avec des tuiles factices.

### Forme d'une tuile

Chaque source produit une tuile `{id, titre, etat, maj, valeur, legende, lignes,
message}` (résumé lisible par n'importe quelle fenêtre) et, si elle en a, un champ
facultatif `donnees` que la nouvelle présentation exploite : `echeances` (Loopstr :
date, marque, tâche), `evenements` (agenda), `comptes` (Instagram : abonnés, écart,
série de relevés), `serveurs` (Discord : salons regroupés par serveur), montants
des factures, météo, colis, titre Spotify. Les émojis décoratifs des noms
(« 🚀 Marque — Publication », « 💬・général ») sont retirés côté serveur. Une
fenêtre récente sait aussi afficher les tuiles d'un serveur plus ancien, sans
`donnees`.

## Sécurité et confidentialité

- **Lecture seule (N1)** : aucune tuile ne déclenche d'action. Le tableau n'est pas
  un outil (`@outil`) : il n'est ni appelable par le modèle, ni exposé au MCP.
- La page reste servie sur `127.0.0.1` uniquement, avec le contrôle d'hôte local
  existant. Rien ne passe par le tunnel ngrok.
- Les tuiles ne contiennent que des résumés : compteurs, titres courts, montants.
  Jamais de jeton, ni de texte brut de facture, ni de contenu de message Discord.
- Les erreurs sont journalisées par leur type seulement (un message d'erreur
  d'API peut contenir une URL à jeton).
- Le texte des tuiles est inséré comme du texte (`textContent`) : un titre
  d'agenda ou un nom de salon ne peut pas injecter de HTML.

### Particularités de certaines sources

- **Factures** : facture.net est lu dans le Chrome dédié (« Chrome + Jarvis »)
  **déjà ouvert**. Le tableau ne lance jamais Chrome lui-même : s'il est fermé, la
  tuile le dit. La lecture tourne dans un **processus séparé** (son propre
  Playwright, pour ne pas gêner les outils navigateur déclenchés à la voix) et
  ouvre un onglet en arrière-plan, refermé aussitôt. Selon Chrome, cet onglet peut
  apparaître brièvement à chaque relecture : augmente `factures_minutes` si c'est
  gênant. Si la lecture dépasse 90 s (cas anormal), le processus est arrêté et
  l'onglet peut rester ouvert : ferme-le à la main. Seuls des compteurs et des
  montants sortent de ce processus. La
  reconnaissance des indicateurs (« impayées », « en retard ») est volontairement
  simple : si rien n'est reconnu, la tuile renvoie vers la question vocale « où en
  sont mes factures ».
- **Agenda** : le tableau n'ouvre jamais la fenêtre d'autorisation Google. Sans
  jeton valide, la tuile affiche « à reconnecter » ; une question vocale sur
  l'agenda relance l'autorisation.
- **Instagram** : un seul appel par compte (nombre d'abonnés). La variation est
  calculée par rapport à l'historique de `instagram_resume`, qui n'est jamais
  modifié par le tableau.

## Où tournent les sources

Les sources lisent la configuration **du processus qui affiche la fenêtre** :

- **Jarvis en local** (`jarvis14.py` sur le PC) : `config.yaml`, toutes les
  sources sont disponibles.
- **Serveur H24 + agent du bureau** : la fenêtre est ouverte par l'agent du
  bureau, qui lit `desktop_agent/config.yaml`. Ce fichier ne contient que des
  réglages matériels, donc seules les sources sans identifiants serveur peuvent y
  fonctionner : `meteo`, et `factures` si tu y ajoutes `factures.url` (Chrome +
  Jarvis tourne sur ce PC). Exemple, dans `desktop_agent/config.yaml` :

  ```yaml
  hud:
    tableau_de_bord:
      actif: true
      sources: [factures, meteo]
  factures:
    url: "https://…"            # ton tableau de bord facture.net
  ```

  L'agenda, Loopstr, Instagram et Discord sont configurés sur le serveur H24 :
  leurs tuiles arrivent par le transport décrit ci-dessous.

## Tuiles du serveur H24 vers le bureau

Le serveur lit lui-même ses sources et envoie les tuiles à la fenêtre du bureau
sur la connexion satellite déjà authentifiée par jeton, comme le relevé `statut`
(modèle, routage, budget) :

- au retour en `veille`, puis à chaque tuile changée tant que Jarvis reste au
  repos : jamais pendant une conversation, rien ne s'intercale dans l'audio ;
- uniquement depuis le cache des sources : un envoi ne déclenche aucune lecture ;
- uniquement aux postes qui le demandent (`satellites[].tableau`), jamais aux
  Raspberry Pi ;
- **uniquement sur une connexion chiffrée `wss://`** (listener TLS du LAN, port
  8792, lot 3 de sécurité) ou interne à la machine. Sur `ws://` en clair, rien
  n'est envoyé et le serveur l'écrit dans son journal : c'est le comportement
  attendu tant que le bureau n'est pas passé en `wss://`, pas une panne. Un poste
  qui arrive par Tailscale en `wss://` reçoit aussi ses tuiles (canal chiffré,
  lecture seule). Le tunnel ngrok reste refusé par la route satellite elle-même.

Le client satellite relaie le message à la fenêtre (`_evenement("tableau", …)`),
et l'agent du bureau le passe à `hud.tableau()`. Les tuiles sont fusionnées par
identifiant avec celles lues sur le PC.

### Mise en place

1. Sur **H24**, dans `config.yaml` : les sources lues par le serveur, et le poste
   qui les reçoit.

   ```yaml
   hud:
     tableau_de_bord:
       actif: true
       sources: [agenda, loopstr, instagram, discord]
   satellites:
     - id: "bureau"
       # … piece, token, etc. inchangés
       tableau: [agenda, loopstr, instagram, discord]
   ```

   Ne liste pas `factures` côté serveur : facture.net se lit dans le Chrome du
   PC, et deux tuiles de même identifiant se remplaceraient l'une l'autre.

2. Sur le **PC de bureau**, dans `desktop_agent/config.yaml` : passer le micro en
   `wss://` sur le port 8792 avec le certificat du serveur (voir
   `docs/satellite.md`, lot 3). Tant que `pc_url` reste en `ws://`, les tuiles
   du serveur n'arrivent pas, ce qui est voulu.

3. Redémarrer Jarvis sur H24 puis l'agent du bureau.

Les tuiles lues sur le PC (factures, météo) arrivent en général avant celles du
serveur : elles s'affichent donc en premier dans la fenêtre.

## Fichiers

- `hud.html` : bascule compacte/grande, tuiles, bouton.
- `hud.py` : `tableau(tuiles)` (fusion par identifiant, relecture à la connexion)
  et démarrage du tableau local dans `demarrer()`.
- `core/tableau_de_bord.py` : sources, délais, boucle de fond, destinataires.
- `core/tableau_factures.py` : lecture isolée de facture.net.
- `core/satellite.py` : envoi des tuiles au poste (option `tableau`, `wss://`).
- `satellite_pi/jarvis_satellite.py`, `desktop_agent/jarvis_desktop_agent.py` :
  relais jusqu'à la fenêtre du bureau.
- `tests/test_tableau_de_bord.py` : tests hors réseau, dont la bascule de la page
  dans un Chromium headless (ignorée si Chromium n'est pas installé).
- `tests/test_tableau_transport.py` : transport serveur → bureau (refus en clair,
  envoi au repos, relais).
