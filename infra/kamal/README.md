# Kamal deployment

The `cpu-render` pool deploys here, to dedicated hardware. Render minutes are the largest
controllable line in COGS and cost roughly 5–10x more on a hyperscaler, so this pool does not
live on one.

Not configured yet — it lands with build order step 2, when there is something to render.

The API and `light` pool deploy to Fly.io from `infra/docker/api.Dockerfile`.
