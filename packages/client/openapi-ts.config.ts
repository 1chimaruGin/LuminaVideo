import { defineConfig } from '@hey-api/openapi-ts'

/**
 * The typed API client is GENERATED from FastAPI's OpenAPI schema. Do not hand-edit
 * src/generated — `make client` overwrites it, and CI fails if the committed output is
 * stale. This is what keeps Pydantic models and frontend types from drifting apart.
 */
export default defineConfig({
  input: 'http://127.0.0.1:8000/openapi.json',
  output: { path: 'src/generated', format: 'prettier' },
  plugins: ['@hey-api/client-fetch'],
})
