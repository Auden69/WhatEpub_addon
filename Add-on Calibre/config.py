# -*- coding: utf-8 -*-
"""
Configuration persistante du plugin, via JSONConfig (mécanisme standard
Calibre — stocke dans le dossier de config de l'utilisateur, survit
aux mises à jour du plugin).
"""

from calibre.utils.config import JSONConfig
from qt.core import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QLabel

load_translations()

prefs = JSONConfig("plugins/whatepub")

# URL du serveur en dur (pas un champ de config) : ce plugin ne parle
# qu'à l'instance officielle WhatEpub, jamais à un serveur arbitraire.
SERVER_URL = "https://api.whatepub.com"
# Site public (fiche œuvre en lecture seule, pas d'API) — distinct du
# serveur d'ingestion ci-dessus, voir web/main.py côté serveur.
WEB_URL = "https://www.whatepub.com"

prefs.defaults["api_key"] = ""
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
# 0 = désactivé (défaut) — synchro retour (WhatEpub -> Calibre)
# automatique et SANS confirmation quand activé (voir run.bulk_sync_library
# côté ui.py) : WhatEpub devient la référence, une correction faite à la
# main directement dans Calibre peut être écrasée au cycle suivant.
# Décision explicite de l'utilisateur (2026-09-18), jamais le défaut.
prefs.defaults["bulk_sync_interval_minutes"] = 0


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.api_key_edit = QLineEdit(prefs["api_key"], self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Clé API :"), self.api_key_edit)

        self.scan_interval_spin = QSpinBox(self)
        self.scan_interval_spin.setRange(5, 240)
        self.scan_interval_spin.setValue(prefs["scan_interval_minutes"])
        form.addRow(_("Scan/push (minutes) :"), self.scan_interval_spin)

        self.poll_interval_spin = QSpinBox(self)
        self.poll_interval_spin.setRange(1, 60)
        self.poll_interval_spin.setValue(prefs["poll_interval_minutes"])
        form.addRow(_("Poll résultats (minutes) :"), self.poll_interval_spin)

        self.batch_size_spin = QSpinBox(self)
        self.batch_size_spin.setRange(10, 500)
        self.batch_size_spin.setValue(prefs["batch_size"])
        form.addRow(_("Taille de batch :"), self.batch_size_spin)

        self.scan_push_limit_spin = QSpinBox(self)
        self.scan_push_limit_spin.setRange(0, 20000)
        self.scan_push_limit_spin.setSpecialValueText(_("illimité"))
        self.scan_push_limit_spin.setValue(prefs["scan_push_limit"])
        form.addRow(_("Plafond livres/cycle de scan :"), self.scan_push_limit_spin)

        self.bulk_sync_interval_spin = QSpinBox(self)
        self.bulk_sync_interval_spin.setRange(0, 1440)
        self.bulk_sync_interval_spin.setSpecialValueText(_("désactivé"))
        self.bulk_sync_interval_spin.setValue(prefs["bulk_sync_interval_minutes"])
        form.addRow(_("Synchro retour auto (minutes) :"), self.bulk_sync_interval_spin)

        layout.addWidget(QLabel(
            _("La clé API est fournie par l'administrateur du serveur "
              "(générée lors de la création de l'installation).")
        ))
        layout.addWidget(QLabel(
            _("Plafond livres/cycle de scan : limite combien de livres NOUVEAUX ou "
              "MODIFIÉS sont poussés à chaque cycle de scan — évite qu'un premier "
              "scan sur une grosse bibliothèque jamais synchronisée pousse tout "
              "d'un coup. 0 = illimité.")
        ))
        layout.addWidget(QLabel(
            _("Synchro retour auto : applique automatiquement, SANS confirmation, "
              "les métadonnées résolues côté WhatEpub sur cette bibliothèque à "
              "l'intervalle choisi. WhatEpub devient la référence — une correction "
              "faite à la main directement dans Calibre peut être écrasée au "
              "cycle suivant. Laisser à 0 (désactivé) pour garder le contrôle "
              "manuel (boutons du menu).")
        ))

    def save_settings(self):
        prefs["api_key"] = self.api_key_edit.text().strip()
        prefs["scan_interval_minutes"] = self.scan_interval_spin.value()
        prefs["poll_interval_minutes"] = self.poll_interval_spin.value()
        prefs["batch_size"] = self.batch_size_spin.value()
        prefs["scan_push_limit"] = self.scan_push_limit_spin.value()
        prefs["bulk_sync_interval_minutes"] = self.bulk_sync_interval_spin.value()
