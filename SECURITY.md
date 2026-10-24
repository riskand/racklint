# Security policy

racklint holds a NetBox API token, a Vault token if you use Vault, and the credentials for every device it
checks, so security reports are taken seriously.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting instead:
the repository's **Security** tab → **Report a vulnerability**.

Include what an attacker needs (for example: network position, a malicious BMC or NetBox server, access to the
output), what they gain, and steps to reproduce. You should get a first response within a week.

## In scope

- The NetBox token, a Vault token or secret ID, or a device password appearing in output, reports, logs or error
  messages
- Credentials sent to a host other than the configured NetBox URL, the configured Vault or the device they belong
  to
- BMC certificates going unverified while `RACKLINT_BMC_VERIFY_TLS` is on and `--insecure` is not given
- A malicious BMC or NetBox response causing code execution or file writes on the host running racklint

## Known limitations (not vulnerabilities)

- `--insecure` and `RACKLINT_BMC_VERIFY_TLS=false` turn off BMC certificate checks by design.
- With the BMC and switch settings as the credential source, one password applies to every BMC in a run, so every
  BMC checked sees it. Vault or a credentials file gives each device the credentials of its own set.
- Whoever can read racklint's environment or the token file can read the credentials.
