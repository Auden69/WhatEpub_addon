# -*- coding: utf-8 -*-
"""
Action d'interface Calibre — bouton dans la barre d'outils + timer
actif seulement quand Calibre est ouvert (pas de daemon séparé,
décision actée), SAUF en mode développement (prefs["dev_mode"]) où
les timers sont désactivés — envoi manuel uniquement, via le menu.
Deux fréquences indépendantes en usage normal : scan/push et poll
des résultats, avec run_lock anti-chevauchement et backoff si le
serveur ne répond pas.
"""

from qt.core import (
    QTimer, QMenu, QThread, pyqtSignal,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QPushButton,
    QRadioButton, QButtonGroup, QLineEdit,
)

from calibre.gui2.actions import InterfaceAction

from calibre_plugins.whatebook.config import prefs
from calibre_plugins.whatebook import sync_worker


class SyncThread(QThread):
    """
    Fait tourner le scan/push et le poll sur un thread séparé —
    obligatoire : avec une bibliothèque de plusieurs dizaines de
    milliers de livres, un traitement synchrone dans le thread UI
    gèle complètement Calibre le temps du cycle (confirmé en test réel).
    """
    log_signal = pyqtSignal(str)
    scan_finished_signal = pyqtSignal(dict)
    poll_finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, db_api, do_scan, do_poll):
        super().__init__()
        self.db_api = db_api
        self.do_scan = do_scan
        self.do_poll = do_poll

    def run(self):
        try:
            if self.do_scan:
                result = sync_worker.run_scan_and_push(self.db_api, log=self.log_signal.emit)
                self.scan_finished_signal.emit(result)
            if self.do_poll:
                result = sync_worker.run_poll_results(log=self.log_signal.emit)
                self.poll_finished_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))


class SampleThread(QThread):
    """
    Thread dédié aux envois de test (échantillon ou IDs précis) —
    séparé de SyncThread pour ne jamais mélanger un test avec un vrai
    cycle de synchro (logs distincts, aucun risque de confusion).

    mode='sample' : échantillon de n livres (aléatoire ou séquentiel).
    mode='ids'    : liste précise de calibre_book_id à renvoyer de force.
    """
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, db_api, mode, n=None, random_order=True, book_ids=None):
        super().__init__()
        self.db_api = db_api
        self.mode = mode
        self.n = n
        self.random_order = random_order
        self.book_ids = book_ids or []

    def run(self):
        try:
            if self.mode == "sample":
                result = sync_worker.push_random_sample(
                    self.db_api, self.n, log=self.log_signal.emit, random_order=self.random_order
                )
            else:
                result = sync_worker.push_specific_books(
                    self.db_api, self.book_ids, log=self.log_signal.emit
                )
            self.finished_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))


class RandomSampleDialog(QDialog):
    """
    Boîte de dialogue : boutons de raccourci (1/5/10/50) + champ libre,
    et choix aléatoire vs séquentiel (déterministe, pour reproduire
    exactement le même échantillon d'un test à l'autre). Dev/test
    uniquement, jamais affichée dans le flux normal d'usage.
    """
    def __init__(self, parent, max_books):
        super().__init__(parent)
        self.setWindowTitle("Envoyer un échantillon de test")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"Combien de livres envoyer (bibliothèque : {max_books} livre(s)) ?"
        ))

        self.spin = QSpinBox(self)
        self.spin.setRange(1, max(1, max_books))
        self.spin.setValue(min(5, max_books))
        layout.addWidget(self.spin)

        presets_layout = QHBoxLayout()
        for n in (1, 5, 10, 50):
            btn = QPushButton(str(n), self)
            btn.clicked.connect(lambda checked, n=n: self.spin.setValue(min(n, max_books)))
            presets_layout.addWidget(btn)
        layout.addLayout(presets_layout)

        layout.addWidget(QLabel("Sélection :"))
        radio_layout = QHBoxLayout()
        self.radio_random = QRadioButton("Aléatoire", self)
        self.radio_sequential = QRadioButton("Les premiers (ordre déterministe)", self)
        self.radio_random.setChecked(True)
        self.radio_group = QButtonGroup(self)
        self.radio_group.addButton(self.radio_random)
        self.radio_group.addButton(self.radio_sequential)
        radio_layout.addWidget(self.radio_random)
        radio_layout.addWidget(self.radio_sequential)
        layout.addLayout(radio_layout)

        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton("Envoyer", self)
        cancel_btn = QPushButton("Annuler", self)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def selected_count(self):
        return self.spin.value()

    def is_random(self):
        return self.radio_random.isChecked()


class SpecificIdsDialog(QDialog):
    """
    Champ texte pour entrer un ou plusieurs calibre_book_id (séparés
    par des virgules) — pour retester un livre précis après une
    modification côté serveur, sans avoir à le modifier dans Calibre
    ni relancer un scan complet.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Renvoyer un ou plusieurs livres précis")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "ID(s) Calibre à renvoyer, séparés par une virgule si "
            "plusieurs (ex. 42 ou 12, 87, 203) :"
        ))

        self.id_edit = QLineEdit(self)
        self.id_edit.setPlaceholderText("42")
        layout.addWidget(self.id_edit)

        buttons_layout = QHBoxLayout()
        ok_btn = QPushButton("Envoyer", self)
        cancel_btn = QPushButton("Annuler", self)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(ok_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def parsed_ids(self):
        """Retourne la liste d'IDs entiers valides, ignore les entrées non numériques."""
        raw = self.id_edit.text().strip()
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids


class WhatebookAction(InterfaceAction):
    name = "WhatEbook"
    action_spec = ("WhatEbook", None, "Synchronise avec le service bibliographique partagé", None)
    action_type = "current"

    def genesis(self):
        self.qaction.triggered.connect(self.sync_now)
        menu = QMenu(self.gui)
        menu.addAction("Synchroniser maintenant", self.sync_now)
        menu.addSeparator()
        menu.addAction("Envoyer un échantillon de test...", self.send_random_sample)
        menu.addAction("Renvoyer un/des livre(s) précis (ID)...", self.send_specific_ids)
        menu.addSeparator()
        menu.addAction("Paramètres...", self.show_config)
        self.qaction.setMenu(menu)

        self._scan_timer = QTimer(self.gui)
        self._scan_timer.timeout.connect(self._run_scan_cycle)

        self._poll_timer = QTimer(self.gui)
        self._poll_timer.timeout.connect(self._run_poll_cycle)

        self._is_running = False          # run_lock anti-chevauchement
        self._consecutive_failures = 0    # pour le backoff
        self._sync_thread = None
        self._sample_thread = None

        self._start_timers()

    # ---------- Démarrage des timers, aux intervalles configurés ----------

    def _start_timers(self):
        """
        En mode développement (prefs["dev_mode"]), les timers ne sont
        jamais démarrés — et ceux déjà actifs sont arrêtés, pour que
        basculer le mode dans les Paramètres prenne effet immédiatement
        sans redémarrer Calibre.
        """
        if prefs["dev_mode"]:
            self._scan_timer.stop()
            self._poll_timer.stop()
            self._log("[whatebook] Mode développement actif — scans automatiques désactivés.")
            return

        scan_ms = prefs["scan_interval_minutes"] * 60 * 1000
        poll_ms = prefs["poll_interval_minutes"] * 60 * 1000
        self._scan_timer.start(scan_ms)
        self._poll_timer.start(poll_ms)

    def _restart_scan_timer(self, minutes):
        if prefs["dev_mode"]:
            return  # pas de backoff à gérer si les timers sont déjà coupés
        self._scan_timer.setInterval(minutes * 60 * 1000)

    # ---------- Lancement du thread (jamais de traitement synchrone) ----------

    def _launch_sync_thread(self, do_scan, do_poll):
        if self._is_running:
            return False  # cycle précédent toujours en cours, on saute ce tick
        self._is_running = True

        db_api = self.gui.current_db.new_api
        self._sync_thread = SyncThread(db_api, do_scan, do_poll)
        self._sync_thread.log_signal.connect(self._log)
        self._sync_thread.scan_finished_signal.connect(self._handle_cycle_result)
        self._sync_thread.error_signal.connect(self._handle_thread_error)
        self._sync_thread.finished.connect(self._on_thread_finished)
        self._sync_thread.start()
        return True

    def _on_thread_finished(self):
        self._is_running = False

    def _handle_thread_error(self, message):
        self._log(f"[whatebook] Erreur : {message}")
        self._register_failure()

    # ---------- Cycles automatiques (déclenchés par les timers) ----------

    def _run_scan_cycle(self):
        self._launch_sync_thread(do_scan=True, do_poll=False)

    def _run_poll_cycle(self):
        self._launch_sync_thread(do_scan=False, do_poll=True)

    def _handle_cycle_result(self, result):
        if result.get("skipped"):
            return
        if result["failed"] > 0 and result["pushed"] == 0:
            self._register_failure()
        else:
            self._consecutive_failures = 0
            self._restart_scan_timer(prefs["scan_interval_minutes"])

    def _register_failure(self):
        """
        Backoff si le serveur est injoignable plusieurs fois de suite —
        espace le timer (x2, plafonné) plutôt que de marteler un
        serveur down toutes les N minutes indéfiniment.
        """
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            base = prefs["scan_interval_minutes"]
            backoff_minutes = min(base * (2 ** (self._consecutive_failures - 2)), 120)
            self._restart_scan_timer(backoff_minutes)
            self._log(f"[whatebook] {self._consecutive_failures} échecs consécutifs — "
                      f"prochain essai dans {backoff_minutes} min.")

    # ---------- Déclenchement manuel : vraie synchro ----------

    def sync_now(self):
        started = self._launch_sync_thread(do_scan=True, do_poll=True)
        if not started:
            self._log("[whatebook] Une synchro est déjà en cours.")

    # ---------- Déclenchement manuel : tests dev ----------

    def _launch_sample_thread(self, **kwargs):
        if self._is_running:
            self._log("[whatebook] Une synchro est déjà en cours.")
            return
        self._is_running = True
        db_api = self.gui.current_db.new_api
        self._sample_thread = SampleThread(db_api, **kwargs)
        self._sample_thread.log_signal.connect(self._log)
        self._sample_thread.finished_signal.connect(self._handle_sample_result)
        self._sample_thread.error_signal.connect(self._handle_thread_error)
        self._sample_thread.finished.connect(self._on_thread_finished)
        self._sample_thread.start()

    def send_random_sample(self):
        db_api = self.gui.current_db.new_api
        max_books = len(db_api.all_book_ids())
        if max_books == 0:
            self._log("[whatebook] Bibliothèque vide.")
            return

        dialog = RandomSampleDialog(self.gui, max_books)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._launch_sample_thread(
            mode="sample", n=dialog.selected_count(), random_order=dialog.is_random()
        )

    def send_specific_ids(self):
        dialog = SpecificIdsDialog(self.gui)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        ids = dialog.parsed_ids()
        if not ids:
            self._log("[whatebook] Aucun ID valide saisi.")
            return

        self._launch_sample_thread(mode="ids", book_ids=ids)

    def _handle_sample_result(self, result):
        self._log(f"[whatebook] Test terminé : {result['pushed']} poussé(s), {result['failed']} échec(s).")

    def show_config(self):
        self.interface_action_base_plugin.do_user_config(self.gui)
        self._start_timers()  # relit dev_mode + les nouveaux intervalles si modifiés

    # ---------- Log ----------

    def _log(self, message):
        # Affiché dans la barre de statut Calibre — discret, pas de popup
        # pour le fonctionnement normal (silencieux par défaut, décision actée).
        try:
            self.gui.status_bar.showMessage(message, 5000)
        except Exception:
            print(message)
