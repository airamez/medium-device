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
        word = word.lower()
        if not word.isalpha():
            return
        rank = len(self.words)
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
        node.word = word
        node.rank = rank
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


if __name__ == "__main__":
    idx = load_index()
    print(f"{len(idx)} words from {DEFAULT_WORDLIST}")
    for prefix in ("th", "hel", "spir", "love", "yes"):
        print(f"  {prefix} -> {idx.complete(prefix)}")
