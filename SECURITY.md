# Security policy

## Supported versions

Only the latest release receives fixes. Update through HACS (or the
Home Assistant Marketplace) to get them.

## Reporting a vulnerability

Please report a vulnerability privately through GitHub's
[private vulnerability reporting](https://github.com/ludgerbeckmann/ha_solarmax/security/advisories/new)
instead of opening a public issue. Include the integration version, a short
description of the problem, and how to reproduce it. Remove network
addresses, serial numbers, and other private details from any logs first.

You can expect an answer within a few days. Fixes are published as a new
release, and the advisory is made public once users can update.

## Scope

The integration talks to an inverter on your local network over the
unauthenticated MaxComm TCP protocol and reads values only. It stores no
cloud credentials. Keep the inverter in a trusted network segment, because
anyone who can reach its TCP port can also talk to it.
