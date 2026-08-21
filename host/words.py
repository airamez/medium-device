#!/usr/bin/env python3
"""Prefix trie for autocomplete on the needle capture GUI.

Word lists live in langs/*.txt (one language per file). Line order is
frequency rank (1 = most common). Accented spellings are kept in the file;
lookup folds them to ASCII so the A–Z grid can match não / café / niño.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

LANGS_DIR = Path(__file__).resolve().parent / "langs"
DEFAULT_LANG = "en-US"
DEFAULT_WORDLIST = LANGS_DIR / "en-us.txt"
MAX_FUZZY_DIST = 2
MIN_FUZZY_LETTERS = 2


class _Node:
    __slots__ = ("kids", "best", "word", "rank")

    def __init__(self) -> None:
        self.kids: dict[str, _Node] = {}
        # Most frequent word that passes through this node (lowest rank).
        self.best: str | None = None
        # Set only on the node where a word ends (its own frequency rank).
        self.word: str | None = None
        self.rank: int = 0


class WordIndex:
    """Trie keyed by lowercase letters. Completes a prefix in O(length)."""

    def __init__(self) -> None:
        self.root = _Node()
        self.words: list[str] = []

    def __len__(self) -> int:
        return len(self.words)

    def insert(self, word: str) -> None:
        original = word.strip().lower()
        key = fold_letters(original)
        if not key:
            return
        rank = len(self.words)
        if self.root.best is None:
            self.root.best = original
        node = self.root
        for ch in key:
            kid = node.kids.get(ch)
            if kid is None:
                kid = _Node()
                node.kids[ch] = kid
            node = kid
            if node.best is None:
                node.best = original
        if node.word is not None:
            return
        node.word = original
        node.rank = rank
        self.words.append(original)

    def complete(self, prefix: str) -> str | None:
        """Most common dictionary word that starts with `prefix`."""
        key = fold_letters(prefix)
        if not key:
            return None
        node = self.root
        for ch in key:
            node = node.kids.get(ch)
            if node is None:
                return None
        return node.best

    def closest(self, letters: str) -> str | None:
        """Best prefix completion, else a nearby word (edit distance ≤ 2)."""
        if not letters or not letters.isalpha():
            return None
        key = fold_letters(letters)
        if not key:
            return None
        hit = self.complete(key)
        if hit is not None:
            return hit
        if len(key) < MIN_FUZZY_LETTERS:
            return None
        return self._fuzzy(key)

    def _fuzzy(self, letters: str) -> str | None:
        """Nearest dictionary word within MAX_FUZZY_DIST, walking the trie.

        Scanning every one of the ~40,000 words with a Levenshtein check
        (the old approach) is O(vocabulary size) on every call. Instead we
        run the classic trie edit-distance search: track one Wagner-Fischer
        DP row per node and stop descending into a subtree as soon as its
        best possible distance already exceeds the budget. Wrong branches
        (most of the alphabet, most of the time) are pruned after a couple
        of letters instead of being fully scanned.
        """
        n = len(letters)
        best: tuple[int, int, str] | None = None
        first_row = list(range(n + 1))
        stack: list[tuple[_Node, str, list[int]]] = [
            (kid, ch, first_row) for ch, kid in self.root.kids.items()
        ]
        while stack:
            node, ch, prev_row = stack.pop()
            row = [prev_row[0] + 1]
            for i in range(1, n + 1):
                cost = 0 if letters[i - 1] == ch else 1
                row.append(
                    min(
                        row[i - 1] + 1,
                        prev_row[i] + 1,
                        prev_row[i - 1] + cost,
                    )
                )
            if min(row) > MAX_FUZZY_DIST:
                continue
            if node.word is not None:
                dist = row[n]
                if dist <= MAX_FUZZY_DIST:
                    cand = (dist, node.rank, node.word)
                    if best is None or cand < best:
                        best = cand
            for kid_ch, kid in node.kids.items():
                stack.append((kid, kid_ch, row))
        return None if best is None else best[2]


class WordDraft:
    """Letters of the word being aimed at, not yet in the transcript.

    Autocomplete may show a longer word than was aimed (HEL → HELP). After
    backspace, that shown word is *pinned* so the next completion cannot
    grow it back — otherwise THE stays THE until every letter is gone.
    """

    def __init__(self, index: WordIndex) -> None:
        self.index = index
        self.letters: list[str] = []
        self.pinned: str | None = None
        self.enabled = True
        self._sug_cache_key: str | None = None
        self._sug_cache_val: str = ""

    @property
    def typed(self) -> str:
        return "".join(self.letters)

    @property
    def suggestion(self) -> str:
        typed = self.typed
        if not self.enabled:
            return typed
        if self.pinned is not None:
            return self.pinned
        # closest() can fall back to a fuzzy scan of every dictionary word
        # (MAX_FUZZY_DIST). Properties get read several times per draw
        # frame (60x/sec) by the GUI, so without this cache an unmatched
        # prefix re-runs that whole-dictionary scan on every single frame
        # for as long as the needle sits on it — the app grinds to a halt.
        if self._sug_cache_key == typed:
            return self._sug_cache_val
        raw = self.index.closest(typed)
        val = raw.upper() if raw else typed
        self._sug_cache_key = typed
        self._sug_cache_val = val
        return val

    @property
    def shown_typed(self) -> str:
        """Typed letters, using the suggestion's accents on a prefix match."""
        typed = self.typed
        sug = self.suggestion
        if not typed:
            return typed
        ft, fs = fold_letters(typed), fold_letters(sug)
        if fs.startswith(ft):
            return original_prefix(sug, ft)
        return typed

    @property
    def ghost(self) -> str:
        """Remainder of a prefix completion, else empty (fuzzy is not a ghost)."""
        if self.pinned is not None:
            return ""
        typed = self.typed
        sug = self.suggestion
        if not typed:
            return ""
        ft, fs = fold_letters(typed), fold_letters(sug)
        if fs.startswith(ft):
            return original_suffix(sug, ft)
        return ""

    @property
    def is_prefix(self) -> bool:
        typed = self.typed
        if not typed:
            return False
        return bool(self.ghost) or fold_letters(self.suggestion) == fold_letters(
            typed
        )

    def add(self, char: str) -> None:
        if len(char) != 1 or not char.isalnum():
            return
        self.pinned = None
        self.letters.append(char.upper())

    def backspace(self) -> bool:
        """Change the word in the box. True if anything changed."""
        if self.ghost:
            self.pinned = self.typed
            return True
        if self.letters:
            self.letters.pop()
            self.pinned = self.typed or None
            return True
        return False

    def clear(self) -> None:
        self.letters.clear()
        self.pinned = None

    def take_typed(self) -> str:
        word = self.typed
        self.clear()
        return word

    def take_suggestion(self) -> str:
        word = self.suggestion
        self.clear()
        return word

    def set_index(self, index: WordIndex) -> None:
        """Swap the dictionary and drop cached suggestions from the old one."""
        self.index = index
        self.pinned = None
        self._sug_cache_key = None
        self._sug_cache_val = ""


def original_prefix(word: str, folded_prefix: str) -> str:
    """Leading characters of `word` whose fold covers `folded_prefix`."""
    need = len(folded_prefix)
    n = 0
    for i, ch in enumerate(word):
        n += len(fold_letters(ch))
        if n >= need:
            return word[: i + 1]
    return word


def original_suffix(word: str, folded_prefix: str) -> str:
    """Characters of `word` after the folded prefix."""
    return word[len(original_prefix(word, folded_prefix)) :]


def fold_letters(word: str) -> str:
    """ASCII letters only, so the A–Z grid matches accented dictionary words."""
    lowered = (
        word.lower().replace("œ", "oe").replace("æ", "ae").replace("ß", "ss")
    )
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(ch)
    )
    ascii_letters = stripped.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_letters if ch.isalpha())


def lang_label(stem: str) -> str:
    """en-us → en-US, pt_br → pt-BR, es → es."""
    parts = [p for p in stem.replace("_", "-").split("-") if p]
    if not parts:
        return stem
    lang = parts[0].lower()
    if len(parts) == 1:
        return lang
    return f"{lang}-{'-'.join(p.upper() for p in parts[1:])}"


def list_languages(folder: Path | None = None) -> list[tuple[str, Path]]:
    """(label, path) for every .txt in langs/, sorted alphabetically by label."""
    root = folder or LANGS_DIR
    if not root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() == ".txt":
            found.append((lang_label(path.stem), path))
    found.sort(key=lambda item: item[0].casefold())
    return found


def resolve_wordlist(
    lang: str | None = None, folder: Path | None = None
) -> Path | None:
    langs = list_languages(folder)
    by_label = {label: path for label, path in langs}
    if lang and lang in by_label:
        return by_label[lang]
    if DEFAULT_LANG in by_label:
        return by_label[DEFAULT_LANG]
    return langs[0][1] if langs else None


def load_index(path: Path | None = None) -> WordIndex:
    index = WordIndex()
    src = path if path is not None else resolve_wordlist()
    if src is None or not src.is_file():
        return index
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            word = line.split()[0]
            index.insert(word)
    return index


def delete_current_word(draft: WordDraft, committed: list[str]) -> bool:
    """Change the word shown in the box. True if it changed.

    If the box is empty, the last committed word is pulled back in and then
    shortened, so a hold on backspace always edits the current word.
    """
    if draft.backspace():
        return True
    word = take_last_word(committed)
    if not word:
        return False
    draft.letters = list(word.upper())
    draft.pinned = word.upper()
    return draft.backspace()


def take_last_word(chars: list[str]) -> str | None:
    """Pop the last committed word (and its trailing space). None if empty."""
    if not chars:
        return None
    while chars and chars[-1] in " \n":
        chars.pop()
    if not chars:
        return ""
    word: list[str] = []
    while chars and chars[-1] not in " \n":
        word.append(chars.pop())
    word.reverse()
    return "".join(word)


if __name__ == "__main__":
    src = resolve_wordlist()
    idx = load_index(src)
    print(f"{len(idx)} words from {src}")
    for prefix in ("th", "hel", "spir", "love", "yes"):
        print(f"  {prefix} -> {idx.complete(prefix)}")
