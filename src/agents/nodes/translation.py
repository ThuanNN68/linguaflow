"""Nodes of the Translation Agent.

Node chain per docs/architecture/diagrams.md section 2:
    detect_language -> build_context -> translate -> validate_output
                                                  -> fallback_translate

Every node takes the full ``AgentState`` and returns a dict holding only the
fields it changed; LangGraph merges those partial updates. That convention is
stated here once rather than repeated in each node's docstring.

Hard rule: no node raises. Failures are recorded in ``state["error"]`` and the
graph continues down the fallback path that returns the original text, so an LLM
outage never blocks the chat flow (NFR-02).
"""

from __future__ import annotations

import logging
import re
import time

from src.agents.context_provider import (
    DEFAULT_CONTEXT_SIZE,
    ContextProvider,
    NullContextProvider,
)
from src.agents.customization import (
    CustomizationProvider,
    NullCustomizationProvider,
)
from src.agents.guardrails import (
    MAX_INPUT_CHARS,
    classify_leak_source,
    detect_language_code,
    discloses_prompt_instructions,
    find_context_echo,
    find_dropped_identifiers,
    find_leaked_identifiers,
    is_input_too_long,
    is_supported_language_code,
    is_untranslated_output,
    looks_like_a_refusal,
    strip_translation_scaffolding,
)
from src.agents.prompts import (
    DETECT_LANGUAGE_PROMPT,
    TRANSLATE_SYSTEM_PROMPT,
    build_audience_block,
    build_user_prompt,
    new_prompt_nonce,
)
from src.agents.state import AgentState
from src.services.language.fallback_translator import FALLBACK_MODEL_NAME, translate_with_secondary_provider
from src.services.shared.llm import LlmCallInfo, extract_call_info, extract_text, get_llm

logger = logging.getLogger(__name__)

# Text with no letters at all (emoji, digits, punctuation, URLs) needs no translation
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

# Below this length neither langdetect nor the LLM is reliable (e.g. "Ok", "Yes"),
# so keep the provisional language instead of spending an LLM call on a guess.
_MIN_DETECT_CHARS = 5

# A translation longer than this is usually the model explaining itself instead of
# translating. Compared against max(len(original) * ratio, floor) so that very short
# inputs are not flagged just because the ratio is easy to exceed.
_MAX_TRANSLATION_RATIO = 4
_MAX_TRANSLATION_FLOOR = 200

# Only the first N characters are sent for language detection
_DETECT_SAMPLE_CHARS = 500


def _telemetry(state: AgentState, **measurements: object) -> dict:
    """Build a state update that adds measurements to the telemetry dict.

    ``telemetry`` has no LangGraph reducer, so a node returning it replaces the
    whole dict. Merging the previous contents here keeps each node free to
    report only what it measured, matching how every other field in the state
    already behaves.
    """
    return {"telemetry": {**state.get("telemetry", {}), **measurements}}


def _running_total(state: AgentState, key: str, amount: int) -> int:
    """Add to a telemetry counter that spans several nodes in one run.

    Detection and translation are two separate LLM calls, and the cost of the
    first is invisible unless its tokens are added to the second's.
    """
    return state.get("telemetry", {}).get(key, 0) + amount


def _elapsed_ms(started: float) -> int:
    """Milliseconds since a ``time.perf_counter()`` reading."""
    return int((time.perf_counter() - started) * 1000)


def _detect_local(text: str) -> str | None:
    """Detect the language of an incoming message (~2ms, no API call).

    A thin wrapper over :func:`guardrails.detect_language_code`, kept as a
    separate name on purpose: detecting the *input* language and verifying the
    *output* language are different decisions, and tests must be able to stub one
    without silently capturing the other. They share one implementation so the
    two can never disagree about what a language code looks like.
    """
    return detect_language_code(text)


async def _detect_with_llm(text: str) -> tuple[str | None, int, LlmCallInfo]:
    """Detect the language with the LLM. Slower but reliable on short text.

    Returns the language code (None on failure), the elapsed milliseconds and
    what the provider reported about the call, so the caller can account for
    both the latency and the tokens this second round trip costs.
    """
    started = time.perf_counter()
    try:
        llm = get_llm()
        response = await llm.ainvoke(
            DETECT_LANGUAGE_PROMPT.format(text=text[:_DETECT_SAMPLE_CHARS])
        )
        detected = extract_text(response).lower()
    except Exception as exc:
        logger.warning("LLM language detection failed: %s", exc)
        return None, _elapsed_ms(started), LlmCallInfo()

    elapsed_ms = _elapsed_ms(started)
    call_info = extract_call_info(response)

    if not is_supported_language_code(detected):
        logger.warning("LLM language detection returned an invalid code: %r", detected)
        return None, elapsed_ms, call_info

    return detected, elapsed_ms, call_info


async def detect_language(state: AgentState) -> dict:
    """Determine the true source language of the message.

    Two-tier strategy that keeps LLM calls off the common path (ADR-11). Measured
    with Groq: langdetect ~2ms versus ~1285ms for an LLM call.

    1. Too short, or no letters: keep the provisional value, detect nothing.
    2. langdetect agrees with the provisional value: trust it, no LLM call. This
       is the common case — users usually write in the language they configured.
    3. langdetect disagrees, or fails: ask the LLM to arbitrate. langdetect's own
       answer is discarded here because it is unreliable on short text ("Ok anh"
       is detected as Tagalog), which is exactly why the two disagree.

    The incoming ``source_language`` is the provisional value (the sender's
    preferred_language) per docs/api/contract.md section 4.3.
    """
    original_text = (state.get("original_text") or "").strip()
    provisional = state.get("source_language", "")
    started = time.perf_counter()

    if not original_text:
        return {
            "error": "original_text is empty, cannot detect language",
            **_telemetry(state, fallback_reason="empty_input"),
        }

    if not _HAS_LETTER.search(original_text) or len(original_text) < _MIN_DETECT_CHARS:
        return {
            "source_language": provisional,
            **_telemetry(state, detect_method="skipped", detect_ms=_elapsed_ms(started)),
        }

    local = _detect_local(original_text)

    # Fast path: langdetect agrees with the language the sender configured
    if local and local == provisional:
        return {
            "source_language": local,
            **_telemetry(
                state,
                detect_method="langdetect",
                langdetect_agreed=True,
                detect_ms=_elapsed_ms(started),
            ),
        }

    if local:
        logger.info(
            "langdetect (%s) disagrees with preferred_language (%s), asking the LLM",
            local,
            provisional,
        )

    detected, elapsed_ms, call_info = await _detect_with_llm(original_text)
    measurements = _telemetry(
        state,
        detect_method="llm" if detected else "llm_failed",
        # False on disagreement, None when langdetect itself returned nothing —
        # the two are different failures and the ADR-11 hit rate needs to tell
        # them apart.
        langdetect_agreed=False if local else None,
        detect_ms=_elapsed_ms(started),
        # Recorded here too, so a run that never reaches the translate node —
        # a passthrough, or a failure before it — still reports which model
        # served it. The translate node overwrites this with its own answer.
        model_served=call_info.model_served,
        llm_calls=_running_total(state, "llm_calls", 1),
        input_tokens=_running_total(state, "input_tokens", call_info.input_tokens),
        output_tokens=_running_total(state, "output_tokens", call_info.output_tokens),
    )

    if detected:
        return {"source_language": detected, "latency_ms": elapsed_ms, **measurements}

    # The LLM failed too. Keep the provisional value rather than langdetect's
    # answer: reaching this point means the two disagreed, and CONTRACT section
    # 4.3 forbids using langdetect's result in that case.
    return {"source_language": provisional, "latency_ms": elapsed_ms, **measurements}


def make_build_context(
    context_provider: ContextProvider | None = None,
    limit: int = DEFAULT_CONTEXT_SIZE,
):
    """Create a build_context node bound to a specific context source.

    Uses a closure rather than a module global so several graphs can run side by
    side with different sources (a real one in production, a fake one in tests).

    Args:
        context_provider: Source of recent messages. Defaults to no context.
        limit: How many recent messages to request from the provider.

    Returns:
        The build_context node function, ready to add to a graph.
    """
    provider = context_provider or NullContextProvider()

    async def build_context(state: AgentState) -> dict:
        """Load recent conversation history, degrading to none on failure."""
        conversation_id = state.get("conversation_id", "")
        started = time.perf_counter()

        def no_context() -> dict:
            """Continue with no history — quality drops, the message still goes."""
            return {
                "context_messages": [],
                **_telemetry(state, context_ms=_elapsed_ms(started), context_lines=0),
            }

        if not conversation_id:
            return no_context()

        try:
            messages = await provider.get_recent_messages(conversation_id, limit)
        except Exception as exc:
            # Missing context degrades translation quality but must not block it
            logger.warning("build_context failed: %s", exc)
            return no_context()

        context_messages = list(messages)
        return {
            "context_messages": context_messages,
            **_telemetry(
                state,
                context_ms=_elapsed_ms(started),
                context_lines=len(context_messages),
            ),
        }

    return build_context


def make_customize(provider: CustomizationProvider | None = None):
    """Bind a customization source and return the `customize` node.

    Bound the same way `build_context` is, and for the same reason: the node
    signature LangGraph expects takes only the state, so anything else it needs
    has to be closed over when the graph is built.

    Args:
        provider: Source of the audience facts. Defaults to knowing nothing.

    Returns:
        The `customize` node.
    """
    source = provider or NullCustomizationProvider()

    async def customize(state: AgentState) -> dict:
        """Load who this translation is for, degrading to knowing nothing.

        No model is called here. The standing already arrived in the state from
        the fan-out, and the subject area and audience were inferred in the
        background long before this message; this node only reads them. Putting
        the inference here instead would run it once per bucket for a single
        message, which is up to four times for the same answer.

        Every failure path returns the empty customization rather than raising.
        The prompt then reads exactly as it did before this node existed, which
        is a worse translation and still a translation — the guarantee NFR-02
        makes about every other step applies here too.
        """
        started = time.perf_counter()

        def unknown() -> dict:
            """Continue with no audience facts."""
            return {
                "domain": "",
                "audience": "",
                "glossary_terms": [],
                **_telemetry(
                    state,
                    customize_ms=_elapsed_ms(started),
                    customized=False,
                    glossary_hits=0,
                ),
            }

        conversation_id = state.get("conversation_id", "")
        if not conversation_id:
            return unknown()

        try:
            customization = await source.get_customization(
                conversation_id,
                original_text=state.get("original_text", ""),
                source_language=state.get("source_language", ""),
                target_language=state.get("target_language", ""),
            )
        except Exception as exc:
            # Same reasoning as build_context: a missing audience costs quality,
            # never delivery.
            logger.warning("customize failed: %s", exc)
            return unknown()

        return {
            "domain": customization.domain,
            "audience": customization.audience,
            "glossary_terms": list(customization.glossary_terms),
            **_telemetry(
                state,
                customize_ms=_elapsed_ms(started),
                customized=bool(customization.domain or customization.audience),
                glossary_hits=len(customization.glossary_terms),
            ),
        }

    return customize


async def translate(state: AgentState) -> dict:
    """Translate the message with the LLM, including conversation context."""
    original_text = (state.get("original_text") or "").strip()
    target_language = state.get("target_language", "")

    if not original_text:
        return {
            "error": "original_text is empty, nothing to translate",
            **_telemetry(state, fallback_reason="empty_input"),
        }
    if not target_language:
        return {
            "error": "target_language was not provided",
            **_telemetry(state, fallback_reason="bad_target_language"),
        }

    # target_language is interpolated into the *system* prompt below, and it
    # originates from a user-editable profile field. Anything that is not a bare
    # ISO code could append arbitrary instructions there (ADR-12).
    if not is_supported_language_code(target_language):
        logger.warning("Rejected malformed target_language: %r", target_language)
        return {
            "error": "target_language is not a valid ISO 639-1 code",
            **_telemetry(state, fallback_reason="bad_target_language"),
        }

    if is_input_too_long(original_text):
        # Not truncated on purpose: half a translated message is worse than none,
        # and NFR-02 means the recipient still reads the original either way.
        logger.warning(
            "Rejected oversized message: %d chars, limit %d",
            len(original_text),
            MAX_INPUT_CHARS,
        )
        return {
            "error": "original_text exceeds the guardrail length limit",
            **_telemetry(state, fallback_reason="oversized_input"),
        }

    # Both prompts share one nonce: the system prompt names the tag the message
    # will arrive in, so the model can tell the delimiter from a forged copy of
    # it in the message body (ADR-12).
    nonce = new_prompt_nonce()
    system_prompt = TRANSLATE_SYSTEM_PROMPT.format(
        target_language=target_language,
        nonce=nonce,
        # Renders to "" when nothing has been inferred and no standing was
        # supplied, which is the state of every conversation for its first few
        # messages. `.format` runs outside the try below, so this must not be
        # able to raise: `build_audience_block` reads unknown values as absent
        # rather than rejecting them.
        audience_block=build_audience_block(
            domain=state.get("domain", ""),
            audience=state.get("audience", ""),
            honorific_profile=state.get("honorific_profile", ""),
            sender_honorific_profile=state.get("sender_honorific_profile", ""),
            translation_tone=state.get("translation_tone", "natural"),
        ),
    )
    user_prompt = build_user_prompt(
        original_text=original_text,
        context_messages=state.get("context_messages", []),
        nonce=nonce,
        glossary_terms=state.get("glossary_terms", []),
    )

    # Any latency already recorded by detect_language is carried forward so the
    # reported figure covers the whole agent run, not just this call
    detect_ms = state.get("latency_ms", 0)
    started = time.perf_counter()

    def total_latency_ms() -> int:
        """Elapsed time for this call plus whatever detection already spent."""
        return detect_ms + _elapsed_ms(started)

    try:
        llm = get_llm()
        response = await llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
    except Exception as exc:
        logger.warning("translate failed: %s", exc)
        return {
            "error": f"LLM call failed: {type(exc).__name__}",
            "latency_ms": total_latency_ms(),
            **_telemetry(
                state,
                translate_ms=_elapsed_ms(started),
                llm_calls=_running_total(state, "llm_calls", 1),
                fallback_reason="llm_error",
            ),
        }

    call_info = extract_call_info(response)
    # What the client was configured to ask for. `model_served` in the telemetry
    # is what the provider says it actually ran, and the two can differ.
    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", "")

    # "Return the translated text only" is an instruction, and instructions are
    # requests. Whatever the provider wrapped around the translation is removed
    # here, so the recipient reads the same thing whichever model served them.
    raw_text = extract_text(response)
    translated_text = strip_translation_scaffolding(raw_text, original_text)

    return {
        "translated_text": translated_text,
        "model": str(model_name),
        "latency_ms": total_latency_ms(),
        **_telemetry(
            state,
            translate_ms=_elapsed_ms(started),
            llm_calls=_running_total(state, "llm_calls", 1),
            # Prompt-tuning evidence: a rising rate means the format rules are
            # losing, and the stripper is the only thing keeping the output clean.
            scaffolding_stripped=translated_text != raw_text.strip(),
            model_served=call_info.model_served,
            finish_reason=call_info.finish_reason,
            request_id=call_info.request_id,
            input_tokens=_running_total(state, "input_tokens", call_info.input_tokens),
            output_tokens=_running_total(state, "output_tokens", call_info.output_tokens),
        ),
    }


async def validate_output(state: AgentState) -> dict:
    """Check the translation and decide whether to fall back.

    The fallback returns the original text verbatim: the recipient still reads
    the message, only untranslated. Required by NFR-02.
    """
    original_text = (state.get("original_text") or "").strip()
    translated_text = (state.get("translated_text") or "").strip()

    def fallback(reason: str, code: str = "", **measurements: object) -> dict:
        """Build the state update that returns the original text unchanged.

        Args:
            reason: Prose for the log, written for whoever reads it.
            code: Machine-readable cause for the telemetry. Empty means the
                cause was already recorded by the node that failed, which knows
                more about it than this one does.
            measurements: Extra telemetry the failing check wants to report
                alongside the reason, such as where a leaked value came from.
        """
        logger.info("Falling back to the original text: %s", reason)
        recorded = state.get("telemetry", {}).get("fallback_reason", "")
        update = {
            "translated_text": original_text,
            "is_valid": False,
            "is_fallback": True,
            # Provisional: fallback_translate raises this to "secondary" if the
            # secondary provider succeeds, and leaves it here if it does not.
            **_telemetry(
                state,
                outcome="original",
                fallback_reason=code or recorded or "llm_error",
                **measurements,
            ),
        }
        # ``error`` carries only reasons the request could not be processed —
        # an LLM outage, or input the guardrails rejected outright. A healthy
        # response that merely failed validation (too long, still in the source
        # language) is deliberately left out, so callers alerting on the field
        # don't page someone over a verbose translation. is_fallback already
        # flags the branch either way, and the reason above is logged.
        if state.get("error"):
            update["error"] = state["error"]
        return update

    if state.get("error"):
        return fallback(state["error"])
    if not translated_text:
        return fallback("the LLM returned empty content", "empty_output")

    max_length = max(
        len(original_text) * _MAX_TRANSLATION_RATIO, _MAX_TRANSLATION_FLOOR
    )
    if len(translated_text) > max_length:
        return fallback(
            f"translation is {len(translated_text)} chars, over the "
            f"{max_length} limit — the model likely explained instead of translating",
            "too_long",
        )

    if discloses_prompt_instructions(translated_text):
        return fallback(
            "translation recites the agent's own instructions",
            "prompt_disclosure",
        )

    # The model answering as itself — "I'm sorry, I can't help with that" — is
    # what a message about credentials, security or anything else that reads as
    # a sensitive request provokes. Written in the target language it passes
    # every other rule here, and the sender's meaning is lost silently
    # (ADR-13, amended).
    if looks_like_a_refusal(translated_text, original_text):
        return fallback(
            "translation is the model declining rather than translating",
            "refusal",
        )

    # The model is shown other participants' messages as context, so an output
    # carrying an identifier the message itself never contained came either from
    # that context or from nowhere. Neither is deliverable (ADR-21).
    context_messages = state.get("context_messages", []) or []
    # A glossary term is by definition wording the message does not contain —
    # that is what forcing a rendering means — so a product code or a
    # part number sitting in one would read to the leak check as an identifier
    # the model invented, and the whole translation would be discarded. The
    # terms are administrator-approved configuration, so they are treated as
    # part of the source for this check and for nothing else (ADR-21, ADR-26).
    known_terms = " ".join(
        term.target_term for term in state.get("glossary_terms", []) or []
    )
    leaked = find_leaked_identifiers(
        translated_text,
        f"{original_text} {known_terms}" if known_terms else original_text,
    )
    if leaked:
        # Count only. The values are the very thing that must not spread, and a
        # server log is read by people the conversation never included.
        leak_source = classify_leak_source(leaked, context_messages)
        logger.warning(
            "Translation introduced %d identifier(s) absent from the source (%s)",
            len(leaked),
            leak_source,
        )
        return fallback(
            "translation contains an identifier the message did not",
            "leaked_identifier",
            leak_source=leak_source,
        )

    # The same rule from the other side: a model that decides a phone number or
    # a link is too sensitive to repeat produces fluent output of the right
    # length in the right language, with the one detail the recipient needed
    # taken out of it (ADR-21).
    dropped = find_dropped_identifiers(translated_text, original_text)
    if dropped:
        logger.warning(
            "Translation dropped %d identifier(s) the message contained", len(dropped)
        )
        return fallback(
            "translation is missing an identifier the message contained",
            "dropped_identifier",
        )

    # A refusal, an untranslated echo, and the model answering instead of
    # translating all leave source-language text in the output. The length rule
    # above misses all three when the response is short — "I can't help with
    # that." is 24 characters, far under the 200-char floor (ADR-13).
    source_language = state.get("source_language", "")
    target_language = state.get("target_language", "")
    if is_untranslated_output(translated_text, source_language, target_language):
        return fallback(
            f"translation still reads as {source_language}, not {target_language} — "
            "the model likely refused, echoed the source, or answered instead",
            "wrong_language",
        )

    # Measured, not enforced. This is the one leak channel with a plausible
    # innocent explanation — chat repeats itself — so the rate is observed on
    # real traffic before it is allowed to discard a translation (ADR-21).
    echo = find_context_echo(translated_text, original_text, context_messages)
    if echo is None:
        return {
            "is_valid": True,
            "is_fallback": False,
            **_telemetry(state, outcome="llm"),
        }

    logger.warning(
        "Translation repeats %d characters written in another message of the "
        "conversation; delivering it anyway while the rule is being measured",
        len(echo),
    )
    return {
        "is_valid": True,
        "is_fallback": False,
        **_telemetry(
            state, outcome="llm", context_echo=True, context_echo_chars=len(echo)
        ),
    }


async def fallback_translate(state: AgentState) -> dict:
    """Try the secondary provider before giving up on translating (ADR-07).

    Only reached after validate_output has already written the original text into
    the state, so returning nothing here simply leaves that safe value in place —
    the recipient still receives the message either way (NFR-02).

    ``is_fallback`` deliberately stays True even when this succeeds: the result
    did not come from the configured LLM, and the client should keep showing the
    degraded-quality indicator. ``model`` records which provider produced it.
    """
    original_text = (state.get("original_text") or "").strip()
    source_language = state.get("source_language", "")
    target_language = state.get("target_language", "")

    # The same guardrails the LLM path enforces. Without them an oversized or
    # malformed request would still reach Google's endpoint (ADR-15).
    if not is_supported_language_code(target_language):
        return {}
    if is_input_too_long(original_text):
        return {}

    # This branch is the slowest path in the graph, so leaving it out of
    # latency_ms would under-report exactly the case NFR-01 needs to see.
    elapsed_ms = state.get("latency_ms", 0)
    started = time.perf_counter()

    translated = await translate_with_secondary_provider(
        original_text,
        target_language=target_language,
        # Empty when detection itself failed; the provider then detects its own
        source_language=source_language,
    )
    elapsed_ms += _elapsed_ms(started)

    if not translated:
        return {
            "latency_ms": elapsed_ms,
            **_telemetry(state, fallback_ms=_elapsed_ms(started)),
        }

    # This output reaches the recipient without passing back through
    # validate_output, so it is verified here or not at all. Only the language
    # check is repeated: the secondary provider is sent the message and nothing
    # else, so it has no conversation context to leak and no prompt to recite.
    if is_untranslated_output(translated, source_language, target_language):
        logger.warning("Discarded secondary translation: still reads as the source")
        return {
            "latency_ms": elapsed_ms,
            # Distinct from the provider simply returning nothing: this one
            # answered, and its answer was rejected. Worth telling apart before
            # concluding the secondary provider is not pulling its weight.
            **_telemetry(
                state, fallback_ms=_elapsed_ms(started), secondary_discarded=True
            ),
        }

    logger.info("Secondary provider translated the message after the LLM path failed")
    return {
        "translated_text": translated,
        "model": FALLBACK_MODEL_NAME,
        "latency_ms": elapsed_ms,
        **_telemetry(state, fallback_ms=_elapsed_ms(started), outcome="secondary"),
    }


async def passthrough(state: AgentState) -> dict:
    """Branch taken when the source and target languages match.

    Skips the LLM entirely (the "yes" branch of the agent flow).
    """
    return {
        "translated_text": state.get("original_text", ""),
        "is_valid": True,
        "is_fallback": False,
        "latency_ms": state.get("latency_ms", 0),
        **_telemetry(state, outcome="passthrough"),
    }
