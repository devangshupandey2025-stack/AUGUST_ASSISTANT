"""Web research engine — production-stable.

Performs live web research via DuckDuckGo with retry logic, trusted-domain
filtering, confidence-aware results, acronym expansion, and anti-hallucination
safeguards.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from duckduckgo_search import DDGS
except Exception:  # pragma: no cover
    DDGS = None  # type: ignore[assignment]

try:
    from newspaper import Article
except Exception:  # pragma: no cover
    Article = None  # type: ignore[assignment]

try:
    from readability import Document
except Exception:  # pragma: no cover
    Document = None  # type: ignore[assignment]

from august.acronym_resolver import expand_acronyms
from august.consensus import build_consensus
from august.providers.provider_router import ProviderRouter
from august.query_normalizer import normalize_query
from august.query_understanding import understand_query
from august.result_filter import filter_results
from august.result_validator import (
    validate_article_content,
    validate_search_result,
    verify_answer_relevance,
)
from august.retrieval_confidence import assess_retrieval_confidence
from august.search_synthesizer import (
    get_deprioritized_domains,
    get_preferred_domains,
    synthesize_search_query,
)
from august.utils.logger import get_logger, log_event

logger = get_logger("WebResearch")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FAILURE_MESSAGE = "I'm having trouble finding reliable information right now."
WEAK_RESEARCH_MESSAGE = "I'm not finding reliable information for that yet."

# Query-type-aware fallback messages.
_TYPED_WEAK_MESSAGES: dict[str, str] = {
    "comparison": "I'm not finding reliable comparison information for that yet.",
    "definition": "I'm not finding a reliable definition for that yet.",
    "tutorial": "I'm not finding a reliable tutorial for that yet.",
    "dynamic_fact": "I'm not finding reliable up-to-date information for that yet.",
}

REQUEST_TIMEOUT_SECONDS = 8
TIMEOUT = 8
MAX_RESULTS = 5
MIN_TEXT_LENGTH = 150
WEB_CACHE_TTL = 300

MAX_RETRIES = 2
BACKOFF = [1, 2]

MIN_RESEARCH_CONFIDENCE = 0.7
MIN_PROVIDER_CONFIDENCE = 0.5

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

LAST_RESEARCHED_TOPIC = ""
LAST_SOURCE_URL = ""
_RESEARCH_CACHE: dict[str, dict[str, object]] = {}
_DDGS_TIMING_PATCHED = False


def _safe_exception_text(exc: Exception) -> str:
    if any(isinstance(arg, timedelta) for arg in getattr(exc, "args", [])):
        formatted: list[str] = []
        for arg in exc.args:
            if isinstance(arg, timedelta):
                formatted.append(f"{arg.total_seconds():.2f}s")
            else:
                formatted.append(str(arg))
        text = " ".join(part for part in formatted if part).strip()
        if text:
            return text
    try:
        text = str(exc)
    except Exception:
        text = ""
    if text:
        return text
    parts: list[str] = []
    for arg in getattr(exc, "args", []):
        if isinstance(arg, timedelta):
            seconds = arg.total_seconds()
            parts.append(f"{seconds:.2f}s")
        else:
            parts.append(str(arg))
    return " ".join(part for part in parts if part).strip() or exc.__class__.__name__


def safe_ddg_search(query: str, max_results: int = 5):
    """Crash-safe DuckDuckGo search wrapper with bounded retries."""
    if DDGS is None:
        logger.warning("duckduckgo_search is not available")
        log_event(logger, "ddg_search_failed", source="web_research", success=False, query=query, error="DDGS unavailable")
        return []

    attempts = MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        log_event(logger, "ddg_search_started", source="web_research", success=True, query=query, attempt=attempt)
        try:
            _patch_ddgs_timedelta_logging()
            with DDGS(headers=HEADERS, timeout=TIMEOUT) as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            error_text = _safe_exception_text(exc)
            log_event(logger, "web_transport_error", source="web_research", success=False, query=query, stage="ddg_search", attempt=attempt, error=error_text)
            if attempt <= MAX_RETRIES:
                backoff = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
                time.sleep(backoff)
                continue
            if _is_timedelta_format_error(error_text) or _is_rate_limited_error(error_text):
                fallback_results = _fallback_ddg_html_search(query, max_results=max_results) or _fallback_bing_search(query, max_results=max_results)
                if fallback_results:
                    log_event(logger, "web_transport_recovered", source="web_research", success=True, query=query, stage="ddg_html_fallback", attempt=attempt)
                    log_event(logger, "ddg_search_success", source="web_research", success=True, query=query, attempt=attempt, result_count=len(fallback_results), fallback="html")
                    return fallback_results
            log_event(logger, "ddg_search_failed", source="web_research", success=False, query=query, attempts=attempt, error=error_text)
            return []

        safe_results: list[dict[str, str]] = []
        for result in raw_results:
            if not isinstance(result, dict):
                continue
            title = str(result.get("title", "") or "").strip()
            href = str(result.get("href", "") or "").strip()
            body = str(result.get("body", "") or "").strip()
            if not href:
                continue
            safe_results.append({"title": title, "href": href, "body": body})

        log_event(logger, "ddg_search_success", source="web_research", success=True, query=query, attempt=attempt, result_count=len(safe_results))
        if attempt > 1:
            log_event(logger, "web_transport_recovered", source="web_research", success=True, query=query, stage="ddg_search", attempt=attempt)
        return safe_results

    return []


def _is_timedelta_format_error(error_text: str) -> bool:
    return "unsupported format string passed to datetime.timedelta.__format__" in error_text


def _is_rate_limited_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return "ratelimit" in lowered or " 202 " in lowered or " 403 " in lowered


def _patch_ddgs_timedelta_logging() -> None:
    """Patch duckduckgo_search 5.3.1's timedelta formatting bug in-process."""
    global _DDGS_TIMING_PATCHED
    if _DDGS_TIMING_PATCHED:
        return
    try:
        import duckduckgo_search.duckduckgo_search_async as ddg_async
    except Exception:
        return

    async def _aget_url(self, method, url, data=None, params=None):
        if self._exception_event.is_set():
            raise ddg_async.DuckDuckGoSearchException("Exception occurred in previous call.")
        try:
            resp = await self._asession.request(method, url, data=data, params=params)
        except Exception as ex:
            self._exception_event.set()
            if "time" in str(ex).lower():
                raise ddg_async.TimeoutException(f"{url} {type(ex).__name__}: {ex}") from ex
            raise ddg_async.DuckDuckGoSearchException(f"{url} {type(ex).__name__}: {ex}") from ex

        elapsed = getattr(resp, "elapsed", 0.0)
        if isinstance(elapsed, timedelta):
            elapsed_seconds = elapsed.total_seconds()
        else:
            try:
                elapsed_seconds = float(elapsed)
            except (TypeError, ValueError):
                elapsed_seconds = 0.0
        ddg_async.logger.debug("_aget_url() %s %s %.2f %s", resp.url, resp.status_code, elapsed_seconds, len(resp.content))
        if resp.status_code == 200:
            return resp.content
        self._exception_event.set()
        if resp.status_code in (202, 301, 403):
            raise ddg_async.RatelimitException(f"{resp.url} {resp.status_code} Ratelimit")
        raise ddg_async.DuckDuckGoSearchException(f"{resp.url} return None. {params=} {data=}")

    ddg_async.AsyncDDGS._aget_url = _aget_url
    _DDGS_TIMING_PATCHED = True


def _fallback_ddg_html_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    try:
        response = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        log_event(logger, "web_transport_error", source="web_research", success=False, query=query, stage="ddg_html_fallback", error=_safe_exception_text(exc))
        return []

    try:
        soup = BeautifulSoup(response.text or "", "html.parser")
        results: list[dict[str, str]] = []
        for container in soup.select(".result"):
            link = container.select_one("a.result__a")
            if link is None:
                continue
            href = str(link.get("href") or "").strip()
            title = link.get_text(" ", strip=True)
            body_node = container.select_one(".result__snippet")
            body = body_node.get_text(" ", strip=True) if body_node else ""
            if href:
                results.append({"title": title, "href": href, "body": body})
            if len(results) >= max_results:
                break
        return results
    except Exception as exc:
        log_event(logger, "web_transport_error", source="web_research", success=False, query=query, stage="ddg_html_parse", error=_safe_exception_text(exc))
        return []


def _fallback_bing_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    candidates = [query, _strip_question_prefix(query)]
    seen_queries: set[str] = set()
    seen_urls: set[str] = set()
    results: list[dict[str, str]] = []

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate.lower() in seen_queries:
            continue
        seen_queries.add(candidate.lower())
        try:
            response = requests.get(
                "https://www.bing.com/search",
                params={"q": candidate},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except Exception as exc:
            log_event(logger, "web_transport_error", source="web_research", success=False, query=candidate, stage="bing_fallback", error=_safe_exception_text(exc))
            continue

        try:
            soup = BeautifulSoup(response.text or "", "html.parser")
            for container in soup.select("li.b_algo"):
                link = container.select_one("h2 a")
                if link is None:
                    continue
                href = _unwrap_bing_url(str(link.get("href") or "").strip())
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                title = link.get_text(" ", strip=True)
                body_node = container.select_one(".b_caption p")
                body = body_node.get_text(" ", strip=True) if body_node else ""
                results.append({"title": title, "href": href, "body": body})
        except Exception as exc:
            log_event(logger, "web_transport_error", source="web_research", success=False, query=candidate, stage="bing_parse", error=_safe_exception_text(exc))
            continue

    results.sort(key=lambda item: _fallback_result_priority(query, item), reverse=True)
    return results[:max_results]


def _unwrap_bing_url(url: str) -> str:
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc:
        return url
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded:
        return url
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8", errors="ignore")
    except Exception:
        return unquote(encoded)


def _strip_question_prefix(query: str) -> str:
    return re.sub(r"^\s*(what|who|where|when|why|how)\s+(is|are|was|were|do|does|did)\s+", "", query, flags=re.IGNORECASE)


def _fallback_result_priority(query: str, result: dict[str, str]) -> float:
    text = f"{result.get('title', '')} {result.get('body', '')}".lower()
    query_terms = {word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) > 2}
    overlap = len(query_terms & set(re.findall(r"[a-z0-9]+", text)))
    host = urlparse(result.get("href", "")).netloc.lower().replace("www.", "")
    trust = 0.0
    if any(host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS):
        trust = 2.0
    elif any(host.endswith(tld) for tld in TRUSTED_TLDS):
        trust = 1.5
    if any(host == domain or host.endswith("." + domain) for domain in LOW_QUALITY_DOMAINS):
        trust -= 2.0
    return trust + overlap

# ---------------------------------------------------------------------------
# Trusted / low-quality domain lists
# ---------------------------------------------------------------------------
TRUSTED_DOMAINS: tuple[str, ...] = (
    "wikipedia.org",
    "britannica.com",
    "geeksforgeeks.org",
    "docs.python.org",
    "developer.mozilla.org",
    "docs.oracle.com",
    "docs.microsoft.com",
    "learn.microsoft.com",
    "stackoverflow.com",
    "reuters.com",
    "bbc.com",
    "bbc.co.uk",
    "apnews.com",
    "nature.com",
    "sciencedirect.com",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "mayoclinic.org",
    "who.int",
    "cdc.gov",
    "khanacademy.org",
    "mit.edu",
    "stanford.edu",
    "harvard.edu",
    "investopedia.com",
)

TRUSTED_TLDS: tuple[str, ...] = (
    ".gov",
    ".edu",
    ".ac.uk",
    ".ac.in",
)

LOW_QUALITY_DOMAINS: tuple[str, ...] = (
    "quora.com",
    "answers.com",
    "brainly.com",
    "chegg.com",
    "coursehero.com",
    "wikihow.com",
    "ehow.com",
    "about.com",
    "reddit.com",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    title: str
    href: str
    snippet: str = ""
    source_quality: float = 0.5


@dataclass
class WebResearchResult:
    success: bool
    answer: str
    source_url: str = ""
    title: str = ""
    article_text: str = ""
    source_urls: list[str] | None = None
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class WebResearchEngine:
    def research(self, query: str, ai_parser: object | None = None, include_attribution: bool = True) -> WebResearchResult:
        clean_query = self._clean_query(query)
        if not clean_query:
            return WebResearchResult(False, FAILURE_MESSAGE)

        started = time.perf_counter()

        # =================================================================
        # PHASE 1 — Query Understanding
        # =================================================================
        intent = understand_query(clean_query)
        log_event(logger, "query_understood", source="web_research", success=True,
                  query=clean_query, query_type=intent.type,
                  entities=intent.entities, topic=intent.topic)

        # =================================================================
        # PHASE 2 — Query Normalization (STT noise + entity correction)
        # =================================================================
        normalized = normalize_query(clean_query, intent)
        log_event(logger, "query_normalized", source="web_research", success=True,
                  original=clean_query, normalized=normalized)

        # --- Cache check (use original query as key for stability) ---
        cache_key = clean_query.lower()
        cache_hit = self._get_cached(cache_key)
        if cache_hit is not None:
            return cache_hit

        # =================================================================
        # PROVIDER ROUTER — check if a specialized provider can handle this
        # =================================================================
        intent_topic = intent.topic or normalized

        provider_router = ProviderRouter()
        provider_result = provider_router.route(intent)
        if provider_result and provider_result.success:
            log_event(logger, "provider_result_received", source="web_research",
                      success=True, provider=provider_result.provider,
                      title=provider_result.title,
                      confidence=round(provider_result.confidence, 3))

            # Validate the provider result through the existing pipeline.
            article_validation = validate_article_content(
                provider_result.raw_text, intent, title=provider_result.title
            )
            article_valid = article_validation.get("valid", False)

            if article_valid:
                answer_verification = verify_answer_relevance(
                    provider_result.raw_text, intent_topic, intent
                )
                answer_valid = answer_verification.get("valid", False)
            else:
                answer_valid = False

            if article_valid and answer_valid and provider_result.confidence >= MIN_PROVIDER_CONFIDENCE:
                answer = provider_result.summary
                confidence = provider_result.confidence

                # Optional Gemini polish (never blocking).
                if ai_parser is not None and hasattr(ai_parser, "summarize_web_content"):
                    try:
                        polished = ai_parser.summarize_web_content(
                            answer,
                            query=intent_topic,
                            source_url=provider_result.url,
                            title=provider_result.title,
                        )
                        if polished.get("success") and polished.get("text"):
                            answer = polished["text"]
                            log_event(logger, "provider_gemini_polish",
                                      source="web_research", success=True,
                                      query=clean_query)
                    except Exception:
                        pass

                if include_attribution:
                    answer = self._with_source_attribution(answer, provider_result.url)

                self._remember_research(clean_query, provider_result.url)
                self._set_cached(cache_key, answer, provider_result.url,
                                 provider_result.title,
                                 provider_result.raw_text, confidence)

                log_event(logger, "provider_answer_returned",
                          source="web_research", success=True,
                          query=clean_query,
                          provider=provider_result.provider,
                          confidence=round(confidence, 3))

                return WebResearchResult(
                    True,
                    answer,
                    source_url=provider_result.url,
                    title=provider_result.title,
                    article_text=provider_result.raw_text,
                    source_urls=[provider_result.url],
                    confidence=confidence,
                )

            # Provider result was not usable — log reason and fall through.
            if not article_valid:
                reason = article_validation.get("reason", "validation_failed")
            elif not answer_valid:
                reason = answer_verification.get("reason", "verification_failed")
            else:
                reason = f"low_confidence:{round(provider_result.confidence, 3)}"

            log_event(logger, "provider_result_skipped",
                      source="web_research", success=False,
                      query=clean_query, provider=provider_result.provider,
                      reason=reason)

        # =================================================================
        # PHASE 3 — Search Query Synthesis (template-based)
        # =================================================================
        search_query = synthesize_search_query(intent, normalized)
        log_event(logger, "search_query_generated", source="web_research", success=True,
                  normalized=normalized, search_query=search_query, query_type=intent.type)

        # --- Fallback: if synthesis produced nothing useful, try acronym expansion ---
        if not search_query or search_query == clean_query:
            expanded_query, expansions = expand_acronyms(clean_query)
            if expansions:
                search_query = expanded_query

        log_event(logger, "research_query_started", source="web_research", success=True, query=clean_query, search_query=search_query)

        # =================================================================
        # PHASE 4 — Intelligent Retrieval + Result Filtering
        # =================================================================
        try:
            raw_results = self._search(search_query)

            # --- Domain-aware result filtering ---
            preferred_domains = get_preferred_domains(intent)
            raw_results = filter_results(raw_results, intent, search_query, preferred_domains)
            log_event(logger, "domain_rank_applied", source="web_research", success=True,
                      query=clean_query, result_count=len(raw_results), query_type=intent.type)

            # =============================================================
            # PHASE 4a — Entity Validation (before article fetch)
            # =============================================================
            log_event(logger, "entity_validation_started", source="web_research", success=True, query=clean_query)
            validated_results = []
            for result in raw_results:
                validation = validate_search_result(result, intent, intent.entities)
                if validation["valid"]:
                    validated_results.append(result)
                    log_event(logger, "source_accepted", source="web_research", success=True, url=result.href, overlap=validation.get("entity_overlap", 0))
                else:
                    log_event(logger, "source_rejected", source="web_research", success=True, url=result.href, reason=validation["reason"])
            raw_results = validated_results
            log_event(logger, "entity_validation_passed", source="web_research", success=True, remaining=len(raw_results))

            # Sort remaining by source quality — trusted domains first.
            raw_results.sort(key=lambda r: self._result_source_quality(r), reverse=True)

            # =============================================================
            # PHASE 5 — Multi-Source Consensus (top 3 results)
            # =============================================================
            valid_extractions: list[dict[str, object]] = []
            MAX_CONSENSUS_SOURCES = 3

            for result in raw_results:
                try:
                    article_text = self._fetch_article_text(result.href)
                except Exception as exc:
                    log_event(logger, "article_fetch_failed", source="web_research", success=False, url=result.href, error=self._safe_error_text(exc))
                    continue

                if len(article_text) < MIN_TEXT_LENGTH:
                    continue

                # ==========================================================
                # PHASE 5a — Article Content Validation
                # ==========================================================
                log_event(logger, "article_validation_started", source="web_research", success=True, url=result.href)
                article_validation = validate_article_content(article_text, intent, title=result.title)
                if not article_validation["valid"]:
                    log_event(logger, "article_validation_failed", source="web_research", success=False, url=result.href, reason=article_validation["reason"])
                    continue
                log_event(logger, "article_validation_passed", source="web_research", success=True, url=result.href, coverage=article_validation.get("coverage", 0))

                extractor_quality = self._extractor_quality(result.href, article_text)

                # ==========================================================
                # PHASE 5b — Answer Verification
                # ==========================================================
                log_event(logger, "answer_validation_started", source="web_research", success=True, url=result.href)
                answer_verification = verify_answer_relevance(article_text, search_query, intent)
                if not answer_verification["valid"]:
                    log_event(logger, "answer_validation_failed", source="web_research", success=False, url=result.href, reason=answer_verification["reason"])
                    continue
                log_event(logger, "answer_validation_passed", source="web_research", success=True, url=result.href)

                try:
                    answer = self._summarize(article_text, search_query, result.title, result.snippet)
                except Exception as exc:
                    log_event(logger, "summary_generation_failed", source="web_research", success=False, query=clean_query, source_url=result.href, error=self._safe_error_text(exc))
                    continue

                # --- AI summarization (optional) ---
                if ai_parser is not None and hasattr(ai_parser, "summarize_web_content"):
                    try:
                        ai_result = ai_parser.summarize_web_content(
                            article_text,
                            query=search_query,
                            source_url=result.href,
                            title=result.title,
                        )
                    except Exception as exc:
                        log_event(logger, "gemini_summarization_failed", source="web_research", success=False, query=clean_query, reason=self._safe_error_text(exc))
                        ai_result = {"success": False, "text": "", "error": self._safe_error_text(exc)}
                    if ai_result.get("success"):
                        summarized = str(ai_result.get("text", "")).strip()
                        if summarized and not self._is_hallucinated_summary(summarized):
                            answer = self._limit_summary(summarized, max_sentences=5, max_chars=900)
                            log_event(logger, "gemini_summarization_used", source="web_research", success=True, query=clean_query, source_url=result.href)

                if not answer or self._is_hallucinated_summary(answer):
                    continue

                # ==========================================================
                # PHASE 6 — Enhanced Confidence Scoring
                # ==========================================================
                source_quality = self._result_source_quality(result)

                # Legacy confidence (preserved).
                legacy_confidence = max(
                    self._assess_research_confidence(
                        article_text=article_text,
                        answer=answer,
                        query=search_query,
                        source_quality=source_quality,
                        extractor_quality=extractor_quality,
                    ),
                    self._assess_research_confidence(
                        article_text=article_text,
                        answer=answer,
                        query=clean_query,
                        source_quality=source_quality,
                        extractor_quality=extractor_quality,
                    ),
                )

                # Enhanced retrieval confidence (new).
                retrieval_conf = assess_retrieval_confidence(
                    article_text=article_text,
                    answer=answer,
                    query=search_query,
                    intent=intent,
                    source_quality=source_quality,
                    extractor_quality=extractor_quality,
                    domain_relevance=self._domain_relevance_for_intent(result.href, preferred_domains),
                )
                retrieval_confidence_value = float(retrieval_conf.get("confidence", 0.0))
                confidence = max(legacy_confidence, retrieval_confidence_value)

                log_event(logger, "retrieval_confidence_assigned", source="web_research", success=True,
                          query=clean_query, confidence=round(confidence, 3),
                          legacy=round(legacy_confidence, 3),
                          retrieval=round(retrieval_confidence_value, 3),
                          reason=retrieval_conf.get("reason", ""),
                          source_url=result.href)

                if confidence < MIN_RESEARCH_CONFIDENCE:
                    log_event(logger, "research_confidence_too_low", source="web_research", success=False, query=clean_query, confidence=round(confidence, 3))
                    continue

                # Collect for multi-source consensus.
                valid_extractions.append({
                    "result": result,
                    "article_text": article_text,
                    "answer": answer,
                    "confidence": confidence,
                    "source_url": result.href,
                    "title": result.title,
                })

                # Collect up to MAX_CONSENSUS_SOURCES, then synthesize.
                if len(valid_extractions) >= MAX_CONSENSUS_SOURCES:
                    break

            # =============================================================
            # PHASE 7 — Deterministic Multi-Source Consensus
            # =============================================================
            if not valid_extractions:
                log_event(logger, "research_all_sources_exhausted", source="web_research", success=False, query=clean_query)
                weak_message = _TYPED_WEAK_MESSAGES.get(intent.type, WEAK_RESEARCH_MESSAGE)
                return WebResearchResult(False, weak_message)

            if len(valid_extractions) >= 2:
                log_event(logger, "multi_source_consensus_started", source="web_research", success=True,
                          query=clean_query, source_count=len(valid_extractions))

            # Build deterministic consensus from collected sources.
            consensus_result = build_consensus(valid_extractions, intent)
            answer = str(consensus_result.get("answer", ""))
            source_urls = [str(ext["source_url"]) for ext in valid_extractions]

            # Use the best source as primary for metadata.
            best = max(valid_extractions, key=lambda x: float(x["confidence"]))
            confidence = float(best["confidence"])
            primary_result = best["result"]
            article_text = str(best["article_text"])

            # Optional: Gemini polish (only if available, never blocking).
            if ai_parser is not None and hasattr(ai_parser, "summarize_web_content"):
                try:
                    polished = ai_parser.summarize_web_content(
                        answer,
                        query=search_query,
                        source_url="consensus",
                        title="Multi-source consensus",
                    )
                    if polished.get("success") and polished.get("text"):
                        answer = polished["text"]
                        log_event(logger, "consensus_gemini_polish", source="web_research", success=True, query=clean_query)
                except Exception:
                    pass  # Gemini failed, local consensus is still correct.

            if include_attribution:
                answer = self._with_source_attribution(answer, str(best["source_url"]))

            self._remember_research(clean_query, str(best["source_url"]))
            self._set_cached(cache_key, answer, str(best["source_url"]),
                             str(best["title"]), article_text, confidence)
            log_event(logger, "summary_generated", source="web_research", success=True,
                      query=clean_query, source_url=str(best["source_url"]),
                      confidence=round(confidence, 3),
                      sources_used=len(valid_extractions))
            log_event(logger, "research_summary_generated", source="web_research", success=True,
                      query=clean_query, source_url=str(best["source_url"]),
                      confidence=round(confidence, 3))

            return WebResearchResult(
                True,
                answer,
                source_url=str(best["source_url"]),
                title=str(best["title"]),
                article_text=article_text,
                source_urls=source_urls,
                confidence=confidence,
            )

        except Exception as exc:
            error_text = self._safe_error_text(exc)
            log_event(logger, "web_transport_error", source="web_research", success=False, query=clean_query, error=error_text)
            elapsed_seconds = time.perf_counter() - started
            log_event(
                logger,
                "web_transport_recovered",
                source="web_research",
                success=True,
                query=clean_query,
                fallback="graceful_failure",
                elapsed_seconds=round(elapsed_seconds, 2),
            )
            return WebResearchResult(False, FAILURE_MESSAGE)

        # All results exhausted without a confident answer.
        log_event(logger, "research_all_sources_exhausted", source="web_research", success=False, query=clean_query)
        weak_message = _TYPED_WEAK_MESSAGES.get(intent.type, WEAK_RESEARCH_MESSAGE)
        return WebResearchResult(False, weak_message)

    # ------------------------------------------------------------------
    # Search with retry
    # ------------------------------------------------------------------
    def _search(self, query: str) -> list[SearchResult]:
        raw_results = safe_ddg_search(query, max_results=MAX_RESULTS)

        results: list[SearchResult] = []
        for item in raw_results:
            try:
                title = str(item.get("title", "") or "").strip()
                href = str(item.get("href", "") or "").strip()
                snippet = str(item.get("body", "") or "").strip()
            except Exception as exc:
                log_event(logger, "ddg_result_skipped", source="web_research", success=False, query=query, error=self._safe_error_text(exc))
                continue
            if not href or self._is_low_quality_url(href):
                continue
            quality = self._score_source_quality(href)
            results.append(SearchResult(title=title, href=href, snippet=snippet, source_quality=quality))
        return results

    # ------------------------------------------------------------------
    # Source quality scoring
    # ------------------------------------------------------------------
    def _score_source_quality(self, url: str) -> float:
        """Score a URL's trustworthiness: 1.0 = trusted, 0.5 = neutral, 0.2 = low."""
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "").strip()
        if not host:
            return 0.3

        # Check exact trusted domains.
        for domain in TRUSTED_DOMAINS:
            if host == domain or host.endswith("." + domain):
                log_event(logger, "source_quality_verified", source="web_research", success=True, url=url, domain=host, quality="trusted")
                return 1.0

        # Check trusted TLDs.
        for tld in TRUSTED_TLDS:
            if host.endswith(tld):
                log_event(logger, "source_quality_verified", source="web_research", success=True, url=url, domain=host, quality="trusted_tld")
                return 0.9

        # Check low-quality domains.
        for domain in LOW_QUALITY_DOMAINS:
            if host == domain or host.endswith("." + domain):
                log_event(logger, "low_quality_source_detected", source="web_research", success=False, url=url, domain=host)
                return 0.2

        return 0.6

    def _result_source_quality(self, result: SearchResult) -> float:
        try:
            return float(result.source_quality)
        except (TypeError, ValueError):
            return self._score_source_quality(result.href)

    def _is_low_quality_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "").strip()
        if not host:
            return True
        return any(host == domain or host.endswith("." + domain) for domain in LOW_QUALITY_DOMAINS)

    def _domain_relevance_for_intent(self, url: str, preferred_domains: list[str]) -> float:
        """Compute domain relevance for the retrieval confidence engine."""
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "").strip()
        if not host:
            return 0.3
        for domain in preferred_domains:
            if host == domain or host.endswith("." + domain):
                return 1.0
        deprioritized = get_deprioritized_domains()
        for domain in deprioritized:
            if host == domain or host.endswith("." + domain):
                return 0.1
        return 0.5

    # ------------------------------------------------------------------
    # Article fetching
    # ------------------------------------------------------------------
    def _fetch_article_text(self, url: str) -> str:
        log_event(logger, "article_fetch_started", source="web_research", success=True, url=url)
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except Exception as exc:
            log_event(logger, "web_transport_error", source="web_research", success=False, url=url, stage="article_fetch", error=self._safe_error_text(exc))
            log_event(logger, "article_fetch_failed", source="web_research", success=False, url=url, error=self._safe_error_text(exc))
            return ""

        log_event(logger, "article_fetch_success", source="web_research", success=True, url=url, status_code=response.status_code)
        html = response.text or ""
        for extractor in (self._extract_with_newspaper, self._extract_with_readability, self._extract_with_bs4):
            try:
                text = extractor(url, html)
            except Exception as exc:
                log_event(logger, "article_extraction_failed", source="web_research", success=False, url=url, extractor=extractor.__name__, error=self._safe_error_text(exc))
                continue
            if len(text) >= MIN_TEXT_LENGTH:
                return text
        return ""

    def _extract_with_readability(self, url: str, html: str) -> str:
        del url
        if Document is None:
            return ""
        try:
            return self._text_from_html(Document(html).summary(html_partial=True))
        except Exception as exc:
            logger.debug("readability extraction failed: %s", self._safe_error_text(exc))
            return ""

    def _extract_with_newspaper(self, url: str, html: str) -> str:
        if Article is None:
            return ""
        try:
            article = Article(url)
            article.set_html(html)
            article.parse()
            return self._clean_text(article.text)
        except Exception as exc:
            logger.debug("newspaper extraction failed for '%s': %s", url, self._safe_error_text(exc))
            return ""

    def _extract_with_bs4(self, url: str, html: str) -> str:
        del url
        try:
            return self._text_from_html(html)
        except Exception as exc:
            logger.debug("BeautifulSoup extraction failed: %s", self._safe_error_text(exc))
            return ""

    def _text_from_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "button"]):
            tag.decompose()
        return self._clean_text(soup.get_text(" "))

    def _extractor_quality(self, url: str, text: str) -> float:
        """Estimate how clean the extraction was.  Higher = better."""
        del url
        if not text:
            return 0.0
        length = len(text)
        if length > 2000:
            return 1.0
        if length > 1000:
            return 0.8
        if length > 500:
            return 0.75
        if length >= MIN_TEXT_LENGTH:
            return 0.55
        return 0.0

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------
    def _summarize(self, text: str, query: str, title: str, snippet: str) -> str:
        sentences = self._split_sentences(text)
        if not sentences:
            return self._clean_text(snippet)

        query_terms = self._keywords(query)
        scored: list[tuple[int, int, str]] = []
        for index, sentence in enumerate(sentences[:80]):
            words = self._keywords(sentence)
            if len(words) < 4:
                continue
            score = len(query_terms & words) * 2  # Boost keyword matches
            # Boost informational markers.
            if any(marker in sentence.lower() for marker in ("announced", "reported", "according to", "latest", "today", "yesterday", "defined as", "refers to", "is a", "are the")):
                score += 1
            # Penalise very short sentences.
            if len(sentence) < 60:
                score -= 1
            scored.append((score, -index, sentence))

        selected = [item[2] for item in sorted(scored, reverse=True)[:5]] or sentences[:5]
        selected.sort(key=lambda sentence: sentences.index(sentence))
        intro = self._intro_sentence(title, snippet)
        if intro and intro not in selected:
            selected = [intro, *selected[:4]]
        summary = self._limit_summary(" ".join(selected), max_sentences=5, max_chars=900)
        if self._is_hallucinated_summary(summary):
            return ""
        return summary

    def _intro_sentence(self, title: str, snippet: str) -> str:
        source_text = self._clean_text(snippet or title)
        sentences = self._split_sentences(source_text)
        return sentences[0] if sentences else source_text

    def _split_sentences(self, text: str) -> list[str]:
        cleaned = self._clean_text(text)
        candidates = re.split(r"(?<=[.!?])\s+", cleaned)
        sentences: list[str] = []
        for candidate in candidates:
            sentence = candidate.strip()
            if 40 <= len(sentence) <= 280 and not self._looks_like_clutter(sentence):
                sentences.append(sentence)
        return sentences

    def _looks_like_clutter(self, sentence: str) -> bool:
        lowered = sentence.lower()
        return any(
            marker in lowered
            for marker in (
                "subscribe",
                "sign in",
                "privacy policy",
                "cookie",
                "advertisement",
                "all rights reserved",
                "enable javascript",
                "click here",
                "learn more",
                "read more",
                "share this",
                "follow us",
                "terms of service",
                "log in",
                "create account",
                "accept cookies",
                "download the app",
            )
        )

    def _is_hallucinated_summary(self, text: str) -> bool:
        """Detect generic filler text that should NOT be returned as a research result."""
        lowered = (text or "").lower()
        hallucination_markers = (
            "is a concept that refers to",
            "is an important concept",
            "core idea, common use cases, and trade-offs",
            "it generally involves understanding",
            "is a concept that refers to how a specific idea",
            "a core idea used to explain how something works",
        )
        return any(marker in lowered for marker in hallucination_markers)

    def _keywords(self, text: str) -> set[str]:
        stop_words = {
            "about",
            "after",
            "again",
            "also",
            "from",
            "have",
            "into",
            "latest",
            "more",
            "that",
            "their",
            "there",
            "this",
            "what",
            "when",
            "where",
            "which",
            "with",
        }
        short_terms = {"ai", "ml", "ui", "ux", "os", "qa"}
        return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if (len(word) > 2 or word in short_terms) and word not in stop_words}

    def _limit_summary(self, text: str, max_sentences: int, max_chars: int) -> str:
        sentences = self._split_sentences(text)
        if not sentences:
            return self._clean_text(text)[:max_chars].strip()
        output: list[str] = []
        length = 0
        for sentence in sentences:
            if len(output) >= max_sentences or length + len(sentence) > max_chars:
                break
            output.append(sentence)
            length += len(sentence) + 1
        return " ".join(output).strip()

    # ------------------------------------------------------------------
    # Confidence assessment
    # ------------------------------------------------------------------
    def _assess_research_confidence(
        self,
        article_text: str,
        answer: str,
        query: str,
        source_quality: float,
        extractor_quality: float,
    ) -> float:
        """Compute a 0.0-1.0 confidence score for a research result."""
        score = 0.0

        # 1. Content length (max +0.25)
        text_len = len(article_text)
        if text_len > 2000:
            score += 0.25
        elif text_len > 1000:
            score += 0.23
        elif text_len > 500:
            score += 0.25
        else:
            score += 0.05

        # 2. Keyword relevance (max +0.30)
        query_terms = self._keywords(query)
        answer_terms = self._keywords(answer)
        if query_terms:
            overlap = len(query_terms & answer_terms) / len(query_terms)
            score += 0.30 * min(overlap, 1.0)

        # 3. Source trust (max +0.25)
        score += 0.25 * source_quality

        # 4. Extraction quality (max +0.20)
        score += 0.20 * extractor_quality

        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Text utilities
    # ------------------------------------------------------------------
    def _clean_query(self, query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").strip())

    def _clean_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        return cleaned.replace(" ,", ",").replace(" .", ".")

    def _as_float(self, value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _with_source_attribution(self, answer: str, source_url: str) -> str:
        parsed = urlparse(source_url)
        host = (parsed.netloc or "").replace("www.", "").strip()
        if not host:
            return answer
        attributed = f"According to {host}, {answer}"
        return self._limit_summary(attributed, max_sentences=5, max_chars=950)

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _get_cached(self, cache_key: str) -> WebResearchResult | None:
        cached = _RESEARCH_CACHE.get(cache_key)
        if not cached:
            return None
        timestamp = float(cached.get("timestamp", 0.0) or 0.0)
        if time.time() - timestamp > WEB_CACHE_TTL:
            _RESEARCH_CACHE.pop(cache_key, None)
            return None
        log_event(logger, "research_cache_hit", source="web_research", success=True, query=cache_key)
        return WebResearchResult(
            success=True,
            answer=str(cached.get("answer", "") or FAILURE_MESSAGE),
            source_url=str(cached.get("source_url", "") or ""),
            title=str(cached.get("title", "") or ""),
            article_text=str(cached.get("article_text", "") or ""),
            source_urls=list(cached.get("source_urls", []) or []),
            confidence=float(cached.get("confidence", 0.8) or 0.8),
        )

    def _set_cached(self, cache_key: str, answer: str, source_url: str, title: str, article_text: str, confidence: float = 0.8) -> None:
        _RESEARCH_CACHE[cache_key] = {
            "answer": answer,
            "source_url": source_url,
            "source_urls": [source_url] if source_url else [],
            "title": title,
            "article_text": article_text,
            "confidence": confidence,
            "timestamp": time.time(),
        }
        log_event(logger, "research_cache_store", source="web_research", success=True, query=cache_key)

    # ------------------------------------------------------------------
    # State tracking
    # ------------------------------------------------------------------
    def _remember_research(self, topic: str, source_url: str) -> None:
        global LAST_RESEARCHED_TOPIC, LAST_SOURCE_URL
        LAST_RESEARCHED_TOPIC = topic
        LAST_SOURCE_URL = source_url

    # ------------------------------------------------------------------
    # Error formatting
    # ------------------------------------------------------------------
    def _safe_error_text(self, exc: Exception) -> str:
        return _safe_exception_text(exc)


# ---------------------------------------------------------------------------
# Module-level convenience functions (preserve existing API surface)
# ---------------------------------------------------------------------------
def research(query: str, ai_parser: object | None = None, include_attribution: bool = True) -> WebResearchResult:
    return WebResearchEngine().research(query, ai_parser=ai_parser, include_attribution=include_attribution)


def get_last_research_context() -> dict[str, str]:
    return {"topic": LAST_RESEARCHED_TOPIC, "source_url": LAST_SOURCE_URL}
