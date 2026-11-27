# Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

## Team roles

- **Committers and Reviewers**: [CEHNICA](https://github.com/CEHNICA)
- **Approvers**: [CEHNICA](https://github.com/CEHNICA)

All code changes are hosted in the public GitHub repository `https://github.com/CEHNICA/question-bank-card`, and only repository-approved release tags (`v*`) built on GitHub-hosted runners may trigger a signing request.

## Signing scope and provenance

- **Signed artifacts** (built exclusively by `.github/workflows/release.yml` on GitHub-hosted `windows-latest` runners):
  - `QuestionBankCard.exe`
  - `tiyouju.exe`
  - `TiYouJu-Setup-<version>.exe`
- **Unsigned third-party runtime files**: Bundled Python standard library, wheel runtime dependencies under `_internal/`, and source/license/skill zip archives are included unmodified under their respective open-source licenses and are not signed by SignPath.

## Privacy policy

See [PRIVACY.md](PRIVACY.md) for how TiYouJu handles local data and explicit network requests.
