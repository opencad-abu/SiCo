# Bundled ripgrep

CAD AI includes ripgrep 15.2.0 (revision e89fff89ac), by Andrew Gallant and
contributors, from <https://github.com/BurntSushi/ripgrep>. It is distributed
under the MIT license or the Unlicense; both upstream texts are included here.

The executable was supplied in `/software/pkgs/rg/rhel7/rg`; the supplied
`rhel8/rg` is identical. The executable size and SHA256 are pinned in
`terminal/ripgrep-runtime.lock` and the installed runtime's `MANIFEST.txt`.
The source-machine path is provenance only and is not used at runtime.
The supplied README's SHA256 is not used as an executable checksum.

This is the x86_64-unknown-linux-musl static PIE build. It reports bundled
PCRE2 10.45; PCRE2 and musl notices are included alongside the ripgrep licenses.
The musl notice is from the v1.2.5 source tree; the supplied executable does not
identify its exact musl build version. CAD AI's Search tool uses ripgrep's
default Rust regex engine. Direct `rg` commands retain the binary's PCRE2 support.

Upstream release:
<https://github.com/BurntSushi/ripgrep/releases/tag/15.2.0>

Redistribute this notice and the adjacent license files with the executable.
