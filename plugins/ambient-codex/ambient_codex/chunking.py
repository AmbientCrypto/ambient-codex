"""Pure, size-safe input chunk-packing primitives."""


def density_factor(text):
    """Return the conservative character-to-token sizing multiplier."""
    if not text:
        return 1.0
    sample = text[:200_000]
    non_ascii = sum(1 for char in sample if ord(char) > 127)
    return 1.0 + (non_ascii / len(sample)) * 1.6


def pack_chunks(labeled_chunks, chunk_chars, *, break_lines):
    """Pack labeled text into blocks that cannot exceed ``chunk_chars``.

    ``break_lines`` returns preferred one-based line boundaries for a label
    and text.  The facade supplies Python-aware parsing while this module
    guarantees size bounds for every input, including an oversized single
    minified line.
    """
    longest = max((len(label) for label, _ in labeled_chunks), default=0)
    chunk_chars = max(chunk_chars, longest + 200)
    pieces = []
    for label, text in labeled_chunks:
        body_budget = max(1, chunk_chars - len(label) - 80)
        if len(text) <= body_budget:
            pieces.append((label, text))
            continue
        boundaries = break_lines(label, text)
        buffer, buffer_length, first = [], 0, 1
        line_number = 0
        for line in text.splitlines(keepends=True):
            line_number += 1
            if len(line) > body_budget:
                if buffer:
                    pieces.append(
                        (f"{label} [lines {first}-{line_number - 1}]",
                         "".join(buffer)))
                    buffer, buffer_length = [], 0
                for offset in range(0, len(line), body_budget):
                    segment = line[offset:offset + body_budget]
                    pieces.append(
                        (f"{label} [line {line_number} chars {offset}-{offset + len(segment)}]",
                         segment))
                first = line_number + 1
                continue
            at_boundary = (line_number in boundaries
                           and buffer_length >= body_budget * 0.6)
            if buffer and (buffer_length + len(line) > body_budget or at_boundary):
                pieces.append(
                    (f"{label} [lines {first}-{line_number - 1}]",
                     "".join(buffer)))
                buffer, buffer_length, first = [], 0, line_number
            buffer.append(line)
            buffer_length += len(line)
        if buffer:
            pieces.append((f"{label} [lines {first}-{line_number}]", "".join(buffer)))

    chunks, current, current_length = [], [], 0
    for label, text in pieces:
        block = f"===== {label} =====\n{text}"
        if current and current_length + len(block) + 2 > chunk_chars:
            chunks.append("\n\n".join(current))
            current, current_length = [], 0
        current.append(block)
        current_length += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


__all__ = ("density_factor", "pack_chunks")
