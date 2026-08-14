"""Lightweight acronym expansion for search query preprocessing.

Expands common abbreviations before web search to improve result relevance.
Dictionary-based, case-insensitive, safe fallback for unknown acronyms.
"""

from __future__ import annotations

import re

from august.utils.logger import get_logger, log_event

logger = get_logger("AcronymResolver")

# ---------------------------------------------------------------------------
# Acronym dictionary — add entries as needed, keep alphabetical.
# Keys MUST be uppercase.  Values are the expanded form.
# ---------------------------------------------------------------------------
ACRONYM_MAP: dict[str, str] = {
    "ACL": "Access Control List",
    "AI": "Artificial Intelligence",
    "API": "Application Programming Interface",
    "AWS": "Amazon Web Services",
    "BIOS": "Basic Input Output System",
    "CDN": "Content Delivery Network",
    "CLI": "Command Line Interface",
    "CPU": "Central Processing Unit",
    "CRUD": "Create Read Update Delete",
    "CSS": "Cascading Style Sheets",
    "DB": "Database",
    "DBMS": "Database Management System",
    "DDoS": "Distributed Denial of Service",
    "DHCP": "Dynamic Host Configuration Protocol",
    "DLL": "Dynamic Link Library",
    "DNS": "Domain Name System",
    "DOM": "Document Object Model",
    "DSA": "Data Structures and Algorithms",
    "FTP": "File Transfer Protocol",
    "GCP": "Google Cloud Platform",
    "GPU": "Graphics Processing Unit",
    "GUI": "Graphical User Interface",
    "HDD": "Hard Disk Drive",
    "HTML": "HyperText Markup Language",
    "HTTP": "HyperText Transfer Protocol",
    "HTTPS": "HyperText Transfer Protocol Secure",
    "IDE": "Integrated Development Environment",
    "IoT": "Internet of Things",
    "IP": "Internet Protocol",
    "ISP": "Internet Service Provider",
    "JVM": "Java Virtual Machine",
    "JSON": "JavaScript Object Notation",
    "JWT": "JSON Web Token",
    "LAN": "Local Area Network",
    "ML": "Machine Learning",
    "MVC": "Model View Controller",
    "NLP": "Natural Language Processing",
    "OOP": "Object Oriented Programming",
    "OS": "Operating System",
    "OSI": "Open Systems Interconnection",
    "PHP": "PHP Hypertext Preprocessor",
    "RAM": "Random Access Memory",
    "RBC": "Red Blood Cell",
    "REST": "Representational State Transfer",
    "ROM": "Read Only Memory",
    "SDK": "Software Development Kit",
    "SQL": "Structured Query Language",
    "SSD": "Solid State Drive",
    "SSH": "Secure Shell",
    "SSL": "Secure Sockets Layer",
    "TCP": "Transmission Control Protocol",
    "TLS": "Transport Layer Security",
    "UDP": "User Datagram Protocol",
    "UI": "User Interface",
    "URL": "Uniform Resource Locator",
    "USB": "Universal Serial Bus",
    "UX": "User Experience",
    "VM": "Virtual Machine",
    "VPN": "Virtual Private Network",
    "WAN": "Wide Area Network",
    "WBC": "White Blood Cell",
    "XML": "Extensible Markup Language",
    "YAML": "YAML Ain't Markup Language",
}

# Pre-build lowercase lookup for O(1) case-insensitive matching.
_LOWER_MAP: dict[str, str] = {k.lower(): v for k, v in ACRONYM_MAP.items()}


def is_acronym(word: str) -> bool:
    """Return True if *word* is a known acronym (case-insensitive)."""
    return (word or "").strip().lower() in _LOWER_MAP


def expand_acronyms(query: str) -> tuple[str, list[str]]:
    """Expand known acronyms in *query*, preserving surrounding text.

    Returns ``(expanded_query, list_of_expansions)`` where each expansion
    is a string like ``"RBC -> Red Blood Cell"``.

    Unknown acronyms pass through unchanged.
    """
    if not query or not query.strip():
        return query or "", []

    expansions: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        expanded = _LOWER_MAP.get(word.lower())
        if expanded is None:
            return word
        expansions.append(f"{word.upper()} -> {expanded}")
        return expanded

    # Match whole words that are 2-6 uppercase-ish letters (case-insensitive).
    # The word-boundary anchors prevent partial matches inside normal words.
    expanded_query = re.sub(r"\b[A-Za-z]{2,6}\b", _replace, query)

    if expansions:
        log_event(
            logger,
            "acronym_expanded",
            source="acronym_resolver",
            success=True,
            original_query=query,
            expanded_query=expanded_query,
            expansions=expansions,
        )

    return expanded_query, expansions
