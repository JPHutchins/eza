# SPDX-FileCopyrightText: 2024 Christina Sørensen
# SPDX-License-Identifier: EUPL-1.2
# /// script
# requires-python = ">=3.11"
# dependencies = ["camas[mcp]>=0.1.27"]
# ///
"""camas task definitions for eza — the single task runner (replaces the justfile).

The fmt/clippy/test leaves are the single source of truth shared with CI: the
main-OS unit-tests cells run them via ``uv run tasks.py`` and the os x rust
matrix is emitted from ``camas unit_tests --github-matrix``. The SSOT is at leaf
granularity — CI still skips clippy on windows in YAML, and the BSD-VM jobs
inline the same commands (uv is impractical to bootstrap there). Runnable
standalone with ``uv run tasks.py <task>`` (PEP 723), so CI and contributors need
only ``uv``. Release and cross-compile leaves shell out to convco/cross/pandoc
and only run on a release host.
"""

from camas import Claude, Config, Parallel, Sequential, Task, run_cli

deny_warnings = {"RUSTFLAGS": "--deny warnings"}

fmt = Task("cargo fmt", mutates=True)
fmt_check = Task("cargo fmt --check")
clippy = Task("cargo clippy -- -D warnings", env=deny_warnings)
test = Task("cargo hack test", env=deny_warnings)

check = Parallel(fmt_check, clippy, test)
all = Sequential(fmt, check)

unit_tests = Parallel(
    check,
    matrix={
        "os": ("ubuntu-latest", "macos-latest", "windows-latest"),
        "rust": ("msrv", "stable", "beta", "nightly"),
    },
    help="the unit-tests os x rust matrix; emit for CI with --github-matrix",
)

build = Task("cargo build")
build_release = Task("cargo build --release --verbose")
build_time = Sequential("cargo +nightly clean", "cargo +nightly build -Z timings")
cargo_check = Task("cargo check")
test_release = Task("cargo test --workspace --release --verbose")

update_deps = Sequential("cargo update", "cargo outdated")
unused_deps = Task("cargo +nightly udeps")
check_features = Task("cargo hack check --feature-powerset")
versions = Sequential("rustc --version", "cargo --version")

nix_check = Task("nix flake check -L", when=("flake.nix", "nix"))

man = Task(
    (
        "bash",
        "-c",
        r"""set -e
mkdir -p "${CARGO_TARGET_DIR:-target}/man"
version=$(awk 'BEGIN { FS = "\"" } ; /^version/ { print $2 ; exit }' Cargo.toml)
for page in eza.1 eza_colors.5 eza_colors-explanation.5; do
    sed "s/\$version/v${version}/g" "man/${page}.md" | pandoc --standalone -f markdown -t man > "${CARGO_TARGET_DIR:-target}/man/${page}"
done
""",
    ),
)
man_1_preview = Sequential(
    man,
    Task(("bash", "-c", r'man "${CARGO_TARGET_DIR:-target}/man/eza.1"'), name="page"),
)
man_5_preview = Sequential(
    man,
    Task(("bash", "-c", r'man "${CARGO_TARGET_DIR:-target}/man/eza_colors.5"'), name="page"),
)
man_5_explanations_preview = Sequential(
    man,
    Task(("bash", "-c", r'man "${CARGO_TARGET_DIR:-target}/man/eza_colors-explanation.5"'), name="page"),
)

mangen = Task(
    (
        "bash",
        "-c",
        r"""set -e
v=$(convco version)
mkdir -p "./target/man-$v"
pandoc --standalone -f markdown -t man man/eza.1.md > "./target/man-$v/eza.1"
pandoc --standalone -f markdown -t man man/eza_colors.5.md > "./target/man-$v/eza_colors.5"
pandoc --standalone -f markdown -t man man/eza_colors-explanation.5.md > "./target/man-$v/eza_colors-explanation.5"
tar czvf "./target/man-$v.tar.gz" "./target/man-$v"
""",
    ),
)

completions = Task(
    (
        "bash",
        "-c",
        r"""set -e
v=$(convco version)
mkdir -p "./target/completions-$v"
cp completions/*/* "./target/completions-$v/"
tar czvf "./target/completions-$v.tar.gz" "./target/completions-$v"
""",
    ),
)


def _cross_build(binary: str, target: str, *, no_libgit: bool = False) -> Task:
    features = "--no-default-features " if no_libgit else ""
    suffix = "_no_libgit" if no_libgit else ""
    return Task(
        (
            "bash",
            "-c",
            f"""set -e
rustup target add {target}
cross build {features}--release --target {target}
v=$(convco version)
tar czvf "./target/bin-$v/{binary}_{target}{suffix}.tar.gz" -C "./target/{target}/release/" "./{binary}"
zip -j "./target/bin-$v/{binary}_{target}{suffix}.zip" "./target/{target}/release/{binary}"
""",
        ),
        name=f"build_{target}{suffix}",
    )


cross = Sequential(
    Task(
        ("bash", "-c", 'set -e\nmkdir -p "./target/bin-$(convco version)"\nrustup toolchain install stable'),
        name="setup",
    ),
    Sequential(
        _cross_build("eza", "x86_64-unknown-linux-gnu"),
        _cross_build("eza", "x86_64-unknown-linux-musl"),
        _cross_build("eza", "aarch64-unknown-linux-gnu"),
        _cross_build("eza", "aarch64-unknown-linux-gnu", no_libgit=True),
        _cross_build("eza", "arm-unknown-linux-gnueabihf"),
        _cross_build("eza", "arm-unknown-linux-gnueabihf", no_libgit=True),
        _cross_build("eza.exe", "x86_64-pc-windows-gnu"),
        name="targets",
    ),
)

checksum = Task(
    (
        "bash",
        "-c",
        r"""set -e
d="./target/bin-$(convco version)"
echo "# Checksums"
echo "## sha256sum"; echo '```'; sha256sum "$d"/*; echo '```'
echo "## md5sum";    echo '```'; md5sum "$d"/*;    echo '```'
echo "## blake3sum"; echo '```'; b3sum "$d"/*;     echo '```'
""",
    ),
)

release = Sequential(
    Task(
        (
            "bash",
            "-c",
            r"""set -e
nv="${VERSION:-$(convco version --bump)}"
cargo bump "$nv"
git cliff -c .config/cliff.toml -t "$nv" > CHANGELOG.md
cargo check
nix build -L ./#clippy
git checkout -b "cafk-release-v$nv"
git commit -asm "chore: eza v$nv changelogs, version bump"
git push
echo "waiting 10 seconds for github to catch up..."
sleep 10
gh pr create --draft --title "chore: release v$nv" --body "This PR was auto-generated by our lovely tasks.py" --reviewer cafkafk
echo "Now go review that and come back and run gh_release"
""",
        ),
        name="run",
    ),
    matrix={"VERSION": ("",)},
)

gh_release = Sequential(
    Task(
        (
            "bash",
            "-c",
            r"""set -e
nv="${VERSION:-$(convco version --bump)}"
git tag -d "v$nv" || echo "tag not found, creating"
git tag --sign -a "v$nv" -m "auto generated by tasks.py for eza v$(convco version)"
""",
        ),
        name="tag",
    ),
    cross,
    mangen,
    completions,
    Task(
        (
            "bash",
            "-c",
            r"""set -e
v=$(convco version)
mkdir -p "./target/release-notes-$v"
git cliff -c .config/cliff.toml -t "v$v" --current > "./target/release-notes-$v/RELEASE.md"
d="./target/bin-$v"
{
  echo "# Checksums"
  echo "## sha256sum"; echo '```'; sha256sum "$d"/*; echo '```'
  echo "## md5sum";    echo '```'; md5sum "$d"/*;    echo '```'
  echo "## blake3sum"; echo '```'; b3sum "$d"/*;     echo '```'
} >> "./target/release-notes-$v/RELEASE.md"
""",
        ),
        name="notes",
    ),
    Task(
        (
            "bash",
            "-c",
            r"""set -e
nv="${VERSION:-$(convco version --bump)}"
v=$(convco version)
git push origin "v$nv"
gh release create "v$v" --target "$(git rev-parse HEAD)" --title "eza v$v" -d -F "./target/release-notes-$v/RELEASE.md" ./target/"bin-$v"/* "./target/man-$v.tar.gz" "./target/completions-$v.tar.gz"
""",
        ),
        name="publish",
    ),
    matrix={"VERSION": ("",)},
)

gen_test_dir = Task("bash devtools/dir-generator.sh tests/test_dir")
itest = Task("nix build -L ./#trycmd-local")
itest_gen = Task("nix build -L ./#trycmd")

idump = Task(
    (
        "bash",
        "-c",
        r"""set -e
rm ./tests/gen/*_nix.stderr -f || echo
rm ./tests/gen/*_nix.stdout -f || echo
rm ./tests/gen/*_unix.stderr -f || echo
rm ./tests/gen/*_unix.stdout -f || echo
rm ./tests/ptests/ptest_*.stderr -f || echo
rm ./tests/ptests/ptest_*.stdout -f || echo
nix build -L ./#trydump
find result/dump -type f \( -name "*.stdout" -o -name "*.stderr" \) -exec sh -c 'base=$(basename {}); if [ -e "tests/gen/${base%.*}.toml" ]; then cp {} tests/gen/; elif [ -e "tests/cmd/${base%.*}.toml" ]; then cp {} tests/cmd/; elif [ -e "tests/ptests/${base%.*}.toml" ]; then cp {} tests/ptests/; fi' \;
""",
    ),
)

regen = Task(
    (
        "bash",
        "-c",
        r"""set -e
which powertest >&- 2>&- || (echo -e "Powertest not installed. Please Clone the repo and run:\n\tcargo install --path . --locked" && exit 1)
echo "WARNING: this will delete all tests in tests/ptest"
sleep 5
echo "Deleting tests/ptests"
rm -rf tests/ptests
echo "Generating tests/ptests"
powertest
nix build -L ./#trydump
find result/dump -type f \( -name "*.stdout" -o -name "*.stderr" \) -exec sh -c 'base=$(basename {}); if [ -e "tests/ptests/${base%.*}.toml" ]; then cp {} tests/ptests/; fi' \;
""",
    ),
)

gen_demo = Task(
    (
        "bash",
        "-c",
        r"""set -e
fish_prompt="> " fish_history="eza_history" vhs < docs/tapes/demo.tape
nsxiv -a docs/images/demo.gif
""",
    ),
)

_ = Config(default_task=all, github_task=check, agent=Claude(fix=fmt, check=check))

if __name__ == "__main__":
    run_cli(globals())
