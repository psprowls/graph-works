# Snapshot evidence fixtures

Tests construct temporary local trees and real Git repositories. Byte sequences
include UTF-8 BOM, CRLF, binary blobs and executable files. These are evidence:
never normalize their contents. The hash-pinned `experiments/` subtree is marked
`-text`; this explanatory README remains ordinary LF text. Future byte-exact
fixtures outside `experiments/` need an explicit attribute and checker allowlist
entry rather than inheriting a blanket exemption.

Native mode bits are recorded as evidence, not as Windows ACLs. Git directories
have no executable/permission metadata; their recorded permission mode is zero.
Source symlinks are retained as bytes, never extracted as filesystem links.
