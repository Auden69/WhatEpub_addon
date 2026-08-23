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

prefs.defaults["api_key"] = ""
prefs.defaults["scan_interval_minutes"] = 20
prefs.defaults["poll_interval_minutes"] = 3
prefs.defaults["batch_size"] = 50


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

        layout.addWidget(QLabel(
            _("La clé API est fournie par l'administrateur du serveur "
              "(générée lors de la création de l'installation).")
        ))

    def save_settings(self):
        prefs["api_key"] = self.api_key_edit.text().strip()
        prefs["scan_interval_minutes"] = self.scan_interval_spin.value()
        prefs["poll_interval_minutes"] = self.poll_interval_spin.value()
        prefs["batch_size"] = self.batch_size_spin.value()
