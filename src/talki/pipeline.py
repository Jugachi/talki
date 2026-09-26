"""Speech to final text: STT, deterministic rules and LLM cleanup."""

import logging
from dataclasses import dataclass, field

import numpy as np

from . import textproc as tp
from .context import Context
from .llm import LLMError, Ollama

log = logging.getLogger(__name__)


@dataclass
class Result:
    raw: str = ""
    text: str = ""
    language: str = ""
    press_enter: bool = False
    llm_used: bool = False
    dictionary_hits: list[str] = field(default_factory=list)
    snippet_ids: list[int] = field(default_factory=list)
    warning: str | None = None


class Pipeline:
    def __init__(self, cfg, store, stt):
        self.cfg = cfg
        self.store = store
        self.stt = stt

    def llm(self) -> Ollama | None:
        if not self.cfg.get("llm.enabled"):
            return None
        return Ollama(
            self.cfg.get("llm.url"),
            self.cfg.get("llm.model"),
            self.cfg.get("llm.keep_alive"),
            self.cfg.get("llm.timeout"),
            self.cfg.get("llm.api"),
        )

    def glossary(self, ctx: Context | None) -> list[str]:
        words = [e.word for e in self.store.dictionary()]
        if ctx and ctx.ide and self.cfg.get("cleanup.ide_features"):
            words += ctx.identifiers
        return words

    def transcribe(self, audio: np.ndarray, ctx: Context | None) -> tuple[str, str]:
        res = self.stt.transcribe(audio, self.cfg.get("stt.languages"), self.glossary(ctx))
        text = res.text.strip()
        return ("" if tp.is_hallucination(text) else text), res.language

    def process_text(self, raw: str, language: str, ctx: Context, on_stage=None) -> Result:
        cfg = self.cfg
        out = Result(raw=raw, language=language)
        text = raw
        if cfg.get("cleanup.level") != "none":
            text = tp.strip_fillers(text)
        if cfg.get("cleanup.press_enter_command"):
            text, out.press_enter = tp.strip_press_enter(text)

        snippets = [tp.SnippetRef(s.id, s.trigger, s.expansion) for s in self.store.snippets()]
        text, used = tp.insert_snippet_placeholders(text, snippets)
        smart = cfg.get("cleanup.smart_formatting")
        if smart:
            text = tp.insert_break_placeholders(text)
        out.snippet_ids = [s.id for s in used.values()]

        level = cfg.get("cleanup.level")
        english = not language or language.startswith("en")
        style = cfg.get("styles").get(ctx.category) if english else None
        glossary = self.glossary(ctx)
        llm = self.llm()
        body = text
        for token in [*used, *tp.BREAK_TOKENS]:
            body = body.replace(token, "")
        long_enough = tp.word_count(body) >= cfg.get("llm.min_words")

        if level != "none" and llm and long_enough:
            system = self._system(level, style, smart, glossary, ctx, "[[" in text)
            if on_stage:
                on_stage("Polishing…")
            try:
                text = llm.cleanup(text, system)
                out.llm_used = True
            except LLMError as e:
                out.warning = f"AI cleanup unavailable ({e}); used basic formatting"
                text = tp.basic_format(text, smart)
        elif level != "none":
            text = tp.basic_format(text, smart)

        # Small models follow style instructions loosely; enforce the mechanical parts.
        text = tp.apply_style(text, style)

        rules = [(e.wrong, e.word) for e in self.store.dictionary() if e.wrong]
        text, out.dictionary_hits = tp.apply_corrections(text, rules)
        text = tp.expand_placeholders(text, used)
        text = tp.expand_breaks(text)

        if ctx.messaging and cfg.get("cleanup.drop_period_in_messages"):
            text = tp.drop_trailing_period(text)
        if ctx.ide and cfg.get("cleanup.ide_features") and not used and not out.dictionary_hits:
            text = tp.ide_file_tags(text)
        if not ctx.terminal:
            text = tp.join_with_context(text, ctx.before, set(glossary))
        out.text = text
        return out

    def _system(self, level, style, smart, glossary, ctx, placeholders) -> str:
        from .llm import cleanup_system_prompt

        return cleanup_system_prompt(level, style, smart, glossary, ctx.before or "", placeholders)

    def edit(self, text: str, instruction: str) -> str:
        llm = self.llm()
        if not llm:
            raise LLMError("the local LLM is disabled in settings")
        return llm.edit(text, instruction)
