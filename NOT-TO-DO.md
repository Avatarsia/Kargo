| Datum | Tag | Fehler | Vermeidung |
|-------|-----|--------|------------|
| 2026-10-23 | python,async | Framework-dispatchte Remote-Callbacks liefen nach Component-close weiter. | Remote-Workflows im Lifecycle tracken/canceln und _closing vor Folgeaktionen pruefen. |
| 2026-10-23 | python,api | Local replace/unlink konnte einen konkurrierenden gleichnamigen Upload verwerfen. | Publikation mit Uploads koordinieren oder verdraengte Inodes atomar erhalten und Identitaet vor Entfernung validieren. |
| 2026-10-23 | python,security | Eine Leaf-stat-Pruefung band die spaetere link/rename-Publikation nicht an den geprueften Temp-Inode. | Publikation an offenen FD oder nachweisbar geschuetztes Staging binden; spaeten Swap an der Syscall-Grenze testen. |
| 2026-10-23 | python,fs | Ein fehlgeschlagener direkter Copy-Fallback liess eine partielle lokale Datei als fetch-bereit erscheinen. | Direkte O_EXCL-Reservierung nur fuer unreferenzierte NAS-Archive; lokale Publikation muss vollstaendige FD-Daten verlinken oder sicher abbrechen. |
| 2026-10-23 | python,async | Im Child erfasste Startgeneration akzeptierte einen Cancel zwischen Dispatch und Child-Start. | Generation am Remote-Eintritt erfassen, ins Child mitgeben und vor/nach Fetch pruefen. |
| 2026-10-23 | git,windows | WSL-Git konnte den absoluten Windows-Gitdir-Pfad des T3-Worktrees nicht aufloesen. | Git-Checks in diesem Worktree mit nativem Windows-Git ausfuehren; WSL nur fuer Linux-Tests verwenden. |
| 2026-10-23 | python,filesystem | Neue Archiv-Elterneintraege wurden vor lokalem Entfernen nicht vollstaendig synchronisiert. | Nach mkdir den Eltern-FD synchronisieren; gesamte neue NAS-Verzeichniskette vor lokalem Commit pruefen. |
| 2026-10-23 | python,filesystem | Atomare Versionspublikation setzte Hardlink-Support auf allen NAS-Shares voraus. | EOPNOTSUPP testen und sichere exklusive FD-basierte Publikation ohne Hardlink-Pflicht anbieten. |
| 2026-10-23 | tooling | Report wurde zuerst im Projektroot statt im vorgegebenen SDD-Unterordner angelegt. | Vollstaendigen Artefakt-Zielpfad vor Schreiben und Existenz am exakten Pfad vor Abschluss pruefen. |
| 2026-10-23 | python,security | nosymfollow schuetzte nicht vor Userspace-realpath mit anschliessendem Schreiben auf externe Host-Ziele. | Keine persistenten NAS-Symlinks ohne vollstaendigen Consumer-Vertrag publizieren; realen Uploadpfad regressionspruefen. |
