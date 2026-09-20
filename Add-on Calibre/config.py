# -*- coding: utf-8 -*-
"""
Configuration persistante du plugin, via JSONConfig (mécanisme standard
Calibre — stocke dans le dossier de config de l'utilisateur, survit
aux mises à jour du plugin).
"""

from calibre.utils.config import JSONConfig
from qt.core import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QCheckBox, QLabel,
)

load_translations()

prefs = JSONConfig("plugins/whatepub")

# URL du serveur en dur (pas un champ de config) : ce plugin ne parle
# qu'à l'instance officielle WhatEpub, jamais à un serveur arbitraire.
SERVER_URL = "https://api.whatepub.com"
# Site public (fiche œuvre en lecture seule, pas d'API) — distinct du
# serveur d'ingestion ci-dessus, voir web/main.py côté serveur.
WEB_URL = "https://www.whatepub.com"

prefs.defaults["api_key"] = ""
# Envoi automatique des epubs (scan de la bibliothèque + push vers
# WhatEpub) — coché par défaut : c'est la fonction de base du plugin,
# sans laquelle rien n'est jamais identifié. Décoché seulement si le
# staff veut garder le contrôle manuel (bouton "Synchroniser
# maintenant"/"Envoyer le livre sélectionné").
prefs.defaults["push_enabled"] = True
prefs.defaults["scan_interval_minutes"] = 20
prefs.defaults["poll_interval_minutes"] = 3
prefs.defaults["batch_size"] = 50
# Plafond de livres NOUVEAUX/MODIFIÉS poussés par cycle de scan — 0 =
# illimité. Décision actée le 2026-09-18 : sur une bibliothèque jamais
# synchronisée (ex. ~60 000 livres), un premier scan sans plafond pousse
# tout d'un coup — gros pic CPU/IO côté Calibre (calcul d'empreinte de
# chaque livre) et file d'attente admin difficile à lire pendant des
# jours, même si le worker lui-même digère déjà les jobs un par un sans
# risque de perte. 200/cycle x 20 min par défaut = bibliothèque complète
# écoulée en un peu plus de 4 jours.
prefs.defaults["scan_push_limit"] = 200
# Synchro retour (WhatEpub -> Calibre) automatique et SANS confirmation
# quand activée (voir bulk_sync_library côté ui.py) : WhatEpub devient
# la référence, une correction faite à la main directement dans Calibre
# peut être écrasée au cycle suivant. Décoché par défaut — décision
# explicite de l'utilisateur, jamais le comportement par défaut.
prefs.defaults["bulk_sync_enabled"] = False
prefs.defaults["bulk_sync_interval_minutes"] = 60

# ---------- Migration ----------
#
# Avant l'ajout de bulk_sync_enabled (2026-09-20), l'activation de la
# synchro retour auto se pilotait uniquement via
# bulk_sync_interval_minutes == 0 (désactivé) / > 0 (activé, valeur =
# l'intervalle). Sur une config existante qui n'a jamais vu cette clé,
# on déduit l'état depuis cette ancienne convention plutôt que
# d'écraser silencieusement un réglage déjà fait par l'utilisateur —
# sans ça, une installation qui avait explicitement mis 60 minutes
# reviendrait à "désactivé" au premier chargement après mise à jour.
if "bulk_sync_enabled" not in prefs:
    prefs["bulk_sync_enabled"] = prefs["bulk_sync_interval_minutes"] > 0
    if prefs["bulk_sync_interval_minutes"] <= 0:
        prefs["bulk_sync_interval_minutes"] = prefs.defaults["bulk_sync_interval_minutes"]


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.api_key_edit = QLineEdit(prefs["api_key"], self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Clé API :"), self.api_key_edit)
        form.addRow(self._help(
            _("Fournie par l'administrateur du serveur (générée lors de la "
              "création de l'installation) — identifie cette bibliothèque "
              "auprès de WhatEpub.")
        ))

        # ---------- Envoi des epubs (Calibre -> WhatEpub) ----------

        self.push_enabled_check = QCheckBox(_("Envoi automatique des epubs"), self)
        self.push_enabled_check.setChecked(prefs["push_enabled"])
        self.push_enabled_check.toggled.connect(self._update_enabled_state)
        form.addRow(self.push_enabled_check)
        form.addRow(self._help(
            _("Scanne périodiquement la bibliothèque et envoie à WhatEpub la "
              "signature (empreinte de contenu) des livres nouveaux ou "
              "modifiés, pour identification — jamais le fichier epub "
              "lui-même. Fonction de base du plugin : sans elle, rien n'est "
              "envoyé automatiquement (reste possible manuellement via "
              "\"Synchroniser maintenant\" ou \"Envoyer le livre "
              "sélectionné\").")
        ))

        self.scan_interval_spin = QSpinBox(self)
        self.scan_interval_spin.setRange(5, 240)
        self.scan_interval_spin.setValue(prefs["scan_interval_minutes"])
        form.addRow(_("… intervalle entre deux scans (minutes) :"), self.scan_interval_spin)

        self.poll_interval_spin = QSpinBox(self)
        self.poll_interval_spin.setRange(1, 60)
        self.poll_interval_spin.setValue(prefs["poll_interval_minutes"])
        form.addRow(_("… vérification des résultats (minutes) :"), self.poll_interval_spin)
        form.addRow(self._help(
            _("Fréquence à laquelle le plugin revient voir si les livres "
              "envoyés ont été identifiés côté serveur (pour mettre à jour "
              "la barre de statut) — indépendante du scan lui-même.")
        ))

        self.batch_size_spin = QSpinBox(self)
        self.batch_size_spin.setRange(10, 500)
        self.batch_size_spin.setValue(prefs["batch_size"])
        form.addRow(_("… taille de batch (livres par requête) :"), self.batch_size_spin)
        form.addRow(self._help(
            _("Nombre de livres envoyés en une seule requête au serveur — "
              "un batch plus grand réduit le nombre de requêtes mais "
              "augmente le temps d'attente avant de voir le premier "
              "résultat.")
        ))

        self.scan_push_limit_spin = QSpinBox(self)
        self.scan_push_limit_spin.setRange(0, 20000)
        self.scan_push_limit_spin.setSpecialValueText(_("illimité"))
        self.scan_push_limit_spin.setValue(prefs["scan_push_limit"])
        form.addRow(_("… plafond livres/cycle de scan :"), self.scan_push_limit_spin)
        form.addRow(self._help(
            _("Limite combien de livres NOUVEAUX ou MODIFIÉS sont poussés à "
              "chaque cycle de scan — évite qu'un premier scan sur une "
              "grosse bibliothèque jamais synchronisée pousse tout d'un "
              "coup. 0 = illimité.")
        ))

        # ---------- Synchro retour (WhatEpub -> Calibre) ----------

        self.bulk_sync_enabled_check = QCheckBox(_("Synchronisation retour automatique"), self)
        self.bulk_sync_enabled_check.setChecked(prefs["bulk_sync_enabled"])
        self.bulk_sync_enabled_check.toggled.connect(self._update_enabled_state)
        form.addRow(self.bulk_sync_enabled_check)
        form.addRow(self._help(
            _("Applique automatiquement, SANS confirmation, les métadonnées "
              "déjà résolues côté WhatEpub (titre, auteur, série, langue, "
              "année, résumé, couverture) sur les livres correspondants de "
              "cette bibliothèque, à l'intervalle choisi ci-dessous. "
              "WhatEpub devient la référence : une correction faite à la "
              "main directement dans Calibre peut être écrasée au cycle "
              "suivant. Décoché = contrôle manuel uniquement (boutons du "
              "menu \"Synchroniser toute la bibliothèque\").")
        ))

        self.bulk_sync_interval_spin = QSpinBox(self)
        self.bulk_sync_interval_spin.setRange(5, 1440)
        self.bulk_sync_interval_spin.setValue(prefs["bulk_sync_interval_minutes"])
        form.addRow(_("… intervalle de synchro retour (minutes) :"), self.bulk_sync_interval_spin)

        self._update_enabled_state()
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)

    @staticmethod
    def _help(text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: palette(mid); font-size: 90%;")
        return label

    def _update_enabled_state(self):
        """Grise les champs d'intervalle quand leur case à cocher
        correspondante est décochée — évite de laisser croire qu'une
        valeur modifiée a un effet alors que le cycle auto est éteint."""
        push_on = self.push_enabled_check.isChecked()
        self.scan_interval_spin.setEnabled(push_on)

        bulk_sync_on = self.bulk_sync_enabled_check.isChecked()
        self.bulk_sync_interval_spin.setEnabled(bulk_sync_on)

    def save_settings(self):
        prefs["api_key"] = self.api_key_edit.text().strip()
        prefs["push_enabled"] = self.push_enabled_check.isChecked()
        prefs["scan_interval_minutes"] = self.scan_interval_spin.value()
        prefs["poll_interval_minutes"] = self.poll_interval_spin.value()
        prefs["batch_size"] = self.batch_size_spin.value()
        prefs["scan_push_limit"] = self.scan_push_limit_spin.value()
        prefs["bulk_sync_enabled"] = self.bulk_sync_enabled_check.isChecked()
        prefs["bulk_sync_interval_minutes"] = self.bulk_sync_interval_spin.value()
