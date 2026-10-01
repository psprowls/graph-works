# repositories-okf

The repositories lane over OKF v0.2. It declares two page types, `ManagedRepository` and `ReferenceRepository`, installs their schemas and section templates into a bundle under `repositories/`, adds the `repository` and `upstream` tags to the bundle's vocabulary, and names the clone glob that graph-works bundle loads ignore so a materialized clone never counts as bundle content.

The package also provides a git runner supplied with its executable and environment, source pins, snapshot rendering, changelog reconciliation and changed-link flagging. The four page types include `RepositorySnapshot` and `RepositoryChangelog`; lifecycle orchestration lives in graph-works-core. Changelog reconciliation preserves human entry text and line endings.
