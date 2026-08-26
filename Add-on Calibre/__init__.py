# -*- coding: utf-8 -*-
"""
Point d'entrée du plugin, requis par l'API Calibre. Déclare les
métadonnées du plugin et pointe vers la vraie classe d'action
(ui.py) — Calibre charge ce fichier en premier pour découvrir le
plugin avant d'importer le reste.
"""

from calibre.customize import InterfaceActionBase

load_translations()


class WhatEpubPlugin(InterfaceActionBase):
    name = "WhatEpub"
    description = _("Pousse les métadonnées Calibre vers le service bibliographique partagé (WhatEpub).")
    supported_platforms = ["windows", "osx", "linux"]
    author = "Local"
    version = (0, 6, 2)
    minimum_calibre_version = (5, 0, 0)

    # Chemin vers la vraie classe InterfaceAction (chargée à la demande,
    # pas à l'import de ce fichier — convention Calibre standard)
    actual_plugin = "calibre_plugins.whatepub.ui:WhatEpubAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.whatepub.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
