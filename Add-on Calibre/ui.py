# -*- coding: utf-8 -*-
"""
Action d'interface Calibre — bouton dans la barre d'outils + timer
actif seulement quand Calibre est ouvert (pas de daemon séparé,
décision actée). Deux fréquences indépendantes : scan/push et poll
des résultats, avec run_lock anti-chevauchement et backoff si le
serveur ne répond pas.
"""

from qt.core import QTimer, QMenu, QThread, pyqtSignal

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.whatepub.config import prefs
from calibre_plugins.whatepub import sync_worker

load_translations()


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


class WhatEpubAction(InterfaceAction):
    name = "WhatEpub"
    action_spec = ("WhatEpub", None, _("Synchronise avec le service bibliographique partagé"), None)
    action_type = "current"

    def genesis(self):
        self.qaction.triggered.connect(self.sync_now)
        menu = QMenu(self.gui)
        menu.addAction(_("Synchroniser maintenant"), self.sync_now)
        menu.addAction(_("Vérifier le livre sélectionné"), self.check_selected_book)
        menu.addSeparator()
        menu.addAction(_("Paramètres..."), self.show_config)
        self.qaction.setMenu(menu)

        self._scan_timer = QTimer(self.gui)
        self._scan_timer.timeout.connect(self._run_scan_cycle)

        self._poll_timer = QTimer(self.gui)
        self._poll_timer.timeout.connect(self._run_poll_cycle)

        self._is_running = False          # run_lock anti-chevauchement
        self._consecutive_failures = 0    # pour le backoff
        self._sync_thread = None

        self._start_timers()

    # ---------- Démarrage des timers, aux intervalles configurés ----------

    def _start_timers(self):
        scan_ms = prefs["scan_interval_minutes"] * 60 * 1000
        poll_ms = prefs["poll_interval_minutes"] * 60 * 1000
        self._scan_timer.start(scan_ms)
        self._poll_timer.start(poll_ms)

    def _restart_scan_timer(self, minutes):
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
        self._log(_("[whatepub] Erreur : {message}").format(message=message))
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
            self._log(_("[whatepub] {n} échecs consécutifs — prochain essai dans {minutes} min.").format(
                n=self._consecutive_failures, minutes=backoff_minutes
            ))

    # ---------- Déclenchement manuel : vraie synchro ----------

    def sync_now(self):
        started = self._launch_sync_thread(do_scan=True, do_poll=True)
        if not started:
            self._log(_("[whatepub] Une synchro est déjà en cours."))

    def show_config(self):
        self.interface_action_base_plugin.do_user_config(self.gui)
        self._start_timers()  # relit les nouveaux intervalles si modifiés

    def check_selected_book(self):
        """Lookup en lecture seule par signature (POST /lookup) pour le
        livre sélectionné dans la bibliothèque — affiche juste ce que
        WhatEpub a en base. Ne modifie JAMAIS les métadonnées Calibre,
        ni aucune donnée côté serveur : purement informatif, décision
        actée de ne rien appliquer automatiquement pour l'instant."""
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            error_dialog(self.gui, "WhatEpub", _("Sélectionne d'abord un livre dans la bibliothèque."), show=True)
            return
        if len(rows) > 1:
            error_dialog(self.gui, "WhatEpub", _("Sélectionne un seul livre à la fois."), show=True)
            return
        if not prefs["api_key"]:
            error_dialog(self.gui, "WhatEpub", _("Configure d'abord ta clé API (Paramètres...)."), show=True)
            return

        db_api = self.gui.current_db.new_api
        book_id = self.gui.library_view.model().id(rows[0])

        epub_path = sync_worker.get_epub_path(db_api, book_id, log=self._log)
        if not epub_path or not epub_path.exists():
            error_dialog(self.gui, "WhatEpub", _("Pas de fichier epub pour ce livre."), show=True)
            return

        try:
            result = sync_worker.lookup_book_by_signature(epub_path, prefs["api_key"])
        except Exception as e:
            error_dialog(
                self.gui, "WhatEpub",
                _("Échec de la vérification : {error}").format(error=str(e)),
                show=True,
            )
            return

        if not result.get("matched"):
            info_dialog(
                self.gui, "WhatEpub",
                _("Aucune correspondance trouvée dans le catalogue WhatEpub pour ce livre."),
                show=True,
            )
            return

        work = result["work"]
        lines = [
            _("Titre : {title}").format(title=work["title"]),
            _("Auteur : {author}").format(author=work["author"] or "—"),
        ]
        if work.get("series_name"):
            lines.append(_("Série : {series} (tome {index})").format(
                series=work["series_name"], index=work.get("series_index") or "?"
            ))
        if work.get("language"):
            lines.append(_("Langue : {lang}").format(lang=work["language"]))
        lines.append(_("Correspondance : {method} (confiance {confidence})").format(
            method=result["match_method"], confidence=result["confidence"]
        ))

        info_dialog(self.gui, "WhatEpub", "\n".join(lines), show=True)

    # ---------- Log ----------

    def _log(self, message):
        # Affiché dans la barre de statut Calibre — discret, pas de popup
        # pour le fonctionnement normal (silencieux par défaut, décision actée).
        try:
            self.gui.status_bar.showMessage(message, 5000)
        except Exception:
            print(message)
