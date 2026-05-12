from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from utils.logger import get_logger, log_event

logger = get_logger("AppRegistry")


class AppRegistry:
    MINIMUM_SUSPICIOUS_BASELINE = 10
    SUSPICIOUS_DROP_RATIO = 0.5

    def __init__(
        self,
        cache_path: Path | str = "app_registry_cache.json",
        start_menu_roots: Iterable[Path | str] | None = None,
        program_roots: Iterable[Path | str] | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self._registry: dict[str, list[str]] = {}
        self._built = False
        self._start_menu_roots = [Path(root) for root in start_menu_roots] if start_menu_roots is not None else self._default_start_menu_roots()
        self._program_roots = [Path(root) for root in program_roots] if program_roots is not None else self._default_program_roots()
        self._last_known_good_registry = self._load_snapshot()

    @property
    def registry(self) -> dict[str, str]:
        return {name: paths[0] for name, paths in self._registry.items() if paths}

    def build_registry(self) -> dict[str, str]:
        if self._built:
            logger.info("Using cached app registry with %s entries", len(self._registry))
            return self.registry

        collected: dict[str, list[str]] = {}
        self._merge_entries(collected, self.scan_start_menu())
        self._merge_entries(collected, self.scan_program_files())
        candidate_registry = {name: self._rank_paths(paths) for name, paths in collected.items() if paths}
        previous_count = len(self._last_known_good_registry)
        new_count = len(candidate_registry)
        if self._is_suspicious_drop(previous_count, new_count):
            log_event(
                logger,
                "registry_validation_failed",
                success=False,
                previous_count=previous_count,
                new_count=new_count,
                cache_fallback=bool(self._last_known_good_registry),
            )
            if self._last_known_good_registry:
                self._registry = self._expand_snapshot(self._last_known_good_registry)
                self._built = True
                logger.warning(
                    "Registry scan looked suspicious (%s -> %s). Falling back to cached registry.",
                    previous_count,
                    new_count,
                )
                return self.registry
        else:
            log_event(
                logger,
                "registry_validation_passed",
                success=True,
                previous_count=previous_count,
                new_count=new_count,
            )

        self._registry = candidate_registry
        self._built = True
        self._save_snapshot()
        logger.info("App registry built with %s entries", len(self._registry))
        return self.registry

    def scan_start_menu(self) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for root in self._start_menu_roots:
            if not root.exists():
                continue
            for candidate in root.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in {".lnk", ".appref-ms", ".exe"}:
                    continue
                self._add_entry(entries, candidate)
        logger.info("Scanned Start Menu roots and found %s app entries", len(entries))
        return entries

    def scan_program_files(self) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for root in self._program_roots:
            if not root.exists():
                continue
            root_is_windows_apps = root.name.lower() == "windowsapps"
            for dirpath, dirnames, filenames in os.walk(root):
                if not root_is_windows_apps:
                    dirnames[:] = [name for name in dirnames if name.lower() not in {"windowsapps", "modifi"}]
                for filename in filenames:
                    if not filename.lower().endswith(".exe"):
                        continue
                    self._add_entry(entries, Path(dirpath) / filename)
        logger.info("Scanned program roots and found %s executable entries", len(entries))
        return entries

    def find_app(self, query: str) -> str | None:
        match = self.find_app_match(query)
        return str(match["path"]) if match else None

    def find_app_match(self, query: str) -> dict[str, str | float] | None:
        if not self._built:
            self.build_registry()

        normalized_query = self._normalize_query(query)
        logger.info("App registry normalized query '%s' -> '%s'", query, normalized_query)
        if not normalized_query:
            logger.info("App registry received empty query")
            return None

        candidates = self._collect_matches(normalized_query)
        if not candidates:
            logger.info("App registry found no confident match for '%s'", normalized_query)
            return None

        best_match = candidates[0]
        if float(best_match["confidence"]) < 0.6:
            logger.info(
                "App registry rejected weak match query='%s' app='%s' type='%s' confidence=%.2f",
                normalized_query,
                best_match["app"],
                best_match["match_type"],
                float(best_match["confidence"]),
            )
            return None

        logger.info(
            "App registry match type=%s query='%s' app='%s' path='%s' confidence=%.2f",
            best_match["match_type"],
            normalized_query,
            best_match["app"],
            best_match["path"],
            float(best_match["confidence"]),
        )
        return best_match

    def _find_exact_match(self, normalized_query: str) -> str | None:
        paths = self._registry.get(normalized_query)
        if not paths:
            return None
        logger.info("App registry match type=exact query='%s' app='%s' path='%s'", normalized_query, normalized_query, paths[0])
        return paths[0]

    def _find_token_match(self, normalized_query: str) -> str | None:
        query_tokens = self._significant_tokens(normalized_query)
        if not query_tokens:
            return None

        best_name = ""
        best_path = ""
        best_score = (-1, -1)
        for app_name, paths in self._registry.items():
            app_tokens = self._significant_tokens(app_name)
            if not app_tokens:
                continue
            if not query_tokens.issubset(app_tokens):
                continue
            score = (len(query_tokens), -len(app_tokens))
            if score > best_score:
                best_score = score
                best_name = app_name
                best_path = paths[0]

        if best_path:
            logger.info("App registry match type=token query='%s' app='%s' path='%s'", normalized_query, best_name, best_path)
            return best_path
        return None

    def _find_partial_match(self, normalized_query: str) -> str | None:
        query_tokens = self._significant_tokens(normalized_query)
        if not query_tokens:
            return None

        best_name = ""
        best_path = ""
        best_score = -1
        for app_name, paths in self._registry.items():
            app_tokens = self._significant_tokens(app_name)
            if not app_tokens:
                continue
            score = self._partial_match_score(normalized_query, query_tokens, app_name, app_tokens)
            if score > best_score:
                best_score = score
                best_name = app_name
                best_path = paths[0]

        if best_score <= 0:
            return None

        logger.info("App registry match type=partial query='%s' app='%s' path='%s'", normalized_query, best_name, best_path)
        return best_path

    def _collect_matches(self, normalized_query: str) -> list[dict[str, str | float]]:
        matches: list[dict[str, str | float]] = []
        query_tokens = self._significant_tokens(normalized_query)
        for app_name, paths in self._registry.items():
            if not paths:
                continue
            app_tokens = self._significant_tokens(app_name)
            match_type, confidence = self._score_match(normalized_query, query_tokens, app_name, app_tokens)
            if not match_type or confidence <= 0:
                continue
            matches.append(
                {
                    "app": app_name,
                    "path": paths[0],
                    "match_type": match_type,
                    "confidence": round(confidence, 2),
                    "priority": float(self._match_priority(match_type)),
                    "score": round(confidence, 2),
                }
            )
        return sorted(matches, key=lambda item: (-float(item["priority"]), -float(item["score"]), len(str(item["app"]))))

    def _score_match(
        self,
        normalized_query: str,
        query_tokens: set[str],
        app_name: str,
        app_tokens: set[str],
    ) -> tuple[str, float]:
        if normalized_query == app_name:
            return "exact", 1.0

        if normalized_query in app_tokens:
            return "alias", 0.85

        ratio = SequenceMatcher(None, normalized_query, app_name).ratio()
        if ratio >= 0.82:
            return "fuzzy", min(0.95, ratio)

        partial_confidence = 0.0
        if normalized_query in app_name and len(normalized_query) >= 4:
            partial_confidence = max(partial_confidence, (len(normalized_query) / max(len(app_name), 1)) * 0.4)
        elif app_name in normalized_query and len(app_name) >= 4:
            partial_confidence = max(partial_confidence, (len(app_name) / max(len(normalized_query), 1)) * 0.4)
        elif query_tokens and query_tokens.issubset(app_tokens):
            overlap = len(query_tokens) / max(len(app_tokens), 1)
            partial_confidence = max(partial_confidence, overlap * 0.4)

        if partial_confidence > 0:
            return "partial", min(0.4, partial_confidence)
        return "", 0.0

    def _match_priority(self, match_type: str) -> int:
        priorities = {
            "exact": 4,
            "alias": 3,
            "fuzzy": 2,
            "partial": 1,
        }
        return priorities.get(match_type, 0)

    def _partial_match_score(
        self,
        normalized_query: str,
        query_tokens: set[str],
        app_name: str,
        app_tokens: set[str],
    ) -> int:
        if normalized_query in app_name:
            return 70 if len(normalized_query) >= 4 else -1

        if app_name in normalized_query:
            if len(app_tokens) >= 2:
                return 60
            if len(query_tokens) == 1:
                return 55
            return -1

        return -1

    def _default_start_menu_roots(self) -> list[Path]:
        roots = [
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        ]
        return [root for root in roots if str(root).strip()]

    def _default_program_roots(self) -> list[Path]:
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
            Path(os.environ.get("APPDATA", "")),
        ]
        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            resolved = str(root)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            deduped.append(root)
        return deduped

    def _merge_entries(self, target: dict[str, list[str]], source: dict[str, list[str]]) -> None:
        for name, paths in source.items():
            bucket = target.setdefault(name, [])
            for path in paths:
                if path not in bucket:
                    bucket.append(path)

    def _add_entry(self, entries: dict[str, list[str]], path: Path) -> None:
        path_str = str(path)
        for candidate_name in self._candidate_names(path):
            bucket = entries.setdefault(candidate_name, [])
            if path_str not in bucket:
                bucket.append(path_str)

    def _candidate_names(self, path: Path) -> set[str]:
        candidates: set[str] = set()
        raw_names = {
            path.name,
            path.stem,
            path.parent.name,
        }
        for raw_name in raw_names:
            normalized = self._normalize_name(raw_name)
            if normalized:
                candidates.add(normalized)
        return candidates

    def _normalize_query(self, value: str) -> str:
        cleaned = self._normalize_name(value)
        cleaned = re.sub(r"\b(for me|for us|please|now|app|application)\b", " ", cleaned)
        return self._collapse_spaces(cleaned)

    def _normalize_name(self, value: str) -> str:
        cleaned = (value or "").strip().lower()
        cleaned = re.sub(r"\.(exe|lnk|appref-ms)$", "", cleaned)
        cleaned = cleaned.replace("_", " ").replace("-", " ")
        cleaned = re.sub(r"[^a-z0-9\s]+", " ", cleaned)
        return self._collapse_spaces(cleaned)

    def _collapse_spaces(self, value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip())

    def _significant_tokens(self, value: str) -> set[str]:
        return {token for token in value.split() if token and token not in {"app", "application", "for", "me", "us"}}

    def _rank_paths(self, paths: list[str]) -> list[str]:
        return sorted(set(paths), key=self._path_rank, reverse=True)

    def _path_rank(self, path: str) -> tuple[int, int, int]:
        lowered = path.lower()
        suffix = Path(path).suffix.lower()
        is_exe = int(suffix == ".exe")
        is_windows_apps = int("windowsapps" in lowered)
        preferred = int("start menu" not in lowered)
        return (is_exe, is_windows_apps, preferred)

    def _save_snapshot(self) -> None:
        try:
            snapshot = self.registry
            self.cache_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            self._last_known_good_registry = dict(snapshot)
        except OSError as exc:
            logger.warning("Failed to save app registry snapshot to %s: %s", self.cache_path, exc)

    def _load_snapshot(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load app registry snapshot from %s: %s", self.cache_path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        snapshot: dict[str, str] = {}
        for name, path in data.items():
            clean_name = self._normalize_name(str(name))
            clean_path = str(path).strip()
            if clean_name and clean_path:
                snapshot[clean_name] = clean_path
        return snapshot

    def _expand_snapshot(self, snapshot: dict[str, str]) -> dict[str, list[str]]:
        return {name: [path] for name, path in snapshot.items() if name and path}

    def _is_suspicious_drop(self, previous_count: int, new_count: int) -> bool:
        if previous_count < self.MINIMUM_SUSPICIOUS_BASELINE:
            return False
        if new_count == 0:
            return True
        return new_count < int(previous_count * self.SUSPICIOUS_DROP_RATIO)
