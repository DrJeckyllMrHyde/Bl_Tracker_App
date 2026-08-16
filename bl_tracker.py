import os
import sys
import sqlite3
import shutil
import webbrowser
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from PIL import Image, ImageTk, ImageOps
    PIL_AVAILABLE = True
except Exception:
    Image = ImageTk = ImageOps = None
    PIL_AVAILABLE = False

APP_NAME = "BL Tracker"
# En mode PyInstaller, __file__ pointe vers un dossier temporaire supprimé à
# la fermeture. Le dossier de l'exécutable garantit la persistance des données.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "bl_tracker.db")

os.makedirs(IMG_DIR, exist_ok=True)


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BL-Tracker/4.0"


class LinkCollector(HTMLParser):
    """Petit collecteur de liens, utilisé pour retrouver la fiche série depuis une page tag."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs = dict(attrs)
            href = attrs.get("href")
            if href:
                self._current = {"href": href, "text": []}

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current is not None:
            text = " ".join("".join(self._current["text"]).split())
            self.links.append({"href": self._current["href"], "text": text})
            self._current = None


class ImageCollector(HTMLParser):
    """Collecte les images possibles d'une fiche WordPress."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "img":
            return
        attrs = dict(attrs)
        src = attrs.get("data-orig-file") or attrs.get("data-large-file") or attrs.get("src")
        if src:
            self.images.append({"src": src, "alt": attrs.get("alt", "")})


class TextCollector(HTMLParser):
    """Transforme un HTML simple en texte avec retours à la ligne exploitables."""
    BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        data = data.replace("\xa0", " ")
        if data.strip():
            self.parts.append(data)

    def get_text(self):
        raw = unescape("".join(self.parts))
        lines = []
        for line in raw.splitlines():
            clean = " ".join(line.replace("\xa0", " ").split())
            if clean:
                lines.append(clean)
        return "\n".join(lines)


def fetch_html(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=25) as resp:
        raw = resp.read(6_000_000)
        final_url = resp.geturl()
        content_type = resp.headers.get("content-type", "")
    charset = "utf-8"
    match = re.search(r"charset=([\w\-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    return raw.decode(charset, errors="replace"), final_url


def html_to_text(html):
    parser = TextCollector()
    parser.feed(html)
    return parser.get_text()


def collect_links(html, base_url):
    parser = LinkCollector()
    parser.feed(html)
    return [{"href": urljoin(base_url, item["href"]), "text": item["text"]} for item in parser.links]


def normalize_label(label):
    return re.sub(r"\s+", " ", label.replace("\xa0", " ")).strip()


def extract_line(text, label):
    label_re = re.escape(label).replace("\\ ", r"\s+")
    match = re.search(rf"(?:^|\n)\s*{label_re}\s*[:：]\s*(.+)", text, re.I)
    if match:
        return normalize_label(match.group(1))
    return ""


def extract_block(text, label, stop_words):
    label_re = re.escape(label).replace("\\ ", r"\s+")
    stops = "|".join(re.escape(w) for w in stop_words)
    pattern = rf"(?:^|\n)\s*{label_re}\s*[:：]\s*(.+?)(?=\n\s*(?:{stops})\s*[:：]|\n\s*C\s*A\s*S\s*T\s*I\s*N\s*G|\n\s*###|\n\s*[-–]?[A-ZÉÈÀÂÊÎÔÛÇ\- ]+[-–]?\s*$|\Z)"
    match = re.search(pattern, text, re.I | re.S)
    if match:
        return "\n".join(" ".join(line.split()) for line in match.group(1).splitlines() if line.strip()).strip()
    return ""


def split_person_name(actor_name):
    actor_name = normalize_label(actor_name)
    parts = actor_name.split()
    if len(parts) <= 1:
        return actor_name, ""
    return parts[0], " ".join(parts[1:])


def extract_casting(text):
    match = re.search(r"C\s*A\s*S\s*T\s*I\s*N\s*G", text, re.I)
    if not match:
        match = re.search(r"(?:^|\n)\s*Casting\s*(?:\n|$)", text, re.I)
    if not match:
        return []

    segment = text[match.end():]
    cut = re.search(r"\n\s*(?:★|###|[-–]?BANDE[-–]?ANNONCE|[-–]?EPISODES|Episode\s+01)\b", segment, re.I)
    if cut:
        segment = segment[:cut.start()]

    people = []
    seen = set()
    for role, actor in re.findall(r"([^()\n]{1,90}?)\s*\(([^()]{2,90})\)", segment):
        role = re.sub(r"\bImage\b", "", role, flags=re.I)
        role = normalize_label(role).strip(" -–•*")
        actor = normalize_label(actor).strip(" -–•*")
        if not actor or len(actor) < 2:
            continue
        if re.search(r"\d{4}", role):
            role = ""
        key = actor.lower(), role.lower()
        if key in seen:
            continue
        seen.add(key)
        first, last = split_person_name(actor)
        people.append({"first_name": first, "last_name": last, "role": role})
    return people[:20]


def find_fiche_url(html, base_url):
    text = html_to_text(html)
    if re.search(r"Titre\s+international\s*[:：]|Durée\s*[:：].*épisodes", text, re.I | re.S):
        return base_url

    links = collect_links(html, base_url)
    for link in links:
        label = link["text"].lower().replace("é", "e")
        if "fiche serie" in label or "fiche drama" in label:
            return link["href"]
    for link in links:
        href = link["href"].lower()
        if "/fiche-drama-" in href or "/fiche-film-" in href:
            return link["href"]
    return base_url


def select_poster_url(html, base_url):
    # WordPress met souvent l'image principale dans og:image.
    for pattern in [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    ]:
        match = re.search(pattern, html, re.I)
        if match:
            return urljoin(base_url, unescape(match.group(1)))

    parser = ImageCollector()
    parser.feed(html)
    for item in parser.images:
        src = urljoin(base_url, item["src"])
        low = src.lower()
        if any(bad in low for bad in ["avatar", "gravatar", "emoji", "blank", "wpcom-smile"]):
            continue
        if "wp-content" in low or "files.wordpress" in low:
            return src
    return ""


def download_image(image_url, title):
    if not image_url:
        return ""
    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", title or "serie").strip("_") or "serie"
    req = Request(image_url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=25) as resp:
        raw = resp.read(10_000_000)
        content_type = resp.headers.get("content-type", "").lower()
        final_url = resp.geturl()

    ext = os.path.splitext(urlparse(final_url).path)[1].lower()
    if ext not in (".png", ".gif", ".jpg", ".jpeg", ".webp"):
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            ext = ".jpg"
    dest = os.path.join(IMG_DIR, f"{safe_title}_{date.today().isoformat()}{ext}")
    with open(dest, "wb") as f:
        f.write(raw)
    return dest


def import_from_blfrance(url):
    html, final_url = fetch_html(url)
    fiche_url = find_fiche_url(html, final_url)
    if fiche_url != final_url:
        html, final_url = fetch_html(fiche_url)

    text = html_to_text(html)
    page_title = ""
    title_match = re.search(r"\[FICHE[^\]]*\]\s*(.+?)(?:\s*\(|\n|$)", text, re.I)
    if title_match:
        page_title = normalize_label(title_match.group(1))

    title = extract_line(text, "Titre international") or page_title
    title_vo = extract_line(text, "Titre VO")
    country = extract_line(text, "Pays")
    duration = extract_line(text, "Durée")
    diffusion = extract_line(text, "Diffusion")
    channel = extract_line(text, "Chaîne") or extract_line(text, "Chaine")
    genre = extract_line(text, "Genre")
    synopsis = extract_block(text, "Synopsis", ["Notes", "Casting", "C A S T I N G", "Liens officiels", "Fiche", "Coffret"])
    cast = extract_casting(text)

    total_episodes = 0
    ep_match = re.search(r"(\d+)\s*épisodes?", duration, re.I)
    if not ep_match:
        ep_match = re.search(r"Episode\s*0?1.*Episode\s*(\d+)", text, re.I | re.S)
    if ep_match:
        try:
            total_episodes = int(ep_match.group(1))
        except ValueError:
            total_episodes = 0

    image_url = select_poster_url(html, final_url)
    notes_parts = []
    if synopsis:
        notes_parts.append("Résumé :\n" + synopsis)
    extra = []
    if title_vo:
        extra.append(f"Titre VO : {title_vo}")
    if duration:
        extra.append(f"Durée : {duration}")
    if diffusion:
        extra.append(f"Diffusion : {diffusion}")
    if channel:
        extra.append(f"Chaîne : {channel}")
    if genre:
        extra.append(f"Genre : {genre}")
    if extra:
        notes_parts.append("Infos importées :\n" + "\n".join(f"- {x}" for x in extra))

    return {
        "source_url": final_url,
        "title": title,
        "country": country,
        "total_episodes": total_episodes,
        "synopsis": synopsis,
        "notes": "\n\n".join(notes_parts),
        "cast": cast,
        "image_url": image_url,
    }


class PersonEditor(simpledialog.Dialog):
    """Petite fenêtre pour corriger un acteur/personnage avant ou après l'enregistrement."""
    def __init__(self, parent, title="Modifier la personne", first_name="", last_name="", role=""):
        self.initial_first_name = first_name or ""
        self.initial_last_name = last_name or ""
        self.initial_role = role or ""
        self.result = None
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Prénom / nom affiché").grid(row=0, column=0, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(master, text="Nom / deuxième partie").grid(row=0, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(master, text="Rôle / personnage").grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 0))

        self.first_var = tk.StringVar(value=self.initial_first_name)
        self.last_var = tk.StringVar(value=self.initial_last_name)
        self.role_var = tk.StringVar(value=self.initial_role)

        first_entry = ttk.Entry(master, textvariable=self.first_var, width=28)
        first_entry.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        ttk.Entry(master, textvariable=self.last_var, width=28).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Entry(master, textvariable=self.role_var, width=58).grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        master.columnconfigure(0, weight=1)
        master.columnconfigure(1, weight=1)
        return first_entry

    def validate(self):
        first = self.first_var.get().strip()
        last = self.last_var.get().strip()
        if not first and not last:
            messagebox.showwarning(APP_NAME, "Renseigne au moins un prénom ou un nom.", parent=self)
            return False
        return True

    def apply(self):
        self.result = {
            "first_name": self.first_var.get().strip(),
            "last_name": self.last_var.get().strip(),
            "role": self.role_var.get().strip(),
        }


class Database:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_db()

    def init_db(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                country TEXT,
                total_episodes INTEGER DEFAULT 0,
                link TEXT,
                photo_path TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT,
                last_name TEXT,
                display_name TEXT GENERATED ALWAYS AS (TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))) VIRTUAL,
                UNIQUE(first_name, last_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS series_people (
                series_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                role TEXT,
                PRIMARY KEY(series_id, person_id),
                FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                watch_date TEXT,
                seen INTEGER DEFAULT 0,
                FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE,
                UNIQUE(series_id, episode_number)
            )
        """)
        cur.execute("DELETE FROM episodes WHERE series_id NOT IN (SELECT id FROM series)")
        cur.execute("DELETE FROM series_people WHERE series_id NOT IN (SELECT id FROM series)")
        cur.execute("DELETE FROM people WHERE id NOT IN (SELECT DISTINCT person_id FROM series_people)")
        self.conn.commit()

    def add_or_update_series(self, series_id, title, country, total_episodes, link, photo_path, notes):
        cur = self.conn.cursor()
        if series_id:
            cur.execute("""
                UPDATE series SET title=?, country=?, total_episodes=?, link=?, photo_path=?, notes=? WHERE id=?
            """, (title, country, total_episodes, link, photo_path, notes, series_id))
        else:
            cur.execute("""
                INSERT INTO series(title, country, total_episodes, link, photo_path, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, country, total_episodes, link, photo_path, notes))
            series_id = cur.lastrowid
        self.ensure_episode_count(series_id, total_episodes)
        self.conn.commit()
        return series_id

    def ensure_episode_count(self, series_id, total):
        cur = self.conn.cursor()
        if total < 0:
            total = 0
        for n in range(1, total + 1):
            cur.execute("INSERT OR IGNORE INTO episodes(series_id, episode_number) VALUES (?, ?)", (series_id, n))
        cur.execute("DELETE FROM episodes WHERE series_id=? AND episode_number > ?", (series_id, total))

    def delete_series(self, series_id):
        self.conn.execute("DELETE FROM series WHERE id=?", (series_id,))
        self.cleanup_orphan_people()
        self.conn.commit()

    def list_countries(self):
        return [row["country"] for row in self.conn.execute("""
            SELECT DISTINCT country FROM series
            WHERE TRIM(COALESCE(country, '')) != ''
            ORDER BY LOWER(country)
        """).fetchall()]

    def list_series_options(self):
        return self.conn.execute("""
            SELECT id, title, country FROM series
            ORDER BY LOWER(title), LOWER(COALESCE(country, '')), id
        """).fetchall()

    def list_series(self, search="", country="", person_id=None):
        params = []
        sql = """
            SELECT s.*, 
                   COALESCE(SUM(e.seen), 0) AS seen_count,
                   COUNT(e.id) AS ep_count
            FROM series s
            LEFT JOIN episodes e ON e.series_id = s.id
        """
        if person_id:
            sql += " JOIN series_people sp ON sp.series_id = s.id "
        where = []
        if search:
            where.append("LOWER(s.title) LIKE ?")
            params.append(f"%{search.lower()}%")
        if country and country != "Tous":
            where.append("LOWER(COALESCE(s.country, '')) = ?")
            params.append(country.lower())
        if person_id:
            where.append("sp.person_id=?")
            params.append(person_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY s.id ORDER BY LOWER(s.title)"
        return self.conn.execute(sql, params).fetchall()

    def get_series(self, series_id):
        return self.conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()

    def get_episodes(self, series_id):
        return self.conn.execute("SELECT * FROM episodes WHERE series_id=? ORDER BY episode_number", (series_id,)).fetchall()

    def update_episode(self, episode_id, seen, watch_date):
        self.conn.execute("UPDATE episodes SET seen=?, watch_date=? WHERE id=?", (1 if seen else 0, watch_date, episode_id))
        self.conn.commit()

    def list_people(self):
        return self.conn.execute("SELECT * FROM people ORDER BY LOWER(display_name)").fetchall()

    def get_or_create_person_id(self, first_name, last_name):
        first_name = first_name.strip()
        last_name = last_name.strip()
        if not first_name and not last_name:
            return None
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO people(first_name, last_name) VALUES (?, ?)", (first_name, last_name))
        person = cur.execute("SELECT id FROM people WHERE first_name=? AND last_name=?", (first_name, last_name)).fetchone()
        return person["id"] if person else None

    def add_person_to_series(self, series_id, first_name, last_name, role=""):
        person_id = self.get_or_create_person_id(first_name, last_name)
        if not person_id:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO series_people(series_id, person_id, role) VALUES (?, ?, ?)",
            (series_id, person_id, role.strip()),
        )
        self.conn.commit()

    def set_person_for_series(self, series_id, old_person_id, first_name, last_name, role=""):
        new_person_id = self.get_or_create_person_id(first_name, last_name)
        if not new_person_id:
            return
        cur = self.conn.cursor()
        cur.execute("DELETE FROM series_people WHERE series_id=? AND person_id=?", (series_id, old_person_id))
        cur.execute(
            "INSERT OR REPLACE INTO series_people(series_id, person_id, role) VALUES (?, ?, ?)",
            (series_id, new_person_id, role.strip()),
        )
        self.cleanup_orphan_people()
        self.conn.commit()

    def remove_person_from_series(self, series_id, person_id):
        self.conn.execute("DELETE FROM series_people WHERE series_id=? AND person_id=?", (series_id, person_id))
        self.cleanup_orphan_people()
        self.conn.commit()

    def cleanup_orphan_people(self):
        self.conn.execute("""
            DELETE FROM people
            WHERE id NOT IN (SELECT DISTINCT person_id FROM series_people)
        """)

    def get_person_for_series(self, series_id, person_id):
        return self.conn.execute("""
            SELECT p.*, sp.role FROM people p
            JOIN series_people sp ON sp.person_id = p.id
            WHERE sp.series_id=? AND p.id=?
        """, (series_id, person_id)).fetchone()

    def people_for_series(self, series_id):
        return self.conn.execute("""
            SELECT p.*, sp.role FROM people p
            JOIN series_people sp ON sp.person_id = p.id
            WHERE sp.series_id=? ORDER BY LOWER(p.display_name)
        """, (series_id,)).fetchall()


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.frame = ttk.Frame(self.canvas)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass


class BLTrackerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(1000, 650)
        self.selected_series_id = None
        self.current_photo_path = ""
        self.photo_image = None
        self.episode_vars = {}
        self.pending_people = []
        self.series_choice_cache = {}
        self.build_ui()
        self.refresh_country_filter()
        self.refresh_people_filter()
        self.refresh_series_selector()
        self.refresh_series()

    def build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Big.TButton", padding=8)
        style.configure("Save.TButton", padding=8, font=("Segoe UI", 10, "bold"), background="#2e7d32", foreground="white")
        style.map("Save.TButton", background=[("active", "#1b5e20"), ("pressed", "#0d4215")], foreground=[("active", "white")])
        style.configure("Import.TButton", padding=8, font=("Segoe UI", 10, "bold"), background="#1565c0", foreground="white")
        style.map("Import.TButton", background=[("active", "#0d47a1"), ("pressed", "#08306b")], foreground=[("active", "white")])
        style.configure("Danger.TButton", padding=7, font=("Segoe UI", 9, "bold"), background="#c62828", foreground="white")
        style.map("Danger.TButton", background=[("active", "#b71c1c"), ("pressed", "#7f0000")], foreground=[("active", "white")])
        style.configure("Card.TFrame", padding=10)

        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Suivi de séries BL", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Nouvelle série", command=self.clear_form, style="Big.TButton").pack(side="right")
        ttk.Button(top, text="IMPORTER depuis URL", command=self.import_from_url, style="Import.TButton").pack(side="right", padx=(0, 8))

        content = ttk.PanedWindow(main, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content, padding=(0, 0, 10, 0))
        right = ttk.Frame(content, padding=(10, 0, 0, 0))
        content.add(left, weight=2)
        content.add(right, weight=3)

        filter_box = ttk.LabelFrame(left, text="Filtres", padding=10)
        filter_box.pack(fill="x")
        self.search_var = tk.StringVar()
        ttk.Label(filter_box, text="Pays").grid(row=0, column=0, sticky="w")
        self.country_filter_var = tk.StringVar(value="Tous")
        self.country_combo = ttk.Combobox(filter_box, textvariable=self.country_filter_var, state="readonly", width=18)
        self.country_combo.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.country_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_series())
        ttk.Label(filter_box, text="Nom des acteurs").grid(row=0, column=1, sticky="w")
        self.person_filter_var = tk.StringVar(value="Tous")
        self.person_combo = ttk.Combobox(filter_box, textvariable=self.person_filter_var, state="readonly", width=24)
        self.person_combo.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        self.person_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_series())
        ttk.Button(filter_box, text="Réinitialiser", command=self.reset_filters).grid(row=1, column=2, sticky="ew")
        filter_box.columnconfigure(0, weight=1)
        filter_box.columnconfigure(1, weight=1)
        filter_box.columnconfigure(2, weight=0)

        list_box = ttk.LabelFrame(left, text="Séries", padding=8)
        list_box.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("title", "country", "progress")
        self.series_tree = ttk.Treeview(list_box, columns=columns, show="headings", height=20)
        self.series_tree.heading("title", text="Nom")
        self.series_tree.heading("country", text="Pays")
        self.series_tree.heading("progress", text="Vu")
        self.series_tree.column("title", width=220)
        self.series_tree.column("country", width=90, anchor="center")
        self.series_tree.column("progress", width=70, anchor="center")
        self.series_tree.pack(fill="both", expand=True, side="left")
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.series_tree.yview)
        self.series_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.series_tree.bind("<<TreeviewSelect>>", self.on_series_select)

        form = ttk.LabelFrame(right, text="Fiche série", padding=12)
        form.pack(fill="both", expand=True)

        self.title_var = tk.StringVar()
        self.country_var = tk.StringVar()
        self.total_ep_var = tk.StringVar(value="0")
        self.link_var = tk.StringVar()

        row = 0
        ttk.Label(form, text="Continuer le suivi d'une série enregistrée").grid(row=row, column=0, columnspan=4, sticky="w")
        self.series_choice_var = tk.StringVar(value="Choisir une série enregistrée...")
        self.series_choice_combo = ttk.Combobox(form, textvariable=self.series_choice_var, state="readonly")
        self.series_choice_combo.grid(row=row+1, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        self.series_choice_combo.bind("<<ComboboxSelected>>", self.on_series_choice)
        row += 2

        ttk.Label(form, text="Nom de la série *").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.title_var).grid(row=row+1, column=0, columnspan=2, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Label(form, text="Pays").grid(row=row, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.country_var).grid(row=row+1, column=2, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Label(form, text="Nombre d'épisodes").grid(row=row, column=3, sticky="w")
        ttk.Entry(form, textvariable=self.total_ep_var, width=10).grid(row=row+1, column=3, sticky="ew", pady=(0, 8))
        row += 2

        ttk.Label(form, text="Lien rapide").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.link_var).grid(row=row+1, column=0, columnspan=3, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Button(form, text="Ouvrir le lien", command=self.open_link).grid(row=row+1, column=3, sticky="ew", pady=(0, 8))
        row += 2

        photo_frame = ttk.Frame(form)
        photo_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        self.photo_label = ttk.Label(photo_frame, text="Aucune photo sélectionnée", anchor="center", relief="groove", width=30)
        self.photo_label.pack(side="left", fill="y", padx=(0, 12))
        ttk.Button(photo_frame, text="Choisir une photo", command=self.choose_photo).pack(side="left", padx=(0, 8))
        ttk.Button(photo_frame, text="Retirer la photo", command=self.remove_photo).pack(side="left")
        row += 1

        ttk.Label(form, text="Résumé / Notes").grid(row=row, column=0, sticky="w")
        self.notes_text = tk.Text(form, height=5, wrap="word", font=("Segoe UI", 9))
        self.notes_text.grid(row=row+1, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        row += 2

        button_row = ttk.Frame(form)
        button_row.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Button(button_row, text="ENREGISTRER", command=self.save_series, style="Save.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Supprimer", command=self.delete_selected_series).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Exporter sauvegarde", command=self.export_backup).pack(side="right")
        row += 1

        lower = ttk.PanedWindow(form, orient="horizontal")
        lower.grid(row=row, column=0, columnspan=4, sticky="nsew")

        episodes_box = ttk.LabelFrame(lower, text="Épisodes vus", padding=8)
        people_box = ttk.LabelFrame(lower, text="Personnages principaux / acteurs", padding=8)
        lower.add(episodes_box, weight=1)
        lower.add(people_box, weight=1)

        self.episodes_frame = ScrollFrame(episodes_box)
        self.episodes_frame.pack(fill="both", expand=True)

        person_form = ttk.Frame(people_box)
        person_form.pack(fill="x", pady=(0, 8))
        self.person_first_var = tk.StringVar()
        self.person_last_var = tk.StringVar()
        self.person_role_var = tk.StringVar()
        ttk.Label(person_form, text="Prénom").grid(row=0, column=0, sticky="w")
        ttk.Entry(person_form, textvariable=self.person_first_var).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(person_form, text="Nom").grid(row=0, column=1, sticky="w")
        ttk.Entry(person_form, textvariable=self.person_last_var).grid(row=1, column=1, sticky="ew", padx=(0, 6))
        ttk.Label(person_form, text="Rôle / personnage").grid(row=0, column=2, sticky="w")
        ttk.Entry(person_form, textvariable=self.person_role_var).grid(row=1, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(person_form, text="Ajouter", command=self.add_person).grid(row=1, column=3, sticky="ew")
        person_form.columnconfigure(0, weight=1)
        person_form.columnconfigure(1, weight=1)
        person_form.columnconfigure(2, weight=1)

        self.people_tree = ttk.Treeview(people_box, columns=("name", "role"), show="headings", height=8)
        self.people_tree.heading("name", text="Nom / Prénom")
        self.people_tree.heading("role", text="Rôle")
        self.people_tree.column("name", width=180)
        self.people_tree.column("role", width=160)
        self.people_tree.pack(fill="both", expand=True)
        self.people_tree.bind("<Double-1>", lambda e: self.edit_person())
        people_buttons = ttk.Frame(people_box)
        people_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(people_buttons, text="Modifier la personne sélectionnée", command=self.edit_person).pack(side="left", padx=(0, 8))
        ttk.Button(people_buttons, text="SUPPRIMER le personnage sélectionné", command=self.remove_person, style="Danger.TButton").pack(side="left")

        form.columnconfigure(0, weight=2)
        form.columnconfigure(1, weight=2)
        form.columnconfigure(2, weight=1)
        form.columnconfigure(3, weight=1)
        form.rowconfigure(row, weight=1)

    def refresh_country_filter(self):
        current = self.country_filter_var.get() if hasattr(self, "country_filter_var") else "Tous"
        values = ["Tous"] + self.db.list_countries()
        self.country_combo["values"] = values
        if current in values:
            self.country_filter_var.set(current)
        else:
            self.country_filter_var.set("Tous")

    def refresh_people_filter(self):
        current = self.person_filter_var.get() if hasattr(self, "person_filter_var") else "Tous"
        self.people_cache = self.db.list_people()
        values = ["Tous"] + [p["display_name"] for p in self.people_cache]
        self.person_combo["values"] = values
        if current in values:
            self.person_filter_var.set(current)
        else:
            self.person_filter_var.set("Tous")

    def refresh_series_selector(self):
        rows = self.db.list_series_options()
        self.series_choice_cache = {}
        values = ["Choisir une série enregistrée..."]
        for row in rows:
            label = row["title"] or "Sans titre"
            if row["country"]:
                label += f" — {row['country']}"
            label += f"  [#{row['id']}]"
            values.append(label)
            self.series_choice_cache[label] = row["id"]
        self.series_choice_combo["values"] = values
        if self.selected_series_id:
            self.set_series_selector(self.selected_series_id)
        elif self.series_choice_var.get() not in values:
            self.series_choice_var.set(values[0])

    def set_series_selector(self, series_id):
        for label, sid in self.series_choice_cache.items():
            if sid == series_id:
                self.series_choice_var.set(label)
                return
        self.series_choice_var.set("Choisir une série enregistrée...")

    def on_series_choice(self, _event=None):
        label = self.series_choice_var.get()
        series_id = self.series_choice_cache.get(label)
        if not series_id:
            return
        self.load_series(series_id)
        if str(series_id) in self.series_tree.get_children():
            self.series_tree.selection_set(str(series_id))
            self.series_tree.see(str(series_id))

    def reset_filters(self):
        self.country_filter_var.set("Tous")
        self.person_filter_var.set("Tous")
        self.refresh_series()

    def selected_country_filter(self):
        value = self.country_filter_var.get().strip()
        return "" if not value or value == "Tous" else value

    def selected_person_filter_id(self):
        name = self.person_filter_var.get()
        if not name or name == "Tous":
            return None
        for p in self.people_cache:
            if p["display_name"] == name:
                return p["id"]
        return None

    def refresh_series(self):
        for i in self.series_tree.get_children():
            self.series_tree.delete(i)
        rows = self.db.list_series("", self.selected_country_filter(), self.selected_person_filter_id())
        for row in rows:
            progress = f"{row['seen_count']}/{row['ep_count']}"
            self.series_tree.insert("", "end", iid=str(row["id"]), values=(row["title"], row["country"] or "", progress))

    def get_notes_text(self):
        return self.notes_text.get("1.0", "end").strip()

    def set_notes_text(self, value):
        self.notes_text.delete("1.0", "end")
        if value:
            self.notes_text.insert("1.0", value)

    def clear_form(self):
        self.selected_series_id = None
        self.title_var.set("")
        self.country_var.set("")
        self.total_ep_var.set("0")
        self.link_var.set("")
        self.set_notes_text("")
        self.pending_people = []
        self.current_photo_path = ""
        self.update_photo_preview("")
        self.clear_episodes()
        for item in self.people_tree.get_children():
            self.people_tree.delete(item)
        if hasattr(self, "series_choice_var"):
            self.series_choice_var.set("Choisir une série enregistrée...")
        self.title_var.set("")

    def on_series_select(self, _event=None):
        selected = self.series_tree.selection()
        if not selected:
            return
        series_id = int(selected[0])
        self.load_series(series_id)

    def load_series(self, series_id):
        s = self.db.get_series(series_id)
        if not s:
            return
        self.selected_series_id = series_id
        if hasattr(self, "series_choice_cache"):
            self.set_series_selector(series_id)
        self.title_var.set(s["title"] or "")
        self.country_var.set(s["country"] or "")
        self.total_ep_var.set(str(s["total_episodes"] or 0))
        self.link_var.set(s["link"] or "")
        self.set_notes_text(s["notes"] or "")
        self.pending_people = []
        self.current_photo_path = s["photo_path"] or ""
        self.update_photo_preview(self.current_photo_path)
        self.load_episodes(series_id)
        self.load_people(series_id)

    def save_series(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning(APP_NAME, "Le nom de la série est obligatoire.")
            return
        try:
            total = int(self.total_ep_var.get().strip() or "0")
            if total < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "Le nombre d'épisodes doit être un nombre positif.")
            return
        self.selected_series_id = self.db.add_or_update_series(
            self.selected_series_id,
            title,
            self.country_var.get().strip(),
            total,
            self.link_var.get().strip(),
            self.current_photo_path,
            self.get_notes_text(),
        )
        if self.pending_people:
            for person in self.pending_people:
                self.db.add_person_to_series(
                    self.selected_series_id,
                    person.get("first_name", ""),
                    person.get("last_name", ""),
                    person.get("role", ""),
                )
            self.pending_people = []
        self.refresh_country_filter()
        self.refresh_people_filter()
        self.refresh_series_selector()
        self.refresh_series()
        self.load_series(self.selected_series_id)
        messagebox.showinfo(APP_NAME, "Série enregistrée.")

    def delete_selected_series(self):
        if not self.selected_series_id:
            return
        if messagebox.askyesno(APP_NAME, "Supprimer cette série et ses épisodes ?"):
            self.db.delete_series(self.selected_series_id)
            self.clear_form()
            self.refresh_country_filter()
            self.refresh_people_filter()
            self.refresh_series_selector()
            self.refresh_series()

    def choose_photo(self):
        path = filedialog.askopenfilename(
            title="Choisir une photo",
            filetypes=[("Images", "*.png *.gif *.jpg *.jpeg *.webp"), ("PNG", "*.png"), ("GIF", "*.gif"), ("JPG", "*.jpg *.jpeg"), ("WEBP", "*.webp"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".gif", ".jpg", ".jpeg", ".webp"):
            messagebox.showwarning(APP_NAME, "Choisis un fichier image PNG, GIF, JPG ou WEBP.")
            return
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", self.title_var.get().strip()).strip("_") or "serie"
        safe_source_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", os.path.basename(path))
        dest = os.path.join(IMG_DIR, f"{safe_title}_{date.today().isoformat()}_{safe_source_name}")
        shutil.copy2(path, dest)
        self.current_photo_path = dest
        self.update_photo_preview(dest)

    def remove_photo(self):
        self.current_photo_path = ""
        self.update_photo_preview("")

    def update_photo_preview(self, path):
        self.photo_image = None
        if path and os.path.exists(path):
            filename = os.path.basename(path)
            try:
                max_w, max_h = 160, 210
                if PIL_AVAILABLE:
                    img = Image.open(path)
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail((max_w, max_h))
                    self.photo_image = ImageTk.PhotoImage(img)
                else:
                    # Tkinter seul affiche surtout PNG/GIF. Pillow ajoute JPG/WEBP.
                    img = tk.PhotoImage(file=path)
                    scale = max(img.width() // max_w, img.height() // max_h, 1)
                    if scale > 1:
                        img = img.subsample(scale, scale)
                    self.photo_image = img
                self.photo_label.configure(image=self.photo_image, text="")
                return
            except Exception:
                aide = "aperçu non disponible"
                if not PIL_AVAILABLE:
                    aide = "installe Pillow ou lance INSTALLER_WINDOWS.bat"
                self.photo_label.configure(image="", text=f"Photo enregistrée\n{filename}\n({aide})")
                return
        self.photo_label.configure(image="", text="Aucune photo sélectionnée")

    def open_link(self):
        link = self.link_var.get().strip()
        if not link:
            messagebox.showinfo(APP_NAME, "Aucun lien n'est renseigné.")
            return
        if not link.startswith(("http://", "https://")):
            link = "https://" + link
        webbrowser.open(link)

    def import_from_url(self):
        url = simpledialog.askstring(
            "Importer depuis URL",
            "Colle ici l'adresse de la page BL France :",
            parent=self,
        )
        if not url:
            return
        url = url.strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            data = import_from_blfrance(url)
        except Exception as exc:
            messagebox.showerror(
                APP_NAME,
                "Impossible d'importer cette page.\n\n"
                "Vérifie l'adresse internet, puis réessaie.\n\n"
                f"Détail technique : {exc}",
            )
            self.configure(cursor="")
            return
        self.configure(cursor="")

        if not data.get("title") and not data.get("total_episodes"):
            messagebox.showwarning(
                APP_NAME,
                "Je n'ai pas trouvé de fiche série exploitable sur cette page.\n"
                "Essaie de coller directement l'URL de la fiche drama.",
            )
            return

        cast_names = []
        for p in data.get("cast", [])[:6]:
            name = " ".join(x for x in [p.get("first_name", ""), p.get("last_name", "")] if x).strip()
            if p.get("role"):
                cast_names.append(f"- {name} ({p['role']})")
            else:
                cast_names.append(f"- {name}")
        cast_preview = "\n".join(cast_names) if cast_names else "- Aucun casting détecté automatiquement"
        summary = (
            f"Titre : {data.get('title') or '(non trouvé)'}\n"
            f"Pays : {data.get('country') or '(non trouvé)'}\n"
            f"Épisodes : {data.get('total_episodes') or 0}\n"
            f"Personnes détectées : {len(data.get('cast', []))}\n\n"
            f"Aperçu casting :\n{cast_preview}\n\n"
            "Remplir une nouvelle fiche avec ces informations ?\n"
            "Tu pourras encore corriger les champs avant d'enregistrer."
        )
        if not messagebox.askyesno("Import trouvé", summary):
            return

        self.clear_form()
        self.title_var.set(data.get("title") or "")
        self.country_var.set(data.get("country") or "")
        self.total_ep_var.set(str(data.get("total_episodes") or 0))
        self.link_var.set(data.get("source_url") or url)
        self.set_notes_text(data.get("notes") or "")

        image_url = data.get("image_url") or ""
        if image_url:
            try:
                self.current_photo_path = download_image(image_url, data.get("title") or "serie")
                self.update_photo_preview(self.current_photo_path)
            except Exception:
                self.current_photo_path = ""
                self.update_photo_preview("")
                messagebox.showwarning(
                    APP_NAME,
                    "Les informations ont été importées, mais l'image n'a pas pu être téléchargée."
                )

        self.pending_people = data.get("cast", [])
        self.show_pending_people()
        self.clear_episodes()
        ttk.Label(
            self.episodes_frame.frame,
            text="Les épisodes seront créés automatiquement après l'enregistrement."
        ).pack(anchor="w")

    def show_pending_people(self):
        for item in self.people_tree.get_children():
            self.people_tree.delete(item)
        for idx, p in enumerate(self.pending_people):
            name = " ".join(x for x in [p.get("first_name", ""), p.get("last_name", "")] if x).strip()
            self.people_tree.insert("", "end", iid=f"pending_{idx}", values=(name, p.get("role", "")))

    def clear_episodes(self):
        for widget in self.episodes_frame.frame.winfo_children():
            widget.destroy()
        self.episode_vars = {}

    def load_episodes(self, series_id):
        self.clear_episodes()
        eps = self.db.get_episodes(series_id)
        if not eps:
            ttk.Label(self.episodes_frame.frame, text="Enregistre un nombre d'épisodes pour créer la liste.").pack(anchor="w")
            return
        for ep in eps:
            line = ttk.Frame(self.episodes_frame.frame)
            line.pack(fill="x", pady=2)
            seen_var = tk.BooleanVar(value=bool(ep["seen"]))
            date_var = tk.StringVar(value=ep["watch_date"] or "")
            cb = ttk.Checkbutton(line, text=f"Épisode {ep['episode_number']}", variable=seen_var,
                                 command=lambda eid=ep["id"], sv=seen_var, dv=date_var: self.on_episode_change(eid, sv, dv))
            cb.pack(side="left", padx=(0, 8))
            ttk.Label(line, text="Date :").pack(side="left")
            entry = ttk.Entry(line, textvariable=date_var, width=14)
            entry.pack(side="left", padx=(4, 8))
            entry.bind("<FocusOut>", lambda e, eid=ep["id"], sv=seen_var, dv=date_var: self.on_episode_change(eid, sv, dv))
            ttk.Button(line, text="Aujourd'hui", command=lambda eid=ep["id"], sv=seen_var, dv=date_var: self.mark_today(eid, sv, dv)).pack(side="left")
            self.episode_vars[ep["id"]] = (seen_var, date_var)

    def mark_today(self, episode_id, seen_var, date_var):
        seen_var.set(True)
        date_var.set(date.today().isoformat())
        self.on_episode_change(episode_id, seen_var, date_var)

    def on_episode_change(self, episode_id, seen_var, date_var):
        if seen_var.get() and not date_var.get().strip():
            date_var.set(date.today().isoformat())
        self.db.update_episode(episode_id, seen_var.get(), date_var.get().strip())
        self.refresh_series()

    def load_people(self, series_id):
        for item in self.people_tree.get_children():
            self.people_tree.delete(item)
        for p in self.db.people_for_series(series_id):
            self.people_tree.insert("", "end", iid=str(p["id"]), values=(p["display_name"], p["role"] or ""))

    def add_person(self):
        first = self.person_first_var.get().strip()
        last = self.person_last_var.get().strip()
        role = self.person_role_var.get().strip()
        if not first and not last:
            return
        if not self.selected_series_id:
            self.pending_people.append({"first_name": first, "last_name": last, "role": role})
            self.person_first_var.set("")
            self.person_last_var.set("")
            self.person_role_var.set("")
            self.show_pending_people()
            return
        self.db.add_person_to_series(self.selected_series_id, first, last, role)
        self.person_first_var.set("")
        self.person_last_var.set("")
        self.person_role_var.set("")
        self.load_people(self.selected_series_id)
        self.refresh_people_filter()
        self.refresh_series()


    def edit_person(self):
        selected = self.people_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Sélectionne d'abord une personne à modifier.")
            return
        selected_id = selected[0]

        if selected_id.startswith("pending_"):
            try:
                idx = int(selected_id.split("_", 1)[1])
                person = self.pending_people[idx]
            except (ValueError, IndexError):
                return
            dialog = PersonEditor(
                self,
                title="Modifier avant enregistrement",
                first_name=person.get("first_name", ""),
                last_name=person.get("last_name", ""),
                role=person.get("role", ""),
            )
            if dialog.result:
                self.pending_people[idx] = dialog.result
                self.show_pending_people()
            return

        if not self.selected_series_id:
            return
        person = self.db.get_person_for_series(self.selected_series_id, int(selected_id))
        if not person:
            return
        dialog = PersonEditor(
            self,
            title="Modifier la personne",
            first_name=person["first_name"] or "",
            last_name=person["last_name"] or "",
            role=person["role"] or "",
        )
        if dialog.result:
            self.db.set_person_for_series(
                self.selected_series_id,
                int(selected_id),
                dialog.result.get("first_name", ""),
                dialog.result.get("last_name", ""),
                dialog.result.get("role", ""),
            )
            self.load_people(self.selected_series_id)
            self.refresh_people_filter()
            self.refresh_series()

    def remove_person(self):
        selected = self.people_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Sélectionne d'abord le personnage / acteur à supprimer.")
            return
        selected_id = selected[0]

        values = self.people_tree.item(selected_id, "values")
        display_name = values[0] if values else "cette ligne"
        if not messagebox.askyesno(APP_NAME, f"Supprimer {display_name} de cette série ?"):
            return

        if selected_id.startswith("pending_"):
            try:
                idx = int(selected_id.split("_", 1)[1])
                del self.pending_people[idx]
            except (ValueError, IndexError):
                pass
            self.show_pending_people()
            return
        if not self.selected_series_id:
            return
        self.db.remove_person_from_series(self.selected_series_id, int(selected_id))
        self.load_people(self.selected_series_id)
        self.refresh_people_filter()
        self.refresh_series()

    def export_backup(self):
        dest = filedialog.asksaveasfilename(
            title="Exporter la sauvegarde",
            defaultextension=".db",
            filetypes=[("Base SQLite", "*.db"), ("Tous les fichiers", "*.*")],
        )
        if not dest:
            return
        self.db.conn.commit()
        shutil.copy2(DB_PATH, dest)
        messagebox.showinfo(APP_NAME, "Sauvegarde exportée.")


if __name__ == "__main__":
    app = BLTrackerApp()
    app.mainloop()
