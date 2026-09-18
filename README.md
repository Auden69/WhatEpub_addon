# WhatEpub — add-on Calibre

Plugin Calibre qui envoie les métadonnées et la **signature** de chaque epub
de ta bibliothèque vers [WhatEpub](https://whatepub.com), qui identifie
l'œuvre et l'auteur (même sans ISBN, même avec des métadonnées absentes ou
fausses) et te renvoie le résultat. C'est un client parmi d'autres de l'API
WhatEpub — pas le seul moyen d'y accéder.

## Installation

1. Télécharge `WhatEpub.zip` depuis la [dernière release](https://github.com/Auden69/WhatEpub_addon/releases/latest).
2. Dans Calibre : **Préférences → Plugins → Charger un plugin depuis un fichier**, sélectionne le zip.
3. Redémarre Calibre.

Nécessite Calibre 5.0 ou supérieur.

L'interface du plugin suit la langue configurée dans Calibre
(**Préférences → Interface → Look & feel**) : français par défaut,
anglais si Calibre est en anglais (`translations/en.po`/`.mo`).

## Configuration

1. Crée un compte sur [whatepub.com](https://whatepub.com) et génère une clé API depuis "Mes clés".
2. Dans Calibre : **Préférences → Plugins → WhatEpub → Personnaliser le plugin**, colle la clé.
3. Une clé par bibliothèque Calibre (une par PC/installation) — ne réutilise pas la même clé sur plusieurs bibliothèques.

Les autres réglages disponibles :

| Paramètre | Défaut | Rôle |
|---|---|---|
| Scan/push (minutes) | 20 | Fréquence d'envoi des livres nouveaux/modifiés |
| Poll résultats (minutes) | 3 | Fréquence de relecture du statut des livres déjà envoyés |
| Taille de batch | 50 | Nombre de livres par requête d'envoi |
| Plafond livres/cycle de scan | 200 (0 = illimité) | Limite combien de livres nouveaux/modifiés sont poussés par cycle — évite qu'un premier scan sur une grosse bibliothèque jamais synchronisée pousse tout d'un coup. À 200/cycle et 20 min entre cycles, ~60 000 livres jamais synchronisés s'écoulent en un peu plus de 4 jours. Chaque cycle reprend là où le précédent s'est arrêté. |
| Synchro retour auto (minutes) | 0 (désactivé) | Applique automatiquement, **sans confirmation**, les métadonnées WhatEpub sur cette bibliothèque à l'intervalle choisi — voir "Synchro retour" ci-dessous avant d'activer |

L'URL du serveur n'est pas configurable — le plugin ne parle qu'à l'instance
officielle WhatEpub (`api.whatepub.com`).

## Fonctionnement

Le plugin tourne uniquement quand Calibre est ouvert (pas de daemon séparé) :

- **Scan/push** : détecte les livres nouveaux ou modifiés (diff par empreinte
  de contenu), calcule leur signature epub et les envoie par lots.
- **Poll** : relit périodiquement le statut des livres envoyés pour savoir
  s'ils ont été résolus.
- **Synchroniser maintenant** (bouton dans la barre d'outils, ou menu) :
  déclenche un cycle scan + poll immédiatement, sans attendre le timer.

En cas d'échec répété du serveur, l'intervalle d'envoi s'espace
automatiquement (backoff) plutôt que de marteler un serveur down.

## Synchro retour (WhatEpub → Calibre)

Une fois un livre résolu côté serveur, ses métadonnées peuvent être
réappliquées sur ta bibliothèque Calibre — toujours via l'API Calibre
officielle (`set_metadata`), jamais un accès direct à `metadata.db`.
**Toujours une action explicite, jamais automatique** : la résolution seule
ne modifie rien localement.

- **Vérifier le livre sélectionné** : interroge le serveur par signature
  epub, affiche ce qu'il a trouvé, et propose un bouton "Mettre à jour mes
  métadonnées" pour l'appliquer à CE livre (titre, auteur, série, langue,
  année, résumé, identifiants, couverture — écrase les valeurs locales), ainsi
  qu'un bouton "Voir la fiche en ligne" vers la page publique du livre.
- **Voir la fiche WhatEpub** : ouvre directement la fiche publique
  (`www.whatepub.com/works/...`) du livre sélectionné dans le navigateur,
  à partir de l'identifiant `whatepub` enregistré lors d'une synchro
  précédente — aucun appel réseau, fonctionne même hors ligne pour un livre
  déjà synchronisé. Message d'info si le livre n'a jamais été synchronisé.
- **Synchroniser toute la bibliothèque** : applique en une fois les
  métadonnées de tous les livres déjà résolus — ne relit que les
  résolutions survenues depuis le dernier appel (reprise automatique).
  Pensé pour de grosses bibliothèques (dizaines de milliers de livres),
  tourne sur un thread séparé pour ne jamais geler l'interface.
- **Resynchroniser tout depuis le début** : comme ci-dessus mais réapplique
  tout le catalogue résolu, y compris ce qui a déjà été synchronisé — utile
  après des corrections faites côté admin WhatEpub sur des livres déjà
  synchronisés une première fois.

**Synchro retour automatique** (réglage "Synchro retour auto", désactivé par
défaut) : au lieu de cliquer "Synchroniser toute la bibliothèque" à la main,
un cycle automatique tourne à l'intervalle choisi — **sans aucune
confirmation**. WhatEpub devient alors la référence : une correction faite à
la main directement dans Calibre sur un livre déjà résolu peut être écrasée
au cycle suivant, WhatEpub ne sachant pas la distinguer d'une donnée à
corriger. Réservé à un usage où WhatEpub fait foi ; laisser à 0 sinon.

## Signature epub

La signature est calculée localement, côté addon, à partir du texte complet
de l'epub (jamais transmis en clair) :

- un hash exact du texte normalisé (`exact_hash`) ;
- des empreintes MinHash sur 6 fenêtres réparties entre 10 % et 90 % du
  texte, robustes aux variations mineures entre éditions (préface, notes...).

Voir [`fingerprint.py`](Add-on%20Calibre/fingerprint.py) pour le détail.
