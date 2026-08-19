# -*- coding: utf-8 -*-
"""
Configuration persistante du plugin, via JSONConfig (mécanisme standard
Calibre — stocke dans le dossier de config de l'utilisateur, survit
aux mises à jour du plugin).
"""

from calibre.utils.config import JSONConfig
from qt.core import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QLabel, QCheckBox

prefs = JSONConfig("plugins/whatepub")

prefs.defaults["server_url"] = "https://api.whatepub.com"
prefs.defaults["api_key"] = ""
prefs.defaults["scan_interval_minutes"] = 20
prefs.defaults["poll_interval_minutes"] = 3
prefs.defaults["batch_size"] = 50
prefs.defaults["dev_mode"] = True  # par défaut ON — pas de scan auto tant que non désactivé explicitement


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.server_url_edit = QLineEdit(prefs["server_url"], self)
        form.addRow("URL du serveur :", self.server_url_edit)

        self.api_key_edit = QLineEdit(prefs["api_key"], self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Clé API :", self.api_key_edit)

        self.scan_interval_spin = QSpinBox(self)
        self.scan_interval_spin.setRange(5, 240)
        self.scan_interval_spin.setValue(prefs["scan_interval_minutes"])
        form.addRow("Scan/push (minutes) :", self.scan_interval_spin)

        self.poll_interval_spin = QSpinBox(self)
        self.poll_interval_spin.setRange(1, 60)
        self.poll_interval_spin.setValue(prefs["poll_interval_minutes"])
        form.addRow("Poll résultats (minutes) :", self.poll_interval_spin)

        self.batch_size_spin = QSpinBox(self)
        self.batch_size_spin.setRange(10, 500)
        self.batch_size_spin.setValue(prefs["batch_size"])
        form.addRow("Taille de batch :", self.batch_size_spin)

        self.dev_mode_check = QCheckBox(
            "Mode développement (désactive les scans/polls automatiques — "
            "envoi manuel uniquement, via le menu)", self
        )
        self.dev_mode_check.setChecked(prefs["dev_mode"])
        layout.addWidget(self.dev_mode_check)

        layout.addWidget(QLabel(
            "La clé API est fournie par l'administrateur du serveur "
            "(générée lors de la création de l'installation)."
        ))

    def save_settings(self):
        prefs["server_url"] = self.server_url_edit.text().strip()
        prefs["api_key"] = self.api_key_edit.text().strip()
        prefs["scan_interval_minutes"] = self.scan_interval_spin.value()
        prefs["poll_interval_minutes"] = self.poll_interval_spin.value()
        prefs["batch_size"] = self.batch_size_spin.value()
        prefs["dev_mode"] = self.dev_mode_check.isChecked()
