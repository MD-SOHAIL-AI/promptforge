# ForgeX bounded braces fork

This private, source-controlled fork is derived from MIT-licensed
[`braces` 3.0.3](https://github.com/micromatch/braces/tree/3.0.3).
Original copyright and license are preserved in LICENSE.

As of 2026-10-03, the upstream package has no patched release for
[CVE-2026-93687](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).
The parser now rejects more than 64 nested brace/parenthesis blocks before
creating the AST. Compile, expand and stringify also enforce a hard depth
limit of 64, including when called directly with externally supplied ASTs.
The limit cannot be disabled by caller options. Rejected input throws a
controlled SyntaxError instead of overflowing the JavaScript call stack.

The package has a distinct name and version to identify this maintained fork;
it is not an upstream patched version. The frontend override routes every
transitive braces consumer through this package. Regression tests verify the
installed dependency used by micromatch and fast-glob, normal pattern behavior,
all public walkers and direct AST entry points. These run in test:release-scripts.
Replace the fork with a verified upstream fix when one becomes available.
