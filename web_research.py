from __future__ import annotations

import re
import time
from datetime import timedelta
from dataclasses import dataclass
from urllib.parse import urlparse

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

from utils.logger import get_logger, log_event

logger = get_logger("WebResearch")

FAILURE_MESSAGE = "I'm having trouble finding reliable information right now."
REQUEST_TIMEOUT_SECONDS = 10
MAX_RESULTS = 5
MIN_TEXT_LENGTH = 250
WEB_CACHE_TTL = 300

LAST_RESEARCHED_TOPIC = ""
LAST_SOURCE_URL = ""
_RESEARCH_CACHE: dict[str, dict[str, object]] = {}


@dataclass
class SearchResult:
    title: str
    href: str
    snippet: str = ""


@dataclass
class WebResearchResult:
    success: bool
    answer: str
    source_url: str = ""
    title: str = ""
    article_text: str = ""
    source_urls: list[str] | None = None


class WebResearchEngine:
    def research(self, query: str, ai_parser: object | None = None, include_attribution: bool = True) -> WebResearchResult:
        clean_query = self._clean_query(query)
        if not clean_query:
            return WebResearchResult(False, FAILURE_MESSAGE)
        started = time.perf_counter()
        cache_key = clean_query.lower()
        cache_hit = self._get_cached(cache_key)
        if cache_hit is not None:
            return cache_hit

        log_event(logger, "web_search_started", source="web_research", success=True, query=clean_query)
        try:
            for result in self._search(clean_query):
                article_text = self._fetch_article_text(result.href)
                if len(article_text) < MIN_TEXT_LENGTH:
                    continue
                answer = self._summarize(article_text, clean_query, result.title, result.snippet)
                if ai_parser is not None and hasattr(ai_parser, "summarize_web_content"):
                    try:
                        ai_result = ai_parser.summarize_web_content(
                            article_text,
                            query=clean_query,
                            source_url=result.href,
                            title=result.title,
                        )
                    except Exception as exc:
                        log_event(logger, "gemini_summarization_failed", source="web_research", success=False, query=clean_query, reason=self._safe_error_text(exc))
                        ai_result = {"success": False, "text": "", "error": self._safe_error_text(exc)}
                    if ai_result.get("success"):
                        summarized = str(ai_result.get("text", "")).strip()
                        if summarized:
                            answer = self._limit_summary(summarized, max_sentences=5, max_chars=900)
                            log_event(logger, "gemini_summarization_used", source="web_research", success=True, query=clean_query, source_url=result.href)
                if not answer:
                    continue
                if include_attribution:
                    answer = self._with_source_attribution(answer, result.href)
                self._remember_research(clean_query, result.href)
                self._set_cached(cache_key, answer, result.href, result.title, article_text)
                log_event(logger, "summarization_complete", source="web_research", success=True, query=clean_query, source_url=result.href)
                return WebResearchResult(
                    True,
                    answer,
                    source_url=result.href,
                    title=result.title,
                    article_text=article_text,
                    source_urls=[result.href],
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

        return WebResearchResult(False, FAILURE_MESSAGE)

    def _search(self, query: str) -> list[SearchResult]:
        if DDGS is None:
            logger.warning("duckduckgo_search is not available")
            return []
        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=MAX_RESULTS))
        except Exception as exc:
            error_text = self._safe_error_text(exc)
            log_event(logger, "web_transport_error", source="web_research", success=False, query=query, error=error_text)
            logger.warning("DuckDuckGo search failed for '%s': %s", query, exc)
            log_event(logger, "web_transport_recovered", source="web_research", success=True, query=query, fallback="empty_results")
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            href = str(item.get("href") or item.get("url") or "").strip()
            if href:
                results.append(
                    SearchResult(
                        title=str(item.get("title") or "").strip(),
                        href=href,
                        snippet=str(item.get("body") or item.get("snippet") or "").strip(),
                    )
                )
        return results

    def _fetch_article_text(self, url: str) -> str:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                    )
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            log_event(logger, "article_fetch_failed", source="web_research", success=False, url=url, error=str(exc))
            return ""

        log_event(logger, "article_fetch_success", source="web_research", success=True, url=url, status_code=response.status_code)
        html = response.text or ""
        for extractor in (self._extract_with_readability, self._extract_with_newspaper, self._extract_with_bs4):
            text = extractor(url, html)
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
            logger.debug("readability extraction failed: %s", exc)
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
            logger.debug("newspaper extraction failed for '%s': %s", url, exc)
            return ""

    def _extract_with_bs4(self, url: str, html: str) -> str:
        del url
        return self._text_from_html(html)

    def _text_from_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
            tag.decompose()
        return self._clean_text(soup.get_text(" "))

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
            score = len(query_terms & words)
            if any(marker in sentence.lower() for marker in ("announced", "reported", "according to", "latest", "today", "yesterday")):
                score += 1
            scored.append((score, -index, sentence))

        selected = [item[2] for item in sorted(scored, reverse=True)[:5]] or sentences[:5]
        selected.sort(key=lambda sentence: sentences.index(sentence))
        intro = self._intro_sentence(title, snippet)
        if intro and intro not in selected:
            selected = [intro, *selected[:4]]
        return self._limit_summary(" ".join(selected), max_sentences=6, max_chars=900)

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
            )
        )

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
        return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in stop_words}

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

    def _clean_query(self, query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").strip())

    def _clean_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        return cleaned.replace(" ,", ",").replace(" .", ".")

    def _with_source_attribution(self, answer: str, source_url: str) -> str:
        parsed = urlparse(source_url)
        host = (parsed.netloc or "").replace("www.", "").strip()
        if not host:
            return answer
        attributed = f"According to {host}, {answer}"
        return self._limit_summary(attributed, max_sentences=5, max_chars=950)

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
        )

    def _set_cached(self, cache_key: str, answer: str, source_url: str, title: str, article_text: str) -> None:
        _RESEARCH_CACHE[cache_key] = {
            "answer": answer,
            "source_url": source_url,
            "source_urls": [source_url] if source_url else [],
            "title": title,
            "article_text": article_text,
            "timestamp": time.time(),
        }

    def _remember_research(self, topic: str, source_url: str) -> None:
        global LAST_RESEARCHED_TOPIC, LAST_SOURCE_URL
        LAST_RESEARCHED_TOPIC = topic
        LAST_SOURCE_URL = source_url

    def _safe_error_text(self, exc: Exception) -> str:
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
        text = str(exc)
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


def research(query: str, ai_parser: object | None = None, include_attribution: bool = True) -> WebResearchResult:
    return WebResearchEngine().research(query, ai_parser=ai_parser, include_attribution=include_attribution)


def get_last_research_context() -> dict[str, str]:
    return {"topic": LAST_RESEARCHED_TOPIC, "source_url": LAST_SOURCE_URL}
