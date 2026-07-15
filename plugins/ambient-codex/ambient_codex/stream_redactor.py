"""Bounded streaming redaction for secrets and terminal control sequences."""

import re


class StreamRedactor:
    """Redact a secret and terminal escapes across arbitrary chunk boundaries.

    Escape parsing and secret buffering are deliberately separate. Only the
    uncommitted tail is retained, keeping total work linear even for hostile
    streams made from many tiny chunks.
    """

    def __init__(self, api_key, redact, *, key_placeholder="[REDACTED]",
                 max_escape_hold=256, escape_keep_tail=64):
        self._key = api_key if (api_key and len(api_key) >= 8) else ""
        self._keep = max(len(self._key) - 1, 0)
        self._redact = redact
        self._placeholder = key_placeholder
        self._max_escape_hold = max_escape_hold
        self._escape_keep_tail = escape_keep_tail
        self._raw = ""
        self._san = ""

    @staticmethod
    def _stable_len(value):
        """Return the prefix ending before any incomplete terminal escape."""
        index = value.rfind("\x1b")
        if index == -1:
            return len(value)
        tail = value[index:]
        if len(tail) == 1:
            return index
        marker = tail[1]
        if marker == "[":
            return (len(value)
                    if re.match(r"\x1b\[[0-?]*[ -/]*[@-~]", tail) else index)
        if marker == "]":
            return (len(value)
                    if "\x07" in tail[2:] or "\x1b\\" in tail[2:] else index)
        if marker in "PX^_":
            return len(value) if "\x1b\\" in tail[2:] else index
        return len(value)

    @staticmethod
    def _needs_sanitizing(value):
        """Return whether ``redact`` must scan this already-stable text.

        Ordinary provider deltas overwhelmingly contain printable text. Avoid
        running the full ANSI/control regex pipeline for every tiny safe delta;
        boundary-sensitive escapes remain in ``_raw`` and every control-bearing
        stable segment still goes through the canonical sanitizer.
        """
        for character in value:
            codepoint = ord(character)
            if (codepoint < 9 or 11 <= codepoint <= 31
                    or 127 <= codepoint <= 159):
                return True
        return False

    def _sanitize_stable(self, value):
        if not value or not self._needs_sanitizing(value):
            return value
        return self._redact(value, "")

    def feed(self, piece):
        self._raw += piece
        cut = self._stable_len(self._raw)
        stable_raw, self._raw = self._raw[:cut], self._raw[cut:]
        if len(self._raw) > self._max_escape_hold:
            self._raw = self._raw[:2] + self._raw[-self._escape_keep_tail:]
        if stable_raw:
            self._san += self._sanitize_stable(stable_raw)
        return self._emit(hold=self._keep)

    def _raw_commit_len(self, segment, redacted_target):
        if not self._key:
            return min(len(segment), redacted_target)
        key_length = len(self._key)
        placeholder_length = len(self._placeholder)
        raw_index = redacted_length = 0
        while raw_index < len(segment):
            if segment.startswith(self._key, raw_index):
                if redacted_length + placeholder_length > redacted_target:
                    break
                raw_index += key_length
                redacted_length += placeholder_length
            else:
                if redacted_length + 1 > redacted_target:
                    break
                raw_index += 1
                redacted_length += 1
        return raw_index

    def _emit(self, hold):
        redacted = (self._san.replace(self._key, self._placeholder)
                    if self._key else self._san)
        target = len(redacted) - hold if hold else len(redacted)
        if target <= 0:
            return ""
        raw_index = self._raw_commit_len(self._san, target)
        if raw_index <= 0:
            return ""
        output = (self._san[:raw_index].replace(self._key, self._placeholder)
                  if self._key else self._san[:raw_index])
        self._san = self._san[raw_index:]
        return output

    def flush(self):
        if self._raw:
            self._san += self._sanitize_stable(self._raw)
            self._raw = ""
        return self._emit(hold=0)


__all__ = ("StreamRedactor",)
