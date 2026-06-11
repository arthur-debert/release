/**
 * Pure half of the release-canary-vscode-ext synthetic extension.
 *
 * Exists so the canary has a unit-testable surface that does not need a
 * VS Code host (`node --test` runs against the compiled output). See
 * arthur-debert/release#587.
 */

/** Build the canary's liveness line for `name`. */
export function report(name: string): string {
  return `${name} is alive`;
}
