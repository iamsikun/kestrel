# Selected-source project integration

This additional mode does not replace complete explicit sidecar snapshots or change
any original acceptance requirement. Selection uses project-relative POSIX patterns:
`*` and `?` match within one component, and `**` matches zero or more components.
Exclusions take precedence; required integration files and references cannot be
excluded. Git and `.gitignore` are never consulted or executed. Selected links,
special files, nested repositories and unresolved LFS pointers fail validation.
Traversal is bounded even for excluded content; excluded directories are pruned.

Preview hashes the rules, contract revision, file bytes and executable bits, and
selected empty directories. Creation requires the preview digest and a second
scan before publication. This describes selected contents, never a complete Git
revision. Changes to excluded files do not change source identity. References are
untrusted information, never authorization. Integration files are protected from
candidate edits. Originals are never worker mounts.

Stable connections and immutable contract revisions use new registry tables.
Legacy registration records and campaign identities remain unchanged. Refresh
is explicit; a snapshot fails if the working contract differs from its revision.
