"""Import a user-downloaded, official ESCO CSV package into the local database.

ESCO datasets are intentionally not bundled with SampoAgent. The user downloads
the files directly from the European Commission and chooses which language
files to index locally.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
from typing import BinaryIO, Iterable
import zlib
from zipfile import BadZipFile, ZipFile

from sampoagent.careers.taxonomy import TaxonomyOccupation, TaxonomyOccupationSkill
from sampoagent.db.repository import Repository


ESCO_DOWNLOAD_URL = "https://esco.ec.europa.eu/en/use-esco/download"
ESCO_LICENSE_STATEMENT = (
    "European Commission ESCO reuse conditions; CC BY 4.0 applies where indicated in the source dataset."
)
ESCO_ATTRIBUTION = "This service uses the ESCO classification of the European Commission."
ESCO_MODIFIED_NOTICE = "SampoAgent builds a local multilingual matching index from the imported ESCO files."
ESCO_QUALITY_NOTICE = "ESCO states that accuracy, currency and translation completeness are not guaranteed."
_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")
MAX_ESCO_ARCHIVE_BYTES = 160 * 1024 * 1024
MAX_ESCO_ARCHIVE_MEMBERS = 512
MAX_ESCO_SELECTED_CSV_BYTES = 320 * 1024 * 1024


@dataclass(frozen=True)
class TaxonomyImportSummary:
    version: str
    languages: tuple[str, ...]
    occupations: int
    skills: int
    relationships: int
    skipped_relationships: int
    source_sha256: str


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _column(headers: dict[str, str], *aliases: str) -> str | None:
    for alias in aliases:
        name = headers.get(_header(alias))
        if name is not None:
            return name
    return None


def _csv_rows(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(stream, dialect=dialect)
            if not reader.fieldnames:
                raise ValueError(f"ESCO CSV has no header: {path.name}")
            headers = {_header(name): name for name in reader.fieldnames if name}
            rows = [{str(key): str(value or "").strip() for key, value in row.items() if key} for row in reader]
            return headers, rows
    except (OSError, UnicodeError, csv.Error) as error:
        raise ValueError(f"Could not read ESCO CSV {path.name}: {error}") from error


def _find_file(package: Path, stem: str, language: str | None = None) -> Path:
    package_files = [path for path in package.rglob("*.csv") if path.is_file()]
    if language:
        expected = f"{stem}_{language}".casefold()
        candidates = [path for path in package_files if path.stem.casefold() == expected]
    else:
        exact = [path for path in package_files if path.stem.casefold() == stem.casefold()]
        candidates = exact or [path for path in package_files if path.stem.casefold().startswith(f"{stem}_".casefold())]
    if not candidates:
        suffix = f"_{language}" if language else ""
        raise ValueError(f"ESCO package is missing {stem}{suffix}.csv")
    if len(candidates) > 1:
        fingerprints = {sha256(path.read_bytes()).hexdigest() for path in candidates}
        if len(fingerprints) != 1:
            raise ValueError(f"ESCO package has ambiguous {stem} CSV files")
    return sorted(candidates, key=lambda path: str(path).casefold())[0]


def _find_files(package: Path, stem: str) -> list[Path]:
    prefix = f"{stem}_".casefold()
    candidates = [
        path for path in package.rglob("*.csv")
        if path.is_file() and (path.stem.casefold() == stem.casefold() or path.stem.casefold().startswith(prefix))
    ]
    if not candidates:
        raise ValueError(f"ESCO package is missing {stem}.csv")
    return sorted(candidates, key=lambda path: str(path).casefold())


def import_esco_archive(
    repository: Repository,
    archive_file: BinaryIO,
    *,
    version: str,
    languages: Iterable[str] = ("fi", "en"),
) -> TaxonomyImportSummary:
    """Safely import selected CSV members from a user-downloaded ESCO ZIP.

    Only the chosen occupation/skill CSVs and occupation-skill relation CSVs
    are materialized in a temporary directory. The archive itself is never
    retained, and unsafe paths, symlinks, encrypted members, and oversized
    inputs fail before the current taxonomy index is replaced.
    """
    if not _VERSION.fullmatch(version):
        raise ValueError("ESCO version must look like 1.2.1")
    selected = tuple(sorted({str(language).casefold() for language in languages}))
    if not selected or any(not _LANGUAGE.fullmatch(language) for language in selected):
        raise ValueError("Select one or more valid ESCO language codes")
    try:
        archive_file.seek(0, 2)
        compressed_bytes = archive_file.tell()
        archive_file.seek(0)
    except (AttributeError, OSError, ValueError):
        raise ValueError("ESCO ZIP must be a seekable file.") from None
    if compressed_bytes <= 0 or compressed_bytes > MAX_ESCO_ARCHIVE_BYTES:
        raise ValueError("ESCO ZIP is empty or exceeds the upload size limit.")

    requested_names = {
        f"{stem}_{language}.csv".casefold()
        for language in selected
        for stem in ("occupations", "skills")
    }
    try:
        zipped = ZipFile(archive_file)
    except (BadZipFile, OSError, ValueError):
        raise ValueError("ESCO upload is not a readable ZIP archive.") from None

    with zipped, tempfile.TemporaryDirectory(prefix="sampoagent-esco-") as temporary:
        try:
            infos = zipped.infolist()
        except (BadZipFile, OSError, EOFError, struct.error):
            raise ValueError("ESCO ZIP has an invalid central directory.") from None
        if len(infos) > MAX_ESCO_ARCHIVE_MEMBERS:
            raise ValueError("ESCO ZIP contains too many archive entries.")
        selected_infos = []
        selected_bytes = 0
        for info in infos:
            member_path = PurePosixPath(info.filename)
            if (
                "\\" in info.filename
                or member_path.is_absolute()
                or ".." in member_path.parts
                or (member_path.parts and ":" in member_path.parts[0])
            ):
                raise ValueError("ESCO ZIP contains an unsafe archive path.")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("ESCO ZIP may not contain symbolic links.")
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted ESCO ZIP members are not supported.")
            if info.is_dir():
                continue
            basename = member_path.name.casefold()
            relation_file = basename.startswith("occupationskillrelations") and basename.endswith(".csv")
            if basename not in requested_names and not relation_file:
                continue
            if info.file_size < 0 or info.file_size > MAX_ESCO_SELECTED_CSV_BYTES:
                raise ValueError("ESCO CSV exceeds the local import size limit.")
            selected_bytes += info.file_size
            if selected_bytes > MAX_ESCO_SELECTED_CSV_BYTES:
                raise ValueError("Selected ESCO CSV files exceed the local import size limit.")
            selected_infos.append(info)
        if not selected_infos:
            raise ValueError("ESCO ZIP contains no CSV files for the selected languages.")

        extracted = Path(temporary)
        for index, info in enumerate(selected_infos):
            destination_dir = extracted / f"member-{index:04d}"
            destination_dir.mkdir()
            destination = destination_dir / PurePosixPath(info.filename).name
            try:
                with zipped.open(info) as source, destination.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            except (BadZipFile, OSError, RuntimeError, EOFError, NotImplementedError, struct.error, zlib.error):
                raise ValueError("ESCO ZIP is corrupt or cannot be read.") from None

        return import_esco_package(repository, extracted, version=version, languages=selected)


def _read_concepts(path: Path, *, occupations: bool) -> list[dict[str, str]]:
    headers, rows = _csv_rows(path)
    uri_key = _column(headers, "conceptUri", "concept URI", "uri")
    label_key = _column(headers, "preferredLabel", "concept PT", "preferred term", "label")
    type_key = _column(headers, "conceptType", "concept type", "type")
    description_key = _column(headers, "definition", "description")
    isco_key = _column(headers, "iscoGroup", "isco code", "iscoCode", "isco group") if occupations else None
    if not uri_key or not label_key:
        kind = "occupations" if occupations else "skills"
        raise ValueError(f"ESCO {kind} file {path.name} lacks a concept URI or preferred label column")
    concepts: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        uri = row.get(uri_key, "").strip()
        label = row.get(label_key, "").strip()
        concept_type = row.get(type_key, "").strip().upper() if type_key else ""
        if not uri or not label or (concept_type and concept_type not in ({"OC"} if occupations else {"SK"})):
            continue
        if uri in seen:
            raise ValueError(f"ESCO {path.name} contains duplicate concept URI {uri}")
        seen.add(uri)
        concepts.append({
            "uri": uri,
            "label": label,
            "description": row.get(description_key, "").strip() if description_key else "",
            "isco_code": row.get(isco_key, "").strip() if isco_key else "",
        })
    return concepts


def _read_relationships(path: Path, occupation_uris: set[str], skill_uris: set[str]) -> tuple[list[tuple[str, str, str]], int]:
    headers, rows = _csv_rows(path)
    occupation_key = _column(headers, "occupationUri", "occupation URI", "occupation")
    skill_key = _column(headers, "skillUri", "skill URI", "conceptUri", "skill")
    importance_key = _column(headers, "relationType", "importance", "relation", "relationshipType")
    if not occupation_key or not skill_key:
        raise ValueError(f"ESCO relationship file {path.name} lacks occupation or skill URI columns")
    relationships: dict[tuple[str, str], str] = {}
    skipped = 0
    for row in rows:
        occupation_uri = row.get(occupation_key, "").strip()
        skill_uri = row.get(skill_key, "").strip()
        if not occupation_uri or not skill_uri:
            continue
        if occupation_uri not in occupation_uris or skill_uri not in skill_uris:
            skipped += 1
            continue
        importance = (row.get(importance_key, "") if importance_key else "").strip().upper()
        if importance not in {"ESSENTIAL", "OPTIONAL"}:
            importance = "UNSPECIFIED"
        key = (occupation_uri, skill_uri)
        existing = relationships.get(key)
        if existing is None or importance == "ESSENTIAL":
            relationships[key] = importance
    result = [(occupation_uri, skill_uri, importance) for (occupation_uri, skill_uri), importance in sorted(relationships.items())]
    return result, skipped


def import_esco_package(
    repository: Repository,
    package_dir: str | Path,
    *,
    version: str,
    languages: Iterable[str] = ("fi", "en"),
) -> TaxonomyImportSummary:
    """Validate and atomically replace the local ESCO matching index."""
    if not _VERSION.fullmatch(version):
        raise ValueError("ESCO version must look like 1.2.1")
    package = Path(package_dir)
    if not package.is_dir():
        raise ValueError("ESCO package directory does not exist")
    selected = tuple(sorted({str(language).casefold() for language in languages}))
    if not selected or any(not _LANGUAGE.fullmatch(language) for language in selected):
        raise ValueError("Select one or more valid ESCO language codes")

    occupations: list[dict[str, str]] = []
    skills: list[dict[str, str]] = []
    language_files: list[tuple[str, list[dict[str, str]], list[dict[str, str]], Path, Path]] = []
    for language in selected:
        occupation_file = _find_file(package, "occupations", language)
        skill_file = _find_file(package, "skills", language)
        occupation_rows = _read_concepts(occupation_file, occupations=True)
        skill_rows = _read_concepts(skill_file, occupations=False)
        if not occupation_rows or not skill_rows:
            raise ValueError(f"ESCO {language} files contain no usable occupations or skills")
        occupations.extend({**item, "language": language} for item in occupation_rows)
        skills.extend({**item, "language": language} for item in skill_rows)
        language_files.append((language, occupation_rows, skill_rows, occupation_file, skill_file))

    relationship_files = _find_files(package, "occupationSkillRelations")
    occupation_uris = {item["uri"] for item in occupations}
    skill_uris = {item["uri"] for item in skills}
    relationship_map: dict[tuple[str, str], str] = {}
    skipped = 0
    for relationship_file in relationship_files:
        current, skipped_in_file = _read_relationships(relationship_file, occupation_uris, skill_uris)
        skipped += skipped_in_file
        for occupation_uri, skill_uri, importance in current:
            key = (occupation_uri, skill_uri)
            existing = relationship_map.get(key)
            if existing is None or importance == "ESSENTIAL":
                relationship_map[key] = importance
    relationships = [
        (occupation_uri, skill_uri, importance)
        for (occupation_uri, skill_uri), importance in sorted(relationship_map.items())
    ]
    if not relationships:
        raise ValueError("ESCO package contains no valid occupation-skill relationships")

    source_files = [path for _, _, _, occupation_file, skill_file in language_files for path in (occupation_file, skill_file)]
    source_files.extend(relationship_files)
    digest = sha256()
    for path in sorted(source_files, key=lambda item: str(item).casefold()):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    repository.replace_esco_taxonomy(
        occupations=occupations,
        skills=skills,
        relationships=relationships,
        metadata={
            "version": version,
            "languages": ",".join(selected),
            "source_url": ESCO_DOWNLOAD_URL,
            "license_statement": ESCO_LICENSE_STATEMENT,
            "attribution": ESCO_ATTRIBUTION,
            "modified": ESCO_MODIFIED_NOTICE,
            "quality_notice": ESCO_QUALITY_NOTICE,
            "source_sha256": digest.hexdigest(),
            "imported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return TaxonomyImportSummary(
        version=version,
        languages=selected,
        occupations=len({item["uri"] for item in occupations}),
        skills=len({item["uri"] for item in skills}),
        relationships=len(relationships),
        skipped_relationships=skipped,
        source_sha256=digest.hexdigest(),
    )
