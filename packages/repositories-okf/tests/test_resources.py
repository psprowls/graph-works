from __future__ import annotations

from repositories_okf.resources import SEED_RELATIVE_PATHS, seed_files


def test_the_package_owns_exactly_nine_files() -> None:
    assert SEED_RELATIVE_PATHS == (
        "schema/_base-repository.schema.json",
        "schema/ManagedRepository.schema.json",
        "schema/ReferenceRepository.schema.json",
        "schema/RepositorySnapshot.schema.json",
        "schema/RepositoryChangelog.schema.json",
        "sections/ManagedRepository.yaml",
        "sections/ReferenceRepository.yaml",
        "sections/RepositorySnapshot.yaml",
        "sections/RepositoryChangelog.yaml",
    )


def test_seed_files_reads_every_owned_file() -> None:
    files = seed_files()
    assert tuple(files) == SEED_RELATIVE_PATHS
    assert all(text.endswith("\n") for text in files.values())
