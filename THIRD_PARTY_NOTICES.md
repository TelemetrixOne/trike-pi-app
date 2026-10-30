# Third-party components

The project Apache-2.0 licence covers original project code only. It does not
relicense operating-system packages, Python dependencies, GStreamer/FFmpeg,
MediaMTX, the Go standard library or their transitive dependencies.

The shipped broker is v1.21.3-tm1.1 from MediaMTX v1.21.3 commit
`9540cb6c1d9f1e9351fcc4ded3afc249ffff22bc`, Go 1.27.3, CGO disabled.
Its upstream MIT licence is preserved in `vendor/mediamtx/LICENSE`.
The build patches x/net to v0.62.0, disables independent network self-update
and uses the upstream unsupported-CSI stub. USB capture remains external;
no libcamera/IPA binaries are embedded.

Dependency manifests identify Python runtime versions separately; their
distribution metadata/licences remain authoritative. Installing system packages
does not make those components original project code.

`build-provenance.json`, `source-go.mod`, `source-go.sum` and
`build-components.json` under `vendor/mediamtx` record build/source/binary and
65 compiled Go module identities. `module-notices/index.json` maps 79 notice
files to exact versions and source archive hashes. All 65 have notice evidence.
The standard Go BSD terms are preserved in `GO-LICENSE`.

The module `github.com/benburkert/openpgp` has no root LICENSE. Both architecture
dependency graphs link only `aes/keywrap`. Its complete Copyright 2014 Matthew
Endsley redistribution terms are preserved verbatim from `keywrap.go`.
This notice is package-scoped; no licence is assigned to unlinked files.
See `package-notice-rules.json` and `package-reachability.json`.

The embedded hls.js v1.7.3 asset is Apache-2.0, including Dailymotion and
Brightcove notices. `embedded-assets/hls.js-1.7.3` preserves the exact asset,
project licence, full Apache-2.0 terms, inline bundle notice and complete MIT
terms for its Babel/regenerator helper. hls.js is not labelled MIT.

Both architecture govulncheck reports retain the OpenPGP module-only match.
Neither binary links the affected `golang.org/x/crypto/openpgp` packages;
neither scan identified vulnerable symbols. This is a dated, bounded result,
not a guarantee against every defect or blanket legal/security clearance.

Before any public binary distribution, produce and review a complete transitive
licence/SBOM bundle for the embedded MediaMTX dependencies and OS/native media
stack, plus the Python lock. This inventory is not that complete bundle and does
not claim every bundled dependency is MIT. Keep repositories private until the
owner's clean-public-export, historical-secret and third-party review is done.
No copyright ownership has been invented by this preparation.
