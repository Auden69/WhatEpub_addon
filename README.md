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

## Signature epub

La signature est calculée localement, côté addon, à partir du texte complet
de l'epub (jamais transmis en clair) :

- un hash exact du texte normalisé (`exact_hash`) ;
- des empreintes MinHash sur 6 fenêtres réparties entre 10 % et 90 % du
  texte, robustes aux variations mineures entre éditions (préface, notes...).

Voir [`fingerprint.py`](Add-on%20Calibre/fingerprint.py) pour le détail.
