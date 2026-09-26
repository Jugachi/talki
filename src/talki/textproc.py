"""Deterministic text rules that run around the LLM (or instead of it)."""

import re
from dataclasses import dataclass
from urllib.parse import quote_plus

# Only unambiguous fillers: "er" or "ah" are real words in some languages.
FILLERS = r"(?<![\w-])(?:u+m+|u+h+|uhm+|erm+|hmm+|ähm*|öhm*)(?![\w-])[,.…]*\s*"

# Whisper tends to emit these on silence or noise.
HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", ".",
    "thanks for watching.", "subtitles by the amara.org community", "[music]", "[blank_audio]",
    "(music)", "[silence]", "untertitel im auftrag des zdf, 2017", "untertitel der amara.org-community",
}

SPOKEN = [
    (r"\s*\bnew paragraph\b[,.]?\s*", "\n\n"),
    (r"\s*\bnew line\b[,.]?\s*", "\n"),
    (r"\s*\b(?:full stop|period)\b", "."),
    (r"\s*\bcomma\b", ","),
    (r"\s*\bquestion mark\b", "?"),
    (r"\s*\bexclamation (?:mark|point)\b", "!"),
    (r"\s*\bsemicolon\b", ";"),
    (r"\s*\bcolon\b", ":"),
    (r"\s*\bellipsis\b", "..."),
    (r"\bopen paren(?:thesis)?\s*", "("),
    (r"\s*\bclose paren(?:thesis)?\b", ")"),
    (r"\bopen quote\s*", "\""),
    (r"\s*\b(?:close|end) quote\b", "\""),
]


def word_count(text: str) -> int:
    return len(re.findall(r"\w+(?:['’]\w+)*", text))


def is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    return not t or t in HALLUCINATIONS


@dataclass
class SnippetRef:
    id: int
    trigger: str
    expansion: str


def _trigger_regex(trigger: str) -> re.Pattern:
    words = trigger.strip().split()
    body = r"[\s,]+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.I)


def insert_snippet_placeholders(text: str, snippets: list[SnippetRef]) -> tuple[str, dict[str, SnippetRef]]:
    """Longest trigger wins. Placeholders keep the LLM from rewriting expansions."""
    used: dict[str, SnippetRef] = {}
    for sn in sorted(snippets, key=lambda s: len(s.trigger), reverse=True):
        if not sn.trigger.strip():
            continue
        rx = _trigger_regex(sn.trigger)

        def repl(_m, sn=sn):
            token = f"[[S{len(used) + 1}]]"
            used[token] = sn
            return token

        text = rx.sub(repl, text)
    return text, used


def expand_placeholders(text: str, used: dict[str, SnippetRef]) -> str:
    for token, sn in used.items():
        if token in text:
            text = text.replace(token, sn.expansion)
        else:
            text = text.rstrip() + " " + sn.expansion
    # The LLM sometimes punctuates right after a placeholder that already ends a sentence.
    return re.sub(r"\[\[S\d+\]\]", "", text)


BREAK_TOKENS = {"[[NL]]": "\n", "[[PARA]]": "\n\n"}


def insert_break_placeholders(text: str) -> str:
    """Spoken line breaks become tokens so the LLM can't drop or reinterpret them."""
    text = re.sub(r"[\s,.]*\bnew paragraph\b[\s,.]*", " [[PARA]] ", text, flags=re.I)
    return re.sub(r"[\s,.]*\bnew line\b[\s,.]*", " [[NL]] ", text, flags=re.I).strip()


def expand_breaks(text: str) -> str:
    for token, rep in BREAK_TOKENS.items():
        text = re.sub(rf"[ \t]*{re.escape(token)}[ \t]*", rep, text)
    # Sentence case after a break.
    return re.sub(r"(\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


def apply_style(text: str, style: str | None) -> str:
    if style in ("casual", "very_casual"):
        text = drop_trailing_period(text)
    if style == "very_casual":
        text = text.lower()
    return text


def strip_press_enter(text: str) -> tuple[str, bool]:
    m = re.search(r"[\s,.;:]*\bpress (?:enter|return)\b[\s.!]*$", text, re.I)
    if not m:
        return text, False
    return text[: m.start()].rstrip(), True


def apply_corrections(text: str, rules: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """rules: (wrong, right). Whole standalone words only; contractions are left alone."""
    hits = []
    for wrong, right in sorted(rules, key=lambda r: len(r[0]), reverse=True):
        if not wrong:
            continue
        rx = re.compile(rf"(?<![\w'’]){re.escape(wrong)}(?![\w]|['’]\w)", re.I)
        text, n = rx.subn(right, text)
        if n:
            hits.append(right)
    return text, hits


def strip_fillers(text: str) -> str:
    out = re.sub(FILLERS, "", text, flags=re.I).strip()
    # Restore sentence case if the filler opened the text.
    if out and text[:1].isupper() and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def remove_fillers(text: str) -> str:
    text = strip_fillers(text)
    return re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.I)


def basic_format(text: str, smart: bool) -> str:
    """Used when the LLM is off or unreachable."""
    text = remove_fillers(text)
    if smart:
        for pat, rep in SPOKEN:
            text = re.sub(pat, rep, text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.;:!?)])", r"\1", text)
    text = re.sub(r"([.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
        if smart and text[-1].isalnum():
            text += "."
    return text


def drop_trailing_period(text: str) -> str:
    """Chat messages of up to two sentences drop the final period; ! and ? stay."""
    if not text.endswith(".") or text.endswith("..") or "\n" in text:
        return text
    sentences = re.findall(r"[^.!?]+[.!?]+", text)
    return text[:-1] if len(sentences) <= 2 else text


def _keeps_capital(word: str, glossary: set[str]) -> bool:
    core = re.sub(r"\W", "", word)
    if not core:
        return True
    if core in ("I",) or word.startswith(("I'", "I’")):
        return True
    if core.lower() in glossary or core in glossary:
        return True
    return core.isupper() and len(core) > 1 or any(c.isupper() for c in core[1:])


def join_with_context(text: str, before: str | None, glossary: set[str] | None = None) -> str:
    """Mid-sentence insertion: add a separating space and lowercase the first word."""
    if not text or before is None or before == "":
        return text
    glossary = {g.lower() for g in (glossary or set())}
    if before[-1] in " \t\n" or text[0] in ",.;:!?)":
        spaced = text
    else:
        spaced = " " + text
    stripped = before.rstrip()
    if stripped and stripped[-1] not in ".!?:\n" and "\n" not in before[len(stripped):]:
        lead = spaced[: len(spaced) - len(spaced.lstrip())]
        body = spaced.lstrip()
        first = body.split(maxsplit=1)[0] if body else ""
        if first and first[0].isupper() and not _keeps_capital(first, glossary):
            body = body[0].lower() + body[1:]
        if len(body) > 1 and body.endswith(".") and body.count(".") == 1 and not body.endswith(".."):
            body = body[:-1]
        spaced = lead + body
    return spaced


FILE_TAG = re.compile(r"\b(?:at|tag)\s+([\w-]+(?:\s*(?:\.|\bdot\b)\s*[\w-]+)+)", re.I)


def ide_file_tags(text: str, visible: set[str] | None = None) -> str:
    def repl(m):
        name = re.sub(r"\s*(?:\.|\bdot\b)\s*", ".", m.group(1), flags=re.I)
        if not re.search(r"\.\w{1,8}$", name):
            return m.group(0)
        if visible and name not in visible and name.lower() not in {v.lower() for v in visible}:
            return m.group(0)
        return "@" + name

    return FILE_TAG.sub(repl, text)


SEARCH = {
    "google": "https://www.google.com/search?q={}",
    "perplexity": "https://www.perplexity.ai/search?q={}",
    "chatgpt": "https://chatgpt.com/?q={}",
    "chat gpt": "https://chatgpt.com/?q={}",
    "claude": "https://claude.ai/new?q={}",
}


def search_url(instruction: str) -> str | None:
    m = re.match(
        r"^\s*(?:hey|ok|okay)?[\s,]*(?:ask|search|query)?\s*(google|perplexity|chat ?gpt|claude)"
        r"[\s,:]*(?:(?:for|to|about)\s+)?(.+?)[.?!]*$",
        instruction,
        re.I,
    )
    if not m or not re.match(r"^\s*(hey|ok|okay|ask|search|query)\b", instruction, re.I):
        return None
    engine = m.group(1).lower()
    return SEARCH[engine].format(quote_plus(m.group(2).strip()))


def extract_identifiers(text: str, limit: int = 60) -> list[str]:
    """camelCase / snake_case / dotted file names visible near the cursor, for IDE dictation."""
    found = re.findall(r"\b(?:[a-z]+[A-Z]\w*|\w+_\w+|[\w-]+\.(?:py|ts|tsx|js|jsx|rs|go|md|json|toml|yaml|yml|c|h|cpp|java|kt|rb|php|sh|css|html))\b", text)
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out[:limit]
