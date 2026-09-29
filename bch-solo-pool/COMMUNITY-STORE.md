# Umbrel Community Store checklist

1. Fork the repository.
2. Change the store ID in `../umbrel-app-store.yml` if desired. The app ID must keep the store-ID prefix.
3. Replace `YOUR_GITHUB_USERNAME` in `umbrel-app.yml` and `docker-compose.yml`.
4. Push to `main`; GitHub Actions publishes a multi-arch image to GHCR.
5. Make the GHCR package public.
6. Pin the `image:` line to the published digest for a release.
7. Add the GitHub repository as a Community App Store in umbrelOS.
8. Install and test against your actual BCHN/AxeBCH node.

For official Umbrel App Store submission, use the package under `bch-solo-pool/` as the app directory and follow the current `getumbrel/umbrel-apps` contribution requirements.
