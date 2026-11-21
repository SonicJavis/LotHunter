# LotHunter Security Policy

## Scope

LotHunter is a local-first application for researching and
valuing UK IT liquidation lots.

The application is intended to run on localhost.

## Security principles

- Never commit passwords, tokens, or private keys.
- Keep local SQLite databases outside Git.
- Use least-privilege GitHub Actions permissions.
- Pin external GitHub Actions to reviewed commit SHAs.
- Require passing tests before merging into main.
- Review all AI-generated code before merging.
- Never expose the local dashboard directly to the internet.
- Do not execute downloaded marketplace content.

## Reporting vulnerabilities

Please avoid disclosing exploitable vulnerabilities
or sensitive data in public GitHub issues.

Use GitHub's private vulnerability reporting feature
if it is enabled for this repository.

## Dependencies

Prefer Python standard-library functionality where
practical. Review external dependencies before adoption.
