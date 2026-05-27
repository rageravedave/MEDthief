"""
Profile Manager — speichert/lädt Kandidatenprofile als JSON.
Speicherort: ~/.cv_job_matcher/profiles/
"""
import json
import os
import re
from datetime import datetime

PROFILES_DIR = os.path.expanduser("~/.cv_job_matcher/profiles")


def _sanitize(name: str) -> str:
    """Dateiname aus Profilname: nur alphanumerisch + Bindestrich."""
    return re.sub(r"[^\w\-]", "_", name.strip())[:64] or "profil"


class ProfileManager:

    # ── Verzeichnis sicherstellen ──────────────────────────────────────────
    @staticmethod
    def _ensure():
        os.makedirs(PROFILES_DIR, exist_ok=True)

    @staticmethod
    def _path(name: str) -> str:
        return os.path.join(PROFILES_DIR, f"{_sanitize(name)}.json")

    # ── CRUD ───────────────────────────────────────────────────────────────
    @staticmethod
    def save(name: str, data: dict):
        ProfileManager._ensure()
        data["_saved_at"] = datetime.now().isoformat()
        with open(ProfileManager._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(name: str) -> dict:
        try:
            with open(ProfileManager._path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def delete(name: str):
        p = ProfileManager._path(name)
        if os.path.exists(p):
            os.remove(p)

    @staticmethod
    def list_profiles() -> list:
        """Gibt alle gespeicherten Profilnamen zurück (alphabetisch)."""
        ProfileManager._ensure()
        names = []
        for fn in os.listdir(PROFILES_DIR):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(PROFILES_DIR, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    names.append(d.get("candidate_name") or fn[:-5])
                except Exception:
                    names.append(fn[:-5])
        return sorted(names)

    # ── Sidebar ↔ Profil ───────────────────────────────────────────────────
    @staticmethod
    def build_from_sidebar(sidebar, candidate_name: str = "") -> dict:
        """Erstellt Profil-Dict aus aktuellen Sidebar-Feldern."""
        return {
            "candidate_name": candidate_name,
            "search": {
                "job_title":   sidebar.entry_job.currentText().strip(),
                "address":     sidebar.entry_addr.text().strip(),
                "einrichtung": sidebar.entry_einrichtung.text().strip(),
                "dept":        sidebar.entry_dept.text().strip(),
                "arbeitszeit": sidebar.combo_arbeitszeit.currentText(),
                "schicht":     sidebar.combo_schicht.currentText(),
                "radius":      sidebar.slider.value(),
            },
            "notes": sidebar.notes_edit.toPlainText(),
        }

    @staticmethod
    def apply_to_sidebar(profile: dict, sidebar):
        """Füllt Sidebar-Felder aus einem geladenen Profil."""
        s = profile.get("search", {})
        sidebar.set_fields(
            s.get("job_title", ""),
            s.get("address", ""),
            s.get("einrichtung", ""),
            s.get("dept", ""),
            s.get("arbeitszeit", ""),
            s.get("schicht", ""),
        )
        if s.get("radius"):
            sidebar.slider.setValue(s["radius"])
        if profile.get("notes"):
            sidebar.notes_edit.setPlainText(profile["notes"])

    # ── Job-Daten (Status / Notizen / gesehene URLs) ───────────────────────
    @staticmethod
    def load_job_data(name: str) -> tuple:
        """Gibt (job_status, job_notes, seen_urls) aus einem Profil zurück."""
        d = ProfileManager.load(name)
        return (
            d.get("job_status", {}),   # {url: "Kontaktiert", ...}
            d.get("job_notes",  {}),   # {url: "Notiz-Text", ...}
            set(d.get("seen_urls", [])),
        )

    @staticmethod
    def save_job_data(name: str, job_status: dict, job_notes: dict, seen_urls: set):
        """Schreibt Job-Daten in das bestehende Profil."""
        d = ProfileManager.load(name) or {}
        d["job_status"] = job_status
        d["job_notes"]  = job_notes
        d["seen_urls"]  = list(seen_urls)
        ProfileManager.save(name, d)
