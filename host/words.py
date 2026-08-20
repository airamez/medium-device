#!/usr/bin/env python3
"""Prefix trie for English autocomplete on the needle capture GUI.

Word list: Peter Norvig's 1-gram counts from the Google Web Trillion Word
Corpus (https://norvig.com/ngrams/count_1w.txt). We keep the 40,000 most
common alphabetic tokens; line order is frequency rank (1 = most common).
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_WORDLIST = Path(__file__).resolve().parent / "data" / "en-40k.txt"
MAX_FUZZY_DIST = 2
MIN_FUZZY_LETTERS = 2


class _Node:
    __slots__ = ("kids", "best")

    def __init__(self) -> None:
        self.kids: dict[str, _Node] = {}
        # Most frequent word that passes through this node (lowest rank).
        self.best: str | None = None


class WordIndex:
    """Trie keyed by lowercase letters. Completes a prefix in O(length)."""

    def __init__(self) -> None:
        self.root = _Node()
        self.words: list[str] = []

    def __len__(self) -> int:
        return len(self.words)

    def insert(self, word: str) -> None:
        word = word.lower()
        if not word.isalpha():
            return
        if self.root.best is None:
            self.root.best = word
        node = self.root
        for ch in word:
            kid = node.kids.get(ch)
            if kid is None:
                kid = _Node()
                node.kids[ch] = kid
            node = kid
            if node.best is None:
                node.best = word
        self.words.append(word)

    def complete(self, prefix: str) -> str | None:
        """Most common dictionary word that starts with `prefix`."""
        node = self.root
        for ch in prefix.lower():
            node = node.kids.get(ch)
            if node is None:
                return None
        return node.best

    def closest(self, letters: str) -> str | None:
        """Best prefix completion, else a nearby word (edit distance ≤ 2)."""
        if not letters or not letters.isalpha():
            return None
        hit = self.complete(letters)
        if hit is not None:
            return hit
        if len(letters) < MIN_FUZZY_LETTERS:
            return None
        return self._fuzzy(letters.lower())

    def _fuzzy(self, letters: str) -> str | None:
        n = len(letters)
        best: tuple[int, int, str] | None = None
        lo, hi = n - MAX_FUZZY_DIST, n + MAX_FUZZY_DIST
        for rank, word in enumerate(self.words):
            if not (lo <= len(word) <= hi):
                continue
            dist = _bounded_levenshtein(letters, word, MAX_FUZZY_DIST)
            if dist is None:
                continue
            cand = (dist, rank, word)
            if best is None or cand < best:
                best = cand
                if dist == 1 and rank < 200:
                    # Common word, one slip: good enough.
                    break
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

    @property
    def typed(self) -> str:
        return "".join(self.letters)

    @property
    def suggestion(self) -> str:
        if self.pinned is not None:
            return self.pinned
        raw = self.index.closest(self.typed)
        if raw:
            return raw.upper()
        return self.typed

    @property
    def ghost(self) -> str:
        """Remainder of a prefix completion, else empty (fuzzy is not a ghost)."""
        if self.pinned is not None:
            return ""
        typed = self.typed
        sug = self.suggestion
        if typed and sug.startswith(typed):
            return sug[len(typed) :]
        return ""

    @property
    def is_prefix(self) -> bool:
        return bool(self.ghost) or (
            bool(self.typed) and self.suggestion == self.typed
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


def load_index(path: Path | None = None) -> WordIndex:
    index = WordIndex()
    src = path or DEFAULT_WORDLIST
    if not src.is_file():
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


def _bounded_levenshtein(a: str, b: str, max_dist: int) -> int | None:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return None
    if la > lb:
        a, b, la, lb = b, a, lb, la
    prev = list(range(la + 1))
    for i, cb in enumerate(b, 1):
        cur = [i] + [0] * la
        row_min = i
        for j, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            v = prev[j - 1] + cost
            ins = cur[j - 1] + 1
            if ins < v:
                v = ins
            delete = prev[j] + 1
            if delete < v:
                v = delete
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > max_dist:
            return None
        prev = cur
    dist = prev[la]
    return dist if dist <= max_dist else None


if __name__ == "__main__":
    idx = load_index()
    print(f"{len(idx)} words from {DEFAULT_WORDLIST}")
    for prefix in ("th", "hel", "spir", "love", "yes"):
        print(f"  {prefix} -> {idx.complete(prefix)}")
