"""Dependency parsing for build configuration files."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from code_intel_mcp.models import (
    Dependency,
    DependencyReport,
    InternalDependency,
    ManagedRepo,
)

logger = logging.getLogger(__name__)

# Maven POM namespace
_MVN_NS = "http://maven.apache.org/POM/4.0.0"
_MVN_PREFIX = f"{{{_MVN_NS}}}"

# Gradle dependency patterns — matches configurations like:
#   implementation 'group:artifact:version'
#   testImplementation "group:artifact:version"
#   api 'group:artifact:version'
#   compileOnly "group:artifact:version"
_GRADLE_DEP_RE = re.compile(
    r"""
    (?P<scope>
        implementation|api|compileOnly|runtimeOnly|
        testImplementation|testCompileOnly|testRuntimeOnly|
        annotationProcessor|kapt|classpath
    )
    \s*
    [(\s]*                          # optional paren or whitespace
    ['"]                            # opening quote
    (?P<group>[^:'"]+)              # group id
    :
    (?P<artifact>[^:'"]+)           # artifact id
    (?::(?P<version>[^'"]*?))?      # optional version
    ['"]                            # closing quote
    """,
    re.VERBOSE,
)

# Map Gradle configuration names to simplified scopes
_GRADLE_SCOPE_MAP: dict[str, str] = {
    "implementation": "compile",
    "api": "compile",
    "compileOnly": "compile",
    "runtimeOnly": "runtime",
    "testImplementation": "test",
    "testCompileOnly": "test",
    "testRuntimeOnly": "test",
    "annotationProcessor": "compile",
    "kapt": "compile",
    "classpath": "compile",
}


class DependencyParser:
    """Parses build configuration files to extract dependency declarations."""

    def parse(self, repo_path: Path) -> DependencyReport:
        """Detect and parse build config at *repo_path* root.

        Scans for pom.xml, build.gradle, build.gradle.kts, and package.json.
        Returns a :class:`DependencyReport` with the parsed dependencies.
        """
        repo_name = repo_path.name

        # Check for build config files in priority order
        candidates: list[tuple[str, str]] = [
            ("pom.xml", "pom.xml"),
            ("build.gradle", "build.gradle"),
            ("build.gradle.kts", "build.gradle.kts"),
            ("package.json", "package.json"),
        ]

        for filename, build_file in candidates:
            config_path = repo_path / filename
            if config_path.is_file():
                try:
                    if filename == "pom.xml":
                        deps = self.parse_pom_xml(config_path)
                    elif filename in ("build.gradle", "build.gradle.kts"):
                        deps = self.parse_build_gradle(config_path)
                    else:
                        deps = self.parse_package_json(config_path)

                    return DependencyReport(
                        repo_name=repo_name,
                        build_file=build_file,
                        dependencies=deps,
                    )
                except Exception:
                    logger.warning(
                        "Failed to parse %s in %s", filename, repo_path, exc_info=True
                    )
                    return DependencyReport(
                        repo_name=repo_name,
                        build_file=build_file,
                        dependencies=[],
                        message=f"Failed to parse {filename}",
                    )

        return DependencyReport(
            repo_name=repo_name,
            build_file=None,
            dependencies=[],
            message="No build configuration found",
        )

    # ------------------------------------------------------------------
    # Maven POM
    # ------------------------------------------------------------------

    def parse_pom_xml(self, path: Path) -> list[Dependency]:
        """Extract dependencies from a Maven POM file.

        Handles both namespaced (``http://maven.apache.org/POM/4.0.0``)
        and non-namespaced POM files.
        """
        tree = ET.parse(path)  # noqa: S314
        root = tree.getroot()

        # Detect whether the POM uses the Maven namespace
        ns = _MVN_PREFIX if root.tag.startswith(_MVN_PREFIX) else ""

        deps: list[Dependency] = []

        # Look in <dependencies> and <dependencyManagement><dependencies>
        dep_containers = root.findall(f".//{ns}dependencies")
        for container in dep_containers:
            for dep_el in container.findall(f"{ns}dependency"):
                group_id = self._xml_text(dep_el, f"{ns}groupId")
                artifact_id = self._xml_text(dep_el, f"{ns}artifactId")
                if not group_id or not artifact_id:
                    continue
                version = self._xml_text(dep_el, f"{ns}version")
                scope = self._xml_text(dep_el, f"{ns}scope")
                deps.append(
                    Dependency(
                        group_id=group_id,
                        artifact_id=artifact_id,
                        version=version,
                        scope=scope,
                    )
                )

        return deps

    @staticmethod
    def _xml_text(parent: ET.Element, tag: str) -> str | None:
        """Return stripped text of a child element, or ``None``."""
        el = parent.find(tag)
        if el is not None and el.text:
            return el.text.strip()
        return None

    # ------------------------------------------------------------------
    # Gradle
    # ------------------------------------------------------------------

    def parse_build_gradle(self, path: Path) -> list[Dependency]:
        """Extract dependencies from a Gradle build file using regex."""
        content = path.read_text(encoding="utf-8")
        deps: list[Dependency] = []

        for match in _GRADLE_DEP_RE.finditer(content):
            scope_raw = match.group("scope")
            group = match.group("group")
            artifact = match.group("artifact")
            version = match.group("version") or None

            deps.append(
                Dependency(
                    group_id=group,
                    artifact_id=artifact,
                    version=version,
                    scope=_GRADLE_SCOPE_MAP.get(scope_raw, scope_raw),
                )
            )

        return deps

    # ------------------------------------------------------------------
    # npm / package.json
    # ------------------------------------------------------------------

    def parse_package_json(self, path: Path) -> list[Dependency]:
        """Extract dependencies from an npm ``package.json`` file."""
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        deps: list[Dependency] = []

        for section, scope in [("dependencies", "compile"), ("devDependencies", "dev")]:
            section_data = data.get(section, {})
            if not isinstance(section_data, dict):
                continue
            for name, version in section_data.items():
                # npm packages use scoped names like @scope/package
                # We treat the scope prefix as group_id
                if name.startswith("@") and "/" in name:
                    group_id, artifact_id = name.split("/", 1)
                else:
                    group_id = ""
                    artifact_id = name

                deps.append(
                    Dependency(
                        group_id=group_id,
                        artifact_id=artifact_id,
                        version=str(version) if version else None,
                        scope=scope,
                    )
                )

        return deps

    # ------------------------------------------------------------------
    # Internal dependency detection
    # ------------------------------------------------------------------

    def find_internal_deps(
        self, deps: list[Dependency], managed_repos: list[ManagedRepo]
    ) -> list[InternalDependency]:
        """Flag dependencies whose ``artifact_id`` matches a managed repo name."""
        repo_names = {repo.name for repo in managed_repos}
        internal: list[InternalDependency] = []

        for dep in deps:
            if dep.artifact_id in repo_names:
                internal.append(
                    InternalDependency(dependency=dep, matched_repo=dep.artifact_id)
                )

        return internal
