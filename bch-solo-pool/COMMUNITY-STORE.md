# Umbrel Community Store checklist

1. Add this repository as a Community App Store in umbrelOS.
2. The store ID is `bch-solo`; the app ID `bch-solo-pool` keeps the required prefix.
3. GitHub Actions publishes `ghcr.io/wx256-min/bch-solo-pool`.
4. Make the GHCR package public if the repository is distributed through a public Community App Store.
5. For a release, pin the Docker image to an immutable multi-architecture digest.
6. Install on umbrelOS and use the browser setup page to configure BCHN/AxeBCH.
7. Test the exact node RPC/ZMQ networking and at least one SHA-256 ASIC before relying on it.

Official Umbrel packaging guidance: https://github.com/getumbrel/umbrel-community-app-store
