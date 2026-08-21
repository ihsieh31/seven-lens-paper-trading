# TradingAgents source inventory

This directory pins the upstream source used as the design and later-porting baseline for P3.
It contains the upstream Apache-2.0 license and a planned-source inventory only. No upstream
runtime Python is vendored or imported by P3-A.

- Repository: `https://github.com/TauricResearch/TradingAgents`
- Commit: `a33fd4c0f134485a43553a2c23a63cb14adbd88f`
- License: Apache-2.0; the exact upstream copy is `LICENSE`.
- Upstream root `NOTICE`: absent at the pinned commit.

The paths in `SOURCE_MANIFEST.json` are candidates for later P3-C/P3-D review. Inclusion does
not authorize copying them. Any later port must record the exact source path, preserve required
attribution, add a prominent modification notice, and pass the project security boundaries.
