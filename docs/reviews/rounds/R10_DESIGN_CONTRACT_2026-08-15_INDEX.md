# R10 Design-Contract Review Round — 2026-08-15

## Review domain

`DESIGN_CONTRACT`

Implementation gaps in the prototype are explicitly out of scope for P0/P1 classification. Active-document contradictions, unreachable actors, missing writers, non-unique wire/idempotency rules, crash/replay gaps, and non-executable contract fixtures remain in scope.

## Frozen input

- Commit: `2f12414f5ce371f876d956aadf36f7954c7a43c7`
- Root tree: `dfddd8e78f144eca01c6eeb3920a04563e4411d7`
- Canonical content manifest SHA-256: `257bc0a91c24bba41fd15c546ffa26039f548e4104a8ab8c11c5d1be3747504c`
- Manifest scope: 165 tracked files from the exported commit; manifest itself and this mutable index are outside the reviewed payload.
- Export/tree verification: 165 files, 28 directories, 0 symlinks; independently reconstructed tree matched.

## Independent verdicts

| Stream | Verdict | P0 | P1 | P2 | Raw report SHA-256 |
|---|---:|---:|---:|---:|---|
| A — execution/State/NATS/API | FAIL | 0 | 7 | 2 | raw text `da3378d0e7f1965dff01043ca300bce394c0aa73ff45b9f0bc693efb6e88d22d`; archive `122263c9866ba1aecefe16fd7c6ce83fad3566447bfebaebb294481b37e8890d` |
| B — runtime/security/config/audit/recovery/diagram | FAIL | 0 | 5 | 1 | raw text `813d8b12a850002e6c97cf9441ad9a092d4a465a20ee1c9b5d3daeaba960246f`; archive `17c0b3caf122a804e626756cb24ee23b0c20e1ea01dd6be98dcb80cb61809b5c` |
| C — Artifact/Reconciliation/governance | FAIL | 0 | 5 | 2 | raw text `8965b3a609e99424b0a790339194a7f102e8cdaa31a640d3fdc5772d7e2a38fc`; archive `c51a6c2515545fa17aa7fd8b4e18c87eb35ba396d999a3e9221e18b2b6d98ce9` |

The raw reports are preserved verbatim as `*.raw.gz` sibling archives in this directory. Decompressing each archive reproduces the raw-text SHA-256 recorded above. A FAIL is not rewritten as PASS and no approval is implied by this archive.

## Manifest reconciliation note

Streams A and C independently validated the canonical manifest above. Stream B reported an alternate corpus hash `ccfd6f44774fe9752d042721d130de7a0da1dfde1e4fecb01ef854f677a54575` while independently validating the same Git tree. This discrepancy is preserved as an administrative verification item: the manifest scope, path ordering, mode encoding, and canonicalization rule must be rechecked before the next freeze. It is not silently converted into a design finding or discarded.

## Next remediation modules

1. Containment sequence plus Task/Plan unique terminal and unsafe-recovery writers.
2. Lease/operation idempotency, Stream renew, Plan handoff, and IPC durable-offset ordering.
3. Audit Relay, Recovery role identity, Merge Broker, ACL, and READY closure.
4. Profile-specific required-slot function and diagram trust-boundary closure.
5. Reconciliation operation identity, Artifact hold expiry, and delete-journal completion writer.
6. ACL Markdown parser safety, IPC replay fixture, and TEST authority registry.

This index is a governance record for a failed review round. It is not an approval record.
