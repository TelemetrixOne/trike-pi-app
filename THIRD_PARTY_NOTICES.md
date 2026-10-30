# Third-party components

The project Apache-2.0 licence covers original project code only. It does not
relicense operating-system packages, Python dependencies, GStreamer/FFmpeg,
MediaMTX, the Go standard library or their transitive dependencies.

`vendor/mediamtx/linux_arm64/mediamtx` identifies itself in embedded Go build
metadata as github.com/bluenviron/mediamtx v1.19.1, built with Go 1.26.4,
revision `6a5761f7e6c41ea2202696a0a683809b79646eba`, CGO disabled, Linux ARM64.
Its upstream MIT licence is preserved verbatim in `vendor/mediamtx/LICENSE`:
<https://github.com/bluenviron/mediamtx/tree/v1.19.1>.

Dependency manifests identify Python runtime versions separately; their
distribution metadata/licences remain authoritative. Installing system packages
does not make those components original project code.

`vendor/mediamtx/build-components.json` records all 85 modules named by the
binary's embedded Go build information, the binary SHA256 and build settings.
`vendor/mediamtx/module-notices/index.json` maps preserved upstream notice files
to exact module versions and source archive hashes. The collector obtained 109
notice files from 83 modules. `github.com/benburkert/openpgp` has no recognised
notice file in that module archive; `github.com/cloudflare/circl` exceeded the
bounded collector limit. Both require further review. Collection does not prove
licence compatibility or full binary attribution. The Go toolchain BSD notice
is preserved in `vendor/mediamtx/GO-LICENSE` from its exact tagged source.

Before any public binary distribution, produce and review a complete transitive
licence/SBOM bundle for the embedded MediaMTX dependencies and OS/native media
stack, plus the Python lock. This inventory is not that complete bundle and does
not claim every bundled dependency is MIT. Keep repositories private until the
owner's clean-public-export, historical-secret and third-party review is done.
No copyright ownership has been invented by this preparation.
