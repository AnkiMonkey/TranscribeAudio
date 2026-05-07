import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    BooleanVar,
    Button,
    Frame,
    Label,
    StringVar,
    Text,
    Tk,
    filedialog,
    messagebox,
    ttk,
)

import soundfile as sf
import speech_recognition as sr

try:
    from moviepy import AudioFileClip
except ImportError:
    AudioFileClip = None

try:
    import winreg
except ImportError:
    winreg = None


LANGUAGES = {
    "Slovak": "sk-SK",
    "English": "en-US",
}

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
DEFAULT_OLLAMA_MODEL = "gemma3:4b"
AI_STYLES = ("Proofread", "Clean notes", "Polish", "Keep raw meaning")
MOVIEPY_AUDIO_EXTENSIONS = {".m4a", ".mp4", ".aac", ".mov"}
EXPORT_LINE_WIDTH = 100
AUTOSAVE_PATH = Path("autosave_transcription.txt")
AUTOSAVE_INTERVAL_MS = 10000
PLACEHOLDER_START = "Choose Slovak or English"

PUNCTUATION = {
    "en-US": {
        "full stop": ".",
        "fullstop": ".",
        "period": ".",
        "comma": ",",
        "semicolon": ";",
        "colon": ":",
        "question mark": "?",
        "exclamation mark": "!",
        "new line": "\n",
        "new paragraph": "\n\n",
        "enter": "\n",
        "new bullet": "\n- ",
        "bullet point": "\n- ",
        "next bullet": "\n- ",
        "heading": "\n\n## ",
        "end of sentence": ".",
        "open bracket": "(",
        "close bracket": ")",
    },
    "sk-SK": {
        "bodka": ".",
        "koniec vety": ".",
        "ciarka": ",",
        "čiarka": ",",
        "bodkociarka": ";",
        "bodkočiarka": ";",
        "dvojbodka": ":",
        "otaznik": "?",
        "otáznik": "?",
        "vykricnik": "!",
        "výkričník": "!",
        "novy riadok": "\n",
        "nový riadok": "\n",
        "novy odstavec": "\n\n",
        "nový odstavec": "\n\n",
        "enter": "\n",
        "novy bod": "\n- ",
        "nový bod": "\n- ",
        "dalsi bod": "\n- ",
        "ďalší bod": "\n- ",
        "novy odrazka": "\n- ",
        "nový odrážka": "\n- ",
        "nova odrazka": "\n- ",
        "nová odrazka": "\n- ",
        "nová odrážka": "\n- ",
        "novu odrazku": "\n- ",
        "novú odrážku": "\n- ",
        "nova odrážka": "\n- ",
        "odrazka": "\n- ",
        "odrážka": "\n- ",
        "nadpis": "\n\n## ",
        "otvor zátvorku": "(",
        "otvor zatvorku": "(",
        "zatvor zátvorku": ")",
        "zatvor zatvorku": ")",
    },
}

PALETTES = {
    "dark": {
        "app_bg": "#0f172a",
        "header_bg": "#020617",
        "text_bg": "#111827",
        "text_fg": "#e5e7eb",
        "muted_fg": "#94a3b8",
        "label_fg": "#f8fafc",
        "border": "#334155",
        "input_bg": "#1f2937",
        "input_fg": "#f8fafc",
        "insert": "#93c5fd",
        "primary": "#3b82f6",
        "primary_active": "#2563eb",
        "success": "#10b981",
        "success_active": "#059669",
        "warning": "#f59e0b",
        "warning_active": "#d97706",
        "danger": "#ef4444",
        "danger_active": "#dc2626",
        "dark_button": "#475569",
        "dark_button_active": "#334155",
        "purple": "#8b5cf6",
        "purple_active": "#7c3aed",
        "off_bg": "#334155",
        "off_fg": "#e5e7eb",
    },
    "light": {
        "app_bg": "#f5f7fb",
        "header_bg": "#1f2937",
        "text_bg": "#ffffff",
        "text_fg": "#111827",
        "muted_fg": "#c7d2fe",
        "label_fg": "#111827",
        "border": "#d1d5db",
        "input_bg": "#ffffff",
        "input_fg": "#111827",
        "insert": "#2563eb",
        "primary": "#2563eb",
        "primary_active": "#1d4ed8",
        "success": "#059669",
        "success_active": "#047857",
        "warning": "#f59e0b",
        "warning_active": "#d97706",
        "danger": "#dc2626",
        "danger_active": "#b91c1c",
        "dark_button": "#111827",
        "dark_button_active": "#030712",
        "purple": "#7c3aed",
        "purple_active": "#6d28d9",
        "off_bg": "#e5e7eb",
        "off_fg": "#111827",
    },
}


def windows_prefers_dark():
    if winreg is None:
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except OSError:
        return False


def build_output_path():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path(f"transcription_{timestamp}.txt")


def convert_with_moviepy(path):
    if AudioFileClip is None:
        raise RuntimeError(
            "M4A support needs moviepy. Install it with: python -m pip install moviepy"
        )

    temp = NamedTemporaryFile(delete=False, suffix=".wav")
    temp_path = Path(temp.name)
    temp.close()

    try:
        with AudioFileClip(str(path)) as clip:
            clip.write_audiofile(
                str(temp_path),
                fps=16000,
                nbytes=2,
                codec="pcm_s16le",
                logger=None,
            )
        return temp_path
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def load_audio(path):
    path = Path(path)
    temp_path = None

    if path.suffix.lower() in MOVIEPY_AUDIO_EXTENSIONS:
        temp_path = convert_with_moviepy(path)
        path_to_read = temp_path
    else:
        path_to_read = path

    try:
        data, sample_rate = sf.read(path_to_read, dtype="int16", always_2d=False)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass

    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1).astype("int16")

    return sr.AudioData(data.tobytes(), sample_rate, 2)


def list_ollama_models():
    request = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []

    return [model["name"] for model in result.get("models", []) if model.get("name")]


def build_ai_prompt(text, language_name, style_name):
    if language_name == "Slovak":
        style_rules = {
            "Proofread": "Oprav text neutrálne a prirodzene.",
            "Clean notes": "Uprav text ako čisté poznámky, ale nezmeň význam.",
            "Polish": "Jemne vylepši formulácie, aby text pôsobil plynulejšie.",
            "Keep raw meaning": "Zachovaj čo najviac pôvodné formulácie, oprav iba chyby.",
        }
        return (
            "Oprav slovenský diktovaný prepis.\n"
            "Oprav chyby rozpoznávania reči, preklepy, diakritiku, interpunkciu, veľké písmená, "
            "gramatiku, pády a nesprávne predložky.\n"
            f"Štýl: {style_rules.get(style_name, style_rules['Proofread'])}\n"
            "Zachovaj pôvodný význam. Text neskracuj, nezhrňuj a nepridávaj nové informácie.\n"
            "Vráť iba opravený text bez komentára.\n\n"
            f"Prepis:\n{text}"
        )

    style_rules = {
        "Proofread": "Proofread neutrally and naturally.",
        "Clean notes": "Format the text as clean notes without changing the meaning.",
        "Polish": "Lightly polish wording so the text reads smoothly.",
        "Keep raw meaning": "Keep wording as close as possible and only fix mistakes.",
    }
    return (
        "You are editing a dictated English transcript.\n"
        "Fix speech recognition mistakes, typos, punctuation, capitalization, grammar, and wording.\n"
        f"Style: {style_rules.get(style_name, style_rules['Proofread'])}\n"
        "Keep the original meaning. Do not summarize. Do not add new ideas.\n"
        "Return only the corrected text, with no notes or explanation.\n\n"
        f"Transcript:\n{text}"
    )


def clean_text_with_ollama(text, language_name, model_name, style_name):
    payload = {
        "model": model_name,
        "prompt": build_ai_prompt(text, language_name, style_name),
        "stream": False,
        "options": {
            "temperature": 0.1,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(
            "Ollama is not running. Start Ollama and make sure the model is installed:\n"
            f"ollama pull {model_name}"
        ) from error

    return result.get("response", "").strip()


def replace_spoken_punctuation(text, language_code):
    replacements = sorted(
        PUNCTUATION[language_code].items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for phrase, punctuation in replacements:
        pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
        text = re.sub(pattern, punctuation, text, flags=re.IGNORECASE)

    return text


def tidy_text(text, language_code):
    text = replace_spoken_punctuation(text, language_code)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +([.,;:?!])", r"\1", text)
    text = re.sub(r"([.,;:?!])(?=[^\s\]\)])", r"\1 ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"##\s+", "## ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"^- +", "- ", text, flags=re.MULTILINE)
    text = text.strip()

    if language_code == "en-US":
        text = re.sub(r"\bi\b", "I", text)

    return text


def wrap_export_text(text, width=EXPORT_LINE_WIDTH):
    wrapped_lines = []

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            wrapped_lines.append("")
            continue

        if stripped.startswith("## "):
            wrapped_lines.append(stripped)
            continue

        prefix = ""
        continuation_prefix = ""
        content = stripped

        if stripped.startswith("- "):
            prefix = "- "
            continuation_prefix = "  "
            content = stripped[2:].strip()

        words = content.split()
        current = prefix

        for word in words:
            separator = "" if current in {prefix, continuation_prefix} else " "
            if len(current) + len(separator) + len(word) <= width:
                current += separator + word
                continue

            if current.strip():
                wrapped_lines.append(current.rstrip())

            current = continuation_prefix + word

            while len(current) > width:
                wrapped_lines.append(current[:width])
                current = continuation_prefix + current[width:]

        if current.strip():
            wrapped_lines.append(current.rstrip())

    return "\n".join(wrapped_lines).strip() + "\n"


class TranscribeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Transcribe")
        self.root.geometry("1180x720")
        self.root.minsize(980, 620)

        self.colors = PALETTES["dark" if windows_prefers_dark() else "light"]
        self.recognizer = sr.Recognizer()
        self.language_name = StringVar(value="Slovak")
        self.status = StringVar(value="Ready")
        self.log_status = StringVar(value="Autosave ready")
        self.auto_punctuation = BooleanVar(value=True)
        self.ai_model = StringVar(value=DEFAULT_OLLAMA_MODEL)
        self.ai_style = StringVar(value="Proofread")
        self.dictation_thread = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.is_dictating = False
        self.last_autosave_text = ""

        self.build_ui()
        self.bind_shortcuts()
        self.refresh_ollama_models(show_errors=False)
        self.update_dictation_buttons()
        self.schedule_autosave()

    @property
    def language_code(self):
        return LANGUAGES[self.language_name.get()]

    def build_ui(self):
        c = self.colors
        self.root.configure(bg=c["app_bg"])

        header = Frame(self.root, bg=c["header_bg"], padx=22, pady=18)
        header.pack(fill="x")

        Label(
            header,
            text="Transcribe",
            bg=c["header_bg"],
            fg="white",
            font=("Segoe UI", 22, "bold"),
        ).pack(side=LEFT)

        Label(
            header,
            textvariable=self.status,
            bg=c["header_bg"],
            fg=c["muted_fg"],
            font=("Segoe UI", 10),
        ).pack(side=RIGHT)

        settings = Frame(self.root, bg=c["app_bg"], padx=22)
        settings.pack(fill="x", pady=(14, 6))

        self.add_label(settings, "Language")
        language = self.make_combo(settings, self.language_name, list(LANGUAGES), 10)
        language.pack(side=LEFT, padx=(0, 12))

        self.add_label(settings, "Ollama")
        self.model_combo = self.make_combo(settings, self.ai_model, [DEFAULT_OLLAMA_MODEL], 16)
        self.model_combo.pack(side=LEFT, padx=(0, 8))

        self.refresh_models_button = self.make_button(
            settings,
            "Reload Models",
            lambda: self.refresh_ollama_models(show_errors=True),
            c["dark_button"],
            c["dark_button_active"],
        )
        self.refresh_models_button.pack(side=LEFT, padx=(0, 12))

        self.add_label(settings, "AI Style")
        style = self.make_combo(settings, self.ai_style, list(AI_STYLES), 14)
        style.pack(side=LEFT, padx=(0, 12))

        toolbar = Frame(self.root, bg=c["app_bg"], padx=22)
        toolbar.pack(fill="x", pady=(6, 14))

        self.new_button = self.make_button(
            toolbar,
            "New",
            self.new_document,
            c["dark_button"],
            c["dark_button_active"],
        )
        self.new_button.pack(side=LEFT, padx=(0, 8))

        self.auto_button = self.make_button(
            toolbar,
            "",
            self.toggle_auto_punctuation,
            c["purple"],
            c["purple_active"],
        )
        self.auto_button.pack(side=LEFT, padx=(0, 8))
        self.update_auto_button()

        self.open_button = self.make_button(
            toolbar,
            "Open Audio",
            self.open_audio,
            c["primary"],
            c["primary_active"],
        )
        self.open_button.pack(side=LEFT, padx=(0, 8))

        self.start_button = self.make_button(
            toolbar,
            "Start",
            self.start_dictation,
            c["success"],
            c["success_active"],
        )
        self.start_button.pack(side=LEFT, padx=(0, 8))

        self.pause_button = self.make_button(
            toolbar,
            "Pause",
            self.toggle_pause,
            c["warning"],
            c["warning_active"],
            fg="#111827",
        )
        self.pause_button.pack(side=LEFT, padx=(0, 8))

        self.stop_button = self.make_button(
            toolbar,
            "Stop",
            self.stop_dictation,
            c["danger"],
            c["danger_active"],
        )
        self.stop_button.pack(side=LEFT, padx=(0, 8))

        self.ai_button = self.make_button(
            toolbar,
            "AI Clean",
            self.clean_current_text_with_ai,
            c["purple"],
            c["purple_active"],
        )
        self.ai_button.pack(side=LEFT, padx=(0, 8))

        self.export_button = self.make_button(
            toolbar,
            "Export TXT",
            self.export_text,
            c["dark_button"],
            c["dark_button_active"],
        )
        self.export_button.pack(side=RIGHT)

        content = Frame(self.root, bg=c["app_bg"], padx=22, pady=0)
        content.pack(fill=BOTH, expand=True)

        Label(
            content,
            text="Transcript",
            bg=c["app_bg"],
            fg=c["label_fg"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        text_frame = Frame(content, bg=c["border"], padx=1, pady=1)
        text_frame.pack(fill=BOTH, expand=True)

        self.text = Text(
            text_frame,
            wrap="word",
            bg=c["text_bg"],
            fg=c["text_fg"],
            insertbackground=c["insert"],
            relief="flat",
            padx=16,
            pady=14,
            font=("Segoe UI", 12),
            undo=True,
        )
        self.text.pack(fill=BOTH, expand=True)
        self.insert_placeholder()

        footer = Frame(self.root, bg=c["header_bg"], padx=22, pady=8)
        footer.pack(fill="x")

        Label(
            footer,
            textvariable=self.log_status,
            bg=c["header_bg"],
            fg=c["muted_fg"],
            font=("Segoe UI", 9),
        ).pack(side=LEFT)

        Label(
            footer,
            text="Shortcuts: Ctrl+S Export | Ctrl+L AI Clean | Ctrl+N New | F5 Start/Pause | Esc Stop",
            bg=c["header_bg"],
            fg=c["muted_fg"],
            font=("Segoe UI", 9),
        ).pack(side=RIGHT)

    def add_label(self, parent, text):
        Label(
            parent,
            text=text,
            bg=self.colors["app_bg"],
            fg=self.colors["label_fg"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side=LEFT, padx=(0, 8))

    def make_combo(self, parent, variable, values, width):
        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "TCombobox",
            fieldbackground=self.colors["input_bg"],
            background=self.colors["input_bg"],
            foreground=self.colors["input_fg"],
            arrowcolor=self.colors["input_fg"],
        )
        return ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            width=width,
            state="readonly",
        )

    def make_button(self, parent, text, command, bg, active_bg, fg="white"):
        return Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=fg,
            disabledforeground="#94a3b8",
            relief="flat",
            padx=12,
            pady=8,
            font=("Segoe UI", 10, "bold"),
        )

    def bind_shortcuts(self):
        self.root.bind("<Control-s>", lambda event: self.export_text())
        self.root.bind("<Control-S>", lambda event: self.export_text())
        self.root.bind("<Control-l>", lambda event: self.clean_current_text_with_ai())
        self.root.bind("<Control-L>", lambda event: self.clean_current_text_with_ai())
        self.root.bind("<Control-n>", lambda event: self.new_document())
        self.root.bind("<Control-N>", lambda event: self.new_document())
        self.root.bind("<F5>", lambda event: self.start_or_pause_shortcut())
        self.root.bind("<Escape>", lambda event: self.stop_dictation())

    def insert_placeholder(self):
        self.text.delete("1.0", END)
        self.text.insert(
            END,
            "Choose Slovak or English, then open an audio file or use Start / Pause / Stop.\n\n"
            "AI Clean uses selected text when something is selected; otherwise it cleans the whole transcript.\n\n"
            "Auto punctuation ON means spoken commands become symbols:\n"
            "Slovak: bodka, čiarka, enter, nový riadok, nový odstavec, nová odrážka, ďalší bod, nadpis.\n"
            "English: period, comma, new line, new paragraph, bullet point, heading.",
        )

    def set_status(self, message):
        self.status.set(message)
        self.log_status.set(f"{datetime.now().strftime('%H:%M:%S')}  {message}")

    def refresh_ollama_models(self, show_errors):
        models = list_ollama_models()

        if models:
            self.model_combo.configure(values=models)
            if self.ai_model.get() not in models:
                self.ai_model.set(models[0])
            self.set_status(f"Loaded {len(models)} Ollama model(s)")
            return

        self.model_combo.configure(values=[self.ai_model.get() or DEFAULT_OLLAMA_MODEL])
        if not self.ai_model.get():
            self.ai_model.set(DEFAULT_OLLAMA_MODEL)

        if show_errors:
            messagebox.showwarning(
                "Transcribe",
                "Could not read Ollama models. Make sure Ollama is running.",
            )

    def toggle_auto_punctuation(self):
        self.auto_punctuation.set(not self.auto_punctuation.get())
        self.update_auto_button()

    def update_auto_button(self):
        c = self.colors
        if self.auto_punctuation.get():
            self.auto_button.configure(
                text="Auto punctuation: ON",
                bg=c["purple"],
                fg="white",
                activebackground=c["purple_active"],
                activeforeground="white",
            )
        else:
            self.auto_button.configure(
                text="Auto punctuation: OFF",
                bg=c["off_bg"],
                fg=c["off_fg"],
                activebackground=c["border"],
                activeforeground=c["off_fg"],
            )

    def update_dictation_buttons(self):
        if self.is_dictating:
            self.start_button.configure(state=DISABLED)
            self.pause_button.configure(state=NORMAL)
            self.stop_button.configure(state=NORMAL)
        else:
            self.start_button.configure(state=NORMAL)
            self.pause_button.configure(state=DISABLED, text="Pause")
            self.stop_button.configure(state=DISABLED)

    def clean_if_needed(self, text):
        if self.auto_punctuation.get():
            return tidy_text(text, self.language_code)
        return text.strip()

    def transcribe_path(self, path):
        audio = load_audio(path)
        return self.recognizer.recognize_google(audio, language=self.language_code)

    def transcribe_audio_data(self, audio):
        return self.recognizer.recognize_google(audio, language=self.language_code)

    def remove_placeholder(self):
        current = self.text.get("1.0", END).strip()
        if current.startswith(PLACEHOLDER_START):
            self.text.delete("1.0", END)

    def get_transcript_text(self):
        return self.text.get("1.0", END).strip()

    def append_file_transcript(self, title, text):
        text = self.clean_if_needed(text)
        self.remove_placeholder()

        current = self.get_transcript_text()
        if current:
            self.text.insert(END, "\n\n")

        self.text.insert(END, f"## {title}\n\n{text}\n")
        self.text.see(END)

    def append_dictation_text(self, text):
        text = self.clean_if_needed(text)
        if not text:
            return

        self.remove_placeholder()

        current = self.get_transcript_text()
        if current:
            last_char = current[-1]
            starts_new_block = text.startswith("\n")
            starts_punctuation = text[0] in ".,;:?!)]"

            if not starts_new_block and not starts_punctuation and not last_char.isspace():
                self.text.insert(END, " ")

        self.text.insert(END, text)
        self.text.see(END)

    def replace_transcript_text(self, text):
        self.text.delete("1.0", END)
        self.text.insert(END, text)
        self.text.see(END)

    def new_document(self):
        current = self.get_transcript_text()
        if current and not current.startswith(PLACEHOLDER_START):
            confirmed = messagebox.askyesno(
                "Transcribe",
                "Clear the current transcript and start a new document?",
            )
            if not confirmed:
                return

        self.insert_placeholder()
        self.last_autosave_text = ""
        self.set_status("New document")

    def selected_range(self):
        try:
            return self.text.index("sel.first"), self.text.index("sel.last")
        except Exception:
            return None

    def clean_current_text_with_ai(self):
        selection = self.selected_range()

        if selection:
            start, end = selection
            text = self.text.get(start, end).strip()
        else:
            start = end = None
            text = self.get_transcript_text()

        if not text or text.startswith(PLACEHOLDER_START):
            messagebox.showinfo("Transcribe", "There is no transcript to clean yet.")
            return

        args = (text, start, end, self.ai_model.get(), self.ai_style.get())
        threading.Thread(target=self.ai_clean_worker, args=args, daemon=True).start()

    def ai_clean_worker(self, text, start, end, model_name, style_name):
        self.root.after(0, self.set_status, "AI cleaning with Ollama...")
        self.root.after(0, self.ai_button.configure, {"state": DISABLED})

        try:
            cleaned = clean_text_with_ollama(text, self.language_name.get(), model_name, style_name)
            if cleaned:
                if start and end:
                    self.root.after(0, self.replace_selected_text, start, end, cleaned)
                else:
                    self.root.after(0, self.replace_transcript_text, cleaned)
                self.root.after(0, self.set_status, "AI clean done")
        except RuntimeError as error:
            self.root.after(0, messagebox.showerror, "Transcribe", str(error))
        except Exception as error:
            self.root.after(0, messagebox.showerror, "Transcribe", f"AI clean failed:\n{error}")
        finally:
            self.root.after(0, self.ai_button.configure, {"state": NORMAL})

    def replace_selected_text(self, start, end, replacement):
        self.text.delete(start, end)
        self.text.insert(start, replacement)
        self.text.see(start)

    def open_audio(self):
        paths = filedialog.askopenfilenames(
            title="Choose audio files",
            filetypes=[
                ("Audio files", "*.wav *.flac *.aiff *.aif *.mp3 *.ogg *.m4a *.mp4 *.aac *.mov"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        threading.Thread(target=self.transcribe_files, args=(paths,), daemon=True).start()

    def transcribe_files(self, paths):
        self.root.after(0, self.set_status, "Transcribing audio...")
        self.root.after(0, self.open_button.configure, {"state": DISABLED})

        try:
            for path in paths:
                file_path = Path(path)
                text = self.transcribe_path(file_path)
                self.root.after(0, self.append_file_transcript, file_path.name, text)
                self.root.after(0, self.set_status, f"Transcribed {file_path.name}")
        except sr.UnknownValueError:
            self.root.after(0, messagebox.showwarning, "Transcribe", "Could not understand the audio.")
        except sr.RequestError as error:
            self.root.after(0, messagebox.showerror, "Transcribe", f"Speech recognition failed:\n{error}")
        except Exception as error:
            self.root.after(0, messagebox.showerror, "Transcribe", f"Transcription failed:\n{error}")
        finally:
            self.root.after(0, self.set_status, "Ready")
            self.root.after(0, self.open_button.configure, {"state": NORMAL})

    def start_or_pause_shortcut(self):
        if not self.is_dictating:
            self.start_dictation()
        else:
            self.toggle_pause()

    def start_dictation(self):
        if self.is_dictating:
            return

        self.stop_event.clear()
        self.pause_event.clear()
        self.is_dictating = True
        self.update_dictation_buttons()
        self.set_status("Starting microphone...")

        self.dictation_thread = threading.Thread(target=self.dictation_loop, daemon=True)
        self.dictation_thread.start()

    def toggle_pause(self):
        if not self.is_dictating:
            return

        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
            self.set_status("Dictation running...")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="Resume")
            self.set_status("Paused")

    def stop_dictation(self):
        if not self.is_dictating:
            return

        self.stop_event.set()
        self.set_status("Stopping...")

    def dictation_loop(self):
        try:
            with sr.Microphone() as source:
                self.root.after(0, self.set_status, "Calibrating microphone...")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                self.root.after(0, self.set_status, "Dictation running...")

                while not self.stop_event.is_set():
                    if self.pause_event.is_set():
                        time.sleep(0.1)
                        continue

                    try:
                        audio = self.recognizer.listen(
                            source,
                            timeout=1,
                            phrase_time_limit=20,
                        )
                    except sr.WaitTimeoutError:
                        continue

                    if self.stop_event.is_set() or self.pause_event.is_set():
                        continue

                    self.root.after(0, self.set_status, "Recognizing speech...")
                    try:
                        text = self.transcribe_audio_data(audio)
                    except sr.UnknownValueError:
                        self.root.after(0, self.set_status, "Could not understand. Continue speaking...")
                        continue
                    except sr.RequestError as error:
                        self.root.after(
                            0,
                            messagebox.showerror,
                            "Transcribe",
                            f"Speech recognition failed:\n{error}",
                        )
                        break
                    except Exception as error:
                        self.root.after(
                            0,
                            messagebox.showerror,
                            "Transcribe",
                            f"Transcription failed:\n{error}",
                        )
                        break

                    self.root.after(0, self.append_dictation_text, text)
                    self.root.after(0, self.set_status, "Recognized chunk")
        except AttributeError:
            self.root.after(
                0,
                messagebox.showerror,
                "Transcribe",
                "Microphone support needs PyAudio. Install it with: python -m pip install PyAudio",
            )
        finally:
            self.root.after(0, self.finish_dictation)

    def finish_dictation(self):
        self.is_dictating = False
        self.pause_event.clear()
        self.stop_event.clear()
        self.set_status("Ready")
        self.update_dictation_buttons()

    def export_text(self):
        default_path = build_output_path()
        output_path = filedialog.asksaveasfilename(
            title="Save transcript",
            defaultextension=".txt",
            initialfile=default_path.name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not output_path:
            return

        export_text = wrap_export_text(self.get_transcript_text())
        Path(output_path).write_text(export_text, encoding="utf-8")
        self.delete_autosave()
        self.set_status(f"Saved {Path(output_path).name}")

    def delete_autosave(self):
        try:
            AUTOSAVE_PATH.unlink(missing_ok=True)
            self.last_autosave_text = ""
            self.log_status.set("Autosave cleared")
        except OSError as error:
            self.log_status.set(f"Could not delete {AUTOSAVE_PATH.name}: {error}")

    def schedule_autosave(self):
        self.autosave()
        self.root.after(AUTOSAVE_INTERVAL_MS, self.schedule_autosave)

    def autosave(self):
        current = self.get_transcript_text()
        if not current or current.startswith(PLACEHOLDER_START):
            return

        if current == self.last_autosave_text:
            return

        AUTOSAVE_PATH.write_text(wrap_export_text(current), encoding="utf-8")
        self.last_autosave_text = current
        self.log_status.set(
            f"{datetime.now().strftime('%H:%M:%S')}  Autosaved to {AUTOSAVE_PATH.name}"
        )


def main():
    root = Tk()
    TranscribeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
