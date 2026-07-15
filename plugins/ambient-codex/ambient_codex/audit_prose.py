"""Bounded recovery of structured audit findings from model prose."""

import re


_PROSE_LEAD = r"[\ \t>#]*(?:\d+[.)])?[\ \t]*[*•]*[\ \t]*"
_PROSE_FILELINE = (r"(?<![A-Za-z0-9_./\-])"
                   r"(?:[A-Za-z0-9_\-]{1,80}[./]){1,40}[A-Za-z0-9_.\-]{0,80}"
                   r"(?::[ \t]*\d+(?:-\d+)?(?![\d/])|\([ \t]*\d+(?:,[ \t]*\d+)?\))")
_SEV_WORD = (r"(?:CRITICAL|HIGH|MEDIUM|LOW|BLOCKER|MAJOR|MINOR|TRIVIAL"
             r"|P[0-4](?![0-9A-Za-z])|SEV(?:ERITY)?[ ]?[0-5](?![0-9A-Za-z]))")
_SEV_ADJ = r"(?:risk|severity|sev|priority|prio|impact|criticality)"
_PROSE_SEV_LABEL_RE = re.compile(
    r"(?im)(?<![A-Za-z])" + _SEV_WORD + r"(?:-" + _SEV_ADJ + r"\b|[ \t]*[:—–,(])")
_PROSE_SEV_HEADING_RE = re.compile(
    r"(?im)^[ \t>]{0,20}(?:#{1,6}[ \t]{0,20})?\*{0,4}(?<![A-Za-z])" + _SEV_WORD
    + r"\*{0,4}[ \t]{0,20}:?[ \t]{0,20}$")
_PROSE_FINDING_RE = re.compile(
    r"(?im)^" + _PROSE_LEAD
    + r"(CRITICAL|HIGH|MEDIUM|LOW)(?![a-z\-])"
    + r"(?:[ \t]{0,20}[—–\-:,]?[ \t]{0,20}\(?[ \t]{0,20}confidence[ \t]{0,20}[:=][ \t]{0,20}(HIGH|LOW)\b[ \t]{0,20}\)?)?"
    + r"[ \t]{0,20}[—–\-:,][ \t]{0,20}"
    + r"([^\s:—–]{1,300}?)"
    + r"(?:[:#][ \t]*L?[ \t]*|[ \t]+L|[ \t]+lines?[ \t]+(?:number[ \t]+|no\.?[ \t]+|#)?)"
    + r"(\d+)(?![-\d])"
    + r"(?:\s*[—–\-]\s*(.{1,2000}?))?\s*$")
_PROSE_VERDICT_RE = re.compile(
    r"(?im)^" + _PROSE_LEAD + r"verdict\b\s*[:\-—]?\s*\*{0,4}\s*"
    r"(SHIP|FIX FIRST|NEEDS WORK)\b")
_PROSE_LINEREF_RE = re.compile(
    r"(?im)\blines?[ \t]+(?:number[ \t]+|no\.?[ \t]+|#)?\d")
_PROSE_SEVERITY_HINT_RE = re.compile(
    r"(?im)^(?=[^\n]*(?<![A-Za-z])" + _SEV_WORD
    + r"(?:-" + _SEV_ADJ + r"\b|[ \t]*[:—–,(]))"
    + r"(?=[^\n]*(?:" + _PROSE_FILELINE
    + r"|(?:[A-Za-z0-9_.\-]{1,80})?[#][ \t]*L?[ \t]*\d"
    + r"|\blines?[ \t]+(?:number[ \t]+|no\.?[ \t]+|#)?\d))[^\n]*$")
_PROSE_XML_SEV_RE = re.compile(
    r"(?is)<\s*(?:severity|sev|risk|priority)\s*>\s*"
    r"(?:CRITICAL|HIGH|MEDIUM|LOW|BLOCKER|P[0-4]|SEV\w*\d)")
_PROSE_CWE_RE = re.compile(r"\b(?:CWE-\d+|CVE-\d{4}-\d+)\b")
_PROSE_DEFECT_VERB_RE = re.compile(
    r"(?i)\b(?:sql[ ]?injection|sqli\b|xss\b|csrf\b|ssrf\b|\brce\b"
    r"|timing attack|race condition|buffer overflow|integer overflow"
    r"|use[ -]after[ -]free|path traversal|directory traversal|command injection"
    r"|auth(?:entication|orization)?[ -]bypass|privilege escalation|open redirect"
    r"|insecure deserialization|hard[ -]?coded[ ](?:secret|password|api[ -]?key|key|credential|token)"
    r"|plaintext[ ](?:password|secret|credential)s?"
    r"|leaks?[ ](?:the[ ]|a[ ])?(?:secret|password|credential|token|session)s?)\b")
_PROSE_FIELD_SEV_RE = re.compile(
    r"(?im)^[\ \t>*•#\-]*(?:\d+[.)][\ \t]*)?\*{0,4}"
    r"(?:severity|priority|risk\s+level|sev)\b"
    r"[^\n:—–]*[:—–\-]\*{0,4}[ \t]*"
    r"(?:CRITICAL|HIGH|MEDIUM|LOW|BLOCKER|MAJOR|MINOR)\b")
_PROSE_TABLE_ROW_RE = re.compile(
    r"(?i)\|[ \t]*(?<![A-Za-z])" + _SEV_WORD + r"(?![A-Za-z])[^\n|]{0,80}\|")
_PROSE_FINDING_HEADING_RE = re.compile(
    r"(?im)^[\ \t>*•#\-]*(?:\d+[.)][ \t]*)?\*{0,4}"
    r"(?:finding|issue|bug|vulnerability|defect|weakness)s?\b[ \t]*\d*[ \t]*"
    r"[:—–\-]\*{0,4}[ \t]*(?:CRITICAL|HIGH|MEDIUM|LOW)\b")
_PROSE_DEFECT_FIELD_RE = re.compile(
    r"(?im)^[\ \t>*•#\-]*\*{0,4}(?:defect|vulnerabilit(?:y|ies)|weakness|flaw|exploit"
    r"|scenario|impact|remediation|reproduction|steps\ to\ reproduce)"
    r"\*{0,4}\s*[:—–\-]\s*\S")
_PROSE_FINDING_HEAD_LINE_RE = re.compile(
    r"(?im)^[\ \t>*•#\-]*(?:\d+[.)][ \t]*)?\*{0,4}"
    r"(?:finding|issue|bug|vulnerability|defect|weakness)s?[ \t]*#?[ \t]*\d+[ \t]*[:—–\-]")
_PROSE_PAREN_SEV_RE = re.compile(r"(?i)\(\s*(?:CRITICAL|HIGH|MEDIUM|LOW)\s*\)")
_PROSE_ANY_SEV_RE = re.compile(r"(?i)\b(?:CRITICAL|HIGH|MEDIUM|LOW)\b")
_PROSE_LOC_LABEL_RE = re.compile(
    r"(?im)^[\ \t>*•#\-]*\*{0,4}(?:file|location|path|affected(?:\s+file)?|source|where|at)"
    r"\*{0,4}\s*[:—–\-]")
_PROSE_BARE_FILELINE_RE = re.compile(r"(?im)" + _PROSE_FILELINE)
_PROSE_FILE_FIELD_RE = re.compile(r"(?im)^[\ \t>*•#\-]*\*{0,4}file\*{0,4}\s*[:—–\-]")
_PROSE_LINE_FIELD_RE = re.compile(r"(?im)^[\ \t>*•#\-]*\*{0,4}lines?\*{0,4}\s*[:—–\-]?[ \t]*\d")
PROSE_SCAN_MAX = 262_144

_CODE_FENCE_RE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[ \t]*$")
_PROSE_RESOLVED_TITLE_RE = re.compile(
    r"(?i)^\s*(?:RESOLVED|FIXED|N/?A|NO LONGER|ALREADY (?:FIXED|RESOLVED|ADDRESSED)"
    r"|WAS FIXED|NOW (?:USES|CORRECT|FIXED|SAFE))\b")
_PROSE_RESOLVED_LINE_RE = re.compile(
    r"(?i)[—–\-][ \t]*(?:RESOLVED|FIXED|NO LONGER|ALREADY (?:FIXED|RESOLVED|ADDRESSED)"
    r"|NOW (?:USES|CORRECT|FIXED|SAFE))\b")
_PROSE_HYPOTHETICAL_RE = re.compile(
    r"(?im)^[^\n]*\b(?:would have looked like|for illustration|for example"
    r"|example of a (?:finding|vulnerability|defect)|sample finding|format (?:is|would be)"
    r"|previous(?:ly)?[ \t]+(?:audit|reported|round)|prior audit|last (?:audit|round)"
    r"|in a previous|the earlier (?:audit|round)|had reported)\b[^\n]*$")
_PROSE_FIELD_LINE_RE = re.compile(
    r"(?im)(?:^[\ \t>*•#\-]*(?:file|location|path)\s*[:—–\-]"
    r"|^[\ \t>*•#\-]*lines?\s*[:—–\-]?\s*\d"
    r"|" + _PROSE_FILELINE + r")")


def _strip_code_fences(text):
    return _CODE_FENCE_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def _strip_illustrative(text):
    lines, output, index = text.split("\n"), [], 0
    while index < len(lines):
        if _PROSE_RESOLVED_LINE_RE.search(lines[index]):
            output.append("")
            index += 1
            continue
        output.append(lines[index])
        if _PROSE_HYPOTHETICAL_RE.match(lines[index]):
            index += 1
            blanked = 0
            while index < len(lines) and blanked < 3:
                line = lines[index]
                if _PROSE_VERDICT_RE.match(line):
                    break
                if not line.strip():
                    output.append(line)
                    if blanked:
                        break
                    index += 1
                    continue
                output.append("")
                blanked += 1
                index += 1
            continue
        index += 1
    return "\n".join(output)


def text_has_unparsed_finding(text):
    """Detect a finding-shaped response that cannot safely be discarded."""
    text = _strip_illustrative(_strip_code_fences(text[:PROSE_SCAN_MAX]))
    has_loc = bool(_PROSE_BARE_FILELINE_RE.search(text) or _PROSE_LINEREF_RE.search(text))
    return bool(
        (has_loc and _PROSE_SEVERITY_HINT_RE.search(text))
        or _PROSE_FIELD_SEV_RE.search(text)
        or _PROSE_TABLE_ROW_RE.search(text)
        or _PROSE_XML_SEV_RE.search(text)
        or _PROSE_FINDING_HEADING_RE.search(text)
        or _PROSE_DEFECT_FIELD_RE.search(text)
        or (_PROSE_SEV_LABEL_RE.search(text) and has_loc)
        or (_PROSE_SEV_HEADING_RE.search(text) and (has_loc or _PROSE_DEFECT_VERB_RE.search(text)))
        or (_PROSE_CWE_RE.search(text) and has_loc)
        or (_PROSE_DEFECT_VERB_RE.search(text) and has_loc)
        or (_PROSE_FINDING_HEAD_LINE_RE.search(text) and _PROSE_ANY_SEV_RE.search(text))
        or (_PROSE_FINDING_HEAD_LINE_RE.search(text) and _PROSE_BARE_FILELINE_RE.search(text))
        or (_PROSE_LOC_LABEL_RE.search(text) and has_loc)
        or (_PROSE_FILE_FIELD_RE.search(text) and _PROSE_LINE_FIELD_RE.search(text)))


def _extract_labeled(body, label):
    match = re.search(
        r"(?im)^" + _PROSE_LEAD + label + r"\b\*{0,4}\s*[:\-—]\s*(.+?)"
        r"(?=\n[\ \t]*\n|\n" + _PROSE_LEAD + r"(?:CRITICAL|HIGH|MEDIUM|LOW)\b"
        r"|\n" + _PROSE_LEAD + r"verdict\b|\Z)", body, re.S)
    return "" if not match else re.sub(r"\s+", " ", match.group(1)).strip().strip("*").strip()


def parse_prose_findings(text, *, verdict):
    """Recover the audit prose contract without importing CLI runtime state."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = _strip_illustrative(_strip_code_fences(text[:PROSE_SCAN_MAX]))
    matches = list(_PROSE_FINDING_RE.finditer(text))
    findings = []
    for index, match in enumerate(matches):
        severity, confidence, filename, line, title = match.groups()
        body = text[match.end():(matches[index + 1].start() if index + 1 < len(matches) else len(text))]
        title = (title or "").strip().strip("*").strip().rstrip(".").strip()
        defect = _extract_labeled(body, "defect")
        title = title or defect
        if not title or _PROSE_RESOLVED_TITLE_RE.match(title):
            continue
        findings.append({
            "severity": severity.upper(), "confidence": (confidence or "HIGH").upper(),
            "file": filename, "line": int(line), "title": title,
            "defect": defect or title, "scenario": _extract_labeled(body, "scenario"),
            "fix": _extract_labeled(body, "fix"),
        })
    verdicts = _PROSE_VERDICT_RE.findall(text)
    final_verdict = verdicts[-1].upper() if verdicts else None
    if not findings:
        if text_has_unparsed_finding(text):
            return None
        return {"findings": [], "verdict": final_verdict} if final_verdict else None
    return {"findings": findings, "verdict": final_verdict or verdict(findings, False)}


__all__ = ("parse_prose_findings", "text_has_unparsed_finding")
