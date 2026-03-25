"""Tests for DependencyParser."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_intel_mcp.dependencies import DependencyParser
from code_intel_mcp.models import Dependency, ManagedRepo, IndexStatus


@pytest.fixture
def parser() -> DependencyParser:
    return DependencyParser()


# ------------------------------------------------------------------
# pom.xml parsing
# ------------------------------------------------------------------

POM_NAMESPACED = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>5.3.0</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""

POM_NO_NAMESPACE = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <dependencies>
    <dependency>
      <groupId>com.google</groupId>
      <artifactId>guava</artifactId>
      <version>31.0</version>
    </dependency>
  </dependencies>
</project>
"""


def test_parse_pom_xml_namespaced(parser: DependencyParser, tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(POM_NAMESPACED)
    deps = parser.parse_pom_xml(pom)
    assert len(deps) == 2
    assert deps[0].group_id == "org.springframework"
    assert deps[0].artifact_id == "spring-core"
    assert deps[0].version == "5.3.0"
    assert deps[0].scope is None
    assert deps[1].scope == "test"


def test_parse_pom_xml_no_namespace(parser: DependencyParser, tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(POM_NO_NAMESPACE)
    deps = parser.parse_pom_xml(pom)
    assert len(deps) == 1
    assert deps[0].group_id == "com.google"
    assert deps[0].artifact_id == "guava"


# ------------------------------------------------------------------
# build.gradle parsing
# ------------------------------------------------------------------

GRADLE_CONTENT = """\
plugins {
    id 'java'
}

dependencies {
    implementation 'org.springframework:spring-core:5.3.0'
    testImplementation "junit:junit:4.13"
    api 'com.google:guava:31.0'
    compileOnly 'org.projectlombok:lombok:1.18.24'
    runtimeOnly 'mysql:mysql-connector-java:8.0.30'
}
"""


def test_parse_build_gradle(parser: DependencyParser, tmp_path: Path) -> None:
    gradle = tmp_path / "build.gradle"
    gradle.write_text(GRADLE_CONTENT)
    deps = parser.parse_build_gradle(gradle)
    assert len(deps) == 5
    assert deps[0].group_id == "org.springframework"
    assert deps[0].artifact_id == "spring-core"
    assert deps[0].version == "5.3.0"
    assert deps[0].scope == "compile"
    assert deps[1].scope == "test"


def test_parse_build_gradle_no_version(parser: DependencyParser, tmp_path: Path) -> None:
    gradle = tmp_path / "build.gradle"
    gradle.write_text("dependencies {\n    implementation 'com.example:lib'\n}\n")
    deps = parser.parse_build_gradle(gradle)
    assert len(deps) == 1
    assert deps[0].version is None


# ------------------------------------------------------------------
# package.json parsing
# ------------------------------------------------------------------

PACKAGE_JSON = """\
{
  "name": "my-app",
  "version": "1.0.0",
  "dependencies": {
    "express": "^4.18.0",
    "@types/node": "^18.0.0"
  },
  "devDependencies": {
    "jest": "^29.0.0"
  }
}
"""


def test_parse_package_json(parser: DependencyParser, tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text(PACKAGE_JSON)
    deps = parser.parse_package_json(pkg)
    assert len(deps) == 3

    # express — no scope prefix
    express = next(d for d in deps if d.artifact_id == "express")
    assert express.group_id == ""
    assert express.version == "^4.18.0"
    assert express.scope == "compile"

    # @types/node — scoped package
    types_node = next(d for d in deps if d.artifact_id == "node")
    assert types_node.group_id == "@types"

    # jest — devDependency
    jest = next(d for d in deps if d.artifact_id == "jest")
    assert jest.scope == "dev"


# ------------------------------------------------------------------
# parse() — top-level detection
# ------------------------------------------------------------------


def test_parse_detects_pom(parser: DependencyParser, tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text(POM_NO_NAMESPACE)
    report = parser.parse(tmp_path)
    assert report.build_file == "pom.xml"
    assert len(report.dependencies) == 1
    assert report.message is None


def test_parse_detects_gradle(parser: DependencyParser, tmp_path: Path) -> None:
    gradle = tmp_path / "build.gradle"
    gradle.write_text(GRADLE_CONTENT)
    report = parser.parse(tmp_path)
    assert report.build_file == "build.gradle"
    assert len(report.dependencies) == 5


def test_parse_detects_package_json(parser: DependencyParser, tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text(PACKAGE_JSON)
    report = parser.parse(tmp_path)
    assert report.build_file == "package.json"
    assert len(report.dependencies) == 3


def test_parse_no_build_config(parser: DependencyParser, tmp_path: Path) -> None:
    report = parser.parse(tmp_path)
    assert report.build_file is None
    assert report.dependencies == []
    assert report.message == "No build configuration found"


def test_parse_malformed_pom(parser: DependencyParser, tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text("this is not valid xml <><>")
    report = parser.parse(tmp_path)
    assert report.dependencies == []
    assert report.message is not None
    assert "Failed to parse" in report.message


def test_parse_malformed_package_json(parser: DependencyParser, tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text("{invalid json")
    report = parser.parse(tmp_path)
    assert report.dependencies == []
    assert report.message is not None
    assert "Failed to parse" in report.message


# ------------------------------------------------------------------
# find_internal_deps
# ------------------------------------------------------------------


def test_find_internal_deps(parser: DependencyParser) -> None:
    deps = [
        Dependency(group_id="org.springframework", artifact_id="spring-core", version="5.3.0"),
        Dependency(group_id="com.internal", artifact_id="shared-lib", version="1.0.0"),
        Dependency(group_id="com.internal", artifact_id="auth-service", version="2.0.0"),
    ]
    managed = [
        ManagedRepo(
            name="shared-lib",
            git_url="https://gitlab.example.com/group/shared-lib.git",
            local_path=Path("/repos/shared-lib"),
            current_ref="main",
            index_status=IndexStatus.CURRENT,
        ),
        ManagedRepo(
            name="other-repo",
            git_url="https://gitlab.example.com/group/other-repo.git",
            local_path=Path("/repos/other-repo"),
            current_ref="main",
            index_status=IndexStatus.CURRENT,
        ),
    ]
    internal = parser.find_internal_deps(deps, managed)
    assert len(internal) == 1
    assert internal[0].matched_repo == "shared-lib"
    assert internal[0].dependency.artifact_id == "shared-lib"


def test_find_internal_deps_empty(parser: DependencyParser) -> None:
    internal = parser.find_internal_deps([], [])
    assert internal == []


def test_find_internal_deps_no_match(parser: DependencyParser) -> None:
    deps = [
        Dependency(group_id="org.external", artifact_id="external-lib", version="1.0"),
    ]
    managed = [
        ManagedRepo(
            name="my-repo",
            git_url="https://example.com/my-repo.git",
            local_path=Path("/repos/my-repo"),
            current_ref="main",
            index_status=IndexStatus.CURRENT,
        ),
    ]
    internal = parser.find_internal_deps(deps, managed)
    assert internal == []
