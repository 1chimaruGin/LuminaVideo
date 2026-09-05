#!/usr/bin/env node
/**
 * CI guard: regenerate the client and fail if the working tree changes.
 *
 * A stale committed client is the exact failure this setup exists to prevent — the
 * generated types silently disagree with the API and nothing catches it until runtime.
 */
import { execSync } from 'node:child_process'

execSync('pnpm generate', { stdio: 'inherit' })
const dirty = execSync('git status --porcelain -- src/generated', { encoding: 'utf8' }).trim()

if (dirty) {
  console.error('\nGenerated API client is stale. Run `make client` and commit the result:\n')
  console.error(dirty)
  process.exit(1)
}
console.log('Generated API client is up to date.')
