"""Input and output guardrails for the Translation Agent (ADR-12, ADR-13).

Kept out of the node module so each check is unit-testable without compiling a
graph, and so `validate_output` and `fallback_translate` can share one
implementation rather than drifting apart.

Two rules govern how a failing check behaves, and they differ on purpose:

* **Checks with no dependency fail closed.** Length limits and the ISO-code shape
  check are pure arithmetic and string work; when they reject, the caller routes
  into the fallback chain.
* **Checks that depend on a library fail open.** If `langdetect` is missing or
  raises, `detect_language_code` returns None and the caller accepts the
  translation. Failing closed here would push every message onto the secondary
  provider the moment one dependency broke — the opposite of NFR-02.

Nothing in this module raises. That is what lets the nodes keep their own
no-raise guarantee without wrapping every call in try/except.
"""

from __future__ import annotations

import logging
import re

from src.schemas.auth import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# ISO 639-1: exactly two lowercase letters. Used to gate `target_language`
# before it is interpolated into the system prompt — a string of this shape
# cannot carry an injection payload.
ISO_639_1 = re.compile(r"^[a-z]{2}$")

# Anything longer is not a chat message. Bounds generation cost, latency and the
# injection surface at once, while sitting far above real traffic.
MAX_INPUT_CHARS = 2000

# Per context line. Five of these plus the message must stay a sane prompt size.
MAX_CONTEXT_MESSAGE_CHARS = 500

# Below this, langdetect says more about itself than about the text. ADR-11
# documents the same weakness at the input side, where the limit is 5 characters
# (_MIN_DETECT_CHARS); the output side sets its own, higher bar because a wrong
# answer there discards a finished translation rather than skipping a step.
MIN_VERIFY_CHARS = 20

# Newlines and control characters are what let a context line forge prompt
# structure; collapsing them to a space removes the capability entirely.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# --- Output scaffolding -----------------------------------------------------
# The system prompt asks for the translation and nothing else. These patterns
# remove what models wrap around it anyway, so "translation only" holds by
# construction instead of by the model's goodwill.

# Reasoning models emit a visible chain of thought before the answer.
_REASONING_BLOCK = re.compile(
    r"<(think|thinking|reasoning|scratchpad)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)

# Our own delimiters, echoed back. The names belong to the prompt, so removing
# them anywhere in the output costs nothing a real chat message would miss.
_DELIMITER_TAG = re.compile(
    r"</?(?:message|conversation_history)(?:_[0-9a-f]+)?\b[^>]*>", re.IGNORECASE
)

# One tag wrapping the whole answer (<translation>...</translation>). Matched as
# an opening/closing pair so a translation that merely starts with "<" survives.
_WRAPPING_TAG = re.compile(
    r"\A<([a-z_][\w.-]*)[^>]*>\s*(?P<body>.*?)\s*</\1\s*>\Z", re.IGNORECASE | re.DOTALL
)

_CODE_FENCE = re.compile(r"\A```[^\n]*\n(?P<body>.*?)\n?```\Z", re.DOTALL)

# "Translation:", "Here is the translation:", "Bản dịch:" and friends.
_OUTPUT_LABEL = re.compile(
    r"\A\s*(?:here\s+(?:is|are|'s)\s+)?(?:the\s+)?"
    r"(?:translation|translated\s+(?:text|message|version)|output|result"
    r"|bản\s+dịch|dịch)"
    r"\s*(?:\([^)\n]*\))?\s*[:：]\s*",
    re.IGNORECASE,
)

# A commentary line appended after the translation.
_TRAILING_NOTE = re.compile(
    r"(?:\r?\n\s*)+[(\[]?\s*(?:note|notes|n\.b\.|explanation|ghi\s*chú|lưu\s*ý)\b.*\Z",
    re.IGNORECASE | re.DOTALL,
)

_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("«", "»"), ("「", "」"))

# --- Leaked identifiers -----------------------------------------------------
# A translation may only contain identifiers its own source contains. Anything
# else was copied out of the conversation history or invented, and both reach a
# recipient who was never shown it.

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Digit runs, separators included so a reformatted phone number still reads as
# one token. Commas and colons are excluded on purpose: they separate list items
# and clock times, which would otherwise glue unrelated figures together.
_DIGIT_RUN = re.compile(r"\d[\d\s.\-()/+]*\d")

# Below this a run is a date, a version or a price, all of which a translation
# may legitimately reformat (16/08/2026 -> 08/16/2026 is eight digits reordered).
# Phone numbers, account numbers and card numbers all sit above it.
MIN_LEAKED_DIGITS = 9

# Key-shaped tokens: long, unbroken, and mixing letters with digits. The mix is
# what separates them from a compound word of the same length.
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,}")

# Phrases that only appear in a translation if the model recited its own
# instructions instead of translating. The length rule misses a partial recital.
_PROMPT_INSTRUCTION = re.compile(
    r"you are the translation engine"
    r"|conversation[_ ]history"
    r"|iso 639-1"
    r"|#\s*(?:role|task|constraints|output format)\b",
    re.IGNORECASE,
)

# --- Refusals ---------------------------------------------------------------
# A refusal written in the *target* language is invisible to the language rule
# further down (its detected language matches the target, which is exactly what
# a correct translation looks like) and far too short for the length rule.
# These markers are what is left: phrases in which the model speaks as itself.

# Deliberately narrow. Every alternative names the model or names the model's
# own inability — never plain negation, which is ordinary chat vocabulary
# ("I can't make the meeting") and would cost real messages their translation.
_REFUSAL_MARKER = re.compile(
    r"\bas an ai\b"
    r"|\bi(?:'m| am) (?:an ai|a language model|an assistant)\b"
    r"|\bi (?:can'?t|cannot|can not|am unable to|won'?t) "
    r"(?:help|assist|comply|translate|provide|fulfil|fulfill|do that)\b"
    # The apology form still has to name what it will not do. Without that,
    # "I'm sorry, I can't come tomorrow" — an ordinary reply — would read as a
    # refusal and the sender's own words would be thrown away.
    r"|\bi(?:'m| am) (?:sorry|afraid)[^.\n]{0,40}?\b(?:can'?t|cannot|unable to)\s+"
    r"(?:help|assist|comply|translate|provide|do|fulfil|fulfill)\b"
    r"|\bi (?:must|have to) (?:decline|refuse)\b"
    r"|\bagainst my (?:guidelines|programming|policy|policies)\b"
    r"|\b(?:this|that) (?:request|content|message) violates\b"
    r"|tôi không thể (?:dịch|giúp|hỗ trợ|thực hiện|đáp ứng)"
    r"|với tư cách (?:là )?(?:một )?(?:ai|trợ lý|mô hình)"
    r"|tôi (?:rất )?xin lỗi[^.\n]{0,40}?không thể",
    re.IGNORECASE,
)

# A refusal stands in place of the translation, so it is about one sentence
# long. Mirrors _MAX_TRANSLATION_FLOOR in the nodes rather than importing it:
# the nodes import this module, and the reverse would close the cycle.
MAX_REFUSAL_CHARS = 200

# --- Dropped identifiers ----------------------------------------------------
# The mirror image of the leak rules: what the message *did* contain has to
# survive into the translation. A model that redacts a phone number or drops a
# link removes the part of the message the recipient most needs.

_URL = re.compile(r"""(?:https?://|www\.)[^\s<>"')\]]+""", re.IGNORECASE)

# Trailing punctuation belongs to the sentence, not to the URL.
_URL_TRAILING = """.,;:!?)]}'\""""

# --- Context echo -----------------------------------------------------------
# Prose copied out of the conversation history. The identifier rules cannot see
# it and the length rule only fires when the model copies a whole block.

# Long enough that a coincidental match between two chat messages is
# implausible, short enough to catch a single copied sentence.
MIN_ECHO_CHARS = 40

# Context lines arrive labelled by `DatabaseContextProvider` ("U01: ..."); the
# label is ours rather than anyone's words, so it must not count as a match.
_SPEAKER_ALIAS = re.compile(r"^U\d{2}:\s*")


def is_supported_language_code(code: str) -> bool:
    """Report whether `code` is a supported ISO 639-1 language code.

    Validates against the canonical ``SUPPORTED_LANGUAGES`` allowlist defined
    in :mod:`src.schemas.auth`.  The shape check (two lowercase ASCII letters)
    is still applied first so that obviously malformed strings are rejected
    before the set lookup.
    """
    return bool(code) and bool(ISO_639_1.match(code)) and code in SUPPORTED_LANGUAGES


def is_input_too_long(text: str) -> bool:
    """Report whether `text` exceeds the per-message input limit."""
    return len(text) > MAX_INPUT_CHARS


def sanitize_context_message(message: str) -> str:
    """Neutralise one context line so it cannot forge prompt structure.

    Conversation context is written by *other* participants, so this is the
    cross-user channel: without it, A could plant a line that closes the history
    section and opens a second message section, hijacking B's translation.

    Two transforms, and both are needed. Collapsing newlines stops the forged
    tags from starting their own line; escaping the angle brackets stops them
    from reading as tags at all, which flattening alone does not achieve.

    Escaping is free here because context lines are only ever *read* by the
    model — they are never echoed into the translation, so the entities cannot
    reach the recipient.

    Args:
        message: Raw context line as supplied by the ContextProvider.

    Returns:
        A single-line, length-capped, tag-free version safe to interpolate.
    """
    flattened = _CONTROL_CHARS.sub(" ", message)
    collapsed = " ".join(flattened.split())
    # Truncate on the real text, before escaping. Escaping expands `<` to four
    # characters and `&` to five, so cutting afterwards would drop context the
    # model was meant to read and could slice an entity in half.
    if len(collapsed) > MAX_CONTEXT_MESSAGE_CHARS:
        collapsed = collapsed[:MAX_CONTEXT_MESSAGE_CHARS].rstrip() + "…"
    return collapsed.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_translation_scaffolding(candidate: str, original_text: str) -> str:
    """Remove everything a model wrapped around the translation it was asked for.

    The output-format rules in the system prompt are an instruction, and an
    instruction is a request. This is the enforcement: reasoning blocks, echoed
    delimiters, code fences, ``Translation:`` labels, quotation marks the source
    never had and appended translator's notes are stripped deterministically,
    whatever the provider decided to send.

    Every rule that could plausibly fire on real text is checked against
    `original_text` first — a message that is itself quoted, or that itself opens
    with "Translation:", keeps that shape in its translation. Stripping is also
    never allowed to empty the output: if nothing survives, the untouched
    candidate is returned and the ordinary validation rules judge it.

    Args:
        candidate: Raw text as the provider returned it.
        original_text: The message that was sent for translation.

    Returns:
        The translation alone, or `candidate` unchanged when nothing matched.
    """
    text = candidate.strip()
    if not text:
        return candidate

    # Structural wrappers can nest — a code fence around a tag around the text —
    # so peel until a pass changes nothing rather than assuming one layer.
    for _ in range(3):
        before = text
        text = _REASONING_BLOCK.sub("", text).strip()
        text = _DELIMITER_TAG.sub("", text).strip()
        wrapped = _WRAPPING_TAG.match(text)
        if wrapped:
            text = wrapped.group("body").strip()
        fenced = _CODE_FENCE.match(text)
        if fenced:
            text = fenced.group("body").strip()
        if text == before:
            break

    if not _OUTPUT_LABEL.match(original_text):
        text = _OUTPUT_LABEL.sub("", text, count=1).strip()

    # Only when the source was a single line: a multi-line message can end on a
    # line that genuinely begins "Note:", and that line is the sender's.
    if "\n" not in original_text.strip():
        text = _TRAILING_NOTE.sub("", text).strip()

    text = _strip_outer_quotes(text, original_text)

    return text or candidate


def _strip_outer_quotes(text: str, original_text: str) -> str:
    """Drop one pair of quotation marks the source did not have.

    Models quote the translation to mark it as quoted speech. Only a pair
    enclosing the whole string is removed, and only when the original does not
    open with the same character — otherwise the quotes are part of the message.
    """
    source = original_text.strip()
    for opening, closing in _QUOTE_PAIRS:
        if (
            len(text) > 1
            and text.startswith(opening)
            and text.endswith(closing)
            and not source.startswith(opening)
            # A closing mark in the middle means these two are not a pair around
            # the whole string: `"a" and "b"` would lose its first and last mark.
            and closing not in text[1:-1]
        ):
            return text[1:-1].strip()
    return text


def find_leaked_identifiers(translated_text: str, original_text: str) -> list[str]:
    """Return identifiers present in the translation but absent from its source.

    The agent shows the model other people's messages so it can resolve pronouns
    (ADR-01). That context is the leak channel: a message crafted to make the
    model recite the history — or a model that simply drifts — delivers another
    participant's phone number, email or account number to a recipient who was
    reading a different sentence. The same check catches an invented identifier,
    which is a correctness failure rather than a privacy one but is no more
    deliverable.

    Only identifier-shaped tokens are compared, never prose. A translation
    rewords everything by definition, so any rule about *wording* would fire on
    correct output; a translation that introduces an email address that was
    never in the message is unambiguous.

    Comparison ignores formatting, because reformatting is legitimate: digits
    are matched against the source's digits with every separator removed, so
    ``0912 345 678`` and ``0912.345.678`` are the same number. Runs shorter than
    `MIN_LEAKED_DIGITS` are ignored, which keeps reordered dates and version
    numbers out of the result.

    Args:
        translated_text: Candidate translation to inspect.
        original_text: The message it was made from.

    Returns:
        The offending values, in the order found. Empty means nothing leaked.
    """
    leaked: list[str] = []

    source_folded = original_text.casefold()
    for email in _EMAIL.findall(translated_text):
        if email.casefold() not in source_folded:
            leaked.append(email)

    source_digits = re.sub(r"\D", "", original_text)
    for run in _DIGIT_RUN.findall(translated_text):
        digits = re.sub(r"\D", "", run)
        if len(digits) >= MIN_LEAKED_DIGITS and digits not in source_digits:
            leaked.append(run.strip())

    for token in _OPAQUE_TOKEN.findall(translated_text):
        looks_like_a_key = any(c.isdigit() for c in token) and any(
            c.isalpha() for c in token
        )
        if looks_like_a_key and token not in original_text:
            leaked.append(token)

    return leaked


def discloses_prompt_instructions(translated_text: str) -> bool:
    """Report whether the output recites the agent's own instructions.

    A full prompt dump is long enough for the ratio rule to reject on its own.
    This covers the partial recital — "You are the translation engine of a chat
    application" is 52 characters — which would otherwise be delivered as the
    translation and would tell an attacker exactly what to aim at next.

    Matches the instruction wording rather than the delimiter tags: by the time
    this runs, `strip_translation_scaffolding` has already removed the tags.
    """
    return bool(_PROMPT_INSTRUCTION.search(translated_text))


def looks_like_a_refusal(translated_text: str, original_text: str) -> bool:
    """Report whether the output is the model declining instead of translating.

    The language rule below only fires when the refusal happens to be written in
    the *source* language. A refusal written in the target language — the common
    case, because the model was just told to answer in that language — looks to
    a detector exactly like a correct translation, and is far under the length
    floor. It was therefore delivered to the recipient as though it were the
    message, and the sender's meaning disappeared without a trace. That is the
    failure this guard exists to stop (ADR-13, amended).

    Two rules keep it off real messages, and neither is optional:

    * A message that itself declines something must keep declining once
      translated, so the check stands down whenever the *original* carries a
      marker of its own.
    * Only a short output can be a refusal standing in for the translation.
      Past `MAX_REFUSAL_CHARS` the text is a translation that happens to contain
      the phrase, and discarding it would cost the recipient a real message.

    Args:
        translated_text: Candidate translation to inspect.
        original_text: The message it was made from.

    Returns:
        True only when the output speaks as the model rather than as the sender.
    """
    if len(translated_text) > MAX_REFUSAL_CHARS:
        return False
    if not _REFUSAL_MARKER.search(translated_text):
        return False
    if _REFUSAL_MARKER.search(original_text):
        # The sender declined something; so must the translation.
        return False

    logger.info("Translation reads as a refusal rather than as a translation")
    return True


def find_dropped_identifiers(translated_text: str, original_text: str) -> list[str]:
    """Return identifiers the message contained and the translation does not.

    The mirror of `find_leaked_identifiers`, needed for the same reason from the
    other side: a model told to be careful with personal data will redact a
    phone number, drop a link, or replace an address with a placeholder. Nothing
    else notices — the output is fluent, the right length and the right
    language — and the recipient acts on a message with its most load-bearing
    detail missing.

    Only the three shapes that mean nothing once altered are checked: emails,
    URLs and digit runs of at least `MIN_LEAKED_DIGITS`. Words are never
    compared, because a translation is expected to change all of them.

    Formatting differences are ignored, as in the leak direction: digits are
    matched against the translation's digits with every separator removed, so
    ``0912 345 678`` still counts as present when rendered ``0912.345.678``.

    Args:
        translated_text: Candidate translation to inspect.
        original_text: The message it was made from.

    Returns:
        The missing values, in the order found. Empty means nothing was lost.
    """
    dropped: list[str] = []

    translated_folded = translated_text.casefold()
    for email in _EMAIL.findall(original_text):
        if email.casefold() not in translated_folded:
            dropped.append(email)

    for url in _URL.findall(original_text):
        trimmed = url.rstrip(_URL_TRAILING)
        if trimmed.casefold() not in translated_folded:
            dropped.append(trimmed)

    translated_digits = re.sub(r"\D", "", translated_text)
    for run in _DIGIT_RUN.findall(original_text):
        digits = re.sub(r"\D", "", run)
        if len(digits) < MIN_LEAKED_DIGITS:
            continue
        # A leading zero is the national trunk prefix and is dropped whenever a
        # number is rewritten in international form: 0912 345 678 becomes
        # (+84) 912 345 678. The number survived; only its notation changed.
        forms = {digits, digits.lstrip("0")}
        if not any(form and form in translated_digits for form in forms):
            dropped.append(run.strip())

    return dropped


def classify_leak_source(values: list[str], context_messages: list[str]) -> str:
    """Say whether leaked identifiers came from the conversation history.

    `find_leaked_identifiers` catches two different failures at once: the model
    copying another participant's details out of the context it was handed, and
    the model inventing details that were never anywhere. Only the first is a
    privacy incident, and the two call for different fixes — a prompt change
    against one, a model change against the other — so the telemetry has to be
    able to tell them apart.

    Args:
        values: Identifiers reported by `find_leaked_identifiers`.
        context_messages: Raw context lines as supplied to the prompt.

    Returns:
        ``"context"`` when at least one value appears in the history,
        ``"invented"`` otherwise.
    """
    if not values or not context_messages:
        return "invented"

    context = " ".join(context_messages)
    context_folded = context.casefold()
    context_digits = re.sub(r"\D", "", context)

    for value in values:
        if value.casefold() in context_folded:
            return "context"
        digits = re.sub(r"\D", "", value)
        if len(digits) >= MIN_LEAKED_DIGITS and digits in context_digits:
            return "context"

    return "invented"


def find_context_echo(
    translated_text: str,
    original_text: str,
    context_messages: list[str],
) -> str | None:
    """Return a stretch of prose the translation copied out of the history.

    The identifier rules only see identifier-shaped tokens, and the length rule
    only fires once the model has copied enough history to make the output long.
    Between the two sits the case where one sentence written by somebody else
    rides into a translation read by somebody who was never shown it.

    Measurement only for now: the caller records the finding and delivers the
    translation anyway. Chat repeats itself — two people agreeing about the same
    deploy write nearly the same sentence — so the false-positive rate has to be
    observed on real traffic before this is allowed to discard anything
    (ADR-21).

    Matching is on `MIN_ECHO_CHARS`-character windows rather than a longest
    common substring: the same evidence at a fraction of the cost, which is what
    keeps the guardrail layer inside its ~2ms budget.

    Args:
        translated_text: Candidate translation to inspect.
        original_text: The message it was made from.
        context_messages: Raw context lines, speaker alias included.

    Returns:
        The overlapping stretch, or None when there is none. Callers log its
        length and never its content — it is by definition somebody's message.
    """
    if not context_messages:
        return None

    translated = _normalize_for_echo(translated_text)
    if len(translated) < MIN_ECHO_CHARS:
        return None
    original = _normalize_for_echo(original_text)

    for message in context_messages:
        line = _normalize_for_echo(_SPEAKER_ALIAS.sub("", message))
        if len(line) < MIN_ECHO_CHARS:
            continue
        windows = {
            line[i : i + MIN_ECHO_CHARS]
            for i in range(len(line) - MIN_ECHO_CHARS + 1)
        }
        for i in range(len(translated) - MIN_ECHO_CHARS + 1):
            window = translated[i : i + MIN_ECHO_CHARS]
            # Wording the message itself contains is the sender's own, however
            # much of it the history happens to repeat.
            if window in windows and window not in original:
                return window

    return None


def _normalize_for_echo(text: str) -> str:
    """Fold case and whitespace so re-wrapping alone is never a match."""
    return " ".join(text.casefold().split())


def detect_language_code(text: str) -> str | None:
    """Detect the language of `text` locally with langdetect (~2ms, no API call).

    The canonical implementation shared by input detection and output
    verification. Returns None when detection is unavailable or the result is not
    a usable ISO 639-1 code — langdetect emits region-qualified codes such as
    ``zh-cn`` and the contract only uses the base code.
    """
    try:
        from langdetect import DetectorFactory, detect

        # langdetect samples randomly; a fixed seed keeps results reproducible
        DetectorFactory.seed = 0
        code = detect(text).lower().split("-")[0]
    except Exception:
        return None

    return code if ISO_639_1.match(code) else None


def is_untranslated_output(
    translated_text: str,
    source_language: str,
    target_language: str,
) -> bool:
    """Report whether the model returned source-language text instead of a translation.

    Catches the refusal case this guard exists for — ``"I can't help with that."``
    is 24 characters, well under the length rule's 200-char floor, so it would
    otherwise be delivered to the recipient as though it were the translation —
    along with the model echoing the input untranslated or answering it.

    **The rule deliberately does not ask "is this text in the target language?"**
    langdetect is confidently wrong on the exact register this product targets:
    ``"Hotfix merged, CI green now"`` is reported as Dutch at 0.86, and
    ``"Docker Kubernetes Jenkins CI/CD pipeline"`` as German at 0.9999. Rejecting
    on ``detected != target`` therefore discarded correct translations of
    jargon-dense chat and served the untranslated original instead — the opposite
    of the intended effect. Raising the length gate does not help, because the
    German misfire is 40 characters, and neither does a confidence margin.

    Instead the detector is only ever asked to *agree with a value already
    known*: the source language established by ``detect_language``. A
    misclassification into some unrelated third language now proves nothing and
    changes nothing, while the failure modes above still produce output that
    matches the source. False negatives are the acceptable direction here — a
    missed refusal is one bad message, a false positive silently degrades every
    technical message in the conversation.

    Args:
        translated_text: Candidate translation to check.
        source_language: ISO 639-1 code the message was written in.
        target_language: ISO 639-1 code the translation should be in.

    Returns:
        True only when the text is long enough to judge and its detected
        language matches the source while differing from the target.
    """
    if len(translated_text) < MIN_VERIFY_CHARS:
        return False
    if not is_supported_language_code(source_language):
        return False
    if not is_supported_language_code(target_language):
        return False
    if source_language == target_language:
        return False

    detected = detect_language_code(translated_text)
    if detected is None:
        return False

    if detected == source_language:
        logger.info(
            "Translation still looks like %s, the source language, not %s",
            source_language,
            target_language,
        )
        return True

    return False
